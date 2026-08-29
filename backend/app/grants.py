"""Grants: a person's standing with a provider — a token they paste, or a sign-in with the
provider (OAuth 2).

A grant is the person's, not a bundle's: made once in their account settings, usable by
any connection they set up, in any team. A connection made with it keeps syncing with it
into a bundle other people read — that is the point, and it is the person's choice: they
put their credential to work for that bundle. Deleting a grant is always allowed (the
credential is theirs to revoke); connections that used it keep their rows and report
"the sign-in this connection used is gone" at their next sync, rather than lose a
bundle's files because one person left.

**The sign-in.** For an `oauth2` kind the connector declares the provider's endpoints
(`OAuth`); this module runs the dance, the same for every provider: `start` builds the
authorize URL (PKCE, and a state that is a signed note of who asked and for what, good
for ten minutes); the provider sends the browser to `callback`, which trades the code for
tokens, asks the connector what to call the grant, keeps the tokens sealed, and sends the
browser back to the app. Before every use, `fresh` renews an access token that is about
to expire; a refresh the provider refuses (`invalid_grant`: the person revoked it) marks
the grant, and the connections that use it say so. The app's own client id and secret
come from `<PROVIDER>_CLIENT_ID` / `<PROVIDER>_CLIENT_SECRET`; a kind whose provider is
not configured is listed but not offered.

User-facing, a grant is a "sign-in". In code it is a grant, because `accounts.py` is
already signing people in to Mindkeep itself.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets as random
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func, select

from app import vault
from app.auth import CurrentUser
from app.connectors import Connector, ConnectorError, registry
from app.connectors.base import Grant as GrantOf
from app.connectors.base import OAuth
from app.db import Connection, Grant, session

log = logging.getLogger(__name__)

router = APIRouter()

STATE_TTL = timedelta(minutes=10)
REFRESH_AHEAD = timedelta(seconds=90)  # renew an access token this close to expiring
EXPIRED = "the sign-in expired or was revoked — sign in again"


class NewGrant(BaseModel):
    kind: str
    secrets: dict[str, str]


# --- rows ------------------------------------------------------------------------------


def as_dict(row: Grant, uses: int) -> dict[str, object]:
    return {
        "id": row.id,
        "kind": row.kind,
        "label": row.label,
        "created_at": row.created_at.isoformat(),
        "error": row.error,
        "uses": uses,
    }


def mine(sub: str) -> list[Grant]:
    with session() as s:
        rows = s.scalars(select(Grant).where(Grant.sub == sub).order_by(Grant.created_at)).all()
        for r in rows:
            s.expunge(r)
        return list(rows)


def uses_of(s: Any, grant_ids: list[str]) -> dict[str, int]:
    if not grant_ids:
        return {}
    rows = s.execute(
        select(Connection.grant_id, func.count())
        .where(Connection.grant_id.in_(grant_ids))
        .group_by(Connection.grant_id)
    ).all()
    return {str(g): int(n) for g, n in rows}


def keep(sub: str, connector: Connector, secrets: dict[str, str], label: str) -> Grant:
    expires = secrets.pop("expires_at", "")
    row = Grant(
        id=random.token_hex(16),
        sub=sub,
        kind=connector.kind,
        label=label,
        secret=vault.seal_all(secrets),
        created_at=datetime.now(UTC),
        expires_at=datetime.fromisoformat(expires) if expires else None,
    )
    with session() as s:
        s.add(row)
        s.commit()
        s.refresh(row)
        s.expunge(row)
    return row


# --- the provider's app -----------------------------------------------------------------


def client(oauth: OAuth) -> tuple[str, str]:
    """The app's own id and secret with this provider, from the environment."""
    prefix = oauth.provider.upper()
    return os.environ.get(f"{prefix}_CLIENT_ID", ""), os.environ.get(f"{prefix}_CLIENT_SECRET", "")


def configured(connector: Connector) -> bool:
    """Whether a sign-in with this connector's provider can be run here."""
    return connector.oauth is not None and all(client(connector.oauth))


