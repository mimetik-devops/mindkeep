import logging
import queue
import threading
from datetime import UTC, datetime
from pathlib import Path

import anthropic
from anthropic import beta_tool

from app import gaps, graph, history, runs

log = logging.getLogger(__name__)

# ponytail: in-process locks, so one writer per tenant only within one worker.
# Move to `SELECT pg_advisory_lock(hashtext($tenant))` before running a second replica.
_locks: dict[str, threading.Lock] = {}


def lock_for(home: Path) -> threading.Lock:
    return _locks.setdefault(str(home), threading.Lock())


MODEL = "claude-sonnet-5"

# lint runs share the ingest_run table; this stands where a source path would
LINT = "(lint)"


def pages_citing(home: Path, source: str) -> int:
    """How many wiki pages currently mention this source.

    A count, not a status: whether a source was ingested is recorded in the run history.
    Right after a move this reads zero, because the pages still name the old path until
    the lint repoints them — which is true, and no longer mistaken for "never read".

    ponytail: reads every page per call. Fine at wiki scale; index it when that stops
    being true.
    """
    wiki = home / "wiki"
    if not wiki.is_dir():
        return 0
    return sum(
        1 for p in wiki.rglob("*.md") if source in p.read_text(encoding="utf-8", errors="replace")
    )


def agent_owns(home: Path, target: Path) -> bool:
    """The agent writes the wiki and its bookkeeping — everything except raw/."""
    return not target.is_relative_to(home / "raw")


def user_owns(home: Path, target: Path) -> bool:
    """The user owns raw/ and only raw/: their material, their business.

    The two halves are exclusive on purpose. The user does CRUD on raw sources; the agent
    does CRUD on wiki pages. Deleting a source leaves pages citing something that is gone
    — that is the lint pass's job to notice and clean up, not a reason to block the delete.
    """
    return target.is_relative_to(home / "raw")


# Today's date is passed in because the model has no clock. Without it every log entry
# was a guess, and the guesses drifted weeks into the future — which broke the date
# ordering the Console sorts on.
INGEST_TASK = (
    "Today is {today}. A new source has arrived at `{source}`. Ingest it into the wiki, "
    "following the ingest workflow. Work autonomously: do not stop to ask. Anything you "
    "cannot settle from the sources goes in `todo.md` for a person to answer later, as "
    "the manual describes."
)

LINT_TASK = (
    "Today is {today}. Lint the wiki, following the Lint section of the manual. Report what "
    "you find in a log.md entry headed `## [{today}] lint`, and fix only what the manual "
    "tells you to fix — broken source links. Everything else is reported, not changed. "
    "If the wiki is in good order, say so in one line rather than inventing work.{hints}"
)

# The server knows exactly what moved, because it did the moving. Telling the agent beats
# making it rediscover the same fact by comparing every citation against every file.
MOVED = (
    "\n\nThese sources have been moved by their owner since the last lint. The documents "
    "themselves are unchanged — repoint the citations and change nothing else:\n{list}\n"
    "Sources moved outside the app may not be listed, so still check for citations "
    "pointing at files that no longer exist."
)

# Same principle: the server has measured the link graph, so it names the thin spots
# rather than leaving the agent to read every page and guess at the shape of the whole.
GAPS = (
    "\n\nThe wiki's link graph has areas that barely connect. Each pair below names the "
    "most central pages on either side. Handle them as the Knowledge gaps section of the "
    "manual says: one question in `todo.md` where the two areas genuinely bear on each "
    "other, a few words in the log entry where they do not.\n{list}"
)


