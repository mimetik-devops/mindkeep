"""Run with: pytest client/

Only the sync logic is worth testing — it is the part that can delete your files.
"""

import mindstash


def fake_server(monkeypatch, tmp_path, tree: dict[str, str], dirs: list[str] | None = None) -> dict:
    """A server reporting `tree` and `dirs`, answering every download with the same bytes."""
    monkeypatch.setattr(
        mindstash, "call_json", lambda cfg, path: (dirs or []) if path.endswith("folders") else tree
    )
    monkeypatch.setattr(mindstash, "call", lambda cfg, path, body=None, method="", kind="": b"x")
    monkeypatch.setattr(mindstash, "STATE", tmp_path / "state.json")
    return {
        "server": "http://x",
        "token": "t",
        "folder": str(tmp_path / "mirror"),
        "bundle": "default",
    }


def test_sync_leaves_somewhere_to_drop_a_file(tmp_path, monkeypatch):
    """raw/ and wiki/ are the bundle's shape, and an empty directory cannot be downloaded.

    Without this the sweep deleted the raw/ folder login had just created, and a fresh
    mirror had nowhere to put anything.
    """
    cfg = fake_server(monkeypatch, tmp_path, {"index.md": "h"})
    root = tmp_path / "mirror"
    (root / "raw").mkdir(parents=True)
    (root / "stray").mkdir()  # not part of the bundle, so it goes

    mindstash.sync(cfg)

    assert (root / "raw").is_dir()
    assert (root / "wiki").is_dir()
    assert not (root / "stray").exists()
    assert (root / "index.md").read_bytes() == b"x"


def test_sync_still_mirrors_deletions(tmp_path, monkeypatch):
    """The layout folders are kept; the files inside them are not."""
    cfg = fake_server(monkeypatch, tmp_path, {"index.md": "h"})
    root = tmp_path / "mirror"
    (root / "wiki").mkdir(parents=True)
    (root / "wiki" / "gone.md").write_text("deleted on the server", encoding="utf-8")

    mindstash.sync(cfg)

    assert not (root / "wiki" / "gone.md").exists()
    assert (root / "wiki").is_dir()


def test_folders_you_make_in_raw_are_uploaded_as_you_made_them(tmp_path, monkeypatch):
    """raw/ is the owner's to organise, so the path they chose is the path we send."""
    sent: list[str] = []
    cfg = fake_server(monkeypatch, tmp_path, {})
    monkeypatch.setattr(
        mindstash,
        "call",
        lambda cfg, path, body=None, method="": sent.append(f"{method or 'POST'} {path}") or b"",
    )

    root = tmp_path / "mirror"
    (root / "raw" / "papers" / "2026").mkdir(parents=True)
    (root / "raw" / "papers" / "2026" / "synthetic users.md").write_text("x", encoding="utf-8")
    (root / "raw" / "loose.md").write_text("y", encoding="utf-8")

    mindstash.sync(cfg)

    assert sorted(sent) == [
        "POST bundles/default/raw/loose.md",
        "POST bundles/default/raw/papers/2026/synthetic users.md",
    ]


def test_a_local_rename_is_sent_as_a_move(tmp_path, monkeypatch):
    """Otherwise it reaches the server as a delete plus an upload, and the lint deletes
    the pages citing it and writes them again from scratch."""
    calls: list[str] = []
    body = b"the same bytes"
    hashed = mindstash.hashlib.sha256(body).hexdigest()

    cfg = fake_server(monkeypatch, tmp_path, {"raw/note.md": hashed})
    monkeypatch.setattr(
        mindstash,
        "call",
        lambda cfg, path, body=None, method="", kind="": calls.append(f"{method or 'POST'} {path}")
        or b"{}",
    )
    mindstash.remember(cfg, {"raw/note.md": hashed})  # the server had it at the last sync

    root = tmp_path / "mirror"
    (root / "raw" / "papers").mkdir(parents=True)
    (root / "raw" / "papers" / "note.md").write_bytes(body)  # renamed on disk

    mindstash.sync(cfg)

    assert "POST bundles/default/move" in calls
    assert not [c for c in calls if c.startswith("DELETE")]
    assert not [c for c in calls if c.endswith("raw/papers/note.md")]  # not re-uploaded


def test_reorganising_everything_is_not_mistaken_for_losing_everything(tmp_path, monkeypatch):
    """The whole-collection guard fires on deletions, and a move is not a deletion."""
    calls: list[str] = []
    tree = {}
    for name in ("a.md", "b.md"):
        tree[f"raw/{name}"] = mindstash.hashlib.sha256(name.encode()).hexdigest()

    cfg = fake_server(monkeypatch, tmp_path, dict(tree))
    monkeypatch.setattr(
        mindstash,
        "call",
        lambda cfg, path, body=None, method="", kind="": calls.append(f"{method or 'POST'} {path}")
        or b"{}",
    )
    mindstash.remember(cfg, dict(tree))

    root = tmp_path / "mirror"
    (root / "raw" / "papers").mkdir(parents=True)
    for name in ("a.md", "b.md"):
        (root / "raw" / "papers" / name).write_bytes(name.encode())

    mindstash.sync(cfg)

    assert calls.count("POST bundles/default/move") == 2
    assert not [c for c in calls if c.startswith("DELETE")]


