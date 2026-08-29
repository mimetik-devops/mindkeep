"""Connectors are plugins; connections are the plumbing under them. A fake connector,
found the way an installed one is, exercises the whole road: catalog, check, first sync,
diff, rename, incremental pull, mirror semantics, secrets at rest, disconnect."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import pytest
from sqlalchemy import select

from app import connections, history, runs, schedule, syncing, vault
from app.connectors import Connector, ConnectorError, Field, Item, Pull, registry
from app.db import Connection, ConnectorItem, session
from app.files import tenant_id
from tests.test_files import B, T

C = f"{B}/connections"
W = "raw/connectors/Team wiki"  # where the fake connection's files land


class Fake(Connector):
    kind = "fake"
    title = "Fake"
    blurb = "for the tests"
    fields = (Field("space", "Space"), Field("token", "Token", secret=True))
    auth = "token"
    # what the next pull returns — the tests set these
    items: ClassVar[list[Item]] = []
    complete: ClassVar[bool] = True
    removed: ClassVar[list[str]] = []
    fail: ClassVar[str] = ""

    def check(self, config: dict[str, str]) -> None:
        if config["token"] != "ok":
            raise ConnectorError("that token was refused")

    def pull(self, config: dict[str, str], cursor: dict[str, Any]) -> Pull:
        if Fake.fail:
            raise ConnectorError(Fake.fail)
        return Pull(
            items=list(Fake.items),
            cursor={"n": cursor.get("n", 0) + 1},
            complete=Fake.complete,
            removed=list(Fake.removed),
        )


class FakeEntryPoint:
    """What importlib.metadata hands back for an installed plugin."""

    name = "fake"

    def load(self) -> type[Connector]:
        return Fake


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr("app.connectors.entry_points", lambda group: [FakeEntryPoint()])
    registry.cache_clear()
    monkeypatch.setattr(connections, "background", lambda fn, *a: fn(*a))  # syncs inline
    Fake.items, Fake.complete, Fake.removed, Fake.fail = [], True, [], ""
    yield
    registry.cache_clear()


def connect(client, name="Team wiki", token="ok", **extra):
    body = {"kind": "fake", "name": name, "config": {"space": "x", "token": token}, **extra}
    return client.post(C, json=body)


def row_of(connection_id: str) -> Connection:
    with session() as s:
        row = s.get(Connection, connection_id)
        assert row is not None
        s.expunge(row)
        return row


def test_a_plugin_is_found_through_its_entry_point_and_listed_with_the_built_ins(client, fake):
    kinds = {c["kind"]: c for c in client.get(f"{T}/connectors").json()}
    assert "url" in kinds and kinds["url"]["available"]
    assert kinds["fake"]["fields"] == [
        {"name": "space", "label": "Space", "secret": False, "help": "", "required": True},
        {"name": "token", "label": "Token", "secret": True, "help": "", "required": True},
    ]


def test_a_connection_pulls_files_into_raw_commits_them_and_queues_the_ingest(
    client, fake, ingested, tmp_path
):
    home = tmp_path / tenant_id("alice") / "default"
    client.get(f"{T}/bundles")
    Fake.items = [Item("1", "notes/a.md", b"A"), Item("2", "b.txt", b"B")]

    made = connect(client)
    assert made.status_code == 201, made.text
    cid = made.json()["id"]
    assert (home / "raw/connectors/Team wiki/notes/a.md").read_bytes() == b"A"
    assert (home / "raw/connectors/Team wiki/b.txt").read_bytes() == b"B"
    assert history.commits(home)[0]["subject"] == "sync Team wiki: +2 ~0 -0"
    assert sorted(c[1] for c in ingested) == [f"{W}/b.txt", f"{W}/notes/a.md"]
    listed = client.get(C).json()
    assert listed[0]["summary"] == "+2 ~0 -0" and listed[0]["error"] == ""
    assert listed[0]["folder"] == "raw/connectors/Team wiki"
    assert json.loads(row_of(cid).cursor) == {"n": 1}

    # the next pull: one changed, one gone, one new — and the cursor came back around
    ingested.clear()
    Fake.items = [Item("1", "notes/a.md", b"A2"), Item("3", "c.md", b"C")]
    assert client.post(f"{C}/{cid}/sync").status_code == 202
    assert (home / "raw/connectors/Team wiki/notes/a.md").read_bytes() == b"A2"
    assert not (home / "raw/connectors/Team wiki/b.txt").exists()
    assert (home / "raw/connectors/Team wiki/c.md").read_bytes() == b"C"
    assert client.get(C).json()[0]["summary"] == "+1 ~1 -1"
    assert history.commits(home)[0]["subject"] == "sync Team wiki: +1 ~1 -1"
    # the gone file cited no page, so nothing is queued to retire it
    assert sorted(c[1] for c in ingested) == [f"{W}/c.md", f"{W}/notes/a.md"]
    assert json.loads(row_of(cid).cursor) == {"n": 2}

    # nothing changed: nothing written, nothing committed, nothing queued
    ingested.clear()
    before = history.head(home)
    client.post(f"{C}/{cid}/sync")
    assert history.head(home) == before and not ingested
    assert client.get(C).json()[0]["summary"] == "+0 ~0 -0"

    # renamed at the source: the same id under a new path is a move
    Fake.items = [Item("1", "notes/renamed.md", b"A2"), Item("3", "c.md", b"C")]
    client.post(f"{C}/{cid}/sync")
    assert not (home / "raw/connectors/Team wiki/notes/a.md").exists()
    assert (home / "raw/connectors/Team wiki/notes/renamed.md").read_bytes() == b"A2"

    # an incremental pull names only what changed; the rest is left alone
    Fake.complete, Fake.items, Fake.removed = False, [Item("4", "d.md", b"D")], ["3"]
    client.post(f"{C}/{cid}/sync")
    assert (home / f"{W}/d.md").exists()
    assert not (home / f"{W}/c.md").exists()
    assert (home / "raw/connectors/Team wiki/notes/renamed.md").exists()
    with session() as s:
        remotes = sorted(s.scalars(select(ConnectorItem.remote)).all())
    assert remotes == ["1", "4"]


def test_a_refused_token_is_told_and_nothing_is_made(client, fake):
    refused = connect(client, token="bad")
    assert refused.status_code == 400 and refused.json()["detail"] == "that token was refused"
    assert client.get(C).json() == []
    assert connect(client, name="../etc").status_code == 400
    assert connect(client, every=1).status_code == 400


def test_secrets_are_sealed_at_rest_and_kept_when_the_marker_comes_back(client, fake):
    cid = connect(client).json()["id"]
    stored = row_of(cid).config
    sealed = json.loads(stored)
    assert sealed["space"] == "x" and sealed["token"] != "ok"
    assert sealed["token"].startswith("gAAAA")  # a Fernet token, not the secret
    assert vault.unseal(stored, registry()["fake"]) == {"space": "x", "token": "ok"}
    shown = client.get(C).json()[0]["config"]
    assert shown == {"space": "x", "token": vault.REDACTED}

    # the form comes back with the marker in the secret field: the secret stays
    changed = client.put(f"{C}/{cid}", json={"config": {"space": "y", "token": vault.REDACTED}})
    assert changed.status_code == 200 and changed.json()["config"]["space"] == "y"
    assert vault.unseal(row_of(cid).config, registry()["fake"]) == {"space": "y", "token": "ok"}
    # a new secret is tried before it is saved
    assert (
        client.put(f"{C}/{cid}", json={"config": {"space": "y", "token": "bad"}}).status_code == 400
    )
    assert client.put(f"{C}/{cid}", json={"every": 30, "enabled": False}).json()["every"] == 30
    assert row_of(cid).enabled is False


def test_a_failing_pull_is_the_connections_error_not_the_servers(client, fake, tmp_path):
    Fake.fail = "the workspace is gone"
    made = connect(client)  # check passes; the pull does not
    assert made.status_code == 201
    listed = client.get(C).json()[0]
    assert listed["error"] == "the workspace is gone" and listed["synced_at"]
    assert not (tmp_path / tenant_id("alice") / "default" / "raw/connectors/Team wiki").exists()
    # a second connection by the same name is refused
    assert connect(client).status_code == 409


def test_a_file_edited_or_deleted_by_hand_is_put_back_by_the_next_sync(client, fake, tmp_path):
    home = tmp_path / tenant_id("alice") / "default"
    Fake.items = [Item("1", "a.md", b"theirs")]
    cid = connect(client).json()["id"]
    assert client.put(f"{B}/files/{W}/a.md", content=b"mine").status_code == 200
    client.post(f"{C}/{cid}/sync")
    assert (home / "raw/connectors/Team wiki/a.md").read_bytes() == b"theirs"
    assert client.get(C).json()[0]["summary"] == "+0 ~1 -0"
    (home / "raw/connectors/Team wiki/a.md").unlink()
    client.post(f"{C}/{cid}/sync")
    assert (home / "raw/connectors/Team wiki/a.md").read_bytes() == b"theirs"


def test_disconnecting_removes_what_the_connection_wrote(client, fake, tmp_path):
    home = tmp_path / tenant_id("alice") / "default"
    Fake.items = [Item("1", "notes/a.md", b"A")]
    cid = connect(client).json()["id"]
    gone = client.delete(f"{C}/{cid}")
    assert gone.status_code == 200 and gone.json()["removed"] == 1
    assert not (home / "raw/connectors/Team wiki").exists()
    assert history.commits(home)[0]["subject"] == "disconnect Team wiki"
    assert client.get(C).json() == []
    with session() as s:
        assert s.scalars(select(ConnectorItem)).all() == []
    assert client.delete(f"{C}/{cid}").status_code == 404


def test_a_connection_follows_its_bundle_when_renamed_and_dies_with_it(client, fake):
    cid = connect(client).json()["id"]
    runs.rename_bundle(tenant_id("alice"), "default", "renamed")
    assert row_of(cid).bundle == "renamed"
    runs.forget_bundle(tenant_id("alice"), "renamed")
    with session() as s:
        assert s.get(Connection, cid) is None


def test_the_sweep_syncs_what_is_due_and_only_that(client, fake, monkeypatch, tmp_path):
    Fake.items = [Item("1", "a.md", b"A")]
    cid = connect(client).json()["id"]
    now = datetime.now(UTC)
    row = row_of(cid)
    assert not syncing.due(row, now)  # synced just now, hourly
    assert syncing.due(row, now + timedelta(minutes=61))
    row.enabled = False
    assert not syncing.due(row, now + timedelta(days=1))

    ran: list[str] = []

    class Inline:
        def __init__(self, target, args, **_):
            self.target, self.args = target, args

        def start(self):
            ran.append(self.args[1])
            self.target(*self.args)

    monkeypatch.setattr(schedule.threading, "Thread", Inline)
    monkeypatch.setenv("WIKI_ROOT", str(tmp_path))
    schedule.sync_due(now)
    assert ran == []
    schedule.sync_due(now + timedelta(hours=2))
    assert ran == [cid]


def test_the_url_connector_fetches_one_address_into_a_named_file(client, monkeypatch, tmp_path):
    monkeypatch.setattr(connections, "background", lambda fn, *a: fn(*a))
    monkeypatch.setattr("app.connectors.url.fetch", lambda url: (b"<h1>hi</h1>", "text/html"))
    home = tmp_path / tenant_id("alice") / "default"
    body = {"kind": "url", "name": "Docs", "config": {"url": "https://example.com/docs/page"}}
    assert client.post(C, json=body).status_code == 201
    assert (home / "raw/connectors/Docs/page.html").read_bytes() == b"<h1>hi</h1>"
    body["name"], body["config"]["url"] = "Home", "https://example.com/"
    client.post(C, json=body)
    # a host name gets the content type's suffix
    assert (home / "raw/connectors/Home/example.com.html").is_file()
    body["name"], body["config"]["url"] = "Bad", "ftp://example.com"
    refused = client.post(C, json=body)
    assert refused.status_code == 400 and "http" in refused.json()["detail"]
