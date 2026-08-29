"""The sign-in with a provider, and the Google Drive connector on top of it. Google is
never called: the token endpoint and the Drive API are stood in for, so what is tested is
the dance (PKCE, the signed state, the exchange, the refresh, a revoked sign-in) and the
connector's own logic (paths, exports, the per-folder clock, what went away)."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app import connections, grants, vault
from app.connectors import ConnectorError, registry
from app.db import Grant, session
from app.files import tenant_id
from tests.test_files import B, T

C = f"{B}/connections"


@pytest.fixture
def google(monkeypatch):
    """A configured Google app, an exchange that never leaves the process, and a Drive
    small enough to hold in a dict."""
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "app-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "app-secret")
    monkeypatch.setenv("WEB_URL", "http://app.test")
    monkeypatch.setattr(connections, "background", lambda fn, *a: fn(*a))
    registry.cache_clear()
    calls: list[dict[str, str]] = []

    def exchange(oauth: Any, data: dict[str, str]) -> dict[str, Any]:
        calls.append(data)
        if data["grant_type"] == "authorization_code":
            assert data["code"] == "the-code" and data["code_verifier"]
            return {"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600}
        if data["refresh_token"] == "rt-revoked":
            raise ConnectorError("invalid_grant: Token has been expired or revoked.")
        return {"access_token": "at-2", "expires_in": 3600}

    monkeypatch.setattr(grants, "exchange", exchange)
    drive = FakeDrive()
    monkeypatch.setattr("app.connectors.drive.call", drive.call)
    monkeypatch.setattr("app.connectors.drive.download", drive.download)
    yield calls, drive
    registry.cache_clear()


DOC, SHEET, PDF, FORM = (
    "application/vnd.google-apps.document",
    "application/vnd.google-apps.spreadsheet",
    "application/pdf",
    "application/vnd.google-apps.form",
)
FOLDER = "application/vnd.google-apps.folder"


class FakeDrive:
    """My Drive: root > Clients > Acme (a Doc, a PDF, a Form, a subfolder with a Sheet),
    and two more folders called Acme at the top, so 'Acme' alone is ambiguous."""

    def __init__(self) -> None:
        self.files: dict[str, dict[str, Any]] = {
            "f-clients": {
                "id": "f-clients",
                "name": "Clients",
                "mimeType": FOLDER,
                "parent": "root",
            },
            "f-archive": {
                "id": "f-archive",
                "name": "Archive",
                "mimeType": FOLDER,
                "parent": "root",
            },
            "f-acme": {"id": "f-acme", "name": "Acme", "mimeType": FOLDER, "parent": "f-clients"},
            "f-acme2": {"id": "f-acme2", "name": "Acme", "mimeType": FOLDER, "parent": "root"},
            "f-acme3": {"id": "f-acme3", "name": "Acme", "mimeType": FOLDER, "parent": "root"},
            "f-notes": {"id": "f-notes", "name": "Notes", "mimeType": FOLDER, "parent": "f-acme"},
            "d1": {
                "id": "d1",
                "name": "Brief",
                "mimeType": DOC,
                "parent": "f-acme",
                "modifiedTime": "t1",
            },
            "p1": {
                "id": "p1",
                "name": "deck.pdf",
                "mimeType": PDF,
                "parent": "f-acme",
                "modifiedTime": "t1",
                "size": "10",
            },
            "q1": {
                "id": "q1",
                "name": "Survey",
                "mimeType": FORM,
                "parent": "f-acme",
                "modifiedTime": "t1",
            },
            "s1": {
                "id": "s1",
                "name": "Budget",
                "mimeType": SHEET,
                "parent": "f-notes",
                "modifiedTime": "t1",
            },
            # a shared drive, Marketing, with a folder and a Doc in it
            "f-mk": {"id": "f-mk", "name": "Campaigns", "mimeType": FOLDER, "parent": "drv-1"},
            "d9": {
                "id": "d9",
                "name": "Launch",
                "mimeType": DOC,
                "parent": "f-mk",
                "modifiedTime": "t1",
            },
        }
        self.drives = [{"id": "drv-1", "name": "Marketing"}]
        self.content = {"d1": b"# Brief\n", "p1": b"%PDF", "s1": b"a,b\n1,2\n", "d9": b"# Launch\n"}
        self.downloads: list[str] = []
        self.tokens: list[str] = []

    def call(self, token: str, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        self.tokens.append(token)
        if token == "at-expired":
            raise ConnectorError("Invalid Credentials")
        if path == "about":
            return {"user": {"emailAddress": "ada@example.com"}}
        if path == "drives":
            return {"drives": self.drives}
        q = (params or {}).get("q", "")
        parent = q.split("'")[1]
        name = q.split("name = '")[1].split("'")[0] if "name = '" in q else None
        only_folders = FOLDER in q
        found = [
            {k: v for k, v in f.items() if k != "parent"}
            for f in self.files.values()
            if f["parent"] == parent
            and (name is None or f["name"] == name)
            and (not only_folders or f["mimeType"] == FOLDER)
        ]
        return {"files": found}

    def download(self, token: str, file_id: str, export: str | None) -> bytes:
        self.downloads.append(file_id)
        if file_id == "p1" and export is None and self.content.get("p1") is None:
            raise ConnectorError("File not found")
        return self.content[file_id]


def signin(client, calls) -> dict:
    """The dance, as the browser would do it: start, then the callback with Google's code."""
    started = client.get("/grants/oauth/drive/start")
    assert started.status_code == 200, started.text
    url = urlparse(started.json()["url"])
    query = parse_qs(url.query)
    assert url.netloc == "accounts.google.com"
    assert query["client_id"] == ["app-id"] and query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"] and query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == ["http://app.test/api/grants/oauth/drive/callback"]
    assert query["scope"] == ["https://www.googleapis.com/auth/drive.readonly"]
    back = client.get(
        f"/grants/oauth/drive/callback?state={query['state'][0]}&code=the-code",
        follow_redirects=False,
    )
    assert (
        back.status_code == 302 and back.headers["location"] == "http://app.test/?connected=drive"
    )
    assert calls[-1]["grant_type"] == "authorization_code"
    mine = client.get("/grants").json()
    return mine[-1]


