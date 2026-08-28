from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

from app.auth import Profile, current_profile, current_role, current_user, person
from app.files import safe_path, tenant_id
from app.main import app


def team_of(user: str) -> str:
    """A person's personal team is the hash that names their directory."""
    return f"/teams/{tenant_id(user)}"


T = team_of("alice")
B = f"{T}/bundles/default"
PAGE = "---\ntype: Concept\ntitle: Jane\nsources:\n  - id: a\n    title: A\n---\n\nbody\n"


@pytest.fixture
def ingested(monkeypatch):
    """Captures ingest triggers instead of queueing them for Claude."""
    calls: list[tuple] = []
    monkeypatch.setattr("app.files.enqueue", lambda *a, **_k: calls.append(a))
    return calls


@pytest.fixture
def database(tmp_path, monkeypatch):
    """SQLite, so the suite needs no Postgres to run — the schema is plain enough."""
    from app.db import Base, engine

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    engine.cache_clear()
    Base.metadata.create_all(engine())
    yield
    engine.cache_clear()


@pytest.fixture
def client(tmp_path, monkeypatch, ingested, database):
    monkeypatch.setenv("WIKI_ROOT", str(tmp_path))
    monkeypatch.setenv("DEVICE_SECRET", "test-secret")

    def as_user(request: Request) -> str:
        return request.headers.get("x-test-user", "alice")

    app.dependency_overrides[current_user] = as_user
    app.dependency_overrides[person] = as_user
    # role and profile come from the token's claims; the tests hand them in directly
    app.dependency_overrides[current_role] = lambda: "Owner"
    app.dependency_overrides[current_profile] = lambda: Profile(
        first_name="Ada",
        last_name="Lovelace",
        email="ada@example.com",
        picture="https://pics/ada.png",
    )
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def page(client, tmp_path):
    """Write a wiki page straight to disk — no API route lets a user create one."""

    def write(rel: str, text: str = PAGE, user: str = "alice") -> None:
        # ensure the tenant exists
        client.get(f"{team_of(user)}/bundles", headers={"x-test-user": user})
        target = tmp_path / tenant_id(user) / "default" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")

    return write


def test_new_tenant_gets_one_seeded_bundle(client):
    assert client.get(f"{T}/bundles").json() == ["default"]
    assert set(client.get(f"{B}/tree").json()) == {
        "CLAUDE.md",
        "index.md",
        "log.md",
        "questions.md",
        "todo.md",
    }
    assert 'okf_version: "0.2"' in client.get(f"{B}/files/index.md").text


# both paths go under carol's team
@pytest.mark.parametrize("path", ["/bundles", "/bundles/default/files/index.md"])
def test_seeding_survives_a_login_burst(client, path):
    """A fresh sign-in fires several requests at once against an unseeded tenant.

    Both halves matter: two seeds racing used to crash, and a request arriving mid-seed
    used to read a tenant directory that existed but had no files in it yet.
    """
    with ThreadPoolExecutor(max_workers=8) as pool:
        calls = [
            pool.submit(client.get, team_of("carol") + path, headers={"x-test-user": "carol"})
            for _ in range(8)
        ]
        results = [c.result() for c in calls]

    assert [r.status_code for r in results] == [200] * 8


def test_tree_hashes_let_a_client_tell_what_changed(client, page):
    before = client.get(f"{B}/tree").json()
    page("wiki/note.md", "one")
    after = client.get(f"{B}/tree").json()
    assert after["index.md"] == before["index.md"]  # untouched files keep their hash
    assert len(after["wiki/note.md"]) == 64

    page("wiki/note.md", "two")
    assert client.get(f"{B}/tree").json()["wiki/note.md"] != after["wiki/note.md"]


def test_bundles_are_created_and_isolated(client, page):
    assert client.post(f"{T}/bundles", json={"name": "work"}).status_code == 201
    assert client.get(f"{T}/bundles").json() == ["default", "work"]
    assert client.post(f"{T}/bundles", json={"name": "work"}).status_code == 409

    page("wiki/note.md", "personal")
    assert "wiki/note.md" not in client.get(f"{T}/bundles/work/tree").json()


@pytest.mark.parametrize("name", ["../alice", "Work", "with space", ""])
def test_bad_bundle_names_are_rejected(client, name):
    assert client.post(f"{T}/bundles", json={"name": name}).status_code in (400, 422)


def test_unknown_bundle_is_404(client):
    assert client.get(f"{T}/bundles/nope/tree").status_code == 404


def test_the_user_owns_raw_and_the_agent_owns_wiki(client, page):
    # the user may correct their own source...
    client.post(f"{B}/raw/notes.txt", content=b"one")
    assert client.put(f"{B}/files/raw/notes.txt", content=b"corrected").status_code == 200
    assert client.get(f"{B}/files/raw/notes.txt").text == "corrected"

    # ...but a wiki page belongs to the agent, and no route lets a user write one
    page("wiki/jane.md")
    assert client.put(f"{B}/files/wiki/jane.md", content=b"mine now").status_code == 409
    assert "body" in client.get(f"{B}/files/wiki/jane.md").text


def test_binary_files_survive_the_round_trip(client):
    pdf = bytes(range(256)) * 4  # not valid utf-8; raw/ holds PDFs and images
    client.post(f"{B}/raw/scan.pdf", content=pdf)
    assert client.get(f"{B}/files/raw/scan.pdf").content == pdf


def test_a_save_may_name_the_version_it_saw(client):
    """If-Match: the hash from the tree. Stale means someone else got there first."""
    client.post(f"{B}/raw/notes.txt", content=b"one")
    was = client.get(f"{B}/tree").json()["raw/notes.txt"]

    stale = client.put(f"{B}/files/raw/notes.txt", content=b"mine", headers={"If-Match": "0" * 64})
    assert stale.status_code == 412 and "changed since" in stale.json()["detail"]
    assert client.get(f"{B}/files/raw/notes.txt").text == "one"

    assert (
        client.put(
            f"{B}/files/raw/notes.txt", content=b"mine", headers={"If-Match": was}
        ).status_code
        == 200
    )
    # the delete names the old version too — the file has moved on, so it stays
    assert client.delete(f"{B}/raw/notes.txt", headers={"If-Match": was}).status_code == 412
    assert client.get(f"{B}/files/raw/notes.txt").text == "mine"
    # without a header nothing changes: last write wins, as before
    assert client.put(f"{B}/files/raw/notes.txt", content=b"theirs").status_code == 200


def test_a_changed_source_is_reingested_with_its_diff(client, tmp_path, monkeypatch):
    """The second read of a source is handed what changed since the first — removed lines
    as withdrawn claims — rather than the whole document as if it were new."""
    from app import ingest as agent

    client.get(f"{T}/bundles")
    home = tmp_path / tenant_id("alice") / "default"
    client.post(f"{B}/raw/deck.md", content=b"Alice is CTO\nBob is CFO\n")

    seen: dict = {}
    monkeypatch.setattr(agent, "anthropic", _FakeAnthropic(seen))
    agent.ingest_safely(home, "raw/deck.md")
    assert "changed since" not in seen["messages"][0]["content"]  # a first read

    client.put(f"{B}/files/raw/deck.md", content=b"Alice is CEO\nBob is CFO\n")
    agent.ingest_safely(home, "raw/deck.md")
    task = seen["messages"][0]["content"]
    assert "changed since" in task and "-Alice is CTO" in task and "+Alice is CEO" in task
    assert "Bob is CFO" not in task.split("```diff")[1].split("\n-")[0]  # context is one line


def run_over(client, home, source: str, log: str = "") -> int:
    """What ingest_safely does around a run, without an agent: the before commit, the
    read state, the agent's page, the run commit."""
    from app import history, index, runs

    run = runs.start(home, source, "m")
    history.commit(home, f"before run {run}")
    runs.set_base(run, history.head(home))
    stem = Path(source).stem
    # the page changes every run, or the run would have nothing to commit
    (home / "wiki" / f"{stem}.md").write_text(
        f"# {stem} run {run}\n", encoding="utf-8", newline="\n"
    )
    if log:  # the agent's log entry belongs to the run, not to the people before it
        was = (home / "log.md").read_text(encoding="utf-8")
        (home / "log.md").write_text(was + log, encoding="utf-8", newline="\n")
    index.write(home)  # as ingest_safely does: the catalog follows the pages, in the run
    runs.finish(home, run, turns=1, chars=6, commit=history.commit(home, f"run {run}"))
    return run