def calls_from(monkeypatch, sink: list[str]) -> None:
    monkeypatch.setattr(
        mindstash,
        "call",
        lambda cfg, path, body=None, method="", kind="": sink.append(f"{method or 'POST'} {path}")
        or b"{}",
    )


def test_an_empty_folder_made_on_the_server_appears_on_disk(tmp_path, monkeypatch):
    """No file carries it down, so it has to be asked for and made."""
    cfg = fake_server(monkeypatch, tmp_path, {}, ["papers", "papers/2026"])
    mindstash.sync(cfg)

    root = tmp_path / "mirror"
    assert (root / "raw" / "papers" / "2026").is_dir()

    mindstash.sync(cfg)  # and the sweep does not take it away again
    assert (root / "raw" / "papers" / "2026").is_dir()


def test_an_empty_folder_made_on_disk_is_sent(tmp_path, monkeypatch):
    calls: list[str] = []
    cfg = fake_server(monkeypatch, tmp_path, {}, [])
    calls_from(monkeypatch, calls)

    (tmp_path / "mirror" / "raw" / "essays").mkdir(parents=True)
    mindstash.sync(cfg)

    assert "POST bundles/default/folders/essays" in calls


def test_a_folder_deleted_on_disk_is_deleted_on_the_server(tmp_path, monkeypatch):
    """The other half of the question: a delete here is a delete there, folders included."""
    calls: list[str] = []
    cfg = fake_server(monkeypatch, tmp_path, {}, ["essays"])
    mindstash.sync(cfg)  # arrives, and is remembered

    root = tmp_path / "mirror"
    (root / "raw" / "essays").rmdir()  # deleted here
    calls_from(monkeypatch, calls)
    mindstash.sync(cfg)

    assert "DELETE bundles/default/folders/essays" in calls


def test_a_folder_deleted_on_the_server_is_not_resurrected(tmp_path, monkeypatch):
    """Without the remembered state this looks identical to a folder you just made."""
    calls: list[str] = []
    cfg = fake_server(monkeypatch, tmp_path, {}, ["essays"])
    mindstash.sync(cfg)

    fake_server(monkeypatch, tmp_path, {}, [])  # gone on the server
    calls_from(monkeypatch, calls)
    mindstash.sync(cfg)

    assert not (tmp_path / "mirror" / "raw" / "essays").exists()
    assert not [c for c in calls if "folders" in c]  # neither re-made nor re-deleted


def test_emptying_raw_on_purpose_empties_it_on_the_server(tmp_path, monkeypatch):
    """Deleting every source is allowed — the rest of the mirror shows the folder is there."""
    calls: list[str] = []
    tree = {"CLAUDE.md": "c", "raw/a.md": "h1", "raw/b.md": "h2"}
    cfg = fake_server(monkeypatch, tmp_path, dict(tree))
    mindstash.remember(cfg, dict(tree))

    root = tmp_path / "mirror"
    (root / "raw").mkdir(parents=True)
    (root / "CLAUDE.md").write_text("the manual is still here", encoding="utf-8")

    calls_from(monkeypatch, calls)
    mindstash.sync(cfg)

    assert sorted(c for c in calls if c.startswith("DELETE")) == [
        "DELETE bundles/default/raw/a.md",
        "DELETE bundles/default/raw/b.md",
    ]


def test_a_folder_that_went_missing_deletes_nothing(tmp_path, monkeypatch):
    """The dangerous case: an unmounted drive looks identical to an emptied raw/."""
    calls: list[str] = []
    tree = {"CLAUDE.md": "c", "raw/a.md": "h1", "raw/b.md": "h2"}
    cfg = fake_server(monkeypatch, tmp_path, dict(tree))
    mindstash.remember(cfg, dict(tree))

    calls_from(monkeypatch, calls)
    mindstash.sync(cfg)  # nothing on disk at all

    assert not [c for c in calls if c.startswith("DELETE")]


def test_answers_written_into_todo_md_go_back_up(tmp_path, monkeypatch):
    """The point of keeping the questions in a file: you can work through them in the
    synced folder with Claude Code, and the answers have to reach the server."""
    calls: list[str] = []
    cfg = fake_server(monkeypatch, tmp_path, {"todo.md": "stale"})
    mindstash.remember(cfg, {"todo.md": "stale"})

    root = tmp_path / "mirror"
    root.mkdir(parents=True)
    (root / "todo.md").write_text("- [x] answered locally\n", encoding="utf-8")

    calls_from(monkeypatch, calls)
    mindstash.sync(cfg)

    assert "PUT bundles/default/files/todo.md" in calls


def test_a_todo_only_the_server_changed_is_not_pushed_back(tmp_path, monkeypatch):
    """The agent adding a question must not be mistaken for a local edit and reverted."""
    calls: list[str] = []
    cfg = fake_server(monkeypatch, tmp_path, {"todo.md": "new-on-the-server"})

    root = tmp_path / "mirror"
    root.mkdir(parents=True)
    body = b"- [ ] as it was at the last sync\n"
    (root / "todo.md").write_bytes(body)
    mindstash.remember(cfg, {"todo.md": mindstash.hashlib.sha256(body).hexdigest()})

    calls_from(monkeypatch, calls)
    mindstash.sync(cfg)

    assert not [c for c in calls if c.startswith("PUT")]
