"""Kinde Management API client (OAuth2 client_credentials).

Reads are best-effort: a Kinde hiccup degrades an identity to the raw `sub` rather
than failing the request. Blank M2M credentials disable the client entirely, which
keeps local dev working without a Kinde tenant.
"""

import logging
import os
import threading
import time

import httpx

from app.auth import issuer

log = logging.getLogger(__name__)

EXPIRY_SAFETY_SECONDS = 30

_lock = threading.Lock()
_token = ""
_valid_until = 0.0


def enabled() -> bool:
    return bool(os.environ.get("KINDE_M2M_CLIENT_ID") and os.environ.get("KINDE_M2M_CLIENT_SECRET"))


def _auth() -> dict[str, str]:
    """One lock around check-and-refresh, so concurrent callers refresh at most once."""
    global _token, _valid_until
    with _lock:
        if time.monotonic() >= _valid_until:
            # For custom-domain tenants the audience stays the canonical *.kinde.com/api.
            response = httpx.post(
                f"{issuer()}/oauth2/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": os.environ["KINDE_M2M_CLIENT_ID"],
                    "client_secret": os.environ["KINDE_M2M_CLIENT_SECRET"],
                    "audience": (
                        os.environ.get("KINDE_MANAGEMENT_API_AUDIENCE") or f"{issuer()}/api"
                    ),
                },
                timeout=10,
            )
            response.raise_for_status()
            body = response.json()
            if not body.get("access_token"):
                raise RuntimeError("Kinde token endpoint returned an empty access token")
            _token = body["access_token"]
            lifetime = float(body.get("expires_in", 0)) - EXPIRY_SAFETY_SECONDS
            _valid_until = time.monotonic() + lifetime
    return {"Authorization": f"Bearer {_token}"}


def profile(sub: str) -> dict[str, str]:
    """Name, email and picture, straight from Kinde. Empty strings where it knows nothing.

    Kinde is the store of record for who someone is — Mindstash keeps no user table to
    drift out of step with it, so this is composed per read rather than mirrored.
    """
    blank = {"first_name": "", "last_name": "", "email": "", "picture": ""}
    if not enabled():
        return blank
    try:
        response = httpx.get(
            f"{issuer()}/api/v1/user", params={"id": sub}, headers=_auth(), timeout=10
        )
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, RuntimeError, KeyError) as e:
        log.warning("Could not resolve Kinde user %s: %s", sub, e)
        return blank

    # Kinde's docs are inconsistent about the field convention — accept both.
    return {
        "first_name": data.get("first_name") or data.get("given_name") or "",
        "last_name": data.get("last_name") or data.get("family_name") or "",
        "email": data.get("preferred_email") or data.get("email") or "",
        "picture": data.get("picture") or "",
    }


def who_is(sub: str) -> str:
    """A person's identity for the wiki's `verified` field. Falls back to the raw sub."""
    who = profile(sub)
    name = " ".join(n for n in (who["first_name"], who["last_name"]) if n)
    return who["email"] or name or sub
