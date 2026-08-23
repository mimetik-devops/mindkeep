"""`todo.md` — what the wiki agent could not resolve on its own.

A third kind of file, deliberately. `raw/` is the owner's and `wiki/` is the agent's; this
one is **shared**: the wiki agent appends questions it hit while ingesting, the assistant
ticks them off as they are answered, and a person can edit it by hand. It is plain
markdown checkboxes so Claude Code can work through it in the synced folder without
Mindstash being involved at all — which is the point of keeping it out of the wiki.

Writing to it never triggers an ingest. It is a record about the knowledge, not knowledge.
"""

import re

ITEM = re.compile(r"^\s*[-*] \[([ xX])\]\s*(.*)$")

EMPTY = "# Todo\n\nOpen questions the agent could not settle on its own.\n"


def parse(text: str) -> list[dict[str, object]]:
    """Checkbox lines, with any indented lines beneath one folded into its detail."""
    items: list[dict[str, object]] = []
    for line in text.split("\n"):
        found = ITEM.match(line)
        if found:
            items.append(
                {
                    "id": len(items),
                    "done": found[1].lower() == "x",
                    "text": found[2].strip(),
                    "detail": "",
                }
            )
        elif items and line[:1] in (" ", "\t") and line.strip():
            was = str(items[-1]["detail"])
            items[-1]["detail"] = f"{was}\n{line.strip()}".strip()
    return items


def tick(text: str, index: int, done: bool) -> str:
    """Flip one checkbox, leaving every other byte of the file alone.

    By position among the checkboxes rather than by matching the text: two questions can
    read alike, and rewriting the file from a parsed model would throw away whatever a
    person wrote around them.
    """
    seen = -1
    lines = text.split("\n")
    for n, line in enumerate(lines):
        if ITEM.match(line):
            seen += 1
            if seen == index:
                lines[n] = re.sub(r"\[[ xX]\]", "[x]" if done else "[ ]", line, count=1)
                break
    return "\n".join(lines)