def ingest(
    home: Path,
    source: str,
    run_id: int | None = None,
    moves: list[tuple[int, str, str]] | None = None,
    thin: list[gaps.Gap] | None = None,
) -> tuple[int, int]:
    """Have Claude fold a new source into the wiki, or lint it. One writer, serialized.

    `source` is a path under raw/, or LINT for a maintenance pass — the tools and the
    manual are the same either way, only the instruction differs. `moves` and `thin` are
    what the server already knows a lint should look at.

    Returns (turns, characters written) so the run history can record the shape of the work.
    """
    written = 0
    steps = 0
    # local import: files.py imports this module
    from app.files import TEMPLATES, put_text, readable_text, refresh_guide, safe_path

    def step(what: str) -> None:
        """Say what just happened, to the server log and to the row the UI reads.

        A lint spends minutes reading pages it will not change, so "still working" is
        not enough — without the running count, re-reading a page looks like a hang.
        """
        nonlocal steps
        steps += 1
        log.info("  %s", what)
        if run_id is not None:
            runs.progress(home, run_id, note=f"{steps} · {what}")

    def read_file(path: str) -> str:
        """Read a file from the knowledge base.

        Args:
            path: Path relative to the knowledge base root, e.g. wiki/people/jane.md
        """
        step(f"reading {path}")
        target = safe_path(home, path)
        if not target.is_file():
            return f"{path} does not exist yet."
        text = readable_text(target)
        if text is None:
            return (
                f"{path} is not readable as text — Mindstash cannot extract "
                f"{target.suffix or 'this format'} yet. Do not guess at its contents. "
                "Note it in log.md as awaiting support and stop."
            )
        return text

    def write_file(path: str, content: str) -> str:
        """Create a page, or replace one wholesale. Cannot write to raw/, the user's.

        For a change to an existing page, use edit_file: rewriting a whole page to add a
        sentence makes it longer every time it is touched, and every later ingest pays to
        read it.

        Args:
            path: Path relative to the knowledge base root, e.g. wiki/people/jane.md
            content: Full file content, including YAML frontmatter.
        """
        nonlocal written
        written += len(content)
        step(f"writing {path} ({len(content)} chars)")
        target = safe_path(home, path)
        if not agent_owns(home, target):
            return "Refused: raw/ belongs to the user. Write to wiki/ instead."
        target.parent.mkdir(parents=True, exist_ok=True)
        put_text(target, content)
        return f"Wrote {path}."

    def edit_file(path: str, edits: list[dict[str, str]]) -> str:
        """Change parts of a page, leaving the rest untouched.

        Pass **every** change you want to make to this page in one call — each round trip
        costs several seconds, so one call with five edits is far cheaper than five calls.
        Prefer this over write_file for anything short of a rewrite.

        Args:
            path: Path relative to the knowledge base root, e.g. wiki/people/jane.md
            edits: Each entry is {"old": exact text appearing once in the file,
                "new": its replacement}. Applied in order; all must match or none apply.
        """
        nonlocal written
        written += sum(len(e.get("new", "")) for e in edits)
        step(f"editing {path} ({len(edits)} changes)")
        target = safe_path(home, path)
        if not agent_owns(home, target):
            return "Refused: raw/ belongs to the user."
        if not target.is_file():
            return f"{path} does not exist."

        text = target.read_text(encoding="utf-8")
        for i, edit in enumerate(edits):
            old, new = edit.get("old", ""), edit.get("new", "")
            found = text.count(old) if old else 0
            if found != 1:
                # nothing is written: a half-applied batch leaves a page nobody can reason about
                return f"Edit {i + 1} of {len(edits)}: `old` appears {found} times, needs 1."
            text = text.replace(old, new)

        put_text(target, text)
        return f"Edited {path}, {len(edits)} changes."

    def delete_file(path: str) -> str:
        """Delete a wiki page. Cannot touch raw/, which belongs to the user.

        Use this when a page's only source has been removed, or a page has been merged
        into another. Say so in the log entry.

        Args:
            path: Path relative to the knowledge base root, e.g. wiki/people/jane.md
        """
        step(f"deleting {path}")
        target = safe_path(home, path)
        if not agent_owns(home, target):
            return "Refused: raw/ belongs to the user."
        if not target.is_file():
            return f"{path} does not exist."
        target.unlink()
        return f"Deleted {path}."

    def list_files() -> str:
        """List every file in the knowledge base."""
        step("listing the knowledge base")
        # dot directories are the bundle's own business (its history, for one), not content
        paths = (
            p
            for p in home.rglob("*")
            if p.is_file() and not any(s.startswith(".") for s in p.relative_to(home).parts)
        )
        return "\n".join(sorted(p.relative_to(home).as_posix() for p in paths))

    def related(path: str) -> str:
        """The pages connected to one page: what it links to, what links to it, and what
        cites a source it cites. Given a path under raw/, the pages that cite it. Call it
        before editing a page — the pages that describe it from the outside are the ones
        a change to it can put out of date, and neither the page nor index.md names them.

        Args:
            path: Path relative to the knowledge base root, e.g. wiki/people/jane.md
        """
        step(f"looking around {path}")
        return graph.related(graph.build(home), path)

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    if source == LINT:
        hints = ""
        if moves:
            listed = "\n".join(f"- `{old}` is now `{new}`" for _, old, new in moves)
            hints += MOVED.format(list=listed)
        if thin:
            hints += GAPS.format(list=gaps.describe(thin))
        task = LINT_TASK.format(today=today, hints=hints)
    else:
        task = INGEST_TASK.format(source=source, today=today)

    client = anthropic.Anthropic()
    with lock_for(home):
        # Two texts, two readers. manual.md is this agent's whole instruction and never
        # leaves the server; CLAUDE.md is the guide people and local tools find in a synced
        # copy. Startup pushes the guide everywhere; this catches a bundle made since.
        manual = (TEMPLATES / "manual.md").read_text(encoding="utf-8")
        if refresh_guide(home):
            log.info("refreshed CLAUDE.md in %s", home)

        runner = client.beta.messages.tool_runner(
            # Sonnet, not Opus: ingest is read-compare-write, and a smaller model does it
            # several times faster and cheaper. Thinking stays ON — deciding what a source
            # actually changes, and spotting a claim that contradicts an existing page, is
            # the judgement the wiki exists for. If ingests stop catching contradictions
            # and merely summarise, the tier is too small and this goes back to opus-5.
            model=MODEL,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            system=manual,
            tools=[
                beta_tool(read_file),
                beta_tool(write_file),
                beta_tool(edit_file),
                beta_tool(delete_file),
                beta_tool(list_files),
                beta_tool(related),
            ],
            messages=[
                {
                    "role": "user",
                    "content": task,
                }
            ],
        )
        log.info("%s %s: starting", "lint" if source == LINT else "ingest", source)
        turns = 0
        for message in runner:
            turns += 1
            log.info("%s: turn %d, stop_reason=%s", source, turns, message.stop_reason)
            # the turn count alone, so a turn spent only thinking still shows movement
            if run_id is not None:
                runs.progress(home, run_id, turns=turns)
        log.info("%s: done after %d turns, %d steps, %d chars", source, turns, steps, written)
        return turns, written


