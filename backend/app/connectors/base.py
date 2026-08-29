"""What a connector is: the contract a plugin implements.

A connector knows how to read one kind of third-party source — a web page, a Notion
workspace, a Drive folder — and hand back files. That is all it does. Where the files
land, what changed since last time, the commit, the ingest, the schedule, the secrets:
the plumbing (`app/connections.py`, `app/syncing.py`) does those, the same way for every
connector, so a plugin is the pull and nothing else.

A plugin is a subclass of `Connector` with a `kind`. Built-ins live in this package;
anyone else's is a Python package that names its class under the entry-point group
`mindkeep.connectors` (see `app/connectors/__init__.py`).
"""

from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal


class ConnectorError(Exception):
    """A pull or a check that could not be done — bad credentials, a source that is gone,
    an API that said no. The message is shown to the person; write it for them."""


@dataclass(frozen=True)
class Field:
    """One thing a person fills in to set a connection up. `secret` values are encrypted
    at rest and never sent back to the browser."""

    name: str
    label: str
    secret: bool = False
    help: str = ""
    required: bool = True


@dataclass(frozen=True)
class Item:
    """One file the source holds.

    `id` is the source's own stable identity for it — a page id, a message id, a URL —
    so a renamed file is a move rather than a delete and an add. `path` is where it goes
    under the connection's folder in raw/, folders allowed, made safe by the plumbing.
    """

    id: str
    path: str
    content: bytes


@dataclass
class Pull:
    """What a pull returned.

    Two shapes. `complete=True` (the default): `items` is everything the source holds
    now, and anything the connection wrote before that is not in it is gone — the
    simplest contract, right for sources that are cheap to list. `complete=False`:
    `items` is what changed since `cursor`, `removed` names the ids that went away, and
    the plumbing touches nothing else — for sources with a real change feed.

    `cursor` is the connector's own, opaque to the plumbing, handed back on the next pull.
    """

    items: list[Item]
    cursor: dict[str, Any] = field(default_factory=dict)
    complete: bool = True
    removed: list[str] = field(default_factory=list)


class Connector:
    """Subclass this. `kind` is the identity (lowercase, stable — it names the rows);
    `title` and `blurb` are what a person sees when choosing; `fields` is the form."""

    kind: ClassVar[str] = ""
    title: ClassVar[str] = ""
    blurb: ClassVar[str] = ""
    fields: ClassVar[tuple[Field, ...]] = ()
    # ponytail: `oauth2` is declared so a plugin can say what it needs, but the plumbing
    # does not do the dance yet — redirect route, app credentials, refresh. It comes with
    # the first connector that needs it. Until then such a kind is listed as unavailable.
    auth: ClassVar[Literal["none", "token", "oauth2"]] = "none"

    def check(self, config: dict[str, str]) -> None:
        """Try the credentials before a connection is saved. Raise ConnectorError with a
        sentence a person can act on; return quietly when all is well."""

    def pull(self, config: dict[str, str], cursor: dict[str, Any]) -> Pull:
        raise NotImplementedError