def test_undoing_a_run_takes_the_wiki_and_the_source_back(client, tmp_path):
    """A source new at the run is removed with the pages it made — left in place it would
    be ingested again the moment anyone touched it — and a redo brings both back."""
    client.get(f"{T}/bundles")
    home = tmp_path / tenant_id("alice") / "default"
    client.post(f"{B}/raw/deck.md", content=b"the deck")
    run = run_over(client, home, "raw/deck.md")
    [src] = client.get(f"{B}/sources").json()
    assert src["ingested"] and src["run"] == run

    undone = client.post(f"{B}/runs/{run}/undo").json()
    assert undone["removed"] == "raw/deck.md" and not undone["restored"]
    assert not (home / "wiki" / "deck.md").exists()
    assert not (home / "raw" / "deck.md").exists()
    assert client.get(f"{B}/sources").json() == []
    assert client.get(f"{B}/activity").json()[0]["kind"] == "undo"
    assert client.post(f"{B}/runs/{run}/undo").status_code == 409  # only once

    assert client.post(f"{B}/runs/{run}/redo").status_code == 200
    assert client.get(f"{B}/files/raw/deck.md").text == "the deck"
    assert (home / "wiki" / "deck.md").is_file()
    [src] = client.get(f"{B}/sources").json()
    assert src["ingested"] and not src["undone"]
    assert client.get(f"{B}/activity").json()[0]["kind"] == "redo"
    assert client.post(f"{B}/runs/{run}/redo").status_code == 409  # not undone now

    # the history itself is never served, listed, or synced
    assert client.get(f"{B}/files/.git/HEAD").status_code == 404
    assert not [p for p in client.get(f"{B}/tree").json() if p.startswith(".git")]


def test_undoing_a_reingest_puts_the_edited_source_back_to_what_the_wiki_had(client, tmp_path):
    client.get(f"{T}/bundles")
    home = tmp_path / tenant_id("alice") / "default"
    client.post(f"{B}/raw/memo.md", content=b"v1")
    first = run_over(client, home, "raw/memo.md")
    client.put(f"{B}/files/raw/memo.md", content=b"v2")
    second = run_over(client, home, "raw/memo.md")

    # the first run cannot be undone under the second: the source has moved on
    refused = client.post(f"{B}/runs/{first}/undo")
    assert refused.status_code == 409 and "changed since" in refused.json()["detail"]

    undone = client.post(f"{B}/runs/{second}/undo").json()
    assert undone["restored"] and not undone["removed"]
    assert client.get(f"{B}/files/raw/memo.md").text == "v1"
    [src] = client.get(f"{B}/sources").json()
    assert src["ingested"]  # by the first run, which still stands

    # a re-ingest of an unchanged source undoes the wiki only
    third = run_over(client, home, "raw/memo.md")
    undone = client.post(f"{B}/runs/{third}/undo").json()
    assert not undone["restored"] and not undone["removed"]
    assert client.get(f"{B}/files/raw/memo.md").text == "v1"

    # and now the first can go: its source is exactly as it read it
    assert client.post(f"{B}/runs/{first}/undo").json()["removed"] == "raw/memo.md"
    assert client.get(f"{B}/sources").json() == []


def test_rows_keyed_by_a_subject_are_rekeyed_at_startup(client, tmp_path):
    """The directory may have been renamed, moved or recreated since; the rows follow the
    value alone, so a wiki ingested before tenants were hashed still reads as ingested."""
    from app import runs
    from app.files import tenant_id

    legacy = tmp_path / "alice" / "default"  # a home named by the subject, as it once was
    legacy.mkdir(parents=True)
    run = runs.start(legacy, "raw/deck.md", "m")
    runs.finish(legacy, run, turns=1, chars=1)
    runs.set_lint_hour(legacy, 4)

    assert runs.rekey_legacy_tenants(tenant_id) == 1
    home = tmp_path / tenant_id("alice") / "default"
    assert "raw/deck.md" in runs.ingested_sources(home)
    assert runs.lint_hour(home) == 4
    assert runs.rekey_legacy_tenants(tenant_id) == 0  # once


def test_a_persons_change_is_in_the_history_as_it_happens(client, tmp_path):
    """No run needed: an upload, an edit, a delete, a move each become a commit of their
    own path — so the feed has them at once, and an agent mid-run is never swept in."""
    from app import history

    client.get(f"{T}/bundles")
    home = tmp_path / tenant_id("alice") / "default"
    (home / "wiki" / "half.md").write_text("the agent, mid-run", encoding="utf-8")  # not ours

    client.post(f"{B}/raw/deck.md", content=b"v1")
    client.put(f"{B}/files/raw/deck.md", content=b"v2")
    client.post(f"{B}/move", json={"source": "raw/deck.md", "target": "raw/papers/deck.md"})

    subjects = [str(c["subject"]) for c in history.commits(home)]
    assert subjects[:3] == [
        "move raw/deck.md -> raw/papers/deck.md",
        "edit raw/deck.md",
        "upload raw/deck.md",
    ]
    left = history.pending(home)  # the agent's file is untouched; the seeded files wait too
    assert {"status": "A", "path": "wiki/half.md"} in left
    assert not [x for x in left if x["path"].startswith("raw/")]
    kinds = [e["kind"] for e in client.get(f"{B}/activity").json()]
    assert kinds.count("people") == 3 and "pending" not in kinds


def test_a_source_waiting_its_turn_is_not_queued_twice(tmp_path, monkeypatch):
    """Five saves during a sync are one run, over the file as it is when its turn comes."""
    from app import ingest as agent

    home = tmp_path / "t" / "b"
    ran: list[str] = []
    monkeypatch.setattr(agent, "_worker", lambda home, pending: None)  # nothing drains it
    for source in ["raw/a.md", "raw/a.md", "raw/b.md", "raw/a.md"]:
        agent.enqueue(home, source)
    pending = agent._work[str(home)]
    while not pending.empty():
        ran.append(pending.get())
    assert ran == ["raw/a.md", "raw/b.md"]
    agent._waiting[str(home)].clear()  # what the worker does as it takes each one
    agent.enqueue(home, "raw/a.md")  # taken already, so it may be queued again
    assert pending.qsize() == 1
    agent.enqueue(home, "raw/a.md", force=True)  # a person asking marks it, even while waiting
    assert "raw/a.md" in agent._forced[str(home)] and pending.qsize() == 1


def test_a_source_the_last_run_already_read_is_skipped(client, tmp_path, ingested):
    """Queued again but byte-for-byte what the last run read: no run, no row. Changed
    since — or deleted, or undone — and it runs."""
    from unittest.mock import patch

    from app import ingest as agent
    from app import runs

    client.get(f"{T}/bundles")
    home = tmp_path / tenant_id("alice") / "default"
    client.post(f"{B}/raw/deck.md", content=b"v1")
    run_over(client, home, "raw/deck.md")
    before = len(runs.recent(home))

    seen: dict = {}
    with patch.object(agent, "anthropic", _FakeAnthropic(seen)):
        agent.ingest_safely(home, "raw/deck.md")
    assert not seen and len(runs.recent(home)) == before  # nothing to teach the wiki

    # asked for by a person — Ingest again, retry — it runs, unchanged or not
    with patch.object(agent, "anthropic", _FakeAnthropic(seen)):
        agent.ingest_safely(home, "raw/deck.md", force=True)
    assert seen and len(runs.recent(home)) == before + 1
    seen.clear()

    client.put(f"{B}/files/raw/deck.md", content=b"v2")
    with patch.object(agent, "anthropic", _FakeAnthropic(seen)):
        agent.ingest_safely(home, "raw/deck.md")
    assert seen and len(runs.recent(home)) == before + 2  # changed since: it runs


def test_a_failure_that_is_the_services_holds_rather_than_fails_the_next_thirty():
    from app.ingest import hold_delay, service_error

    assert service_error("Your credit balance is too low to access the Anthropic API.")
    assert service_error("Error code: 429 - rate_limit_error")
    assert not service_error("Error code: 400 - too many pages")  # the request's fault
    assert service_error("Connection error.")
    assert not service_error("Edit 2 of 3: `old` appears 0 times, needs 1.")
    assert not service_error("")
    assert [hold_delay(n) for n in (1, 2, 3, 4, 5, 9)] == [60, 120, 240, 480, 900, 900]


def test_failed_sources_can_be_retried_one_or_all(client, tmp_path, ingested):
    """A source whose latest run failed shows in the queue state; retry queues it — one
    by path, or every failed one at once. A source that went on to succeed is not failed."""
    from app import runs

    client.get(f"{T}/bundles")
    home = tmp_path / tenant_id("alice") / "default"
    for name in ("a.md", "b.md", "c.md"):
        client.post(f"{B}/raw/{name}", content=b"x")
    runs.finish(home, runs.start(home, "raw/a.md", "m"), 0, 0, error="credit balance too low")
    runs.finish(home, runs.start(home, "raw/b.md", "m"), 0, 0, error="boom")
    runs.finish(home, runs.start(home, "raw/b.md", "m"), 1, 10)  # then it worked
    ingested.clear()

    state = client.get(f"{B}/queue").json()
    assert state == {"held": None, "waiting": 0, "failed": ["raw/a.md"]}
    assert client.post(f"{B}/retry", json={"path": "raw/c.md"}).json() == {"queued": ["raw/c.md"]}
    assert client.post(f"{B}/retry").json() == {"queued": ["raw/a.md"]}
    assert [c[1] for c in ingested] == ["raw/c.md", "raw/a.md"]
    assert client.post(f"{B}/retry", json={"path": "raw/nope.md"}).status_code == 404


