"""A connection's config at rest: the secret fields encrypted, the rest plain.

Fernet, with a key derived from DEVICE_SECRET — the one secret the server already has
to have. A leaked database row gives up the address of a Notion workspace, not the token
that reads it. Only fields the connector marks `secret` are sealed; a browser never gets
those back, redacted or not — it gets a marker that says one is set.
"""

import json
import os
from base64 import urlsafe_b64encode
from hashlib import sha256

from cryptography.fernet import Fernet, InvalidToken

from app.connectors.base import Connector

# what a secret reads as on the way out, and what means "leave it as it is" on the way in
REDACTED = "••••••••"


def _fernet() -> Fernet:
    secret = os.environ.get("DEVICE_SECRET", "")
    if not secret:
        raise RuntimeError("DEVICE_SECRET is not set")  # never derive from an empty secret
    return Fernet(urlsafe_b64encode(sha256(f"connections:{secret}".encode()).digest()))


def seal(config: dict[str, str], connector: Connector) -> str:
    """The JSON stored on the row."""
    out = {}
    for f in connector.fields:
        value = config.get(f.name, "")
        out[f.name] = _fernet().encrypt(value.encode()).decode() if f.secret and value else value
    return json.dumps(out)


def unseal(stored: str, connector: Connector) -> dict[str, str]:
    """The config as the connector takes it."""
    raw = json.loads(stored or "{}")
    out = {}
    for f in connector.fields:
        value = raw.get(f.name, "")
        if f.secret and value:
            try:
                value = _fernet().decrypt(value.encode()).decode()
            except InvalidToken:  # a DEVICE_SECRET that changed: the secret is unreadable
                value = ""
        out[f.name] = value
    return out


def redact(stored: str, connector: Connector) -> dict[str, str]:
    """The config as a browser sees it: secrets replaced by a marker when set."""
    raw = json.loads(stored or "{}")
    return {
        f.name: (REDACTED if raw.get(f.name) else "") if f.secret else raw.get(f.name, "")
        for f in connector.fields
    }


def merge(given: dict[str, str], stored: str, connector: Connector) -> dict[str, str]:
    """A form sent back with the marker still in a secret field means "keep what is set"."""
    kept = unseal(stored, connector)
    return {
        f.name: kept[f.name]
        if f.secret and given.get(f.name) == REDACTED
        else given.get(f.name, "")
        for f in connector.fields
    }
