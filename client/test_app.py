"""The tray app's logic, without Qt: what it saves, where it puts folders, what it
tells the person and how often. Run from client/."""

import plistlib
from pathlib import Path

from mindkeep import sync as engine
from mindkeep.app import alerts, autostart, config
from mindkeep.app.log import Log
from stamp import stamp


def test_config_is_saved_where_it_is_loaded_from_and_survives_a_damaged_file(tmp_path):
    path = tmp_path / "app.json"
    cfg = config.load(path)
    assert cfg["token"] == "" and cfg["watch"] == [] and cfg["root"].endswith("Mindkeep")
    cfg["token"] = "t"
    config.save(cfg, path)
    assert config.load(path)["token"] == "t"
    path.write_text("{not json", encoding="utf-8")
    assert config.load(path)["token"] == ""


def test_a_watched_bundle_keeps_the_folder_it_was_given(tmp_path):
    cfg = {"server": "s", "token": "t", "root": str(tmp_path), "watch": []}
    entry = config.watch(cfg, "t1", "Mimetik", "default")
    assert Path(entry["folder"]) == tmp_path / "Mimetik" / "default"
    # a second bundle of the same team lands beside the first; a rename does not move it
    entry2 = config.watch(cfg, "t1", "Mimetik renamed", "notes")
    assert Path(entry2["folder"]) == tmp_path / "Mimetik" / "notes"
    # the same bundle again is the same entry
    assert config.watch(cfg, "t1", "Mimetik", "default") is entry
    assert len(cfg["watch"]) == 2
    config.unwatch(cfg, "t1", "notes")
    assert [e["bundle"] for e in cfg["watch"]] == ["default"]


def test_two_teams_with_one_name_get_two_folders(tmp_path):
    cfg = {"server": "s", "token": "t", "root": str(tmp_path), "watch": []}
    config.watch(cfg, "t1", "Work", "default")
    config.watch(cfg, "t2", "Work", "default")
    config.watch(cfg, "t3", 'We/b: "ops"?', "default")
    assert [Path(e["folder"]).parent.name for e in cfg["watch"]] == [
        "Work",
        "Work 2",
        "We-b- -ops--",  # nothing a filesystem refuses; a name is not a path
    ]


def test_sync_config_is_what_the_engine_takes():
    cfg = {"server": "http://s", "token": "tok", "root": "/r", "watch": []}
    entry = {"team": "t1", "name": "Work", "bundle": "b", "folder": "/r/Work/b"}
    one = config.sync_config(cfg, entry)
    assert one == {
        "server": "http://s",
        "token": "tok",
        "folder": "/r/Work/b",
        "team": "t1",
        "bundle": "b",
    }
    assert set(one) == {"server", "token", "folder", "team", "bundle"}  # nothing the engine ignores


def test_a_failing_ingest_is_announced_once_until_it_stops_failing():
    a = alerts.Alerts()
    broken = [{"path": "raw/a.md", "error": "boom"}, {"path": "raw/b.md", "error": ""}]
    assert a.failures("k", broken) == ["raw/a.md"]
    assert a.failures("k", broken) == []  # same failure, said already
    assert a.failures("other", broken) == ["raw/a.md"]  # another bundle's file is its own news
    still_running = [{"path": "raw/a.md", "error": "old", "ingesting": True}]
    assert a.failures("k", still_running) == []  # a retry in flight is not a failure yet
    assert a.failures("k", [{"path": "raw/a.md", "error": ""}]) == []  # fixed
    assert a.failures("k", broken) == ["raw/a.md"]  # failed again: news again


def test_an_unreachable_server_is_announced_once_after_three_misses():
    a = alerts.Alerts()
    assert [a.unreachable() for _ in range(5)] == [False, False, True, False, False]
    a.reachable()
    assert [a.unreachable() for _ in range(3)] == [False, False, True]


def test_a_dead_token_is_announced_once_per_signing_out():
    a = alerts.Alerts()
    assert a.dead_token() and not a.dead_token()
    a.signed_in()
    assert a.dead_token()


def test_autostart_entries_launch_the_app_again():
    cmd = ["/usr/bin/python3", "-m", "mindkeep.app"]
    plist = plistlib.loads(autostart.plist_for(cmd))
    assert plist == {"Label": "io.mindkeep.app", "ProgramArguments": cmd, "RunAtLoad": True}
    desktop = autostart.desktop_for(["/opt/Mind stash/app"])
    assert "Exec='/opt/Mind stash/app'" in desktop and "Name=Mindkeep" in desktop
    assert autostart.command()[-2:] == ["-m", "mindkeep.app"]  # not frozen under pytest


def test_the_engine_reports_through_hooks_the_app_can_replace(tmp_path, monkeypatch):
    heard: list = []
    monkeypatch.setattr(engine, "say", lambda *parts: heard.append(("say", parts)))
    monkeypatch.setattr(
        engine, "notify", lambda cfg, kind, text: heard.append((kind, cfg["bundle"], text))
    )
    root = tmp_path / "mirror"
    (root / "raw").mkdir(parents=True)
    (root / "raw" / "deck.md").write_bytes(b"mine")
    engine.conflict({"bundle": "b"}, root, "raw/deck.md", "changed over there too")
    assert (root / ".conflicts" / "raw" / "deck.md").read_bytes() == b"mine"
    assert heard[0][0] == "say" and "conflict raw/deck.md" in heard[0][1][0]
    assert heard[1] == (
        "conflict",
        "b",
        "raw/deck.md: changed over there too. Yours is kept in .conflicts/raw/deck.md.",
    )


def test_the_log_keeps_the_last_lines_stamped_with_the_time():
    from datetime import datetime

    log = Log(keep=3)
    assert log.add("got wiki/a.md", datetime(2026, 8, 28, 9, 5, 7)) == "09:05:07  got wiki/a.md"
    for n in range(4):
        log.add(f"line {n}")
    assert log.text().count("\n") == 2 and "line 1" in log.text() and "got" not in log.text()
    log.clear()
    assert log.text() == ""


def test_the_tag_is_stamped_into_every_file_that_carries_a_version(tmp_path):
    (tmp_path / "mindkeep").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0.0.0"\n[tool.other]\nversion = "9"\n', encoding="utf-8"
    )
    (tmp_path / "mindkeep" / "__init__.py").write_text('__version__ = "0.0.0"\n', encoding="utf-8")
    assert stamp("v1.2.3", tmp_path) == "1.2.3"
    # only [project]'s line is stamped; the other section's is left alone
    stamped = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    assert stamped == '[project]\nname = "x"\nversion = "1.2.3"\n[tool.other]\nversion = "9"\n'
    assert (tmp_path / "mindkeep" / "__init__.py").read_text(
        encoding="utf-8"
    ) == '__version__ = "1.2.3"\n'