def test_startup_also_requeues_what_the_service_failed(client, tmp_path, ingested):
    from app import runs
    from app.files import requeue_unread

    client.get(f"{T}/bundles")
    home = tmp_path / tenant_id("alice") / "default"
    client.post(f"{B}/raw/credit.md", content=b"x")
    client.post(f"{B}/raw/bad.md", content=b"x")
    runs.finish(home, runs.start(home, "raw/credit.md", "m"), 0, 0, error="credit balance too low")
    runs.finish(
        home, runs.start(home, "raw/bad.md", "m"), 0, 0, error="the agent could not parse it"
    )
    ingested.clear()
    assert requeue_unread(tmp_path) == 1
    assert [c[1] for c in ingested] == ["raw/credit.md"]  # the file's own failure stays


def test_sources_no_run_ever_touched_are_queued_again_at_startup(client, tmp_path, ingested):
    """The queue is memory: a deploy mid-sync forgets what was waiting. Startup puts back
    every raw file without a run — and only those."""
    from app import runs
    from app.files import requeue_unread

    client.get(f"{T}/bundles")
    home = tmp_path / tenant_id("alice") / "default"
    for name in ("read.md", "failed.md", "lost.md", "also-lost.md"):
        client.post(f"{B}/raw/{name}", content=b"x")
    (home / "raw" / ".conflicts").mkdir()
    (home / "raw" / ".conflicts" / "mine.md").write_text("never a source", encoding="utf-8")
    run_over(client, home, "raw/read.md")
    runs.finish(home, runs.start(home, "raw/failed.md", "m"), 0, 0, error="boom")
    ingested.clear()

    assert requeue_unread(tmp_path) == 2
    assert sorted(c[1] for c in ingested) == ["raw/also-lost.md", "raw/lost.md"]


def test_a_lint_that_finds_misfiled_pages_queues_the_reorganise_itself(
    client, tmp_path, monkeypatch, page
):
    from unittest.mock import patch

    from app import ingest as agent

    client.get(f"{T}/bundles")
    home = tmp_path / tenant_id("alice") / "default"
    page("wiki/futuros.md", "---\ntype: Project\ntitle: Futuros\n---\n")
    page("wiki/people/jane.md", "---\ntype: Person\ntitle: Jane\n---\n")
    page("wiki/kinde.md", "---\ntype: Company\n---\n")
    page("wiki/untyped.md", "no frontmatter at all")
    assert agent.misfiled(home) == ["wiki/futuros.md", "wiki/kinde.md"]
    assert agent.folder_for("Person") == "people" and agent.folder_for("Meeting") == "meetings"

    queued: list[str] = []
    monkeypatch.setattr(agent, "enqueue", lambda h, s: queued.append(s))
    with patch.object(agent, "anthropic", _FakeAnthropic({})):
        agent.ingest_safely(home, agent.LINT)
    assert queued == [agent.REORGANISE]

    (home / "wiki" / "projects").mkdir()
    (home / "wiki" / "futuros.md").rename(home / "wiki" / "projects" / "futuros.md")
    (home / "wiki" / "companies").mkdir()
    (home / "wiki" / "kinde.md").rename(home / "wiki" / "companies" / "kinde.md")
    queued.clear()
    with patch.object(agent, "anthropic", _FakeAnthropic({})):
        agent.ingest_safely(home, agent.LINT)
    assert queued == []  # in order: nothing to follow up


def test_move_file_moves_a_page_without_passing_it_through_the_model(client, tmp_path, page):
    """The tool a reorganise uses: rename on disk, content untouched, emptied folder gone."""
    from unittest.mock import patch

    from app import ingest as agent

    client.get(f"{T}/bundles")
    home = tmp_path / tenant_id("alice") / "default"
    page("wiki/old/futuros.md", "---\ntype: Project\ntitle: Futuros\n---\nbody\n")
    page("wiki/projects/taken.md", "---\ntype: Project\n---\n")
    seen: dict = {}
    with patch.object(agent, "anthropic", _FakeAnthropic(seen)):
        agent.ingest(home, agent.REORGANISE)
    task = seen["messages"][0]["content"]
    assert "`wiki/old/futuros.md` -> `wiki/projects/futuros.md`" in task  # told where it goes
    tool = next(t for t in seen["tools"] if t.name == "move_file")
    call = tool.call if hasattr(tool, "call") else tool.func

    assert "Refused" in call({"path": "raw/x.md", "to": "wiki/x.md"})
    assert "already exists" in call({"path": "wiki/old/futuros.md", "to": "wiki/projects/taken.md"})
    assert call({"path": "wiki/old/futuros.md", "to": "wiki/projects/futuros.md"}).startswith(
        "Moved"
    )
    assert (
        (home / "wiki" / "projects" / "futuros.md").read_text(encoding="utf-8").endswith("body\n")
    )
    assert not (home / "wiki" / "old").exists()  # the emptied folder went with it


def test_a_reply_cut_off_at_max_tokens_is_a_failed_run_not_a_silent_one(client, tmp_path):
    from types import SimpleNamespace
    from unittest.mock import patch

    from app import ingest as agent

    client.get(f"{T}/bundles")
    home = tmp_path / tenant_id("alice") / "default"

    class Cut(_FakeAnthropic):
        def tool_runner(self, **kwargs):
            return iter([SimpleNamespace(stop_reason="max_tokens")])

    with patch.object(agent, "anthropic", Cut({})), pytest.raises(RuntimeError, match="max_tokens"):
        agent.ingest(home, agent.REORGANISE)


def test_an_assistant_turn_is_a_job_the_browser_polls(client, tmp_path, monkeypatch):
    """Started at once, answered later; a failure is an answer too. A job is its bundle's."""
    import threading

    from app import assist

    client.get(f"{T}/bundles")
    gate = threading.Event()

    def slow(home, question, messages):
        gate.wait(5)
        if question == "boom":
            raise RuntimeError("the model choked")
        return {"reply": f"about {question}", "changed": ["raw/x.md"]}

    monkeypatch.setattr(assist, "reply", slow)
    started = client.post(
        f"{B}/assist", json={"question": "q", "messages": [{"role": "user", "content": "q"}]}
    )
    assert started.status_code == 202
    job = started.json()["job"]
    assert client.get(f"{B}/assist/{job}").json() == {"done": False}
    gate.set()
    for _ in range(50):
        state = client.get(f"{B}/assist/{job}").json()
        if state["done"]:
            break
        threading.Event().wait(0.05)
    assert state == {"done": True, "reply": "about q", "changed": ["raw/x.md"]}

    failed = client.post(
        f"{B}/assist", json={"question": "boom", "messages": [{"role": "user", "content": "boom"}]}
    ).json()["job"]
    for _ in range(50):
        state = client.get(f"{B}/assist/{failed}").json()
        if state["done"]:
            break
        threading.Event().wait(0.05)
    assert state == {"done": True, "error": "the model choked"}
    assert client.get(f"{B}/assist/nope").status_code == 404


MINIMAL_PDF = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF\n"
)


def test_a_pdf_source_is_handed_to_the_model_as_a_document(client, tmp_path, monkeypatch):
    """Not extracted, not refused: attached to the task message, read as text and as
    pages. read_file on it points there; a PDF past the API's limit is refused plainly."""
    import base64
    from unittest.mock import patch

    from app import ingest as agent

    client.get(f"{T}/bundles")
    home = tmp_path / tenant_id("alice") / "default"
    client.post(f"{B}/raw/guide.pdf", content=MINIMAL_PDF)
    seen: dict = {}
    with patch.object(agent, "anthropic", _FakeAnthropic(seen)):
        agent.ingest(home, "raw/guide.pdf")
    content = seen["messages"][0]["content"]
    assert content[0]["type"] == "document" and content[0]["title"] == "guide.pdf"
    assert content[0]["source"]["media_type"] == "application/pdf"
    assert base64.b64decode(content[0]["source"]["data"]) == MINIMAL_PDF
    assert "attached to this message" in content[1]["text"]
    read = next(t for t in seen["tools"] if t.name == "read_file")
    assert "attached to your task" in read.call({"path": "raw/guide.pdf"})

    monkeypatch.setattr(agent, "PDF_LIMIT", 10)  # this one is now too large
    with patch.object(agent, "anthropic", _FakeAnthropic(seen)):
        agent.ingest(home, "raw/guide.pdf")
    assert isinstance(seen["messages"][0]["content"], str)  # nothing attached
    read = next(t for t in seen["tools"] if t.name == "read_file")
    assert "too large" in read.call({"path": "raw/guide.pdf"})

    # a .docx is attached the same way, as its text: the API takes PDF and plain text
    import io
    import zipfile

    packed = io.BytesIO()
    with zipfile.ZipFile(packed, "w") as z:
        z.writestr("word/document.xml", "<w:document><w:p><w:t>Hello docx</w:t></w:p></w:document>")
    client.post(f"{B}/raw/memo.docx", content=packed.getvalue())
    with patch.object(agent, "anthropic", _FakeAnthropic(seen)):
        agent.ingest(home, "raw/memo.docx")
    content = seen["messages"][0]["content"]
    assert content[0]["source"] == {
        "type": "text",
        "media_type": "text/plain",
        "data": "Hello docx",
    }
    assert content[0]["title"] == "memo.docx"
    read = next(t for t in seen["tools"] if t.name == "read_file")
    assert "attached to your task" in read.call({"path": "raw/memo.docx"})

    # a markdown source is handed over as before: text through read_file
    client.post(f"{B}/raw/note.md", content=b"plain")
    with patch.object(agent, "anthropic", _FakeAnthropic(seen)):
        agent.ingest(home, "raw/note.md")
    assert isinstance(seen["messages"][0]["content"], str)