def redirect_uri(kind: str) -> str:
    """Where the provider sends the browser back: this API, at its public address — the
    site's, with /api in front, unless API_PUBLIC_URL says otherwise."""
    base = os.environ.get("API_PUBLIC_URL") or os.environ.get("WEB_URL", "").rstrip("/") + "/api"
    return f"{base.rstrip('/')}/grants/oauth/{kind}/callback"


def exchange(oauth: OAuth, data: dict[str, str]) -> dict[str, Any]:
    """One call to the provider's token endpoint — a code for tokens, or a refresh."""
    client_id, client_secret = client(oauth)
    try:
        got = httpx.post(
            oauth.token_url,
            data={**data, "client_id": client_id, "client_secret": client_secret},
            headers={"Accept": "application/json"},
            timeout=30,
        )
    except httpx.HTTPError as e:
        raise ConnectorError(f"could not reach {oauth.provider}: {e}") from e
    body: dict[str, Any] = got.json() if got.content else {}
    if got.status_code >= 400 or "access_token" not in body:
        raise ConnectorError(
            str(body.get("error_description") or body.get("error") or got.status_code)
        )
    return body


def tokens_of(body: dict[str, Any], keep_refresh: str = "") -> dict[str, str]:
    """The secrets a token response gives, in the shape a grant holds."""
    lifetime = int(body.get("expires_in", 3600))
    return {
        "access_token": str(body["access_token"]),
        "refresh_token": str(body.get("refresh_token") or keep_refresh),
        "expires_at": (datetime.now(UTC) + timedelta(seconds=lifetime)).isoformat(),
    }


# --- state: a signed note of who asked, for what --------------------------------------------


def _key() -> bytes:
    secret = os.environ.get("DEVICE_SECRET", "")
    if not secret:
        raise HTTPException(500, "DEVICE_SECRET is not set")
    return hashlib.sha256(f"oauth-state:{secret}".encode()).digest()


