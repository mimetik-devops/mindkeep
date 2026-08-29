"""Connectors are plugins; connections are the plumbing under them; grants are a person's
standing with a provider. A fake connector, found the way an installed one is, exercises
the whole road: catalog, grant, check, first sync, diff, rename, incremental pull, mirror
semantics, secrets at rest, disconnect."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import pytest
from sqlalchemy import select

from app import connections, history, runs, schedule, syncing, vault
from app.connectors import Connector, ConnectorError, Field, Grant, Item, Pull, registry
from app.db import Connection, ConnectorItem, session
from app.db import Grant as GrantRow
from app.files import tenant_id
from tests.test_files import B, T

C = f"{B}/connections"
W = "raw/connectors/fake"  # where the fake connection's files land: its kind


class Fake(Connector):
    """A token kind: a grant from one secret, a connection scoped by a space."""

    kind = "fake"
    title = "Fake"
    blurb = "for the tests"
    auth = "token"
    grant_fields = (Field("token", "Token", secret=True),)
    fields = (Field("space", "Space"),)
    # what the next pull returns — the tests set these
    items: ClassVar[list[Item]] = []
    complete: ClassVar[bool] = True
    removed: ClassVar[list[str]] = []
    fail: ClassVar[str] = ""
    pulled_with: ClassVar[list[Grant | None]] = []

    def check_grant(self, secrets: dict[str, str]) -> str:
        if secrets["token"] != "ok":
            raise ConnectorError("that token was refused")
        return "Ada's workspace"

    def check(self, config: dict[str, str], grant: Grant | None) -> None:
        if config["space"] == "nope":
            raise ConnectorError("no such space")

    def pull(self, config: dict[str, str], cursor: dict[str, Any], grant: Grant | None) -> Pull:
        Fake.pulled_with.append(grant)
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
    Fake.items, Fake.complete, Fake.removed, Fake.fail, Fake.pulled_with = [], True, [], "", []
    yield
    registry.cache_clear()


def grant(client, token="ok", user="alice") -> dict:
    body = {"kind": "fake", "secrets": {"token": token}}
    made = client.post("/grants", json=body, headers={"x-test-user": user})
    return made.json() if made.status_code == 201 else {"status": made.status_code, **made.json()}


def connect(client, space="x", grant_id=None, **extra):
    body = {"kind": "fake", "config": {"space": space}, "grant": grant_id, **extra}
    return client.post(C, json=body)


def row_of(connection_id: str) -> Connection:
    with session() as s:
        row = s.get(Connection, connection_id)
        assert row is not None
        s.expunge(row)
        return row


def test_a_plugin_is_found_through_its_entry_point_and_listed_with_the_built_ins(client, fake):
    kinds = {c["kind"]: c for c in client.get(f"{T}/connectors").json()}
    assert "website" in kinds and kinds["website"]["available"]
    assert kinds["website"]["auth"] == "none" and kinds["website"]["grant_fields"] == []
    sites = kinds["website"]["fields"][0]
    assert sites["name"] == "sites" and [r["name"] for r in sites["rows"]] == [
        "url",
        "pages",
        "every",
    ]
    assert sites["rows"][2]["options"][0] == ["15", "every 15 minutes"]
    assert kinds["website"]["tick"] == 15 and kinds["website"]["folder"] == "raw/connectors/website"
    assert kinds["fake"]["auth"] == "token"
    assert kinds["fake"]["grant_fields"] == [
        {
            "name": "token",
            "label": "Token",
            "secret": True,
            "help": "",
            "required": True,
            "multiline": False,
            "options": [],
            "rows": [],
        }
    ]


def test_a_grant_is_tried_named_by_the_connector_sealed_and_the_persons_own(client, fake):
    refused = grant(client, token="bad")
    assert refused["status"] == 400 and refused["detail"] == "that token was refused"
    made = grant(client)
    assert made["label"] == "Ada's workspace" and made["uses"] == 0
    listed = client.get("/grants").json()
    assert [g["id"] for g in listed] == [made["id"]] and "secret" not in listed[0]
    # sealed at rest, readable only through the vault
    with session() as s:
        stored = s.get(GrantRow, made["id"]).secret
    assert json.loads(stored)["token"].startswith("gAAAA")
    assert vault.unseal_all(stored) == {"token": "ok"}
    # another person sees nothing of it, and cannot use it
    assert client.get("/grants", headers={"x-test-user": "bob"}).json() == []
    bob = {"x-test-user": "bob"}
    assert client.delete(f"/grants/{made['id']}", headers=bob).status_code == 404
    # a kind that needs no sign-in refuses one
    none = client.post("/grants", json={"kind": "website", "secrets": {}})
    assert none.status_code == 400 and "no sign-in needed" in none.json()["detail"]


def test_a_connection_needs_the_callers_grant_of_the_right_kind(client, fake):
    assert connect(client).status_code == 400  # none given
    assert "sign-in" in connect(client).json()["detail"]
    bobs = grant(client, user="bob")["id"]
    assert connect(client, grant_id=bobs).status_code == 404  # not alice's
    mine = grant(client)["id"]
    assert connect(client, space="nope", grant_id=mine).status_code == 400  # scope refused
    made = connect(client, grant_id=mine)
    assert made.status_code == 201, made.text
    assert made.json()["grant"] == {"id": mine, "label": "Ada's workspace"}
    assert client.get("/grants").json()[0]["uses"] == 1
    # a kind that needs no grant refuses one
    site = {"kind": "website", "config": {"sites": "[]"}, "grant": mine}
    assert client.post(C, json=site).status_code == 400


def test_a_connection_pulls_files_into_raw_commits_them_and_queues_the_ingest(
    client, fake, ingested, tmp_path
):
    home = tmp_path / tenant_id("alice") / "default"
    client.get(f"{T}/bundles")
    Fake.items = [Item("1", "notes/a.md", b"A"), Item("2", "b.txt", b"B")]
    mine = grant(client)["id"]

    made = connect(client, grant_id=mine)
    assert made.status_code == 201, made.text
    cid = made.json()["id"]
    # the pull was handed the grant, secrets in the clear
    assert Fake.pulled_with[-1] and Fake.pulled_with[-1].token == "ok"
    assert Fake.pulled_with[-1].label == "Ada's workspace"
    assert (home / f"{W}/notes/a.md").read_bytes() == b"A"
    assert (home / f"{W}/b.txt").read_bytes() == b"B"
    assert history.commits(home)[0]["subject"] == "sync x: +2 ~0 -0"
    assert sorted(c[1] for c in ingested) == [f"{W}/b.txt", f"{W}/notes/a.md"]
    listed = client.get(C).json()
    assert listed[0]["summary"] == "+2 ~0 -0" and listed[0]["error"] == ""
    assert listed[0]["folder"] == W
    assert json.loads(row_of(cid).cursor) == {"n": 1}

    # the next pull: one changed, one gone, one new — and the cursor came back around
    ingested.clear()
    Fake.items = [Item("1", "notes/a.md", b"A2"), Item("3", "c.md", b"C")]
    assert client.post(f"{C}/{cid}/sync").status_code == 202
    assert (home / f"{W}/notes/a.md").read_bytes() == b"A2"
    assert not (home / f"{W}/b.txt").exists()
    assert (home / f"{W}/c.md").read_bytes() == b"C"
    assert client.get(C).json()[0]["summary"] == "+1 ~1 -1"
    assert history.commits(home)[0]["subject"] == "sync x: +1 ~1 -1"
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
    assert not (home / f"{W}/notes/a.md").exists()
    assert (home / f"{W}/notes/renamed.md").read_bytes() == b"A2"

    # an incremental pull names only what changed; the rest is left alone
    Fake.complete, Fake.items, Fake.removed = False, [Item("4", "d.md", b"D")], ["3"]
    client.post(f"{C}/{cid}/sync")
    assert (home / f"{W}/d.md").exists()
    assert not (home / f"{W}/c.md").exists()
    assert (home / f"{W}/notes/renamed.md").exists()
    with session() as s:
        remotes = sorted(s.scalars(select(ConnectorItem.remote)).all())
    assert remotes == ["1", "4"]


def test_a_revoked_grant_leaves_the_connection_standing_and_says_so(client, fake, tmp_path):
    Fake.items = [Item("1", "a.md", b"A")]
    mine = grant(client)["id"]
    cid = connect(client, grant_id=mine).json()["id"]
    gone = client.delete(f"/grants/{mine}")
    assert gone.status_code == 200 and gone.json()["orphaned"] == 1
    client.post(f"{C}/{cid}/sync")
    listed = client.get(C).json()[0]
    assert listed["grant"] is None and listed["grant_gone"] is True
    assert "sign-in this connection used is gone" in listed["error"]
    # the files it pulled are still there — one person's revocation does not empty a bundle
    assert (tmp_path / tenant_id("alice") / "default" / W / "a.md").exists()
    # pick another sign-in and it is back
    other = grant(client)["id"]
    fixed = client.put(f"{C}/{cid}", json={"grant": other})
    assert fixed.status_code == 200 and fixed.json()["grant"]["id"] == other
    client.post(f"{C}/{cid}/sync")
    assert client.get(C).json()[0]["error"] == ""


def test_the_connector_names_the_connection_and_a_bundle_has_one_of_a_kind(client, fake):
    mine = grant(client)["id"]
    assert connect(client, every=1, grant_id=mine).status_code == 400
    assert client.get(C).json() == []
    # the name is the connector's, from the config — the default: the first plain field
    made = connect(client, space="docs", grant_id=mine)
    assert made.status_code == 201 and made.json()["name"] == "docs"
    assert made.json()["folder"] == "raw/connectors/fake"
    # a second of the kind is refused: the form holds the plural
    assert connect(client, space="other", grant_id=mine).status_code == 409
    # the name follows the settings
    cid = made.json()["id"]
    assert client.put(f"{C}/{cid}", json={"config": {"space": "other"}}).json()["name"] == "other"


def test_secrets_in_a_connections_config_are_sealed_and_kept_when_the_marker_comes_back(
    client, fake, monkeypatch
):
    # a connector may keep a secret in the connection itself — a per-scope key, say
    fields = (Field("space", "Space"), Field("key", "Key", secret=True))
    monkeypatch.setattr(Fake, "fields", fields)
    mine = grant(client)["id"]
    body = {"kind": "fake", "config": {"space": "x", "key": "k1"}, "grant": mine}
    made = client.post(C, json=body)
    assert made.status_code == 201, made.text
    cid = made.json()["id"]
    sealed = json.loads(row_of(cid).config)
    assert sealed["space"] == "x" and sealed["key"] != "k1"
    assert vault.unseal(row_of(cid).config, registry()["fake"]) == {"space": "x", "key": "k1"}
    assert client.get(C).json()[0]["config"] == {"space": "x", "key": vault.REDACTED}
    changed = client.put(f"{C}/{cid}", json={"config": {"space": "y", "key": vault.REDACTED}})
    assert changed.status_code == 200 and changed.json()["config"]["space"] == "y"
    assert vault.unseal(row_of(cid).config, registry()["fake"]) == {"space": "y", "key": "k1"}
    assert client.put(f"{C}/{cid}", json={"every": 30, "enabled": False}).json()["every"] == 30
    assert row_of(cid).enabled is False


def test_a_failing_pull_is_the_connections_error_not_the_servers(client, fake, tmp_path):
    Fake.fail = "the workspace is gone"
    mine = grant(client)["id"]
    made = connect(client, grant_id=mine)  # check passes; the pull does not
    assert made.status_code == 201
    listed = client.get(C).json()[0]
    assert listed["error"] == "the workspace is gone" and listed["synced_at"]
    assert not (tmp_path / tenant_id("alice") / "default" / W).exists()
    # a second of the kind is refused
    assert connect(client, grant_id=mine).status_code == 409


def test_a_file_edited_or_deleted_by_hand_is_put_back_by_the_next_sync(client, fake, tmp_path):
    home = tmp_path / tenant_id("alice") / "default"
    Fake.items = [Item("1", "a.md", b"theirs")]
    cid = connect(client, grant_id=grant(client)["id"]).json()["id"]
    assert client.put(f"{B}/files/{W}/a.md", content=b"mine").status_code == 200
    client.post(f"{C}/{cid}/sync")
    assert (home / f"{W}/a.md").read_bytes() == b"theirs"
    assert client.get(C).json()[0]["summary"] == "+0 ~1 -0"
    (home / f"{W}/a.md").unlink()
    client.post(f"{C}/{cid}/sync")
    assert (home / f"{W}/a.md").read_bytes() == b"theirs"


def test_disconnecting_removes_what_the_connection_wrote(client, fake, tmp_path):
    home = tmp_path / tenant_id("alice") / "default"
    Fake.items = [Item("1", "notes/a.md", b"A")]
    cid = connect(client, grant_id=grant(client)["id"]).json()["id"]
    gone = client.delete(f"{C}/{cid}")
    assert gone.status_code == 200 and gone.json()["removed"] == 1
    assert not (home / W).exists()
    assert history.commits(home)[0]["subject"] == "disconnect x"
    assert client.get(C).json() == []
    with session() as s:
        assert s.scalars(select(ConnectorItem)).all() == []
    assert client.delete(f"{C}/{cid}").status_code == 404


def test_a_connection_follows_its_bundle_when_renamed_and_dies_with_it(client, fake):
    cid = connect(client, grant_id=grant(client)["id"]).json()["id"]
    runs.rename_bundle(tenant_id("alice"), "default", "renamed")
    assert row_of(cid).bundle == "renamed"
    runs.forget_bundle(tenant_id("alice"), "renamed")
    with session() as s:
        assert s.get(Connection, cid) is None


def test_the_sweep_syncs_what_is_due_and_only_that(client, fake, monkeypatch, tmp_path):
    Fake.items = [Item("1", "a.md", b"A")]
    cid = connect(client, grant_id=grant(client)["id"]).json()["id"]
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


SITE = {
    "https://site.test/": (
        b"<html><head><title>Site</title><meta name=description content='A test site'>"
        b"<script>x()</script></head><body><nav><a href='/docs'>Docs</a></nav>"
        b"<h1>Hello</h1><p>See <a href='/docs/'>the docs</a>, <a href='/about#team'>us</a>, "
        b"<a href='https://elsewhere.test/'>them</a> and <a href='/deck.pdf'>the deck</a>.</p>"
        b"<ul><li>one</li><li>two</li></ul><footer>foot</footer></body></html>",
        "text/html",
    ),
    "https://site.test/docs": (
        b"<html><body><h2>Docs</h2><p>Read me.</p></body></html>",
        "text/html",
    ),
    "https://site.test/about": (b"<html><body><p>About us.</p></body></html>", "text/html"),
    "https://site.test/deck.pdf": (b"%PDF-1.4 fake", "application/pdf"),
    "https://other.test/": (b"<html><body><h1>Other</h1></body></html>", "text/html"),
}


def fake_fetch(url: str) -> tuple[bytes, str]:
    if url not in SITE:
        raise ConnectorError(f"{url} answered 404")
    return SITE[url]


def sites(*rows: dict) -> dict:
    return {"kind": "website", "config": {"sites": json.dumps(list(rows))}}


def test_one_website_connection_holds_the_sites_each_with_its_own_depth_and_clock(
    client, monkeypatch, tmp_path
):
    monkeypatch.setattr(connections, "background", lambda fn, *a: fn(*a))
    monkeypatch.setattr("app.connectors.website.fetch", fake_fetch)
    home = tmp_path / tenant_id("alice") / "default"
    made = client.post(
        C,
        json=sites(
            {"url": "https://site.test/", "pages": "", "every": "1440"},
            {"url": "https://other.test/", "pages": "1", "every": "15"},
        ),
    )
    assert made.status_code == 201, made.text
    # named by the connector — the hosts — filed under its folder, on the plumbing's tick
    assert made.json()["name"] == "site.test, other.test" and made.json()["every"] == 15
    assert made.json()["folder"] == "raw/connectors/website"
    folder = home / "raw/connectors/website"
    assert sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*.md")) == [
        "other.test/index.md",
        "site.test/about.md",
        "site.test/docs.md",
        "site.test/index.md",
    ]
    page = (folder / "site.test/index.md").read_text(encoding="utf-8")
    # frontmatter from the page's own head; links absolute; noise gone; structure kept
    head = '---\ntitle: "Site"\nsource: https://site.test/\ndescription: "A test site"\n---\n'
    assert page.startswith(head)
    assert "# Hello" in page and "- one\n- two" in page
    assert "[the docs](https://site.test/docs/)" in page
    assert "x()" not in page and "foot" not in page and "Docs\n" not in page.split("# Hello")[0]
    # the same site only, fragments dropped, a linked PDF is not a page
    assert not (folder / "site.test/deck.pdf").exists()
    assert client.get(C).json()[0]["summary"] == "+4 ~0 -0"
    cid = client.get(C).json()[0]["id"]

    # the next tick: neither site is due, so nothing is fetched and nothing changes
    calls: list[str] = []
    monkeypatch.setattr(
        "app.connectors.website.fetch", lambda url: (calls.append(url), fake_fetch(url))[1]
    )
    client.post(f"{C}/{cid}/sync")
    assert calls == [] and client.get(C).json()[0]["summary"] == "+0 ~0 -0"

    # a second website connection is refused: the list is the place
    assert client.post(C, json=sites({"url": "https://other.test/"})).status_code == 409

    # dropping a site from the list removes its pages; the other site is left alone
    edited = client.put(f"{C}/{cid}", json=sites({"url": "https://site.test/", "every": "15"}))
    assert edited.status_code == 200 and edited.json()["name"] == "site.test"
    client.post(f"{C}/{cid}/sync")
    assert not (folder / "other.test").exists()
    assert (folder / "site.test/about.md").exists()

    # a section: the address's path bounds its crawl
    client.put(f"{C}/{cid}", json=sites({"url": "https://site.test/docs/", "every": "15"}))
    client.post(f"{C}/{cid}/sync")
    assert sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*.md")) == [
        "site.test/docs.md"
    ]

    # the list is checked before anything is saved
    for bad in (
        sites({"url": "https://site.test/", "pages": "0"}),
        sites({"url": "ftp://site.test"}),
        sites({"url": "https://site.test/", "every": "7"}),
        sites({"url": "https://site.test/"}, {"url": "https://site.test/"}),
        {"kind": "website", "config": {"sites": "[]"}},
    ):
        assert client.put(f"{C}/{cid}", json=bad).status_code == 400, bad


def test_an_address_that_is_not_a_page_is_kept_as_the_file_it_is(client, monkeypatch, tmp_path):
    monkeypatch.setattr(connections, "background", lambda fn, *a: fn(*a))
    monkeypatch.setattr("app.connectors.website.fetch", fake_fetch)
    home = tmp_path / tenant_id("alice") / "default"
    made = client.post(C, json=sites({"url": "https://site.test/deck.pdf"}))
    assert made.status_code == 201, made.text
    deck = home / "raw/connectors/website/site.test/deck.pdf"
    assert deck.read_bytes() == b"%PDF-1.4 fake"


def test_page_paths_and_file_names_come_from_the_address():
    from app.connectors.website import canonical, file_path, page_path

    assert page_path("https://a.test/") == "a.test/index.md"
    assert page_path("https://a.test/docs/setup.html") == "a.test/docs/setup.md"
    assert page_path("https://a.test/docs") == "a.test/docs.md"
    assert canonical("https://a.test/docs/#top") == "https://a.test/docs"
    assert canonical("https://a.test/") == "https://a.test/"
    assert file_path("https://a.test/", "text/csv") == "a.test/a.test.csv"
    assert file_path("https://a.test/data/export", "application/json") == "a.test/export.json"
    assert file_path("https://a.test/deck.pdf", "application/pdf") == "a.test/deck.pdf"
