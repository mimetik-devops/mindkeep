"""What a connector is: the contract a plugin implements.

A connector knows how to read one kind of third-party source — a website, a Notion
workspace, a Drive folder — and hand back files. That is all it does. Where the files
land, what changed since last time, the commit, the ingest, the schedule, the secrets:
the plumbing (`app/connections.py`, `app/syncing.py`, `app/grants.py`) does those, the
same way for every connector, so a plugin is the pull and nothing else.

Two things a person fills in, kept apart because they live apart:

- A **grant** is a person's standing with the provider — a token, or (to come) an OAuth
  sign-in. It is the person's, made once in their account settings, and any connection
  they set up may use it. `auth` says whether a connector needs one; `grant_fields` is
  the form for a token; `oauth` is the dance for a sign-in.
- A **connection**'s `fields` are the scope on one bundle: which addresses, which
  folders, which channels. A field may be `multiline`: a list, one per line, so one
  connection watches several things and a token is entered once.

A plugin is a subclass of `Connector` with a `kind`. Built-ins live in this package;
anyone else's is a Python package that names its class under the entry-point group
`mindkeep.connectors` (see `app/connectors/__init__.py`).
"""

import json
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal


class ConnectorError(Exception):
    """A pull or a check that could not be done — bad credentials, a source that is gone,
    an API that said no. The message is shown to the person; write it for them."""


@dataclass(frozen=True)
class Field:
    """One thing a person fills in. `secret` values are encrypted at rest and never sent
    back to the browser; `multiline` ones are a list, one item per line; `options` makes
    it a choice; `rows` makes it a list of rows, each with these sub-fields — the sites of
    a website connection, each with its own depth and frequency — stored as JSON.
    Sub-fields are never secret: a credential belongs in a grant."""

    name: str
    label: str
    secret: bool = False
    help: str = ""
    required: bool = True
    multiline: bool = False
    options: tuple[tuple[str, str], ...] = ()  # (value, label)
    rows: tuple["Field", ...] = ()


def rows_of(config: dict[str, str], name: str) -> list[dict[str, Any]]:
    """A rows field's value: the list of rows, from the JSON the form sent."""
    raw = config.get(name, "").strip()
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except ValueError as e:
        raise ConnectorError(f"{name}: not a list") from e
    if not isinstance(value, list) or not all(isinstance(r, dict) for r in value):
        raise ConnectorError(f"{name}: not a list")
    return value


@dataclass(frozen=True)
class OAuth:
    """How a provider's sign-in works, for the plumbing to run (grants.py). Declared by
    the connector; the app's own client id and secret come from the server's environment
    as `<PROVIDER>_CLIENT_ID` and `<PROVIDER>_CLIENT_SECRET` — `provider` is that prefix,
    lowercase, shared by connectors of one provider. `params` are extra query parameters
    for the authorize step — Google's `access_type=offline`, say."""

    provider: str
    authorize_url: str
    token_url: str
    scopes: tuple[str, ...]
    params: tuple[tuple[str, str], ...] = ()


@dataclass
class Grant:
    """A person's standing with the provider, as the connector receives it: the secrets
    it asked for (`grant_fields`), or for an OAuth kind an `access_token` the plumbing has
    refreshed before the call. `label` is what the person sees it named — an e-mail, a
    workspace — which the connector chose when the grant was made."""

    kind: str
    label: str
    secrets: dict[str, str] = field(default_factory=dict)

    @property
    def token(self) -> str:
        """The one secret most providers need, whatever it was called."""
        return self.secrets.get("access_token") or self.secrets.get("token", "")


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
    # "none": no grant. "token": a grant made from `grant_fields`, checked by
    # `check_grant`. "oauth2": a grant made by the provider's sign-in, described by
    # `oauth` and run by grants.py — available once the server has the provider's
    # client id and secret.
    auth: ClassVar[Literal["none", "token", "oauth2"]] = "none"
    grant_fields: ClassVar[tuple[Field, ...]] = ()
    oauth: ClassVar[OAuth | None] = None
    # where a connection's files land: raw/connectors/<folder>/. The kind unless said. A
    # bundle has one connection of a kind — the connector's form holds the plural.
    folder: ClassVar[str] = ""
    # a connector that keeps its own clock — the sites of a website connection, each on
    # its own frequency — says how often the plumbing should let it look: minutes. Then
    # the connection has no interval of its own.
    tick: ClassVar[int] = 0

    def name(self, config: dict[str, str]) -> str:
        """What a connection is called, from its config, never typed by a person: the
        hosts of a website connection, the pages of a Notion one. The default is the
        first plain field's value, which is right more often than not."""
        for f in self.fields:
            if not f.secret and not f.rows and config.get(f.name, "").strip():
                return config[f.name].strip()
        return self.title

    def check_grant(self, secrets: dict[str, str]) -> str:
        """Try a grant before it is kept — a pasted token, or the tokens a sign-in just
        returned (`access_token` among them). Return what to call it — the e-mail, the
        workspace, the bot's name; raise ConnectorError with a sentence otherwise."""
        return self.title

    def check(self, config: dict[str, str], grant: Grant | None) -> None:
        """Try a connection's scope before it is saved. Raise ConnectorError with a
        sentence a person can act on; return quietly when all is well."""

    def pull(self, config: dict[str, str], cursor: dict[str, Any], grant: Grant | None) -> Pull:
        raise NotImplementedError


def lines(value: str) -> list[str]:
    """A multiline field's items: one per line, blank lines and edges dropped."""
    return [line.strip() for line in value.splitlines() if line.strip()]