# One worker thread per bundle, made on first use. Before this, every upload became a
# Starlette background task, and a background task blocked on the ingest lock holds a
# threadpool thread for as long as it waits. Copy forty files into the synced folder and
# forty tasks sit on that lock, exhausting the pool (40 by default) — after which every
# sync route, which is nearly all of them, has nothing to run on and the API stops
# answering. Waiting in a queue costs nothing.
#
# ponytail: the queue is in memory, so a restart loses what has not started. The run rows
# are already swept to "interrupted" at boot, which is where recovery would read from.
_work: dict[str, "queue.Queue[str]"] = {}
_work_lock = threading.Lock()


def busy(home: Path) -> bool:
    """A run in progress, or one waiting its turn. Moving a bundle under either would
    strand it: the worker holds the old path."""
    waiting = _work.get(str(home))
    return lock_for(home).locked() or bool(waiting and not waiting.empty())


def enqueue(home: Path, source: str) -> None:
    """Hand an ingest to the bundle's worker and return immediately."""
    with _work_lock:
        pending = _work.get(str(home))
        if pending is None:
            pending = _work[str(home)] = queue.Queue()
            threading.Thread(
                target=_worker,
                args=(home, pending),
                daemon=True,
                name=f"ingest-{home.parent.name}-{home.name}",
            ).start()
    pending.put(source)
    log.info("queued %s (%d waiting)", source, pending.qsize())


def _worker(home: Path, pending: "queue.Queue[str]") -> None:
    while True:
        source = pending.get()
        try:
            ingest_safely(home, source)
        except Exception:  # ingest_safely logs its own failures; this is the last resort
            log.exception("ingest worker survived a failure on %s", source)


def snapshot(home: Path, message: str) -> str:
    """A commit, or "" — and never a failed run: history is a convenience the ingest must
    not depend on, so a missing git binary is logged and the run goes on without it."""
    try:
        return history.commit(home, message)
    except Exception:
        log.exception("could not commit %s in %s", message, home)
        return ""


def ingest_safely(home: Path, source: str) -> None:
    """Background entry point: a failed ingest must not lose the source that triggered it.

    The run row is opened here and closed here, so a source can never be left looking
    like it is still running when nothing is.
    """
    # read before the run, settled after it: a lint that dies must not lose the hints it
    # was given, and one that succeeds must not see them again
    moves = runs.pending_moves(home) if source == LINT else []
    # measured now rather than inside the run: this worker is the bundle's only writer,
    # so nothing changes the wiki between here and the lint reading the hint
    thin = gaps.find(home) if source == LINT else []
    run_id = runs.start(home, source, MODEL)
    # What people changed since the last run — uploads, answers — is committed on its own
    # first, so undoing this run takes back only what the agent wrote.
    snapshot(home, f"before run {run_id}")
    turns = written = 0
    error = ""
    try:
        turns, written = ingest(home, source, run_id, moves, thin)
    except Exception as e:
        log.exception("ingest failed for %s (source is still on disk, retry is safe)", source)
        # the sentence a person can act on, not the serialised error body around it
        body = getattr(e, "body", None)
        message = (body or {}).get("error", {}).get("message", "") if isinstance(body, dict) else ""
        error = (message or str(e) or type(e).__name__)[:300]
    finally:
        # committed even when the run failed: half a run is exactly what someone wants to undo
        runs.finish(
            home, run_id, turns, written, error, commit=snapshot(home, f"run {run_id}: {source}")
        )
        if not error:
            runs.settle_moves([move_id for move_id, _, _ in moves])
