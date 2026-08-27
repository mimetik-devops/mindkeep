"""The wiki as a graph: pages, the links between them, and the sources they rest on.

Nothing here is stored. A page's links live in its body and its sources in its
frontmatter; this reads both and builds the graph in memory, in milliseconds, whenever
one is needed — after every write it is simply built again. The files stay the only
truth, which is why the frontmatter carries no `related:` list to keep in step.

Two uses: `gaps` measures the whole graph to find areas that never connect, and
`related` answers the agent's question about one page — what links here, and what
rests on the same document — which the page itself cannot tell it.
"""

from __future__ import annotations  # nx.DiGraph[str] exists in the stubs, not at runtime

import re
from pathlib import Path

import networkx as nx
import yaml

# [text](../people/jane.md), [text](/wiki/people/jane.md#section)
LINK = re.compile(r"\]\(([^)\s]+?\.md)(?:#[^)]*)?\)")
# a bundle-absolute path on its own, which is how the manual tells the agent to link
BARE = re.compile(r"(?<![\w/(])/wiki/[^\s)\]>\"'`#]+\.md")

MIN_PAGES = 3  # fewer than this is a page, not an area


def frontmatter(text: str) -> dict[str, object]:
    from app.files import FRONTMATTER  # local import: files.py imports ingest, which imports this

    match = FRONTMATTER.match(text)
    if not match:
        return {}
    try:
        found = yaml.safe_load(match[1])
    except yaml.YAMLError:
        return {}
    return found if isinstance(found, dict) else {}


def cited(fm: dict[str, object], page: Path, home: Path) -> frozenset[str]:
    """The sources a page's frontmatter names, as bundle-relative paths — or as given,
    for URLs and anything outside the bundle. Not checked for existence: two pages that
    cite the same missing file still rest on the same document."""
    entries = fm.get("sources")
    if not isinstance(entries, list):
        return frozenset()
    found = set()
    for entry in entries:
        ref = entry.get("resource") if isinstance(entry, dict) else entry
        if not isinstance(ref, str) or not ref:
            continue
        if "://" in ref:
            found.add(ref)
            continue
        base = home if ref.startswith("/") else page.parent
        full = (base / ref.lstrip("/")).resolve()
        root = home.resolve()
        found.add(full.relative_to(root).as_posix() if full.is_relative_to(root) else ref)
    return frozenset(found)


def build(home: Path) -> nx.DiGraph[str]:
    """Pages as nodes carrying title, description and the sources they cite; a link from
    one page to another as an edge. Links to pages that do not exist are dropped — the
    lint already reports those."""
    wiki = home / "wiki"
    G: nx.DiGraph[str] = nx.DiGraph()
    if not wiki.is_dir():
        return G
    pages: dict[Path, tuple[str, str]] = {}  # resolved path -> (bundle-relative, text)
    for p in sorted(wiki.rglob("*.md")):
        text = p.read_text(encoding="utf-8", errors="replace")
        fm = frontmatter(text)
        rel = p.relative_to(home).as_posix()
        pages[p.resolve()] = (rel, text)
        G.add_node(
            rel,
            title=str(fm.get("title") or p.stem),
            description=str(fm.get("description") or ""),
            sources=cited(fm, p, home),
        )
    for full, (rel, text) in pages.items():
        for target in [*LINK.findall(text), *BARE.findall(text)]:
            base = home if target.startswith("/") else full.parent
            linked = (base / target.lstrip("/")).resolve()
            if linked in pages and linked != full:
                G.add_edge(rel, pages[linked][0])
    return G


def areas(U: nx.Graph[str]) -> list[set[str]]:
    """Louvain communities of MIN_PAGES pages or more, largest first. Seeded, so the same
    wiki always falls into the same areas — the lint and the graph view must agree."""
    if U.number_of_edges() == 0:
        return []
    found = [c for c in nx.community.louvain_communities(U, seed=0) if len(c) >= MIN_PAGES]
    return sorted(found, key=lambda c: (-len(c), min(c)))


def export(home: Path) -> dict[str, object]:
    """The graph as the UI draws it: every page with its area (-1 when it belongs to none),
    the links as pairs, and the sources each page cites."""
    G = build(home)
    area = {p: n for n, c in enumerate(areas(G.to_undirected())) for p in c}
    return {
        "pages": [
            {
                "path": p,
                "title": G.nodes[p]["title"],
                "description": G.nodes[p]["description"],
                "area": area.get(p, -1),
                "sources": sorted(G.nodes[p]["sources"]),
            }
            for p in sorted(G)
        ],
        "links": [list(e) for e in sorted(G.edges)],
    }


def line(G: nx.DiGraph[str], node: str) -> str:
    return f"  - `{node}` — {G.nodes[node]['title']}: {G.nodes[node]['description']}".rstrip(": ")


def related(G: nx.DiGraph[str], path: str) -> str:
    """What one page is connected to, as text for the agent: the pages it links to, the
    pages that link to it, and the pages citing a source it cites. Given a source path
    instead, the pages that cite it."""
    path = path.strip().lstrip("/")
    if path not in G:
        citing = sorted(n for n in G if path in G.nodes[n]["sources"])
        if citing:
            return f"`{path}` is cited by:\n" + "\n".join(line(G, n) for n in citing)
        return f"No page at `{path}`, and no page cites it."

    mine = G.nodes[path]["sources"]
    shared = {
        n: sorted(mine & G.nodes[n]["sources"])
        for n in G
        if n != path and mine & G.nodes[n]["sources"]
    }
    sections = [
        ("Links to:", [line(G, n) for n in sorted(G.successors(path))]),
        ("Linked from:", [line(G, n) for n in sorted(G.predecessors(path))]),
        (
            "Cites the same source:",
            [f"{line(G, n)}  (via {', '.join(shared[n])})" for n in sorted(shared)],
        ),
    ]
    out = [line(G, path).removeprefix("  - ")]
    for heading, lines in sections:
        if lines:
            out.append(heading)
            out.extend(lines)
    if len(out) == 1:
        out.append("Nothing links to or from it, and no other page cites a source it cites.")
    return "\n".join(out)
