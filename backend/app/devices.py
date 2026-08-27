"""Machines signed in with a long-lived token: the desktop client, the tray app.

A device token is `<device id>.<digest>`. The digest proves the id was issued here; the
row is what makes the token revocable — delete the row and that one machine is out,
nobody else's token is touched. Before this there was one token per person, and
revoking it meant rotating the server secret, which revoked everyone.

Helpers only, no routes: auth.py needs `holder()` to answer a request, and the routes
in main.py need the rest, so this module imports neither.
"""

import secrets
from datetime import timedelta

from sqlalchemy import select

from app.db import Device, now, session

# last_seen is for a person deciding which of their devices to revoke; a minute's
# precision is plenty, and a write per request is not
SEEN_EVERY = timedelta(minutes=1)


def create(sub: str, name: str) -> Device:
    device = Device(
        id=secrets.token_hex(16), sub=sub, name=name.strip()[:80] or "device", created_at=now()
    )
    with session() as s:
        s.add(device)
        s.commit()
        s.refresh(device)
        s.expunge(device)
    return device


def holder(device_id: str) -> str | None:
    """Whose device this is, or None once it has been revoked. Notes that it was seen."""
    with session() as s:
        device = s.get(Device, device_id)
        if device is None:
            return None
        moment = now()
        if device.last_seen is None or moment - device.last_seen > SEEN_EVERY:
            device.last_seen = moment
            s.commit()
        return device.sub


def mine(sub: str) -> list[Device]:
    with session() as s:
        rows = list(s.scalars(select(Device).where(Device.sub == sub).order_by(Device.created_at)))
        for row in rows:
            s.expunge(row)
        return rows


def forget(sub: str, device_id: str) -> bool:
    """Revoke one of *this person's* devices. False when it is not theirs, or not there."""
    with session() as s:
        device = s.get(Device, device_id)
        if device is None or device.sub != sub:
            return False
        s.delete(device)
        s.commit()
        return True


def as_dict(device: Device) -> dict[str, object]:
    return {
        "id": device.id,
        "name": device.name,
        "created_at": device.created_at.isoformat(),
        "last_seen": device.last_seen.isoformat() if device.last_seen else None,
    }
