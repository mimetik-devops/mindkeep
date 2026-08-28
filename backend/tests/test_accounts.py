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
    app.dependency_overrides.clear()
    if not any(getattr(r, "path", "") == "/auth/register" for r in app.routes):
        app.include_router(accounts.router)  # main.py mounts it only when enabled at import
    with TestClient(app) as c:
        yield c


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_register_then_sign_in(local):
    made = local.post(
        "/auth/register",
        json={"email": "Ada@Example.com", "password": "correct horse", "name": "Ada Lovelace"},
    )
    assert made.status_code == 201
    who = local.get("/me", headers=bearer(made.json()["token"])).json()
    assert who["email"] == "ada@example.com" and who["name"] == "Ada Lovelace"
    assert who["id"].startswith("local_") and auth.SAFE_SUB.match(who["id"])

    ok = local.post("/auth/login", json={"email": "ADA@example.com", "password": "correct horse"})
    assert ok.status_code == 200
    assert local.get("/me", headers=bearer(ok.json()["token"])).json()["email"] == "ada@example.com"
    wrong = local.post("/auth/login", json={"email": "ada@example.com", "password": "no"})
    assert wrong.status_code == 401
    nobody = local.post("/auth/login", json={"email": "nobody@example.com", "password": "x"})
    assert nobody.status_code == 401


def test_what_is_refused(local):
    bad = local.post(
        "/auth/register", json={"email": "not-an-address", "password": "correct horse"}
    )
    assert bad.status_code == 422
    weak = local.post("/auth/register", json={"email": "ada@example.com", "password": "short"})
    assert weak.status_code == 422
    good = {"email": "ada@example.com", "password": "correct horse"}
    assert local.post("/auth/register", json=good).status_code == 201
    assert local.post("/auth/register", json=good).status_code == 409


def test_a_session_token_is_this_servers_and_expires(local):
    made = local.post(
        "/auth/register", json={"email": "ada@example.com", "password": "correct horse"}
    )
    token = made.json()["token"]
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
    assert device.count(".") == 1
    assert local.get("/me", headers=bearer(device)).json()["role"] == "Device"


def test_password_hashes_carry_their_parameters():
    stored = accounts.hash_password("correct horse")
    kind, n, r, p, salt, digest = stored.split("$")
    assert kind == "scrypt" and int(n) == accounts.SCRYPT[0] and len(salt) == 32
    assert accounts.check_password("correct horse", stored)
    assert not accounts.check_password("correct horsE", stored)
    assert not accounts.check_password("x", "garbage")
    assert accounts.hash_password("correct horse") != stored  # a fresh salt every time