def test_drive_is_offered_only_when_google_is_configured(client, monkeypatch):
    registry.cache_clear()
    kinds = {c["kind"]: c for c in client.get(f"{T}/connectors").json()}
    assert kinds["drive"]["auth"] == "oauth2" and kinds["drive"]["available"] is False
    assert client.get("/grants/oauth/drive/start").status_code == 400
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "x")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "y")
    kinds = {c["kind"]: c for c in client.get(f"{T}/connectors").json()}
    assert kinds["drive"]["available"] is True
    # a token is not the road in
    refused = client.post("/grants", json={"kind": "drive", "secrets": {}})
    assert refused.status_code == 400 and "sign-in button" in refused.json()["detail"]


def test_the_sign_in_makes_a_grant_named_by_drive_and_sealed(client, google):
    calls, drive = google
    made = signin(client, calls)
    assert made["kind"] == "drive" and made["label"] == "ada@example.com" and made["error"] == ""
    with session() as s:
        row = s.get(Grant, made["id"])
        assert row is not None and row.expires_at is not None
        assert vault.unseal_all(row.secret) == {"access_token": "at-1", "refresh_token": "rt-1"}
    # the callback is refused without a state of ours, or with someone else's kind in it
    forged = client.get("/grants/oauth/drive/callback?state=x.y&code=c", follow_redirects=False)
    assert "connect_error" in forged.headers["location"]
    denied = client.get("/grants/oauth/drive/callback?error=access_denied", follow_redirects=False)
    assert "connect_error=Google+Drive%3A+access_denied" in denied.headers["location"]


