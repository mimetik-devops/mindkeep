"""The built-in identity provider: register, sign in, and what a session token carries —
without a provider in the loop, because there is none."""

import time

import jwt
import pytest
from fastapi.testclient import TestClient

from app import accounts, auth
from app.main import app
from tests.test_files import database  # noqa: F401 - the fixture


@pytest.fixture
def local(tmp_path, monkeypatch, database):  # noqa: F811
    """The real auth path, in builtin mode: nothing overridden."""
    monkeypatch.setenv("WIKI_ROOT", str(tmp_path))
    monkeypatch.setenv("DEVICE_SECRET", "test-secret")
    monkeypatch.setenv("AUTH_PROVIDER", "builtin")
    monkeypatch.setenv("AUTH_SECRET", "a-secret-long-enough-to-sign-with")
    monkeypatch.delenv("AUTH_REGISTRATION", raising=False)
    accounts._failures.clear()
    app.dependency_overrides.clear()
    if not any(getattr(r, "path", "") == "/auth/register" for r in app.routes):
        app.include_router(accounts.router)  # main.py mounts it only when enabled at import
    with TestClient(app) as c:
        yield c


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_the_first_account_is_the_admin_and_the_next_needs_an_invite(local):
    assert local.get("/auth/config").json() == {"provider": "builtin", "registration": "first"}
    made = local.post(
        "/auth/register",
        json={"email": "Ada@Example.com", "password": "correct horse", "name": "Ada"},
    )
    assert made.status_code == 201
    token = made.json()["token"]
    who = local.get("/me", headers=bearer(token)).json()
    assert who["email"] == "ada@example.com" and who["name"] == "Ada" and who["role"] == "Admin"
    assert who["id"].startswith("local_") and auth.SAFE_SUB.match(who["id"])
    assert local.get("/auth/config").json()["registration"] == "invite"

    # the second person: not without an invite, not with a spent one; the same address twice, never
    again = local.post(
        "/auth/register", json={"email": "bob@example.com", "password": "correct horse"}
    )
    assert again.status_code == 403 and "invitation" in again.json()["detail"]
    local.get("/teams", headers=bearer(token))  # Ada's personal team exists now
    team = local.post("/teams", json={"name": "Acme"}, headers=bearer(token)).json()
    link = local.post(
        f"/teams/{team['id']}/invites", json={"role": "contributor"}, headers=bearer(token)
    ).json()
    bob = local.post(
        "/auth/register",
        json={"email": "bob@example.com", "password": "correct horse", "invite": link["token"]},
    )
    assert bob.status_code == 201
    assert local.get("/me", headers=bearer(bob.json()["token"])).json()["role"] == "Member"
    dup = local.post(
        "/auth/register",
        json={"email": "bob@example.com", "password": "correct horse", "invite": link["token"]},
    )
    assert dup.status_code == 409


def test_registration_can_be_left_open(local, monkeypatch):
    local.post("/auth/register", json={"email": "ada@example.com", "password": "correct horse"})
    monkeypatch.setenv("AUTH_REGISTRATION", "open")
    assert local.get("/auth/config").json()["registration"] == "open"
    assert (
        local.post(
            "/auth/register", json={"email": "bob@example.com", "password": "correct horse"}
        ).status_code
        == 201
    )


def test_signing_in_checks_the_password_and_backs_off_after_five_misses(local):
    local.post("/auth/register", json={"email": "ada@example.com", "password": "correct horse"})
    ok = local.post("/auth/login", json={"email": "ADA@example.com", "password": "correct horse"})
    assert (
        ok.status_code == 200
        and local.get("/me", headers=bearer(ok.json()["token"])).status_code == 200
    )
    for _ in range(5):
        assert (
            local.post(
                "/auth/login", json={"email": "ada@example.com", "password": "nope"}
            ).status_code
            == 401
        )
    locked = local.post(
        "/auth/login", json={"email": "ada@example.com", "password": "correct horse"}
    )
    assert locked.status_code == 429
    assert (
        local.post("/auth/login", json={"email": "nobody@example.com", "password": "x"}).status_code
        == 401
    )


def test_a_weak_password_or_a_bad_address_is_refused(local):
    assert (
        local.post(
            "/auth/register", json={"email": "not-an-address", "password": "correct horse"}
        ).status_code
        == 422
    )
    assert (
        local.post(
            "/auth/register", json={"email": "ada@example.com", "password": "short"}
        ).status_code
        == 422
    )


def test_a_session_token_is_this_servers_and_expires(local, monkeypatch):
    token = local.post(
        "/auth/register", json={"email": "ada@example.com", "password": "correct horse"}
    ).json()["token"]
    sub = accounts.claims(token)["sub"]
    forged = jwt.encode(
        {"iss": "mindkeep", "sub": sub, "exp": int(time.time()) + 60},
        "another secret",
        algorithm="HS256",
    )
    assert local.get("/me", headers=bearer(forged)).status_code == 401
    expired = jwt.encode(
        {"iss": "mindkeep", "sub": sub, "exp": int(time.time()) - 1},
        "a-secret-long-enough-to-sign-with",
        algorithm="HS256",
    )
    assert local.get("/me", headers=bearer(expired)).status_code == 401
    # a device token still tells itself apart: one dot, not two
    device = local.post("/devices", json={"name": "laptop"}, headers=bearer(token)).json()["token"]
    assert (
        device.count(".") == 1
        and local.get("/me", headers=bearer(device)).json()["role"] == "Device"
    )


def test_a_password_can_be_changed_with_the_current_one(local):
    token = local.post(
        "/auth/register", json={"email": "ada@example.com", "password": "correct horse"}
    ).json()["token"]
    wrong = local.put(
        "/auth/password", json={"current": "nope", "new": "battery staple"}, headers=bearer(token)
    )
    assert wrong.status_code == 401
    ok = local.put(
        "/auth/password",
        json={"current": "correct horse", "new": "battery staple"},
        headers=bearer(token),
    )
    assert ok.status_code == 200
    assert (
        local.post(
            "/auth/login", json={"email": "ada@example.com", "password": "correct horse"}
        ).status_code
        == 401
    )
    assert (
        local.post(
            "/auth/login", json={"email": "ada@example.com", "password": "battery staple"}
        ).status_code
        == 200
    )


def test_password_hashes_carry_their_parameters():
    stored = accounts.hash_password("correct horse")
    kind, n, r, p, salt, digest = stored.split("$")
    assert kind == "scrypt" and int(n) == accounts.SCRYPT[0] and len(salt) == 32
    assert accounts.check_password("correct horse", stored)
    assert not accounts.check_password("correct horsE", stored)
    assert not accounts.check_password("x", "garbage")
    assert accounts.hash_password("correct horse") != stored  # a fresh salt every time
