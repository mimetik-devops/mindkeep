"""The assistant: the agent you talk to about an open question.

A second role, with a second set of permissions. The wiki agent owns `wiki/` and may not
touch `raw/`; this one is the mirror image — it may write `raw/` and the two lists, and it may
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
import secrets
import threading
import time
from pathlib import Path

from app import graph, llm
from app.ingest import enqueue, lock_for
from app.todos import QUESTIONS, TODO

log = logging.getLogger(__name__)


ROLE = """You are the assistant in Mindkeep, a knowledge base that a second agent builds
from source documents.

That agent reads everything in `raw/` and writes the pages under `wiki/`. When it cannot
settle something — two sources disagree, a claim has no support, a name is ambiguous — it
writes the question down in `questions.md` and moves on. You are what happens next: the person
you are talking to knows the answer, and your job is to get that answer into the sources
so the wiki can be rebuilt from them correctly.

**What you may write.** Files under `raw/`, and the two lists — `questions.md`, `todo.md`.
Nothing else — the wiki is the
other agent's, and it is regenerated from `raw/`, so a page you edited by hand would be
overwritten the moment its source is read again. Correct the source and the page follows.

**How to work.**
- Read the question, then read the files it names before saying anything about them.
  `related` on a source path lists the pages that cite it — check it before editing a
  source, so you can say what the correction will change.
- Ask the person what you actually need to know. One question at a time. Do not guess, and
  do not invent facts to make a document consistent.
- When you have the answer, write it into the source it belongs to with `edit_raw`, in the
  document's own voice. A correction is part of the document, not a note bolted onto it.
- If the answer belongs to no existing source, `write_raw` a short new one that says where
  it came from — "clarified by <person>, <date>" — so the wiki agent can cite it.
- Then tick the question off in `questions.md` with `resolve`, and say in one line what
  changed.
- If the person's answer raises a new question, add it with `resolve` rather than losing
  it. If it produces work for a person — a document to upload, a page to check — put that
  in `todo.md` with `task`; a task is not a question.

Editing a source re-ingests it automatically, so the wiki updates itself. Say so plainly
rather than promising to update pages yourself; you cannot.

Be brief. You are a colleague at a desk, not a report."""


def reply(home: Path, question: str, messages: list[dict[str, str]]) -> dict[str, object]:
    """One turn. Returns the assistant's text and the raw files it changed."""
    from app.files import put_text, readable_text, safe_path

    touched: list[str] = []

    def writable(target: Path) -> bool:
        """raw/ and the question list. The mirror image of the wiki agent's permissions."""
        return target.is_relative_to(home / "raw") or target.name in (QUESTIONS, TODO)

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

    def related(path: str) -> str:
        """The pages connected to one page: what it links to, what links to it, and what
        cites a source it cites. Given a path under raw/, the pages that cite it.

        Args:
            path: Path relative to the knowledge base root, e.g. raw/notes.md
        """
        return graph.related(graph.build(home), path)

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
            return (
                "Refused: you may only write raw/, questions.md and todo.md. The wiki is not yours."
            )
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
            return (
                "Refused: you may only write raw/, questions.md and todo.md. The wiki is not yours."
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        put_text(target, content)
        _changed(target)
        return f"Wrote {path}. It will be ingested, so the wiki will pick it up."

    def resolve(answered: str, add: str = "") -> str:
        """Tick a question off in questions.md, and optionally add one the answer raised.

        Args:
            answered: The exact text of the question line to tick off.
            add: A new question to append, if the conversation raised one. Optional.
        """
        from app import todos

        text = todos.read(home, QUESTIONS)
        items = todos.parse(text)
        hit = next((i for i in items if str(i["text"]).strip() == answered.strip()), None)
        if hit is None:
            return f"No open question reads exactly {answered!r}. Read questions.md again."
        text = todos.tick(text, int(str(hit["id"])), True)
        if add.strip():
            text = todos.append(text, add)
        put_text(home / QUESTIONS, text)
        log.info("  assist resolved %r", answered[:60])
        return "Ticked off in questions.md."

    def task(text: str) -> str:
        """Add a task for a person to todo.md — work the answer produced, not a question.

        Args:
            text: One line, phrased so someone who was not here can do it.
        """
        from app import todos

        put_text(home / TODO, todos.append(todos.read(home, TODO), text))
        log.info("  assist added a task %r", text[:60])
        return "Added to todo.md."

    def _changed(target: Path) -> None:
        rel = target.relative_to(home).as_posix()
        if rel.startswith("raw/") and rel not in touched:
            touched.append(rel)

    # The same per-bundle lock the wiki agent takes. The assistant only writes raw/, but
    # a source rewritten while an ingest is reading it is exactly the race that lock is for.
    with lock_for(home):
        runner = llm.loop(
            model=llm.model_for("assist"),
            max_tokens=8000,
            effort="medium",
            system=f"{ROLE}\n\nThe question you are working on is:\n{question}",
            tools=[read_file, list_files, related, edit_raw, write_raw, resolve, task],
            messages=[dict(m) for m in messages],
        )
        said = [message["text"] for message in runner if message["text"]]

    # after the lock, so the ingest queue is not started from underneath it
    for rel in touched:
        enqueue(home, rel)
    return {"reply": "\n\n".join(s for s in said if s.strip()), "changed": touched}


# --- a turn as a job -----------------------------------------------------------------------
# A turn can take minutes — reading pages, writing a source — and anything in front of the
# API (Cloudflare: 100 s) gives up on a request that long. So the browser starts the turn,
# gets a job id back at once, and polls. Jobs live in memory: one process, and an answer
# nobody collected within the hour is not worth keeping.
KEEP = 3600
_jobs: dict[str, dict[str, object]] = {}
_jobs_lock = threading.Lock()


def start(home: Path, question: str, messages: list[dict[str, str]]) -> str:
    job = secrets.token_hex(8)
    with _jobs_lock:
        stale = [k for k, j in _jobs.items() if time.time() - float(str(j["at"])) > KEEP]
        for k in stale:
            del _jobs[k]
        _jobs[job] = {"home": str(home), "at": time.time(), "done": False}

    def work() -> None:
        try:
            result = reply(home, question, messages)
            outcome: dict[str, object] = {"done": True, **result}
        except Exception as e:  # noqa: BLE001 - the person sees the sentence, whatever it was
            log.exception("assistant turn failed in %s", home)
            # llm.LLMError already is the sentence a person can act on
            outcome = {"done": True, "error": (str(e) or type(e).__name__)[:300]}
        with _jobs_lock:
            _jobs[job].update(outcome)

    threading.Thread(target=work, daemon=True, name=f"assist-{job}").start()
    return job


def poll(home: Path, job: str) -> dict[str, object] | None:
    """The job's state, or None when there is no such job for this bundle."""
    with _jobs_lock:
        found = _jobs.get(job)
        if found is None or found["home"] != str(home):
            return None
        return {k: v for k, v in found.items() if k not in ("home", "at")}