def test_an_expiring_token_is_renewed_before_use_and_a_revoked_one_is_told(client, google):
    calls, drive = google
    made = signin(client, calls)
    with session() as s:
        row = s.get(Grant, made["id"])
        row.expires_at = datetime.now(UTC) + timedelta(seconds=30)  # about to expire
        s.commit()
    folders = {"folders": json.dumps([{"path": "Clients/Acme", "every": "60"}])}
    made_c = client.post(C, json={"kind": "drive", "config": folders, "grant": made["id"]})
    assert made_c.status_code == 201, made_c.text
    assert calls[-1]["grant_type"] == "refresh_token" and calls[-1]["refresh_token"] == "rt-1"
    assert drive.tokens[-1] == "at-2"  # the renewed token is what Drive was called with
    with session() as s:
        row = s.get(Grant, made["id"])
        secrets = vault.unseal_all(row.secret)
        assert secrets["access_token"] == "at-2" and secrets["refresh_token"] == "rt-1"  # kept
        assert row.expires_at > datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=30)
        # now Google refuses the refresh: revoked
        row.secret = vault.seal_all({**secrets, "refresh_token": "rt-revoked"})
        row.expires_at = datetime.now(UTC)
        s.commit()
    cid = made_c.json()["id"]
    client.post(f"{C}/{cid}/sync")
    listed = client.get(C).json()[0]
    assert "sign in again" in listed["error"]
    assert client.get("/grants").json()[-1]["error"] == grants.EXPIRED


def test_folders_each_on_their_own_clock_docs_exported_forms_skipped(client, google, tmp_path):
    calls, drive = google
    made = signin(client, calls)
    home = tmp_path / tenant_id("alice") / "default"
    folders = {
        "folders": json.dumps(
            [
                {"path": "Clients/Acme", "every": "60"},
                {
                    "path": "https://drive.google.com/drive/folders/f-notes?usp=sharing",
                    "every": "15",
                },
            ]
        )
    }
    made_c = client.post(C, json={"kind": "drive", "config": folders, "grant": made["id"]})
    assert made_c.status_code == 201, made_c.text
    assert made_c.json()["name"] == "Acme, f-notes"
    base = home / "raw/connectors/drive"
    assert sorted(p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file()) == [
        "Clients/Acme/Brief.md",
        "Clients/Acme/Notes/Budget.csv",
        "Clients/Acme/deck.pdf",
    ]  # the Sheet under Notes is reached through both rows and taken once
    assert (base / "Clients/Acme/Brief.md").read_bytes() == b"# Brief\n"
    assert client.get(C).json()[0]["summary"] == "+3 ~0 -0"  # the form was skipped

    # the next tick: neither folder is due — nothing is listed, nothing downloaded
    drive.downloads.clear()
    cid = made_c.json()["id"]
    client.post(f"{C}/{cid}/sync")
    assert drive.downloads == [] and client.get(C).json()[0]["summary"] == "+0 ~0 -0"

    # a Doc edited, a PDF gone, a new Doc: only those move, when the folder is due
    drive.files["d1"]["modifiedTime"] = "t2"
    drive.content["d1"] = b"# Brief v2\n"
    del drive.files["p1"]
    drive.files["d2"] = {
        "id": "d2",
        "name": "Plan",
        "mimeType": DOC,
        "parent": "f-acme",
        "modifiedTime": "t1",
    }
    drive.content["d2"] = b"# Plan\n"
    with session() as s:
        from app.db import Connection

        row = s.get(Connection, cid)
        cursor = json.loads(row.cursor)
        cursor["folders"]["Clients/Acme"]["pulled"] = (
            datetime.now(UTC) - timedelta(hours=2)
        ).isoformat()
        row.cursor = json.dumps(cursor)
        s.commit()
    client.post(f"{C}/{cid}/sync")
    assert sorted(drive.downloads) == ["d1", "d2"]
    assert client.get(C).json()[0]["summary"] == "+1 ~1 -1"
    assert (base / "Clients/Acme/Brief.md").read_bytes() == b"# Brief v2\n"
    assert not (base / "Clients/Acme/deck.pdf").exists()
    assert (base / "Clients/Acme/Plan.md").exists()

    # an ambiguous name is refused with the count; a missing one is refused too
    for bad in ("Acme", "Clients/Nope"):
        refused = client.put(
            f"{C}/{cid}", json={"config": {"folders": json.dumps([{"path": bad, "every": "60"}])}}
        )
        assert refused.status_code == 400, bad
    assert (
        "2 folders called Acme"
        in client.put(
            f"{C}/{cid}",
            json={"config": {"folders": json.dumps([{"path": "Acme", "every": "60"}])}},
        ).json()["detail"]
    )


