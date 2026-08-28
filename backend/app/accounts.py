"""The built-in identity provider: an e-mail, a password, a session token. Nothing else.

Mindkeep works out of the box with this and nothing but a host. `AUTH_PROVIDER=builtin`
turns it on, explicitly — a deploy that lost its `AUTH_ISSUER` must fail closed, not fall
into a mode where anyone can register. Sessions are HS256 tokens signed with
`AUTH_SECRET`, verified here and only here: the RS256 path in auth.py keeps its own
algorithm pin, and one decode call never accepts both.

Kept to the bone on purpose: registration is open, there is no reset, no verification,
no roles, no throttle. Logout is the browser forgetting the token; revoking every session
is rotating `AUTH_SECRET`. Anything more is a decision for when someone needs it.
"""

import hashlib
import hmac
import os
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import jwt
from fastapi import APIRouter, Body, HTTPException
from sqlalchemy import select

from app.db import Account, now, session

router = APIRouter(prefix="/auth")

ISSUER = "mindkeep"
TTL = timedelta(days=30)
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 8

# scrypt, with the parameters kept in the stored string so they can be raised later
# without invalidating every account. maxmem is explicit: the default cap would make a
# stronger n raise at login rather than take longer.
SCRYPT = (2**14, 8, 1)
MAXMEM = 64 * 1024 * 1024


def enabled() -> bool:
    return os.environ.get("AUTH_PROVIDER", "oidc") == "builtin"


def secret() -> str:
    found = os.environ.get("AUTH_SECRET", "")
    if len(found) < 16:
        raise HTTPException(500, "AUTH_SECRET is not set")  # never sign with nothing
    return found


def hash_password(password: str) -> str:
    n, r, p = SCRYPT
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=MAXMEM)
    return f"scrypt${n}${r}${p}${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt, digest = stored.split("$")
        if kind != "scrypt":
            return False
        got = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), maxmem=MAXMEM
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got.hex(), digest)


def token_for(account: Account) -> str:
    issued = datetime.now(UTC)
    return jwt.encode(
        {
            "iss": ISSUER,
            "sub": account.sub,
            "email": account.email,
            "name": account.name,
            "iat": int(issued.timestamp()),
            "exp": int((issued + TTL).timestamp()),
        },
        secret(),
        algorithm="HS256",
    )


def claims(token: str) -> dict[str, Any]:
    """A session token's claims, or 401. HS256 against AUTH_SECRET, this issuer, unexpired."""
    try:
        found: dict[str, Any] = jwt.decode(
            token,
            secret(),
            algorithms=["HS256"],
            issuer=ISSUER,
            options={"require": ["exp", "sub", "iss"]},
        )
    except jwt.PyJWTError:
        raise HTTPException(401, "invalid token") from None
    return found


@router.post("/register", status_code=201)
def register(
    email: Annotated[str, Body()],
    password: Annotated[str, Body()],
    name: Annotated[str, Body()] = "",
) -> dict[str, str]:
    email = email.strip().lower()
    if not EMAIL.match(email):
        raise HTTPException(422, "that is not an e-mail address")
    if len(password) < MIN_PASSWORD:
        raise HTTPException(422, f"a password is at least {MIN_PASSWORD} characters")
    account = Account(
        sub="local_" + secrets.token_hex(12),
        email=email,
        name=name.strip()[:80] or email.split("@")[0],
        password=hash_password(password),
        created_at=now(),
    )
    with session() as s:
        if s.scalar(select(Account).where(Account.email == email)) is not None:
            raise HTTPException(409, "an account with that e-mail already exists")
        s.add(account)
        s.commit()
        s.refresh(account)
        s.expunge(account)
    return {"token": token_for(account)}


@router.post("/login")
def login(email: Annotated[str, Body()], password: Annotated[str, Body()]) -> dict[str, str]:
    with session() as s:
        account = s.scalar(select(Account).where(Account.email == email.strip().lower()))
        if account is None or not check_password(password, account.password):
            raise HTTPException(401, "wrong e-mail or password")
        s.expunge(account)
    return {"token": token_for(account)}
