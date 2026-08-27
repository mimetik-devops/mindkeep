from pathlib import Path

from app import graph


def page(
    home: Path,
    rel: str,
    title: str,
    links: list[str] = (),
    sources: list[str] = (),
    description: str = "",
) -> None:
    target = home / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    cited = "".join(f"\n  - id: s{n}\n    resource: {s}" for n, s in enumerate(sources))
    body = "\n".join(f"See [{link}]({link})." for link in links)
    target.write_text(
        f"---\ntype: Concept\ntitle: {title}\ndescription: {description}\n"
        f"sources:{cited}\n---\n\n{body}\n",
        encoding="utf-8",
        newline="\n",
    )


def test_relative_and_absolute_links_both_count(tmp_path):
    page(tmp_path, "wiki/people/jane.md", "Jane", ["../projects/x.md"])
    page(tmp_path, "wiki/projects/x.md", "X", ["/wiki/people/jane.md", "y.md"])
    page(tmp_path, "wiki/projects/y.md", "Y")
    # a bare bundle-absolute path, the form the manual asks for
    page(tmp_path, "wiki/projects/z.md", "Z")
    (tmp_path / "wiki/projects/z.md").write_text(
        "---\ntitle: Z\n---\nRelated: /wiki/projects/y.md and a missing /wiki/nope.md\n"
    )

    G = graph.build(tmp_path)

    assert set(G.edges) == {
        ("wiki/people/jane.md", "wiki/projects/x.md"),
        ("wiki/projects/x.md", "wiki/people/jane.md"),
        ("wiki/projects/x.md", "wiki/projects/y.md"),
        ("wiki/projects/z.md", "wiki/projects/y.md"),
    }
    assert "wiki/nope.md" not in G


def test_sources_resolve_to_bundle_paths(tmp_path):
    page(
        tmp_path,
        "wiki/projects/x.md",
        "X",
        sources=["/raw/deck.md", "../../raw/notes.md", "https://example.com/"],
    )
    # a sources block that is not a list is ignored rather than crashing the build
    page(tmp_path, "wiki/projects/y.md", "Y")
    (tmp_path / "wiki/projects/y.md").write_text("---\ntitle: Y\nsources: none\n---\n")

    G = graph.build(tmp_path)

    assert G.nodes["wiki/projects/x.md"]["sources"] == {
        "raw/deck.md",
        "raw/notes.md",
        "https://example.com/",
    }
    assert G.nodes["wiki/projects/y.md"]["sources"] == frozenset()


def test_export_gives_pages_an_area_and_lists_links(tmp_path):
    for side in "ab":
        for n in range(3):
            others = [f"/wiki/{side}/{side}{o}.md" for o in range(3) if o != n]
            page(tmp_path, f"wiki/{side}/{side}{n}.md", f"{side}{n}", others)
    page(tmp_path, "wiki/alone.md", "Alone", sources=["/raw/x.md"])

    out = graph.export(tmp_path)

    area = {p["path"]: p["area"] for p in out["pages"]}
    assert area["wiki/a/a0.md"] == area["wiki/a/a1.md"] == area["wiki/a/a2.md"]
    assert area["wiki/b/b0.md"] != area["wiki/a/a0.md"]
    assert area["wiki/alone.md"] == -1  # a page in no area, not an area of one
    assert ["wiki/a/a0.md", "wiki/a/a1.md"] in out["links"]
    alone = next(p for p in out["pages"] if p["path"] == "wiki/alone.md")
    assert alone["sources"] == ["raw/x.md"]


def test_related_names_links_both_ways_and_shared_sources(tmp_path):
    page(tmp_path, "wiki/a.md", "A", ["/wiki/b.md"], ["/raw/deck.md"], "about a")
    page(tmp_path, "wiki/b.md", "B", [], ["/raw/other.md"], "about b")
    page(tmp_path, "wiki/c.md", "C", ["/wiki/a.md"], [], "about c")
    page(tmp_path, "wiki/d.md", "D", [], ["/raw/deck.md", "/raw/other.md"])

    text = graph.related(graph.build(tmp_path), "/wiki/a.md")

    assert text == (
        "`wiki/a.md` — A: about a\n"
        "Links to:\n"
        "  - `wiki/b.md` — B: about b\n"
        "Linked from:\n"
        "  - `wiki/c.md` — C: about c\n"
        "Cites the same source:\n"
        "  - `wiki/d.md` — D  (via raw/deck.md)"
    )


def test_related_on_a_source_lists_the_pages_citing_it(tmp_path):
    page(tmp_path, "wiki/a.md", "A", sources=["/raw/deck.md"])
    page(tmp_path, "wiki/b.md", "B", sources=["../raw/deck.md"])
    page(tmp_path, "wiki/c.md", "C", sources=["/raw/other.md"])

    G = graph.build(tmp_path)

    assert graph.related(G, "raw/deck.md") == (
        "`raw/deck.md` is cited by:\n  - `wiki/a.md` — A\n  - `wiki/b.md` — B"
    )
    assert graph.related(G, "raw/nope.md") == "No page at `raw/nope.md`, and no page cites it."


def test_related_says_when_a_page_is_alone(tmp_path):
    page(tmp_path, "wiki/a.md", "A", sources=["/raw/deck.md"])
    page(tmp_path, "wiki/b.md", "B")

    assert graph.related(graph.build(tmp_path), "wiki/a.md") == (
        "`wiki/a.md` — A\nNothing links to or from it, and no other page cites a source it cites."
    )