def make_state(payload: dict[str, str]) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    mac = hmac.new(_key(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{mac}"


def read_state(state: str) -> dict[str, str]:
    body, _, mac = state.partition(".")
    if not hmac.compare_digest(
        hmac.new(_key(), body.encode(), hashlib.sha256).hexdigest()[:32], mac
    ):
        raise HTTPException(400, "the sign-in did not start here")
    payload: dict[str, str] = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    if datetime.fromisoformat(payload["exp"]) < datetime.now(UTC):
        raise HTTPException(400, "the sign-in took too long — start again")
    return payload


# --- what a connector receives -----------------------------------------------------------------


def unpack(row: Grant | None) -> GrantOf | None:
    """The grant as a connector receives it: secrets in the clear, for this call only."""
    if row is None:
        return None
    return GrantOf(kind=row.kind, label=row.label, secrets=vault.unseal_all(row.secret))


def fresh(s: Any, row: Grant | None) -> GrantOf | None:
    """The grant as a connector receives it, its access token renewed when about to
    expire — the one road for a sync and for a check alike. A provider that refuses the
    refresh has had the sign-in revoked: the row says so, and so does the connection."""
    if row is None:
        return None
    connector = registry().get(row.kind)
    oauth = connector.oauth if connector else None
    if oauth is None or row.expires_at is None:
        return unpack(row)
    expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=UTC)
    if expires - datetime.now(UTC) > REFRESH_AHEAD:
        return unpack(row)
    secrets = vault.unseal_all(row.secret)
    try:
        body = exchange(
            oauth,
            {"grant_type": "refresh_token", "refresh_token": secrets.get("refresh_token", "")},
        )
    except ConnectorError as e:
        row.error = EXPIRED if "invalid_grant" in str(e) or "revoked" in str(e) else str(e)
        s.commit()
        raise ConnectorError(row.error) from e
    renewed = tokens_of(body, keep_refresh=secrets.get("refresh_token", ""))
    row.expires_at = datetime.fromisoformat(renewed.pop("expires_at"))
    row.secret = vault.seal_all({**secrets, **renewed})
    row.error = ""
    s.commit()
    return unpack(row)


# --- routes -------------------------------------------------------------------------------------


@router.get("/grants")
def list_grants(user: CurrentUser) -> list[dict[str, object]]:
    """Your sign-ins, with how many connections use each."""
    rows = mine(user)
    with session() as s:
        uses = uses_of(s, [r.id for r in rows])
    return [as_dict(r, uses.get(r.id, 0)) for r in rows]


@router.post("/grants", status_code=201)
def add_grant(user: CurrentUser, new: NewGrant) -> dict[str, object]:
    """A token, tried before it is kept: the connector says what to call it."""
    connector = registry().get(new.kind)
    if connector is None:
        raise HTTPException(404, f"no connector of kind {new.kind}")
    if connector.auth != "token":
        raise HTTPException(
            400,
            "no sign-in needed"
            if connector.auth == "none"
            else f"{connector.title} signs in through the provider — use the sign-in button",
        )
    given = {f.name: new.secrets.get(f.name, "").strip() for f in connector.grant_fields}
    for f in connector.grant_fields:
        if f.required and not given[f.name]:
            raise HTTPException(400, f"{f.label} is required")
    try:
        label = connector.check_grant(given)
    except ConnectorError as e:
        raise HTTPException(400, str(e)) from e
    return as_dict(keep(user, connector, given, label), 0)


@router.get("/grants/oauth/{kind}/start")
def start_oauth(user: CurrentUser, kind: str) -> dict[str, str]:
    """The address to send the browser to: the provider's consent page. The app
    navigates there; the provider brings the browser back to `callback`."""
    connector = registry().get(kind)
    if connector is None or connector.oauth is None:
        raise HTTPException(404, f"no connector of kind {kind} signs in through a provider")
    if not configured(connector):
        raise HTTPException(400, f"{connector.title} is not configured on this server")
    oauth = connector.oauth
    verifier = random.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    state = make_state(
        {
            "sub": user,
            "kind": kind,
            "verifier": verifier,
            "exp": (datetime.now(UTC) + STATE_TTL).isoformat(),
        }
    )
    query = {
        "client_id": client(oauth)[0],
        "redirect_uri": redirect_uri(kind),
        "response_type": "code",
        "scope": " ".join(oauth.scopes),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        **dict(oauth.params),
    }
    return {"url": f"{oauth.authorize_url}?{urlencode(query)}"}


@router.get("/grants/oauth/{kind}/callback")
def finish_oauth(kind: str, state: str = "", code: str = "", error: str = "") -> RedirectResponse:
    """Where the provider sends the browser. No bearer token here — the state is the
    proof of who asked. Ends back in the app, with what happened in the address."""
    web = os.environ.get("WEB_URL", "").rstrip("/") or "/"

    def back(**what: str) -> RedirectResponse:
        return RedirectResponse(f"{web}/?{urlencode(what)}", status_code=302)

    connector = registry().get(kind)
    if connector is None or connector.oauth is None:
        return back(connect_error=f"no connector of kind {kind}")
    if error:
        return back(connect_error=f"{connector.title}: {error}")
    try:
        asked = read_state(state)
    except HTTPException as e:
        return back(connect_error=str(e.detail))
    if asked.get("kind") != kind:
        return back(connect_error="the sign-in did not start here")
    try:
        body = exchange(
            connector.oauth,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri(kind),
                "code_verifier": asked["verifier"],
            },
        )
        secrets = tokens_of(body)
        label = connector.check_grant(secrets)
    except ConnectorError as e:
        return back(connect_error=f"{connector.title}: {e}")
    keep(asked["sub"], connector, secrets, label)
    return back(connected=kind)


@router.delete("/grants/{grant_id}")
def remove_grant(user: CurrentUser, grant_id: str) -> dict[str, object]:
    """Gone at once. Connections that used it stay, and say so at their next sync."""
    with session() as s:
        row = s.get(Grant, grant_id)
        if row is None or row.sub != user:
            raise HTTPException(404, "no such sign-in")
        uses = uses_of(s, [grant_id]).get(grant_id, 0)
        s.delete(row)
        s.commit()
    return {"deleted": grant_id, "orphaned": uses}
