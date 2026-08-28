"""Two lists the agent keeps for people.

`questions.md` is what the wiki agent could not settle from the sources — two documents
disagree, a claim rests on nothing — and needs someone who *knows*. `todo.md` is what
someone has to *do*: a source to upload, a draft to verify, a duplicate to remove, work an
answer produced. Different readers, different verbs, so two files.

Both are the agent's, like `wiki/`: a local edit is overwritten by the sync. A question is
answered through the assistant, which ticks it once the answer is in the sources, or with
a note in `raw/`; a task is ticked by a person in the app. Plain markdown checkboxes, so a
local tool can read them without Mindkeep in the loop. Writing to either never triggers an
ingest: they are records *about* the knowledge, not knowledge.
"""

import re
from pathlib import Path

ITEM = re.compile(r"^\s*[-*] \[([ xX])\]\s*(.*)$")

QUESTIONS = "questions.md"
TODO = "todo.md"
LISTS = (QUESTIONS, TODO)

EMPTY = {
    QUESTIONS: (
        "# Questions\n\nOpen questions the agent could not settle from the sources. Answer "
        "one through the assistant, or with a note in raw/.\n"
    ),
    TODO: "# Todo\n\nThings a person has to do, found by the agent. Tick them in the app.\n",
}
# what todo.md said before the split, when it held the questions
OLD_EMPTY = "# Todo\n\nOpen questions the agent could not settle on its own.\n"


def read(home: Path, name: str) -> str:
    target = home / name
    return target.read_text(encoding="utf-8") if target.is_file() else EMPTY[name]


def ensure(home: Path) -> bool:
    """Both lists present. A bundle from before the split had only todo.md, and every
    line in it was a question: that content becomes questions.md, and todo.md starts
    empty. True when anything was written."""
    from app.files import put_text  # local import: files.py imports this module

    wrote = False
    old, new = home / TODO, home / QUESTIONS
    if not new.exists() and old.is_file() and parse(old.read_text(encoding="utf-8")):
        text = old.read_text(encoding="utf-8")
        if text.startswith(OLD_EMPTY):
            text = EMPTY[QUESTIONS] + text[len(OLD_EMPTY) :]
        elif text.startswith("# Todo"):
            text = "# Questions" + text[len("# Todo") :]
        put_text(new, text)
        put_text(old, EMPTY[TODO])
        wrote = True
    for name in LISTS:
        if not (home / name).exists():
            put_text(home / name, EMPTY[name])
            wrote = True
    return wrote


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


def append(text: str, line: str) -> str:
    """One more open item at the end."""
    return text.rstrip("\n") + f"\n- [ ] {line.strip()}\n"
