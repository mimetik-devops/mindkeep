"""The assistant: the agent you talk to about an open question.

A second role, with a second set of permissions. The wiki agent owns `wiki/` and may not
touch `raw/`; this one is the mirror image — it may write `raw/` and `todo.md`, and it may
not write a single wiki page.

**Why it must not edit the wiki.** A page is derived from a source. Editing the page and
leaving the source alone puts the two out of step, and the next ingest of that source —
which any later correction to it will cause — regenerates the page from the source and
throws the edit away, question intact. Fixing the source instead means the correction
survives, and the wiki catches up the way it always does. The answer goes where the
question came from.

The conversation itself is not stored. Each turn sends the whole exchange, so the browser
holds the state and the server holds none.

ponytail: no conversation table. Add one when someone needs to leave a thread and come
back to it, which is also when it stops being a chat and starts being a ticket.
"""

import logging
from pathlib import Path

import anthropic
from anthropic import beta_tool

from app.ingest import MODEL, enqueue, lock_for

log = logging.getLogger(__name__)

TODO = "todo.md"

ROLE = """You are the assistant in Mindstash, a knowledge base that a second agent builds
from source documents.

That agent reads everything in `raw/` and writes the pages under `wiki/`. When it cannot
settle something — two sources disagree, a claim has no support, a name is ambiguous — it
writes the question down in `todo.md` and moves on. You are what happens next: the person
you are talking to knows the answer, and your job is to get that answer into the sources
so the wiki can be rebuilt from them correctly.

**What you may write.** Files under `raw/`, and `todo.md`. Nothing else — the wiki is the
other agent's, and it is regenerated from `raw/`, so a page you edited by hand would be
overwritten the moment its source is read again. Correct the source and the page follows.

**How to work.**
- Read the question, then read the files it names before saying anything about them.
- Ask the person what you actually need to know. One question at a time. Do not guess, and
  do not invent facts to make a document consistent.
- When you have the answer, write it into the source it belongs to with `edit_raw`, in the
  document's own voice. A correction is part of the document, not a note bolted onto it.
- If the answer belongs to no existing source, `write_raw` a short new one that says where
  it came from — "clarified by <person>, <date>" — so the wiki agent can cite it.
- Then tick the question off in `todo.md` with `resolve`, and say in one line what changed.
- If the person's answer raises a new question, add it to `todo.md` rather than losing it.

Editing a source re-ingests it automatically, so the wiki updates itself. Say so plainly
rather than promising to update pages yourself; you cannot.

Be brief. You are a colleague at a desk, not a report."""


def reply(home: Path, question: str, messages: list[dict[str, str]]) -> dict[str, object]:
    """One turn. Returns the assistant's text and the raw files it changed."""
    from app.files import put_text, readable_text, safe_path

    touched: list[str] = []

    def writable(target: Path) -> bool:
        """raw/ and the question list. The mirror image of the wiki agent's permissions."""
        return target.is_relative_to(home / "raw") or target == home / TODO

    def read_file(path: str) -> str:
        """Read any file in the knowledge base — sources, wiki pages, the index.

        Args:
            path: Path relative to the knowledge base root, e.g. raw/notes.md
        """
        log.info("  assist read %s", path)
        target = safe_path(home, path)
        if not target.is_file():
            return f"{path} does not exist."
        text = readable_text(target)
        return text if text is not None else f"{path} is not readable as text."

    def list_files() -> str:
        """List every file in the knowledge base."""
        paths = (p for p in home.rglob("*") if p.is_file())
        return "\n".join(sorted(p.relative_to(home).as_posix() for p in paths))

    def edit_raw(path: str, edits: list[dict[str, str]]) -> str:
        """Change parts of a source document, leaving the rest untouched.

        Pass every change to one file in a single call. Applied in order; all must match
        or none apply.

        Args:
            path: A file under raw/, e.g. raw/notes.md
            edits: Each entry is {"old": exact text appearing once in the file,
                "new": its replacement}.
        """
        log.info("  assist edit %s (%d changes)", path, len(edits))
        target = safe_path(home, path)
        if not writable(target):
            return "Refused: you may only write raw/ and todo.md. The wiki is not yours."
        if not target.is_file():
            return f"{path} does not exist."

        text = target.read_text(encoding="utf-8")
        for i, edit in enumerate(edits):
            old, new = edit.get("old", ""), edit.get("new", "")
            found = text.count(old) if old else 0
            if found != 1:
                return f"Edit {i + 1} of {len(edits)}: `old` appears {found} times, needs 1."
            text = text.replace(old, new)

        put_text(target, text)
        _changed(target)
        return f"Edited {path}. It will be re-ingested, so the wiki will catch up."

    def write_raw(path: str, content: str) -> str:
        """Create a source document, or replace one wholesale.

        Prefer edit_raw for a correction to an existing document. Use this for a genuinely
        new source — something the person told you that no document records yet.

        Args:
            path: A file under raw/, e.g. raw/clarifications/pricing.md
            content: The whole file.
        """
        log.info("  assist write %s (%d chars)", path, len(content))
        target = safe_path(home, path)
        if not writable(target):
            return "Refused: you may only write raw/ and todo.md. The wiki is not yours."
        target.parent.mkdir(parents=True, exist_ok=True)
        put_text(target, content)
        _changed(target)
        return f"Wrote {path}. It will be ingested, so the wiki will pick it up."

    def resolve(answered: str, add: str = "") -> str:
        """Tick a question off in todo.md, and optionally add one the answer raised.

        Args:
            answered: The exact text of the question line to tick off.
            add: A new question to append, if the conversation raised one. Optional.
        """
        from app import todos

        target = home / TODO
        text = target.read_text(encoding="utf-8") if target.is_file() else todos.EMPTY
        items = todos.parse(text)
        hit = next((i for i in items if str(i["text"]).strip() == answered.strip()), None)
        if hit is None:
            return f"No open question reads exactly {answered!r}. Read todo.md again."
        text = todos.tick(text, int(hit["id"]), True)
        if add.strip():
            text = text.rstrip("\n") + f"\n- [ ] {add.strip()}\n"
        put_text(target, text)
        log.info("  assist resolved %r", answered[:60])
        return "Ticked off in todo.md."

    def _changed(target: Path) -> None:
        rel = target.relative_to(home).as_posix()
        if rel.startswith("raw/") and rel not in touched:
            touched.append(rel)

    client = anthropic.Anthropic()
    # The same per-bundle lock the wiki agent takes. The assistant only writes raw/, but
    # a source rewritten while an ingest is reading it is exactly the race that lock is for.
    with lock_for(home):
        runner = client.beta.messages.tool_runner(
            model=MODEL,
            max_tokens=8000,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            system=f"{ROLE}\n\nThe question you are working on is:\n{question}",
            tools=[
                beta_tool(read_file),
                beta_tool(list_files),
                beta_tool(edit_raw),
                beta_tool(write_raw),
                beta_tool(resolve),
            ],
            messages=list(messages),
        )
        said: list[str] = []
        for message in runner:
            said.extend(b.text for b in message.content if getattr(b, "type", "") == "text")

    # after the lock, so the ingest queue is not started from underneath it
    for rel in touched:
        enqueue(home, rel)
    return {"reply": "\n\n".join(s for s in said if s.strip()), "changed": touched}