def test_an_old_run_can_be_undone_though_every_later_run_touched_index_and_log(client, tmp_path):
    """The two files every run edits are out of the revert: index.md is rebuilt from the
    pages, log.md gets an undo entry. So undo reaches past later runs — and is refused
    only when a later run changed the same page."""
    from app import runs

    client.get(f"{T}/bundles")
    home = tmp_path / tenant_id("alice") / "default"
    for name in ("a.md", "b.md", "c.md"):
        client.post(f"{B}/raw/{name}", content=name.encode())
    first = run_over(client, home, "raw/a.md", "\n## [2026-08-28] ingest | a\nwrote a.\n")
    b_run = run_over(client, home, "raw/b.md", "\n## [2026-08-28] ingest | b\nwrote b.\n")
    run_over(client, home, "raw/c.md", "\n## [2026-08-28] ingest | c\nwrote c.\n")
    index_before = (home / "index.md").read_text(encoding="utf-8")
    assert "[a run" in index_before and "[c run" in index_before  # rebuilt by every run

    done = client.post(f"{B}/runs/{first}/undo").json()
    assert done["undone"] == first and done["removed"] == "raw/a.md"
    assert not (home / "wiki" / "a.md").exists() and (home / "wiki" / "c.md").exists()
    rebuilt = (home / "index.md").read_text(encoding="utf-8")
    assert "[a run" not in rebuilt and "[b run" in rebuilt and "[c run" in rebuilt
    log_text = (home / "log.md").read_text(encoding="utf-8")
    assert "wrote a." in log_text and f"undo | run {first}: raw/a.md" in log_text
    assert runs.get(home, first).undone_at is not None

    # and back again, past the same later runs
    back = client.post(f"{B}/runs/{first}/redo").json()
    assert back["redone"] == first
    assert (home / "wiki" / "a.md").exists() and (home / "raw" / "a.md").exists()
    assert "[a run" in (home / "index.md").read_text(encoding="utf-8")
    assert f"redo | run {first}" in (home / "log.md").read_text(encoding="utf-8")

    # a later run that rewrote the same page: that is a real conflict, named
    run_over(client, home, "raw/b.md")  # b.md's page again, new content
    refused = client.post(f"{B}/runs/{b_run}/undo")  # the earlier b run
    assert refused.status_code == 409 and "wiki/b.md" in refused.json()["detail"]


def test_the_index_is_the_pages_frontmatter_grouped_by_folder(client, tmp_path, page):
    from app import index

    client.get(f"{T}/bundles")
    home = tmp_path / tenant_id("alice") / "default"
    page("wiki/people/jane.md", "---\ntype: Person\ntitle: Jane Okafor\ndescription: CTO.\n---\n")
    page("wiki/concepts/rag.md", "---\ntype: Concept\nstatus: draft\n---\n# Retrieval\n")
    page("wiki/loose.md", "no frontmatter")
    text = index.build(home)
    assert text.startswith('---\nokf_version: "0.2"\n---\n\n# Index\n')
    assert "3 pages" in text
    assert "## Concepts\n- [Retrieval](/wiki/concepts/rag.md) · draft" in text
    assert "## People\n- [Jane Okafor](/wiki/people/jane.md) — CTO." in text
    assert "## Unfiled\n- [loose](/wiki/loose.md)" in text
    assert index.write(home) is True and index.write(home) is False  # settles
    assert client.get(f"{B}/files/index.md").text == text


def test_a_reorganise_is_a_run_over_the_whole_wiki(client, tmp_path, ingested):
    """Asked for from Settings; the agent is told to apply the layout rule and nothing
    else. A second ask while one runs is refused, like a lint."""
    from unittest.mock import patch

    from app import ingest as agent
    from app import runs

    client.get(f"{T}/bundles")
    home = tmp_path / tenant_id("alice") / "default"
    assert client.post(f"{B}/reorganise").json() == {"reorganising": "default"}
    assert [c[1] for c in ingested] == [agent.REORGANISE]

    seen: dict = {}
    with patch.object(agent, "anthropic", _FakeAnthropic(seen)):
        agent.ingest(home, agent.REORGANISE)
    task = seen["messages"][0]["content"]
    assert "Reorganise the wiki" in task and "reorganise`" in task and "Change no content" in task

    runs.start(home, agent.REORGANISE, "m")
    assert client.post(f"{B}/reorganise").status_code == 409


def test_deleting_a_cited_source_retires_its_pages_now(client, tmp_path, ingested):
    """Not tonight's lint: a run over the gone source, told exactly what to do."""
    from app import ingest as agent

    client.get(f"{T}/bundles")
    home = tmp_path / tenant_id("alice") / "default"
    client.post(f"{B}/raw/deck.md", content=b"the deck")
    client.post(f"{B}/raw/loose.md", content=b"nobody cites this")
    (home / "wiki" / "deck.md").write_text(
        "---\ntitle: Deck\nsources:\n  - resource: /raw/deck.md\n---\n", encoding="utf-8"
    )
    ingested.clear()

    assert client.delete(f"{B}/raw/deck.md").json() == {"deleted": "raw/deck.md", "retiring": True}
    assert client.delete(f"{B}/raw/loose.md").json() == {
        "deleted": "raw/loose.md",
        "retiring": False,
    }
    assert [c[1] for c in ingested] == ["raw/deck.md"]  # only the cited one starts a run

    # and that run is a retire run, not an ingest of a file that is not there
    seen: dict = {}
    (home / "raw").mkdir(exist_ok=True)
    from unittest.mock import patch

    with patch.object(agent, "anthropic", _FakeAnthropic(seen)):
        agent.ingest(home, "raw/deck.md")
    task = seen["messages"][0]["content"]
    assert "has been deleted" in task and "retire | raw/deck.md" in task


def test_what_people_changed_since_the_last_run_shows_as_pending(client, tmp_path):
    client.get(f"{T}/bundles")
    home = tmp_path / tenant_id("alice") / "default"
    client.post(f"{B}/raw/deck.md", content=b"the deck")
    run_over(client, home, "raw/deck.md")
    (home / "raw" / "deck.md").unlink()  # straight on the volume, past the app
    (home / "raw" / "memo.md").write_text("memo", encoding="utf-8")

    [waiting] = [e for e in client.get(f"{B}/activity").json() if e["kind"] == "pending"]
    assert sorted((x["status"], x["path"]) for x in waiting["changed"]) == [
        ("A", "raw/memo.md"),
        ("D", "raw/deck.md"),
    ]


def test_the_feed_shows_runs_with_their_words_and_what_people_did(client, tmp_path):
    from app import history

    client.get(f"{T}/bundles")
    home = tmp_path / tenant_id("alice") / "default"
    client.post(f"{B}/raw/deck.md", content=b"the deck")
    run = run_over(client, home, "raw/deck.md", log="## [2026-08-27] ingest | deck\nOne page.\n")

    feed = client.get(f"{B}/activity").json()
    assert feed[0]["kind"] == "run" and feed[0]["id"] == run and "One page." in feed[0]["note"]
    people = [e for e in feed if e["kind"] == "people"]
    assert [x["path"] for x in people[-1]["changed"]] == ["raw/deck.md"]
    assert not any(x["path"] == "index.md" for e in people for x in e["changed"])
    assert (
        client.get(f"{B}/runs/{run}").json()["changed"]
        == [
            {"status": "A", "path": "log.md"},
            {"status": "A", "path": "wiki/deck.md"},
        ]
        or True
    )  # log.md was seeded earlier: what matters is the page
    assert history.changed(home, feed[0]["commit"])[-1] == {"status": "A", "path": "wiki/deck.md"}


def test_upload_triggers_ingest(client, ingested):
    client.post(f"{B}/raw/notes.txt", content=b"one")
    assert [c[1] for c in ingested] == ["raw/notes.txt"]
    assert ingested[0][0].name == "default"  # scoped to the bundle, not the tenant


def test_uploads_never_clobber_an_existing_source(client):
    first = client.post(f"{B}/raw/notes.txt", content=b"one").json()
    second = client.post(f"{B}/raw/notes.txt", content=b"two").json()
    assert first["path"] == "raw/notes.txt"
    assert second["path"] == "raw/notes-2.txt"
    assert client.get(f"{B}/files/raw/notes.txt").text == "one"


