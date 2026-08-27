"""Who is asking.

Two credentials, one bearer header. The browser sends the access token its identity
provider minted — any OIDC provider will do: Kinde, Clerk, Auth0, Keycloak, Zitadel,
Logto. What is checked is the standard set — RS256 signature against the issuer's
published keys, the issuer itself, expiry — and what is read is the standard claims:
`sub` for who, `email`/`given_name`/`family_name`/`picture` for the profile, and one
configurable claim for the role. The desktop client sends a device token instead,
derived here from the subject, because a scheduled sync cannot do an interactive login.

Mindstash keeps no user table. A person is their `sub`; everything else is read off the
token they present, so there is nothing to drift out of step with the provider.
"""

import hmac
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from typing import Annotated, Any

import httpx
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer = HTTPBearer()

# A subject rides inside the device token, split from its digest on the token's one dot
# — so no dots, and nothing that will not survive a URL or a log line. Kinde (kp_…),
# Clerk (user_…), Auth0 (auth0|…), Keycloak (a UUID) and Zitadel (digits) all pass.
SAFE_SUB = re.compile(r"^[A-Za-z0-9_@:|-]{1,128}$")

# Kinde's shape, which is also the default: [{"id": …, "key": "admin", "name": "Admin"}].
# A list of strings or a single string is accepted too, which covers most others.
ROLE_CLAIM = "roles"


def issuer() -> str:
    """The provider's issuer URL, pinned as the JWT `iss`. Scheme optional, slash tolerated."""
    return "https://" + os.environ["AUTH_ISSUER"].removeprefix("https://").rstrip("/")


@lru_cache
def _jwks() -> jwt.PyJWKClient:
    """The signing keys, found through OIDC discovery — or at AUTH_JWKS_URL for a provider
    that publishes them somewhere discovery does not say."""
    url = os.environ.get("AUTH_JWKS_URL")
    if not url:
        discovery = httpx.get(f"{issuer()}/.well-known/openid-configuration", timeout=10)
        discovery.raise_for_status()
        url = discovery.json()["jwks_uri"]
    return jwt.PyJWKClient(url)


def _claims(token: str) -> dict[str, Any]:
    """Signature, issuer, expiry — and the audience only when one is configured.

    Most providers put nothing meaningful in `aud` for a first-party single-page app;
    Kinde's access tokens carry none at all. Set AUTH_AUDIENCE when yours does.
    """
    audience = os.environ.get("AUTH_AUDIENCE")
    try:
        key = _jwks().get_signing_key_from_jwt(token).key
        claims: dict[str, Any] = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=issuer(),
            audience=audience,
            options={"verify_aud": bool(audience), "require": ["exp", "sub", "iss"]},
        )
    except (jwt.PyJWTError, httpx.HTTPError, KeyError):
        raise HTTPException(401, "invalid token") from None
    if not SAFE_SUB.match(str(claims.get("sub", ""))):
        raise HTTPException(401, "unusable subject")
    return claims


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


def _is_device(token: str) -> bool:
    return token.count(".") == 1  # a JWT has two dots, a device token has one


# ponytail: sync def, so FastAPI runs the (network-touching, cached) JWKS fetch in a threadpool.
def current_user(cred: Annotated[HTTPAuthorizationCredentials, Depends(bearer)]) -> str:
    """The browser sends the provider's JWT; the desktop client sends a device token."""
    token = cred.credentials
    if _is_device(token):
        holder = _holder_of(token)
        if holder is None:
            raise HTTPException(401, "invalid token")
        return holder
    return str(_claims(token)["sub"])


CurrentUser = Annotated[str, Depends(current_user)]


def role_from(claims: dict[str, Any]) -> str:
    """The role names in the configured claim, or "Member" when there are none.

    Read for display today and for gating actions later — which is why it comes from a
    verified token and never from the browser's say-so. A provider only puts roles in
    the token when the tenant defines them, so a blank is normal rather than an error.
    """
    found = claims.get(os.environ.get("AUTH_ROLE_CLAIM") or ROLE_CLAIM)
    names: list[str] = []
    for r in found if isinstance(found, list) else [found]:
        n = (r.get("name") or r.get("key")) if isinstance(r, dict) else r
        if isinstance(n, str) and n:
            names.append(n)
    return ", ".join(n.title() for n in names) or "Member"


def current_role(cred: Annotated[HTTPAuthorizationCredentials, Depends(bearer)]) -> str:
    token = cred.credentials
    if _is_device(token):
        return "Device"
    try:
        return role_from(_claims(token))
    except HTTPException:
        return "Member"


CurrentRole = Annotated[str, Depends(current_role)]


@dataclass(frozen=True)
class Profile:
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    picture: str = ""

    @property
    def name(self) -> str:
        return " ".join(n for n in (self.first_name, self.last_name) if n)


def profile_from(claims: dict[str, Any]) -> Profile:
    """The standard OIDC profile claims, blank where the token carries none.

    Access tokens do not always carry them — providers put the profile in the ID token
    by default and make adding it to the access token a setting. When it is missing the
    UI falls back to the ID token it already holds, and a `verified` stamp falls back to
    the subject; both are honest, neither is an error.
    """
    return Profile(
        first_name=str(claims.get("given_name") or claims.get("first_name") or ""),
        last_name=str(claims.get("family_name") or claims.get("last_name") or ""),
        email=str(claims.get("email") or ""),
        picture=str(claims.get("picture") or ""),
    )


def current_profile(cred: Annotated[HTTPAuthorizationCredentials, Depends(bearer)]) -> Profile:
    token = cred.credentials
    if _is_device(token):
        return Profile()
    try:
        return profile_from(_claims(token))
    except HTTPException:
        return Profile()


CurrentProfile = Annotated[Profile, Depends(current_profile)]


def who_is(user: CurrentUser, profile: CurrentProfile) -> str:
    """A person's identity for the wiki's `verified` field: email, else name, else sub."""
    return profile.email or profile.name or user


CurrentIdentity = Annotated[str, Depends(who_is)]


if __name__ == "__main__":  # python -m app.auth <sub> — issue the first token by hand
    import sys

    if len(sys.argv) != 2 or not SAFE_SUB.match(sys.argv[1]):
        sys.exit("usage: python -m app.auth <user-id>   (no dots or spaces)")
    if not os.environ.get("DEVICE_SECRET"):
        sys.exit("DEVICE_SECRET is not set")
    print(device_token(sys.argv[1]))
