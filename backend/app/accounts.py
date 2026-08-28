"""The built-in identity provider: an email, a password, a session token — nothing else.

Mindkeep works out of the box with this, and with nothing but a host. `AUTH_PROVIDER=builtin`
turns it on, explicitly — a deploy that lost its `AUTH_ISSUER` must fail closed, not fall
into a mode where anyone can register. Sessions are HS256 tokens signed with
`AUTH_SECRET`, verified here and only here: the RS256 path in auth.py keeps its own
algorithm pin, and one decode call never accepts both.

What it is not, by decision: no password reset and no e-mail verification (both need a
mail sender, which is another dependency); logout is the browser forgetting the token;
revoking every session is rotating `AUTH_SECRET`. Registration is open until the first
account exists — the person installing it — and then follows `AUTH_REGISTRATION`:
`invite` (default: an invite link from a team) or `open`.
"""

import hashlib
import hmac
import logging
import os
import re
import secrets
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import jwt
from fastapi import APIRouter, Body, HTTPException
from sqlalchemy import func, select

from app.db import Account, Invite, now, session
from app.teams import _open

log = logging.getLogger(__name__)
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


def registration() -> str:
    return "open" if os.environ.get("AUTH_REGISTRATION", "invite") == "open" else "invite"


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
            "roles": ["admin"] if account.admin else [],
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


# --- who may register --------------------------------------------------------------------
_first = threading.Lock()  # the first account is the admin; two at once must not both be


def _count() -> int:
    with session() as s:
        return int(s.scalar(select(func.count()).select_from(Account)) or 0)


def _invited(token: str) -> bool:
    if not token:
        return False
    with session() as s:
        return _open(s.scalar(select(Invite).where(Invite.token == token)))


# --- a few wrong passwords, then a wait ------------------------------------------------------
WINDOW, STRIKES = 300, 5
_failures: dict[str, list[float]] = {}
_failures_lock = threading.Lock()


def _strikes(email: str) -> int:
    with _failures_lock:
        recent = [t for t in _failures.get(email, []) if time.time() - t < WINDOW]
        _failures[email] = recent
        return len(recent)


def _strike(email: str) -> None:
    with _failures_lock:
        _failures.setdefault(email, []).append(time.time())


def _clear(email: str) -> None:
    with _failures_lock:
        _failures.pop(email, None)


# --- routes -------------------------------------------------------------------------------------
def config() -> dict[str, str]:
    """What the sign-in page needs to know. `registration` is `first` while nobody has an
    account yet — the installer is about to make theirs — then the policy."""
    if not enabled():
        return {"provider": "oidc"}
    return {
        "provider": "builtin",
        "registration": "first" if _count() == 0 else registration(),
    }


@router.post("/register", status_code=201)
def register(
    email: Annotated[str, Body()],
    password: Annotated[str, Body()],
    name: Annotated[str, Body()] = "",
    invite: Annotated[str, Body()] = "",
) -> dict[str, str]:
    email = email.strip().lower()
    if not EMAIL.match(email):
        raise HTTPException(422, "that is not an e-mail address")
    if len(password) < MIN_PASSWORD:
        raise HTTPException(422, f"a password is at least {MIN_PASSWORD} characters")
    with _first:
        first = _count() == 0
        if not first and registration() != "open" and not _invited(invite):
            raise HTTPException(403, "registration is by invitation — ask a team for a link")
        account = Account(
            sub="local_" + secrets.token_hex(12),
            email=email,
            name=name.strip()[:80] or email.split("@")[0],
            password=hash_password(password),
            admin=first,
            created_at=now(),
        )
        with session() as s:
            if s.scalar(select(Account).where(Account.email == email)) is not None:
                raise HTTPException(409, "an account with that e-mail already exists")
            s.add(account)
            s.commit()
            s.refresh(account)
            s.expunge(account)
    log.info("registered %s%s", account.sub, " (admin)" if first else "")
    return {"token": token_for(account)}


@router.post("/login")
def login(email: Annotated[str, Body()], password: Annotated[str, Body()]) -> dict[str, str]:
    email = email.strip().lower()
    if _strikes(email) >= STRIKES:
        raise HTTPException(429, "too many attempts — wait a few minutes")
    with session() as s:
        account = s.scalar(select(Account).where(Account.email == email))
        if account is None or not check_password(password, account.password):
            _strike(email)
            raise HTTPException(401, "wrong e-mail or password")
        account.last_seen = now()
        s.commit()
        s.refresh(account)
        s.expunge(account)
    _clear(email)
    return {"token": token_for(account)}


def change_password(sub: str, current: str, new: str) -> None:
    if len(new) < MIN_PASSWORD:
        raise HTTPException(422, f"a password is at least {MIN_PASSWORD} characters")
    with session() as s:
        account = s.get(Account, sub)
        if account is None or not check_password(current, account.password):
            raise HTTPException(401, "the current password is wrong")
        account.password = hash_password(new)
        s.commit()