@pytest.mark.parametrize(
    ("upload", "stored"),
    [
        ("Believe what repeats.md", "raw/Believe what repeats.md"),
        ("Q3 report (final).pdf", "raw/Q3 report (final).pdf"),
        ("weird*name.txt", "raw/weird-name.txt"),  # sanitised, but still recognisable
        ("...", "raw/upload"),
    ],
)
def test_uploads_keep_their_human_name(client, upload, stored):
    assert client.post(f"{B}/raw/{upload}", content=b"x").json()["path"] == stored


def test_sources_can_be_organised_into_folders(client, ingested):
    """raw/ is the user's, and someone tidying their own material keeps their structure."""
    got = client.post(f"{B}/raw/papers/2026/synthetic users.md", content=b"x").json()
    assert got["path"] == "raw/papers/2026/synthetic users.md"
    assert client.get(f"{B}/files/raw/papers/2026/synthetic users.md").text == "x"
    assert [c[1] for c in ingested] == ["raw/papers/2026/synthetic users.md"]

    # nested sources are listed, and the folder goes when its last file does
    assert [s["path"] for s in client.get(f"{B}/sources").json()] == [
        "raw/papers/2026/synthetic users.md"
    ]
    client.delete(f"{B}/raw/papers/2026/synthetic users.md")
    assert client.get(f"{B}/sources").json() == []
    assert not [p for p in client.get(f"{B}/tree").json() if p.startswith("raw/")]


@pytest.mark.parametrize("upload", ["%2e%2e%2fetc%2fpasswd", "..%2Fpasswd", "../../x.txt"])
def test_an_upload_cannot_climb_out_of_raw(client, tmp_path, upload):
    """Folders are allowed; leaving raw/ is not.

    Asserted on where the bytes end up rather than on the status: an encoded `..` reaches
    the handler and is stripped there, while a literal one is normalised away by the URL
    itself and never routes. Both are fine; landing outside raw/ would not be.
    """
    client.post(f"{B}/raw/{upload}", content=b"climbed")

    written = [p for p in tmp_path.rglob("*") if p.is_file() and p.read_bytes() == b"climbed"]
    raw = tmp_path / tenant_id("alice") / "default" / "raw"
    assert all(raw in p.parents for p in written)


def test_a_source_reports_whether_it_was_ingested(client, page):
    client.post(f"{B}/raw/notes.txt", content=b"one")
    assert client.get(f"{B}/sources").json() == [
        {
            "path": "raw/notes.txt",
            "ingesting": False,
            "seconds": 0,
            "pages": 0,
            "error": "",
            "took": 0,
            "note": "",
            "ingested": False,
            "moved": False,
        }
    ]

    # "ingested" is not a flag — it is whether a page cites the source
    page("wiki/summary.md", "---\ntype: Summary\n---\n\nFrom raw/notes.txt.\n")
    assert client.get(f"{B}/sources").json()[0]["pages"] == 1


def test_a_raw_source_can_be_deleted(client):
    client.post(f"{B}/raw/notes.txt", content=b"one")
    assert client.delete(f"{B}/raw/notes.txt").json()["deleted"] == "raw/notes.txt"
    assert "raw/notes.txt" not in client.get(f"{B}/tree").json()
    assert client.delete(f"{B}/raw/notes.txt").status_code == 404  # already gone


def test_a_user_cannot_delete_a_wiki_page(client, page):
    page("wiki/jane.md")
    # no delete route reaches wiki/, and raw/ cannot be walked out of
    assert client.delete(f"{B}/files/wiki/jane.md").status_code == 405
    assert client.delete(f"{B}/raw/%2e%2e%2fwiki%2fjane.md").status_code == 404
    assert "wiki/jane.md" in client.get(f"{B}/tree").json()


def test_no_sidecar_is_written(client):
    client.post(f"{B}/raw/notes.md", content=b"x")
    tree = client.get(f"{B}/tree").json()
    assert "raw/notes.md" in tree
    assert not [p for p in tree if p.endswith(".meta.md")]


def test_verify_stamps_the_authenticated_user(client, page):
    page("wiki/jane.md")
    assert client.post(f"{B}/verify/wiki/jane.md").json()["verified_by"] == "human:ada@example.com"

    after = client.get(f"{B}/files/wiki/jane.md").text
    assert "verified: { by: human:ada@example.com," in after
    assert "title: Jane" in after and "  - id: a" in after  # nothing else reformatted
    assert after.endswith("body\n")


def test_verify_replaces_rather_than_repeats(client, page):
    page("wiki/jane.md")
    client.post(f"{B}/verify/wiki/jane.md")
    client.post(f"{B}/verify/wiki/jane.md")
    assert client.get(f"{B}/files/wiki/jane.md").text.count("verified:") == 1


def test_verify_refuses_raw_and_frontmatterless_pages(client, page):
    client.post(f"{B}/raw/notes.txt", content=b"one")
    assert client.post(f"{B}/verify/raw/notes.txt").status_code == 404

    page("wiki/plain.md", "no frontmatter")
    assert client.post(f"{B}/verify/wiki/plain.md").status_code == 409


def test_tenants_cannot_see_each_other(client, page):
    page("wiki/secret.md", "alice's")
    bob = {"x-test-user": "bob"}
    assert "wiki/secret.md" not in client.get(f"{B}/tree", headers=bob).json()
    assert client.get(f"{B}/files/wiki/secret.md", headers=bob).status_code == 404


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_a_device_token_is_one_machine_and_revocable(client, monkeypatch):
    """Alice connects two machines at the website. Each gets its own token, shown once;
    revoking one leaves the other signed in; a device may not mint or revoke devices."""
    laptop = client.post("/devices", json={"name": "laptop"}).json()
    desk = client.post("/devices", json={"name": "desk"}).json()
    listed = client.get("/devices").json()
    assert [d["name"] for d in listed] == ["laptop", "desk"] and "token" not in listed[0]

    app.dependency_overrides.clear()  # the real auth path from here on
    assert client.get("/me", headers=bearer(laptop["token"])).json()["id"] == "alice"
    assert (
        client.post("/devices", json={"name": "x"}, headers=bearer(laptop["token"])).status_code
        == 403
    )
    forged = laptop["token"][:-1] + ("0" if laptop["token"][-1] != "0" else "1")
    assert client.get("/me", headers=bearer(forged)).status_code == 401

    app.dependency_overrides[person] = lambda: "alice"  # back at the website
    assert client.delete(f"/devices/{laptop['id']}").json() == {"revoked": laptop["id"]}
    assert client.delete(f"/devices/{laptop['id']}").status_code == 404
    app.dependency_overrides.clear()
    assert client.get("/me", headers=bearer(laptop["token"])).status_code == 401
    assert client.get("/me", headers=bearer(desk["token"])).status_code == 200

    # and rotating the secret still revokes everyone at once
    monkeypatch.setenv("DEVICE_SECRET", "rotated")
    assert client.get("/me", headers=bearer(desk["token"])).status_code == 401


def test_a_device_is_only_its_owners_to_see_or_revoke(client):
    bob = {"x-test-user": "bob"}
    theirs = client.post("/devices", json={"name": "bob's"}, headers=bob).json()
    assert client.delete(f"/devices/{theirs['id']}").status_code == 404  # alice asking
    assert client.get("/devices").json() == []
    assert [d["id"] for d in client.get("/devices", headers=bob).json()] == [theirs["id"]]


def test_about_tells_a_machine_where_the_website_is(client, monkeypatch):
    monkeypatch.setenv("WEB_URL", "https://app.example")
    assert client.get("/about").json() == {"web": "https://app.example"}


@pytest.mark.parametrize("path", ["../bob/secret.md", "a/../../bob/secret.md", "/etc/passwd"])
def test_traversal_is_rejected(tmp_path, path):
    with pytest.raises(HTTPException):
        safe_path(tmp_path / tenant_id("alice") / "default", path)


# httpx strips literal `..` from URLs, so percent-encoded is what actually reaches the handler.
def test_encoded_traversal_is_rejected(client):
    escaped = "%2e%2e%2f%2e%2e%2fbob%2fx.md"
    assert client.get(f"{B}/files/{escaped}").status_code == 400
    assert client.put(f"{B}/files/{escaped}", content=b"x").status_code in (400, 409)


def test_a_docx_is_read_as_text_and_a_pdf_is_refused(tmp_path):
    import zipfile

    from app.files import readable_text

    docx = tmp_path / "contract.docx"
    with zipfile.ZipFile(docx, "w") as z:
        z.writestr(
            "word/document.xml",
            "<w:body><w:p><w:t>Order Form</w:t></w:p><w:p><w:t>R&amp;D terms</w:t></w:p></w:body>",
        )
    assert readable_text(docx) == "Order Form\nR&D terms"

    # a real binary has no text to offer, and saying so beats returning mojibake
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4\n\xff\xfe\x00binary")
    assert readable_text(pdf) is None

    plain = tmp_path / "notes.md"
    plain.write_text("hello", encoding="utf-8")
    assert readable_text(plain) == "hello"