def test_folder_paths_are_made_safe():
    from app.connectors.drive import safe_path

    assert safe_path("Clients/Acme") == "Clients/Acme"
    assert safe_path("Clients/A:c*me") == "Clients/A-c-me"
    assert safe_path("https://drive.google.com/drive/folders/abc_123?usp=sharing") == "abc_123"


def test_state_is_ours_and_expires(client, monkeypatch):
    monkeypatch.setenv("DEVICE_SECRET", "test-secret")
    state = grants.make_state(
        {"sub": "alice", "kind": "drive", "verifier": "v", "exp": "2999-01-01T00:00:00+00:00"}
    )
    assert grants.read_state(state)["sub"] == "alice"
    with pytest.raises(HTTPException):
        grants.read_state(state[:-1] + ("0" if state[-1] != "0" else "1"))
    old = grants.make_state(
        {"sub": "alice", "kind": "drive", "verifier": "v", "exp": "2000-01-01T00:00:00+00:00"}
    )
    with pytest.raises(HTTPException):
        grants.read_state(old)
    with session() as s:
        assert s.scalars(select(Grant)).all() == []


def test_browsing_walks_my_drive_and_the_shared_drives_and_a_pick_is_a_path(
    client, google, tmp_path
):
    calls, drive = google
    made = signin(client, calls)
    browse = f"{B}/connectors/drive/browse"

    def look(at: str) -> list[dict]:
        got = client.post(browse, json={"field": "path", "at": at, "grant": made["id"]})
        assert got.status_code == 200, got.text
        return got.json()["choices"]

    # the top: My Drive's folders (three Acmes at the top are still three choices) and
    # the shared drives; then down, values being paths a person could have typed
    assert [c["label"] for c in look("")] == ["Acme", "Acme", "Archive", "Clients", "Shared drives"]
    assert [c["value"] for c in look("Clients")] == ["Clients/Acme"]
    assert [c["value"] for c in look("Clients/Acme")] == ["Clients/Acme/Notes"]
    assert [c["value"] for c in look("Shared drives")] == ["Shared drives/Marketing"]
    assert [c["value"] for c in look("Shared drives/Marketing")] == [
        "Shared drives/Marketing/Campaigns"
    ]
    # the field must be a real one, the sign-in the caller's
    assert (
        client.post(browse, json={"field": "path", "at": "Nope", "grant": made["id"]}).status_code
        == 400
    )
    assert client.post(browse, json={"field": "path", "at": "", "grant": None}).status_code == 400

    # a shared-drive path connects like any other
    home = tmp_path / tenant_id("alice") / "default"
    folders = {
        "folders": json.dumps([{"path": "Shared drives/Marketing/Campaigns", "every": "60"}])
    }
    made_c = client.post(C, json={"kind": "drive", "config": folders, "grant": made["id"]})
    assert made_c.status_code == 201, made_c.text
    assert made_c.json()["name"] == "Campaigns"
    launch = home / "raw/connectors/drive/Shared drives/Marketing/Campaigns/Launch.md"
    assert launch.read_bytes() == b"# Launch\n"
    nope = {"folders": json.dumps([{"path": "Shared drives/Nope", "every": "60"}])}
    refused = client.put(f"{C}/{made_c.json()['id']}", json={"config": nope})
    assert refused.status_code == 400 and "no shared drive called Nope" in refused.json()["detail"]
