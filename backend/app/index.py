"""`index.md`, kept by the server from the pages' own frontmatter.

The catalog used to be the agent's to write, one line per page — and so every run edited
it, and undoing any run but the latest collided on it. It was always a function of the
pages: folder, title, description, status. So it is built from them after every run and
every undo, and the agent reads it and never writes it. Plainer than a hand-written
index — grouped by type folder, sorted — and never stale.
"""

from pathlib import Path

from app import graph

HEADER = '---\nokf_version: "0.2"\n---\n\n# Index\n'


def _title(page: Path, fm: dict[str, object]) -> str:
    if fm.get("title"):
        return str(fm["title"]).strip()
    for line in page.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return page.stem


def build(home: Path) -> str:
    """The index text for the pages on disk right now."""
    wiki = home / "wiki"
    sections: dict[str, list[str]] = {}
    count = 0
    for page in sorted(wiki.rglob("*.md")) if wiki.is_dir() else []:
        rel = page.relative_to(home)
        if any(part.startswith(".") for part in rel.parts):
            continue
        fm = graph.frontmatter(page.read_text(encoding="utf-8", errors="replace"))
        folder = rel.parts[1] if len(rel.parts) > 2 else "unfiled"
        line = f"- [{_title(page, fm)}](/{rel.as_posix()})"
        if fm.get("description"):
            line += f" — {str(fm['description']).strip()}"
        if fm.get("status") == "draft":
            line += " · draft"
        sections.setdefault(folder, []).append(line)
        count += 1
    if not count:
        return HEADER + "\nNo pages yet.\n"
    plural = "s" if count != 1 else ""
    lines = [
        HEADER.rstrip("\n"),
        "",
        f"{count} page{plural}, kept by Mindkeep from their frontmatter.",
    ]
    for folder in sorted(sections):
        lines += ["", f"## {folder.replace('-', ' ').capitalize()}", *sections[folder]]
    return "\n".join(lines) + "\n"


def write(home: Path) -> bool:
    """Bring index.md up to date. True when it changed."""
    from app.files import put_text  # local import: files.py imports this module

    text = build(home)
    target = home / "index.md"
    if target.is_file() and target.read_text(encoding="utf-8") == text:
        return False
    put_text(target, text)
    return True