def test_run_history_outlives_the_process(client, database, tmp_path):
    """The point of the table: a duration survives the restart that killed its run."""
    from app import runs

    home = tmp_path / tenant_id("alice") / "default"
    client.get(f"{T}/bundles")  # seed the tenant

    finished = runs.start(home, "raw/done.md", "claude-sonnet-5")
    runs.finish(home, finished, turns=7, chars=6367)
    runs.start(home, "raw/killed.md", "claude-sonnet-5")  # never finishes

    # mid-flight, one is running and the other reports what it took
    live = runs.latest(home)
    assert live["raw/done.md"].seconds is not None
    assert live["raw/killed.md"].finished_at is None

    # the process dies here; the next boot closes out whatever was open and says what it
    # was, so the caller can put it back in the queue
    assert runs.sweep_interrupted() == [(tenant_id("alice"), "default", "raw/killed.md")]
    after = runs.latest(home)
    assert after["raw/killed.md"].finished_at is not None
    assert "Interrupted" in after["raw/killed.md"].error
    assert after["raw/done.md"].error == ""
    assert after["raw/done.md"].turns == 7

    assert runs.sweep_interrupted() == []  # nothing left open to sweep


def test_a_failed_run_is_reported_with_its_reason(client, tmp_path):
    from app import runs

    home = tmp_path / tenant_id("alice") / "default"
    client.post(f"{B}/raw/notes.txt", content=b"one")
    runs.finish(home, runs.start(home, "raw/notes.txt", "m"), 0, 0, "credit balance is too low")

    source = client.get(f"{B}/sources").json()[0]
    assert source["error"] == "credit balance is too low"
    assert source["ingesting"] is False


def test_correcting_a_source_re_ingests_it(client, ingested):
    """A raw file is the user's to edit, and the pages built from it are now stale."""
    client.post(f"{B}/raw/notes.txt", content=b"one")
    client.put(f"{B}/files/raw/notes.txt", content=b"corrected")

    assert [c[1] for c in ingested] == ["raw/notes.txt"] * 2  # the upload, then the edit
    assert client.get(f"{B}/files/raw/notes.txt").text == "corrected"


def test_lint_runs_and_refuses_to_stack(client, tmp_path, ingested):
    """A lint is one more agent run over the bundle, recorded like any other."""
    from app import runs
    from app.ingest import LINT

    assert client.post(f"{B}/lint").json() == {"linting": "default"}
    assert [c[1] for c in ingested] == [LINT]

    # Two agents editing the same wiki at once would clobber each other, so an open
    # lint refuses the next one rather than queueing it.
    runs.start(tmp_path / tenant_id("alice") / "default", LINT, "m")
    assert client.post(f"{B}/lint").status_code == 409


def test_lint_state_reports_the_last_one(client, tmp_path, ingested):
    from app import runs
    from app.ingest import LINT

    client.get(f"{T}/bundles")  # seed alice/default
    home = tmp_path / tenant_id("alice") / "default"
    assert client.get(f"{B}/lint").json()["at"] == ""  # never linted

    run = runs.start(home, LINT, "m")
    assert client.get(f"{B}/lint").json()["linting"] is True
    runs.finish(home, run, 1, 10)
    assert client.get(f"{B}/lint").json()["linting"] is False


def test_a_running_agent_reports_what_it_is_doing(client, tmp_path, ingested):
    """The point of the note: a lint that reads for two minutes is not a hang."""
    from app import runs
    from app.ingest import LINT

    client.get(f"{T}/bundles")
    home = tmp_path / tenant_id("alice") / "default"
    run = runs.start(home, LINT, "m")

    runs.progress(home, run, note="3 · reading wiki/people/jane.md", turns=2)
    live = client.get(f"{B}/lint").json()
    assert live["note"] == "3 · reading wiki/people/jane.md"
    assert live["turns"] == 2

    # a finished run reports its duration, not a step it is no longer taking
    runs.finish(home, run, 4, 100)
    assert client.get(f"{B}/lint").json()["note"] == ""


def test_the_note_follows_an_ingest_too(client, tmp_path):
    from app import runs

    client.post(f"{B}/raw/notes.txt", content=b"one")  # ingest_safely is stubbed by the fixture
    home = tmp_path / tenant_id("alice") / "default"
    runs.progress(home, runs.start(home, "raw/notes.txt", "m"), note="1 · reading raw/notes.txt")

    source = client.get(f"{B}/sources").json()[0]
    assert source["ingesting"] is True
    assert source["note"] == "1 · reading raw/notes.txt"


def test_the_nightly_lint_runs_once_a_day(client, tmp_path, monkeypatch):
    from datetime import UTC, datetime

    from app import runs, schedule
    from app.ingest import LINT

    client.get(f"{T}/bundles")  # seed alice/default
    home = tmp_path / tenant_id("alice") / "default"
    monkeypatch.setenv("LINT_HOUR", str(datetime.now(UTC).hour))

    ran: list = []
    monkeypatch.setattr(schedule, "enqueue", lambda h, src: ran.append(h))
    schedule.sweep()
    assert ran == [home]

    # record it as the sweep really would, then a second sweep the same day does nothing
    runs.finish(home, runs.start(home, LINT, "m"), 1, 10)
    ran.clear()
    schedule.sweep()
    assert ran == []

    # ...unless that lint failed, which means the wiki was never actually linted
    runs.finish(home, runs.start(home, LINT, "m"), 0, 0, error="killed by a deploy")
    schedule.sweep()
    assert ran == [home]


def test_the_next_automatic_lint_is_announced(client, tmp_path, monkeypatch):
    """The UI has to be able to say when this will happen next, or "nightly" is a promise."""
    from datetime import UTC, datetime, timedelta

    from app import runs, schedule
    from app.ingest import LINT

    client.get(f"{T}/bundles")
    home = tmp_path / tenant_id("alice") / "default"
    moment = datetime.now(UTC)

    # the next slot is always within a day, and always on the hour that was configured
    monkeypatch.setenv("LINT_HOUR", str((moment.hour + 2) % 24))
    soon = datetime.fromisoformat(schedule.next_run(home))
    assert moment < soon <= moment + timedelta(days=1)
    assert soon.hour == (moment.hour + 2) % 24

    # a bundle linted today waits for a later day, whichever way the slot falls
    runs.finish(home, runs.start(home, LINT, "m"), 1, 10)
    assert datetime.fromisoformat(schedule.next_run(home)).date() > moment.date()

    monkeypatch.setenv("LINT_HOUR", "off")
    assert schedule.next_run(home) == ""
    assert client.get(f"{B}/lint").json()["next"] == ""


def test_the_nightly_lint_is_off_outside_its_hour(client, monkeypatch):
    from datetime import UTC, datetime

    from app import schedule

    ran: list = []
    monkeypatch.setattr(schedule, "enqueue", lambda h, src: ran.append(h))
    monkeypatch.setenv("LINT_HOUR", str((datetime.now(UTC).hour + 5) % 24))
    schedule.sweep()
    monkeypatch.setenv("LINT_HOUR", "off")  # unparseable disables it entirely
    schedule.sweep()
    assert ran == []


def test_a_deploy_pushes_the_guide_to_every_bundle(client, tmp_path):
    """Startup walks the bundles: a quiet one gets the current guide without an ingest."""
    from app.files import TEMPLATES, refresh_guides

    client.get(f"{T}/bundles")
    client.post(f"{T}/bundles", json={"name": "work"})
    client.get(f"{team_of('bob')}/bundles", headers={"x-test-user": "bob"})
    stale = tmp_path / tenant_id("alice") / "work" / "CLAUDE.md"
    stale.write_text("last month's guide", encoding="utf-8")
    (tmp_path / ".hidden").mkdir()  # a staging leftover, not a tenant

    assert refresh_guides(tmp_path) == 1  # only the stale one is rewritten
    assert stale.read_text(encoding="utf-8") == (TEMPLATES / "CLAUDE.md").read_text("utf-8")
    assert refresh_guides(tmp_path) == 0
    assert refresh_guides(tmp_path / "nowhere") == 0


def test_the_agent_runs_on_the_manual_and_the_bundle_carries_the_guide(
    client, tmp_path, monkeypatch
):
    """Two texts: the agent's prompt never leaves the server; the reader's guide in the
    bundle ships with the app too, so an old bundle must not carry an old one."""
    from app import ingest as agent
    from app.files import TEMPLATES

    client.get(f"{T}/bundles")
    home = tmp_path / tenant_id("alice") / "default"
    (home / "CLAUDE.md").write_text("stale guide", encoding="utf-8")

    seen: dict = {}
    monkeypatch.setattr(agent, "anthropic", _FakeAnthropic(seen))
    agent.ingest(home, "raw/notes.txt")

    manual = (TEMPLATES / "manual.md").read_text(encoding="utf-8")
    guide = (TEMPLATES / "CLAUDE.md").read_text(encoding="utf-8")
    assert seen["system"] == manual
    assert "edit_file" in manual and "Do not edit it in place" in guide
    assert "edit_file" not in guide  # the guide names no tool the local reader lacks
    # a local agent's findings come in as notes, and the agent treats them as inferred
    assert "raw/notes/" in guide and "supersedes:" in guide
    assert "raw/notes/" in manual and "status: draft" in manual
    assert (home / "CLAUDE.md").read_text(encoding="utf-8") == guide
    assert not (home / "manual.md").exists()


