"""The connectors Mindkeep knows: the built-ins in this package, and any package installed
alongside the backend that names a `Connector` subclass under the entry-point group
`mindkeep.connectors`. A plugin is `pip install`ed into the backend image and shows up
in the catalog on the next start; nothing here has to know it exists.

    [project.entry-points."mindkeep.connectors"]
    notion = "mindkeep_notion:NotionConnector"

Both roads end in the same dict, keyed by `kind`. A plugin with a built-in's kind
replaces it — deliberately: that is how someone ships a better `url`.
"""

import importlib
import inspect
import logging
import pkgutil
from functools import lru_cache
from importlib.metadata import entry_points

from app.connectors.base import (
    Connector,
    ConnectorError,
    Field,
    Grant,
    Item,
    OAuth,
    Pull,
    lines,
    rows_of,
)

GROUP = "mindkeep.connectors"

log = logging.getLogger(__name__)

__all__ = [
    "GROUP",
    "Connector",
    "ConnectorError",
    "Field",
    "Grant",
    "Item",
    "OAuth",
    "Pull",
    "lines",
    "registry",
    "rows_of",
]


def _builtin() -> list[type[Connector]]:
    found: list[type[Connector]] = []
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_") or info.name == "base":
            continue
        module = importlib.import_module(f"{__name__}.{info.name}")
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if issubclass(cls, Connector) and cls is not Connector and cls.kind:
                found.append(cls)
    return found


def _installed() -> list[type[Connector]]:
    found: list[type[Connector]] = []
    for ep in entry_points(group=GROUP):
        try:
            cls = ep.load()
        except Exception:
            log.exception("connector plugin %s could not be loaded", ep.name)
            continue
        if not (inspect.isclass(cls) and issubclass(cls, Connector) and cls.kind):
            log.error("connector plugin %s is not a Connector subclass with a kind", ep.name)
            continue
        found.append(cls)
    return found


@lru_cache
def registry() -> dict[str, Connector]:
    """Every connector by kind, one instance each. Cached: the set is fixed at start."""
    known: dict[str, Connector] = {}
    for cls in _builtin() + _installed():
        if cls.kind in known:
            log.info("connector %s: %s replaces %s", cls.kind, cls, type(known[cls.kind]))
        known[cls.kind] = cls()
    return known
