import hmac
import os
import re
from functools import lru_cache
from hashlib import sha256
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer = HTTPBearer()

# A Kinde `sub` becomes a directory name, so it must survive being one.
SAFE_SUB = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def issuer() -> str:
    """The Kinde tenant root, pinned as the JWT `iss`. Scheme optional, slash tolerated."""
    return "https://" + os.environ["KINDE_ISSUER"].removeprefix("https://").rstrip("/")


@lru_cache
def _jwks() -> jwt.PyJWKClient:
    return jwt.PyJWKClient(f"{issuer()}/.well-known/jwks")


# ponytail: one derived token per user, nothing stored. A scheduled sync cannot do an
# interactive login, so it needs a credential that outlives a browser session.
# Ceiling: revoking one person means rotating DEVICE_SECRET, which revokes everyone.
def device_token(sub: str) -> str:
    secret = os.environ.get("DEVICE_SECRET", "")
    if not secret:
        raise HTTPException(500, "DEVICE_SECRET is not set")  # never derive from an empty secret
    return f"{sub}.{hmac.new(secret.encode(), sub.encode(), sha256).hexdigest()}"


def _holder_of(token: str) -> str | None:
    """The user a device token belongs to, or None. The token names its own owner."""
    sub, _, digest = token.rpartition(".")
    if not SAFE_SUB.match(sub):
        return None
    return sub if hmac.compare_digest(device_token(sub).rpartition(".")[2], digest) else None


def _from_kinde(token: str) -> str:
    """Signature, issuer and expiry — nothing else.

    House convention (see Futuros' app/core/security.py): Kinde access tokens carry no
    meaningful audience, so verifying one is theatre. The issuer pin is what matters.
    """
    try:
        key = _jwks().get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=issuer(),
            options={"verify_aud": False, "require": ["exp", "sub", "iss"]},
        )
    except jwt.PyJWTError:
        raise HTTPException(401, "invalid token") from None

    sub = claims.get("sub", "")
    if not SAFE_SUB.match(sub):
        raise HTTPException(401, "unusable subject")
    return sub


# ponytail: sync def, so FastAPI runs the (network-touching, cached) JWKS fetch in a threadpool.
def current_user(cred: Annotated[HTTPAuthorizationCredentials, Depends(bearer)]) -> str:
    """The browser sends a Kinde JWT; the desktop client sends a device token."""
    token = cred.credentials
    if token.count(".") == 1:  # a JWT has two dots, a device token has one
        holder = _holder_of(token)
        if holder is None:
            raise HTTPException(401, "invalid token")
        return holder
    return _from_kinde(token)


CurrentUser = Annotated[str, Depends(current_user)]


def current_role(cred: Annotated[HTTPAuthorizationCredentials, Depends(bearer)]) -> str:
    """The Kinde role to show on the profile, or "Member" when the token carries none.

    Display only — nothing is gated on it, because Mindstash has no privileged actions:
    a tenant can only ever reach their own directory. Kinde only puts `roles` in the
    token when the tenant defines them, and a device token has no claims at all, so a
    blank is the normal case rather than an error.
    """
    token = cred.credentials
    if token.count(".") == 1:
        return "Device"
    try:
        key = _jwks().get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=issuer(),
            options={"verify_aud": False, "require": ["exp", "sub", "iss"]},
        )
    except jwt.PyJWTError:
        return "Member"
    roles = claims.get("roles")
    names = [r.get("name") or r.get("key") for r in roles if isinstance(r, dict)] if roles else []
    return ", ".join(n.title() for n in names if n) or "Member"


CurrentRole = Annotated[str, Depends(current_role)]


if __name__ == "__main__":  # python -m app.auth <sub> — issue the first token by hand
    import sys

    if len(sys.argv) != 2 or not SAFE_SUB.match(sys.argv[1]):
        sys.exit("usage: python -m app.auth <user-id>   (letters, digits, _ and - only)")
    if not os.environ.get("DEVICE_SECRET"):
        sys.exit("DEVICE_SECRET is not set")
    print(device_token(sys.argv[1]))