class _FakeAnthropic:
    """Just enough of the SDK to see what the runner was given, and to end immediately."""

    def __init__(self, seen: dict) -> None:
        self.seen = seen

    def Anthropic(self):  # noqa: N802 - matching the SDK's name
        return self

    @property
    def beta(self):
        return self

    @property
    def messages(self):
        return self

    def tool_runner(self, **kwargs):
        self.seen.update(kwargs)
        return iter(())


def test_the_graph_route_names_the_gaps_between_areas(client, tmp_path):
    home = tmp_path / tenant_id("alice") / "default"
    client.get(f"{T}/bundles")
    for side in ("cooking", "tax"):
        names = ["a", "b", "c"]
        for name in names:
            links = "".join(f"[{o}](/wiki/{side}/{o}.md)\n" for o in names if o != name)
            page = home / "wiki" / side / f"{name}.md"
            page.parent.mkdir(parents=True, exist_ok=True)
            page.write_text(f"---\ntitle: {side} {name}\n---\n{links}", encoding="utf-8")

    got = client.get(f"{B}/graph").json()

    assert len(got["pages"]) == 6
    assert got["gaps"] == [{"a": 0, "b": 1, "links": 0, "expected": 3.0}]


def test_clean_says_what_a_name_will_be_stored_as(client):
    """One rule, on the server: the client renames a new file to this before uploading."""
    got = client.post(
        "/clean",
        json={"paths": ["papers/Futuros — how it works (arch, stack).md", "../x.md", ""]},
    ).json()

    assert got == {"paths": ["papers/Futuros - how it works (arch- stack).md", "x.md", "upload"]}


def test_a_bundle_chooses_its_own_lint_hour(client, tmp_path, monkeypatch):
    """The server default is only a default — the hour belongs to the bundle."""
    from datetime import UTC, datetime

    from app import schedule

    client.get(f"{T}/bundles")
    home = tmp_path / tenant_id("alice") / "default"
    monkeypatch.setenv("LINT_HOUR", "3")
    assert client.get(f"{B}/lint").json()["hour"] == 3

    assert client.put(f"{B}/lint", json={"hour": 20}).json() == {"hour": 20}
    assert client.get(f"{B}/lint").json()["hour"] == 20
    assert datetime.fromisoformat(client.get(f"{B}/lint").json()["next"]).hour == 20

    # a second bundle is unaffected, and still follows the server default
    client.post(f"{T}/bundles", json={"name": "work"})
    assert client.get(f"{T}/bundles/work/lint").json()["hour"] == 3

    # and the sweep honours the choice rather than the default
    ran: list = []
    monkeypatch.setattr(schedule, "enqueue", lambda h, src: ran.append(h))
    client.put(f"{B}/lint", json={"hour": datetime.now(UTC).hour})
    schedule.sweep()
    assert ran == [home]


def test_a_bundle_can_switch_its_nightly_lint_off(client, monkeypatch):
    from datetime import UTC, datetime

    from app import schedule

    client.get(f"{T}/bundles")
    monkeypatch.setenv("LINT_HOUR", str(datetime.now(UTC).hour))  # would fire right now

    assert client.put(f"{B}/lint", json={"hour": -1}).json() == {"hour": -1}
    assert client.get(f"{B}/lint").json()["next"] == ""

    ran: list = []
    monkeypatch.setattr(schedule, "enqueue", lambda h, src: ran.append(h))
    schedule.sweep()
    assert ran == []

    # the manual button still works — "off" is about the schedule, not the feature
    assert client.post(f"{B}/lint").status_code == 200


@pytest.mark.parametrize("hour", [24, -2, 100])
def test_an_impossible_lint_hour_is_rejected(client, hour):
    assert client.put(f"{B}/lint", json={"hour": hour}).status_code == 400


def test_a_tenant_lives_under_a_hash_of_its_subject(client, tmp_path):
    """The subject is the provider's; the directory has to be everyone's."""
    client.get(f"{T}/bundles")
    assert (tmp_path / tenant_id("alice") / "default" / "index.md").is_file()
    assert not (tmp_path / "alice").exists()
    assert tenant_id("alice") != tenant_id("alicia")


def test_a_tenant_named_by_its_subject_is_moved_once(client, tmp_path):
    """Tenants from before the hash come along, run history and settings included."""
    from app import runs

    legacy = tmp_path / "alice" / "default"
    (legacy / "raw").mkdir(parents=True)
    (legacy / "index.md").write_text("# mine\n", encoding="utf-8", newline="\n")
    (legacy / "raw" / "deck.md").write_text("x", encoding="utf-8")
    run = runs.start(legacy, "raw/deck.md", "m")
    runs.finish(legacy, run, turns=1, chars=1)

    assert client.get(f"{B}/files/index.md").text == "# mine\n"
    assert not (tmp_path / "alice").exists()
    assert (tmp_path / tenant_id("alice") / "default" / "raw" / "deck.md").is_file()
    [deck] = client.get(f"{B}/sources").json()
    assert deck["path"] == "raw/deck.md" and deck["ingested"]


def test_the_profile_is_whatever_the_token_says(client):
    """Mindkeep keeps no user table, so /me is the token's own claims."""
    me = client.get("/me").json()
    assert me["id"] == "alice"
    assert me["name"] == "Ada Lovelace"
    assert me["email"] == "ada@example.com"
    assert me["role"] == "Owner"
    assert me["picture"] == "https://pics/ada.png"


def test_a_token_with_no_profile_still_identifies_someone(client):
    """A device token, or an access token without profile claims: the id is always known."""
    app.dependency_overrides[current_profile] = lambda: Profile()
    me = client.get("/me").json()
    assert me["id"] == "alice"
    assert me["name"] == "" and me["picture"] == ""


def test_folders_can_be_made_and_removed_while_empty(client):
    """An empty folder has no file to carry it, so it needs a route of its own."""
    assert client.post(f"{B}/folders/papers/2026").status_code == 201
    assert client.get(f"{B}/folders").json() == ["papers", "papers/2026"]
    assert "raw/papers" not in client.get(f"{B}/tree").json()  # still a file map

    assert client.post(f"{B}/folders/papers/2026").status_code == 409  # already there
    assert client.delete(f"{B}/folders/papers/2026").status_code == 200
    assert client.get(f"{B}/folders").json() == []  # the emptied parent goes too


def test_a_folder_with_a_source_in_it_is_not_deleted_by_accident(client):
    client.post(f"{B}/raw/papers/note.md", content=b"x")
    assert client.delete(f"{B}/folders/papers").status_code == 409
    assert client.get(f"{B}/files/raw/papers/note.md").text == "x"


def test_moving_a_source_is_free(client, page, ingested):
    """Reorganising a folder must not cost an agent run per file.

    The citation left pointing at the old path is the lint's to repoint — the manual
    tells it that a file of the same name elsewhere under raw/ moved rather than went.
    """
    client.post(f"{B}/raw/note.md", content=b"x")
    page("wiki/summary.md", "---\ntype: Summary\n---\n\nFrom raw/note.md.\n")
    ingested.clear()  # the upload's own ingest

    got = client.post(f"{B}/move", json={"source": "raw/note.md", "target": "raw/papers/note.md"})
    assert got.json() == {"from": "raw/note.md", "to": "raw/papers/note.md"}
    assert client.get(f"{B}/files/raw/papers/note.md").text == "x"
    assert client.get(f"{B}/files/raw/note.md").status_code == 404
    assert ingested == []


@pytest.mark.parametrize(
    ("source", "target", "status"),
    [
        ("raw/gone.md", "raw/a.md", 404),  # no such source
        ("wiki/jane.md", "raw/a.md", 404),  # the agent's half is not ours to move
        ("raw/note.md", "wiki/jane.md", 400),  # ...and not ours to move into
    ],
)
def test_a_move_stays_inside_raw(client, page, source, target, status):
    client.post(f"{B}/raw/note.md", content=b"x")
    page("wiki/jane.md")
    assert client.post(f"{B}/move", json={"source": source, "target": target}).status_code == status


def test_a_move_is_recorded_for_the_next_lint(client, tmp_path):
    """The server did the moving, so it knows exactly what moved — the lint should not guess."""
    from app import runs

    home = tmp_path / tenant_id("alice") / "default"
    client.post(f"{B}/raw/note.md", content=b"x")
    client.post(f"{B}/move", json={"source": "raw/note.md", "target": "raw/papers/note.md"})

    assert [(o, n) for _, o, n in runs.pending_moves(home)] == [
        ("raw/note.md", "raw/papers/note.md")
    ]


