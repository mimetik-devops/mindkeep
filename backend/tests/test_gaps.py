from pathlib import Path

from app import gaps


def page(home: Path, rel: str, title: str, links: list[str] = (), description: str = "") -> None:
    target = home / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"See [{link}]({link})." for link in links)
    target.write_text(
        f"---\ntype: Concept\ntitle: {title}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
        newline="\n",
    )


def cluster(home: Path, folder: str, names: list[str], description: str = "") -> None:
    """Pages that all link to each other, with bundle-absolute links."""
    for name in names:
        others = [f"/wiki/{folder}/{o}.md" for o in names if o != name]
        page(home, f"wiki/{folder}/{name}.md", name.title(), others, description)


def test_two_unlinked_areas_are_a_gap(tmp_path):
    cluster(tmp_path, "cooking", ["stock", "roux", "braise"], "kitchen")
    cluster(tmp_path, "tax", ["vat", "payroll", "audit"], "money")

    found = gaps.find(tmp_path)

    assert len(found) == 1
    sides = {tuple(sorted(p.path for p in side)) for side in (found[0].a, found[0].b)}
    assert sides == {
        ("wiki/cooking/braise.md", "wiki/cooking/roux.md", "wiki/cooking/stock.md"),
        ("wiki/tax/audit.md", "wiki/tax/payroll.md", "wiki/tax/vat.md"),
    }


def test_well_linked_areas_are_not(tmp_path):
    cluster(tmp_path, "a", ["a1", "a2", "a3"])
    cluster(tmp_path, "b", ["b1", "b2", "b3"])
    # two bridges is enough to say the areas know about each other
    page(tmp_path, "wiki/a/a1.md", "A1", ["/wiki/a/a2.md", "/wiki/b/b1.md"])
    page(tmp_path, "wiki/a/a2.md", "A2", ["/wiki/a/a1.md", "/wiki/b/b2.md"])

    assert gaps.find(tmp_path) == []


def test_an_area_needs_three_pages(tmp_path):
    cluster(tmp_path, "a", ["a1", "a2", "a3"])
    cluster(tmp_path, "b", ["b1", "b2"])

    assert gaps.find(tmp_path) == []


def test_hubs_lead_each_side(tmp_path):
    # `hub` links to every page in its area; the others only link to the hub
    for name in ["s1", "s2", "s3", "s4", "s5"]:
        page(tmp_path, f"wiki/a/{name}.md", name, ["/wiki/a/hub.md"])
    page(tmp_path, "wiki/a/hub.md", "Hub", [f"/wiki/a/s{n}.md" for n in range(1, 6)], "centre")
    cluster(tmp_path, "b", ["b1", "b2", "b3"])

    [gap] = gaps.find(tmp_path)

    lead = gap.a if gap.a[0].path.startswith("wiki/a/") else gap.b
    assert lead[0] == gaps.Page("wiki/a/hub.md", "Hub", "centre")
    assert len(lead) == gaps.HUBS


def test_no_links_no_gaps(tmp_path):
    for name in "abcdef":
        page(tmp_path, f"wiki/{name}.md", name)

    assert gaps.find(tmp_path) == []
    assert gaps.find(tmp_path / "nowhere") == []


def test_describe_names_pages_with_their_index_lines():
    gap = gaps.Gap(
        [gaps.Page("wiki/a.md", "A", "about a"), gaps.Page("wiki/b.md", "B", "")],
        [gaps.Page("wiki/c.md", "C", "about c")],
    )

    text = gaps.describe([gap])

    assert text == (
        "Gap 1:\n one area:\n"
        "  - `wiki/a.md` — A: about a\n"
        "  - `wiki/b.md` — B\n"
        " the other:\n"
        "  - `wiki/c.md` — C: about c"
    )
