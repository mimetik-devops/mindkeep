"""Knowledge gaps: two areas the wiki knows about that it has never connected.

The same idea as InfraNodus's structural gaps, run on the graph the agent actually built
rather than on word co-occurrence: pages are nodes, the links between them are edges.
Louvain groups the pages into areas; two sizeable areas with almost no link between them
are a gap — the owner has material on both and nobody has said how they relate. The lint
is handed the gap and asks the owner the question that would close it.

The measuring is deterministic and free — networkx over a few hundred nodes runs in
milliseconds — so it happens here, before the run, the way source moves do. The one
judgement call, what the question is, stays with the agent.
"""

import logging
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

import networkx as nx

from app import graph

log = logging.getLogger(__name__)

MIN_PAGES = 3  # fewer than this is a page, not an area
MAX_GAPS = 3  # per lint: todo.md is a list for a person, not a dump
HUBS = 4  # pages named per side, most central first
# A gap is a pair with under this share of the links random wiring would give them.
# Relative rather than a count, because a company page that links to every project
# gives every pair a link or two without saying anything about how the areas relate.
THIN = 1 / 3


@dataclass(frozen=True)
class Page:
    path: str  # bundle-relative, e.g. wiki/people/jane.md
    title: str
    description: str


@dataclass(frozen=True)
class Gap:
    a: list[Page]  # each side's hub pages, most central first
    b: list[Page]


def find(home: Path) -> list[Gap]:
    """The pairs of areas with the fewest links between them, thinnest first.

    "Few" is against the configuration null model — the links two areas would share if
    every page kept its degree but chose its neighbours at random — which is the same
    yardstick Louvain used to draw the areas in the first place.
    """
    # undirected: a link either way says the two areas know about each other
    G = graph.build(home).to_undirected()
    m = G.number_of_edges()
    if m == 0:
        return []
    areas = [c for c in nx.community.louvain_communities(G, seed=0) if len(c) >= MIN_PAGES]
    central = nx.betweenness_centrality(G)

    def hubs(area: set[str]) -> list[Page]:
        ranked = sorted(area, key=lambda n: (-central[n], -G.degree(n), n))
        return [Page(n, G.nodes[n]["title"], G.nodes[n]["description"]) for n in ranked[:HUBS]]

    found = []
    for a, b in combinations(areas, 2):
        between = sum(1 for u in a for v in G[u] if v in b)
        expected = sum(G.degree(u) for u in a) * sum(G.degree(v) for v in b) / (2 * m)
        if between / expected < THIN:
            found.append((between / expected, -len(a) * len(b), hubs(a), hubs(b)))
    # sorted on the hub paths too, so the same wiki always yields the same list
    found.sort(key=lambda g: (g[0], g[1], [p.path for p in g[2]], [p.path for p in g[3]]))
    gaps = [Gap(a, b) for _, _, a, b in found[:MAX_GAPS]]
    log.info(
        "gaps: %d pages, %d links, %d areas, %d gaps (%d listed)",
        G.number_of_nodes(),
        G.number_of_edges(),
        len(areas),
        len(found),
        len(gaps),
    )
    return gaps


def describe(gaps: list[Gap]) -> str:
    """The gaps as the lint is told them: each side by its hub pages and their index lines."""

    def side(pages: list[Page]) -> str:
        return "\n".join(f"  - `{p.path}` — {p.title}: {p.description}".rstrip(": ") for p in pages)

    return "\n".join(
        f"Gap {n}:\n one area:\n{side(g.a)}\n the other:\n{side(g.b)}"
        for n, g in enumerate(gaps, 1)
    )