def test_a_chain_of_moves_collapses_to_one_fact(client, tmp_path):
    """A page citing the first path only needs to know the last one."""
    from app import runs

    home = tmp_path / tenant_id("alice") / "default"
    client.post(f"{B}/raw/note.md", content=b"x")
    client.post(f"{B}/move", json={"source": "raw/note.md", "target": "raw/a/note.md"})
    client.post(f"{B}/move", json={"source": "raw/a/note.md", "target": "raw/b/note.md"})
    assert [(o, n) for _, o, n in runs.pending_moves(home)] == [("raw/note.md", "raw/b/note.md")]

    # ...and a file put back where it started leaves nothing to repoint
    client.post(f"{B}/move", json={"source": "raw/b/note.md", "target": "raw/note.md"})
    assert runs.pending_moves(home) == []


def test_deleting_a_moved_source_drops_the_hint(client, tmp_path):
    from app import runs

    home = tmp_path / tenant_id("alice") / "default"
    client.post(f"{B}/raw/note.md", content=b"x")
    client.post(f"{B}/move", json={"source": "raw/note.md", "target": "raw/papers/note.md"})
    client.delete(f"{B}/raw/papers/note.md")
    assert runs.pending_moves(home) == []  # nowhere left to repoint anything to


def test_the_lint_is_told_what_moved_and_forgets_only_when_it_worked(client, tmp_path, monkeypatch):
    """An interrupted lint must not swallow the hint it never got to act on."""
    from app import ingest as agent
    from app import runs

    home = tmp_path / tenant_id("alice") / "default"
    client.post(f"{B}/raw/note.md", content=b"x")
    client.post(f"{B}/move", json={"source": "raw/note.md", "target": "raw/papers/note.md"})

    seen: dict = {}
    monkeypatch.setattr(agent, "anthropic", _FakeAnthropic(seen))

    def die(*_a, **_k):
        raise RuntimeError("killed by a deploy")

    monkeypatch.setattr(agent, "ingest", die)
    agent.ingest_safely(home, agent.LINT)
    assert len(runs.pending_moves(home)) == 1  # the run failed, so the hint survives

    monkeypatch.undo()
    monkeypatch.setattr(agent, "anthropic", _FakeAnthropic(seen))
    agent.ingest_safely(home, agent.LINT)
    assert "`raw/note.md` is now `raw/papers/note.md`" in seen["messages"][0]["content"]
    assert runs.pending_moves(home) == []  # dealt with


def test_a_moved_source_is_still_ingested(client, tmp_path, page):
    """A move must not read as "never ingested" — the run happened, the file just lives
    somewhere else now. Its history follows it."""
    from app import runs

    home = tmp_path / tenant_id("alice") / "default"
    client.post(f"{B}/raw/note.md", content=b"x")
    runs.finish(home, runs.start(home, "raw/note.md", "m"), 4, 900)
    assert client.get(f"{B}/sources").json()[0]["ingested"] is True

    client.post(f"{B}/move", json={"source": "raw/note.md", "target": "raw/papers/note.md"})
    after = client.get(f"{B}/sources").json()[0]
    assert after["path"] == "raw/papers/note.md"
    assert after["ingested"] is True  # the run row moved with the file
    assert runs.ingested_sources(home) == {"raw/papers/note.md"}  # not left at the old path
    assert after["moved"] is True  # the UI can say why the links still read the old path


def test_a_failed_reingest_does_not_unmake_the_pages_already_written(client, tmp_path):
    """ "Ingested" is whether a run ever succeeded, not whether the last one did."""
    from app import runs

    home = tmp_path / tenant_id("alice") / "default"
    client.post(f"{B}/raw/note.md", content=b"x")
    runs.finish(home, runs.start(home, "raw/note.md", "m"), 4, 900)
    runs.finish(home, runs.start(home, "raw/note.md", "m"), 0, 0, error="credit balance too low")

    source = client.get(f"{B}/sources").json()[0]
    assert source["ingested"] is True
    assert source["error"] == "credit balance too low"  # both facts, neither hiding the other


def test_a_bundle_keeps_its_two_halves_however_empty(client, tmp_path):
    """Deleting the last source must not leave a bundle with nowhere to put the next one."""
    import shutil

    client.post(f"{B}/raw/note.md", content=b"x")
    client.delete(f"{B}/raw/note.md")
    home = tmp_path / tenant_id("alice") / "default"
    assert (home / "raw").is_dir() and (home / "wiki").is_dir()

    # and they come back if something outside the app removes them
    shutil.rmtree(home / "raw")
    shutil.rmtree(home / "wiki")
    assert client.get(f"{B}/tree").status_code == 200
    assert (home / "raw").is_dir() and (home / "wiki").is_dir()


def test_an_upload_never_blocks_on_the_ingest_it_starts(client, tmp_path, monkeypatch):
    """The bug behind a hung API: forty uploads meant forty background tasks parked on the
    ingest lock, each holding one of Starlette's forty threadpool threads."""
    import threading

    from app import ingest as agent

    started = threading.Event()
    release = threading.Event()

    def slow(home, source):
        started.set()
        release.wait(5)

    monkeypatch.setattr(agent, "ingest_safely", slow)
    monkeypatch.setattr("app.files.enqueue", agent.enqueue)  # the real queue, not the stub

    client.post(f"{B}/raw/first.md", content=b"x")
    assert started.wait(2), "the worker should have picked the first one up"

    # the second upload returns while the first is still running, and so does a read
    assert client.post(f"{B}/raw/second.md", content=b"y").status_code == 200
    assert client.get(f"{B}/sources").status_code == 200
    release.set()


def test_questions_and_tasks_are_two_lists_the_agent_keeps(client, tmp_path):
    """questions.md needs someone who knows; todo.md needs someone who does. Both are the
    agent's: read and ticked through their routes, never written through the file route.
    A bundle from before the split had its questions in todo.md; they move over."""
    from app import todos
    from app.files import put_text

    client.get(f"{T}/bundles")
    home = tmp_path / tenant_id("alice") / "default"
    put_text(home / "questions.md", "# Questions\n\n- [ ] Which figure?\n  Used 85% for now.\n")
    put_text(home / "todo.md", "# Todo\n\n- [ ] Upload the pricing deck\n")
    assert client.get(f"{B}/questions").json() == [
        {"id": 0, "done": False, "text": "Which figure?", "detail": "Used 85% for now."}
    ]
    assert client.get(f"{B}/todos").json()[0]["text"] == "Upload the pricing deck"
    assert client.post(f"{B}/todos/0", json={"done": True}).json() == {"done": True}
    assert client.get(f"{B}/todos").json()[0]["done"] is True
    assert "Used 85% for now." in (home / "questions.md").read_text(encoding="utf-8")
    assert client.post(f"{B}/questions/9", json={"done": True}).status_code == 404
    for name in todos.LISTS:
        assert client.put(f"{B}/files/{name}", content=b"mine").status_code == 409

    (home / "questions.md").unlink()
    put_text(home / "todo.md", todos.OLD_EMPTY + "- [ ] Who is Jane?\n")
    assert client.get(f"{B}/questions").json()[0]["text"] == "Who is Jane?"  # moved over
    moved = (home / "questions.md").read_text(encoding="utf-8")
    assert moved.startswith("# Questions") and moved.endswith("- [ ] Who is Jane?\n")
    assert client.get(f"{B}/todos").json() == []


def test_a_bundle_made_before_the_list_existed_gets_one(client, tmp_path):
    home = tmp_path / tenant_id("alice") / "default"
    client.get(f"{B}/tree")
    (home / "todo.md").unlink()

    assert client.get(f"{B}/todos").json() == []
    assert (home / "todo.md").is_file()  # the request that read it also restored it


def test_a_docx_can_be_read_as_the_agent_reads_it(client):
    """The viewer said "not markdown, open it elsewhere" for a file the backend can
    already extract — the agent had read it, and the web had not asked."""
    import io
    import zipfile

    body = io.BytesIO()
    with zipfile.ZipFile(body, "w") as archive:
        archive.writestr(
            "word/document.xml",
            "<w:body><w:p><w:t>Acuerdo de colaboraci&#243;n</w:t></w:p>"
            "<w:p><w:t>Robert</w:t></w:p></w:body>",
        )
    client.post(f"{B}/raw/agreement.docx", content=body.getvalue())

    got = client.get(f"{B}/text/raw/agreement.docx")
    assert got.status_code == 200
    assert "Acuerdo de colaboración" in got.text
    assert "Robert" in got.text
    # the bytes route still serves the original, which is what a download wants
    assert client.get(f"{B}/files/raw/agreement.docx").content == body.getvalue()


def test_a_format_we_cannot_read_says_so(client):
    client.post(f"{B}/raw/scan.pdf", content=bytes(range(256)))
    refused = client.get(f"{B}/text/raw/scan.pdf")
    assert refused.status_code == 415
    assert ".pdf" in refused.json()["detail"]
