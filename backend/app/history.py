"""Every run is a commit, so every run can be undone.

A git repository inside each bundle, which nobody has to know about: the tree endpoint
and the agent's file listing skip dot directories, the mirror never sees it, and it
rides along when a bundle is renamed, moved or deleted. Around each ingest or lint,
two commits — "before run N", which is whatever people changed since the last one
(uploads, todo answers), and "run N", which is what the agent wrote. Undoing a run
reverts the second as a new commit, so the sources people added stay, the wiki goes
back to the way it was, and the undo is itself in the history.

The git binary, not a Python port: revert and "what changed" are the two things this
needs, and both are one command.
"""

import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

# The identity and the settings every command runs with. autocrlf off, because the
# bundle's files are written with "\n" on purpose and git must not second-guess that.
GIT = [
    "git",
    "-c",
    "user.name=Mindstash",
    "-c",
    "user.email=agent@mindstash.invalid",
    "-c",
    "core.autocrlf=false",
    "-c",
    "core.quotepath=false",
    "-c",
    "commit.gpgsign=false",
]


class Conflict(Exception):
    """A later run changed the same lines, so this one cannot be taken back on its own."""


def _git(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([*GIT, *args], cwd=home, capture_output=True, text=True, encoding="utf-8")


def ensure(home: Path) -> None:
    if (home / ".git").is_dir():
        return
    out = _git(home, "init", "-q", "-b", "main")
    if out.returncode:
        raise RuntimeError(out.stderr.strip())


def commit(home: Path, message: str) -> str:
    """Everything in the bundle, as one commit. The short hash — or "" when nothing changed,
    which is most "before run" commits and every run that read but did not write."""
    ensure(home)
    _git(home, "add", "-A", "--", ".")
    if _git(home, "diff", "--cached", "--quiet").returncode == 0:
        return ""
    out = _git(home, "commit", "-q", "-m", message)
    if out.returncode:
        raise RuntimeError(out.stderr.strip())
    return _git(home, "rev-parse", "--short", "HEAD").stdout.strip()


def blob(home: Path, ref: str, path: str) -> str:
    """The id of a file's content at a commit, or "" when it was not there."""
    out = _git(home, "rev-parse", "--verify", "-q", f"{ref}:{path}")
    return out.stdout.strip() if out.returncode == 0 else ""


def take_back(
    home: Path, sha: str, message: str, restore: tuple[str, str] | None = None, remove: str = ""
) -> str:
    """Revert a run's commit and, in the same new commit, put its source back: to the
    version at `restore` (a ref and a path), or remove it. History is kept, so this can
    itself be reverted. Left exactly as it was on any failure."""
    out = _git(home, "revert", "--no-commit", sha)
    if out.returncode:
        _git(home, "revert", "--abort")
        raise Conflict("later runs changed the same lines — undo those first")
    try:
        if restore:
            ref, path = restore
            out = _git(home, "checkout", ref, "--", path)
            if out.returncode:
                raise RuntimeError(out.stderr.strip())
        if remove:
            out = _git(home, "rm", "-q", "--", remove)
            if out.returncode:
                raise RuntimeError(out.stderr.strip())
        out = _git(home, "commit", "-q", "-m", message)
        if out.returncode:
            raise RuntimeError(out.stderr.strip())
    except Exception:
        _git(home, "reset", "-q", "--hard", "HEAD")
        raise
    return _git(home, "rev-parse", "--short", "HEAD").stdout.strip()


def record(home: Path, message: str, *paths: str) -> str:
    """Commit these paths only — a person's upload, edit, delete or move, as it happens.
    Scoped to the paths, so the agent's half-written pages are never swept into a
    people's commit. The short hash, or "" when the paths are as committed already."""
    ensure(home)
    before = head(home)
    # new files need staging; a path that is gone (a delete, the old side of a move) is
    # nothing to add, and `commit -- path` records its removal on its own
    _git(home, "add", "--ignore-errors", "--", *paths)
    out = _git(home, "commit", "-q", "-m", message, "--", *paths)
    after = head(home)
    if after == before:
        if out.returncode and "nothing to commit" not in out.stdout + out.stderr:
            if "no changes added" not in out.stdout + out.stderr:
                raise RuntimeError((out.stderr or out.stdout).strip())
        return ""
    return after


def undo(home: Path, sha: str, message: str) -> str:
    """Revert one commit as a new one. History is kept, so an undo can itself be undone."""
    out = _git(home, "revert", "--no-edit", sha)
    if out.returncode:
        _git(home, "revert", "--abort")
        raise Conflict("later runs changed the same lines — undo those first")
    _git(home, "commit", "-q", "--amend", "-m", message)
    return _git(home, "rev-parse", "--short", "HEAD").stdout.strip()


def head(home: Path) -> str:
    """The commit the bundle is at, or "" before the first one."""
    out = _git(home, "rev-parse", "--short", "HEAD")
    return out.stdout.strip() if out.returncode == 0 else ""


DIFF_LINES = 400


def same(home: Path, since: str, path: str) -> bool:
    """True when the file on disk is what that commit holds — a source queued again that
    nobody changed in between. Anything git cannot tell (an unknown commit) counts as
    changed, since a needless run is cheaper than a missed one."""
    return _git(home, "diff", "--quiet", since, "--", path).returncode == 0


def diff(home: Path, since: str, path: str) -> str:
    """One file's changes since a commit, as a unified diff without the header noise —
    what a re-ingest is handed. Cut off past DIFF_LINES; a rewrite that long is a new
    document, and the agent is told so."""
    out = _git(home, "diff", "--no-color", "-U1", since, "HEAD", "--", path)
    lines = [ln for ln in out.stdout.splitlines() if not ln.startswith(("diff --git", "index "))]
    if len(lines) > DIFF_LINES:
        lines = lines[:DIFF_LINES] + [
            f"… {len(lines) - DIFF_LINES} more lines; read the whole source"
        ]
    return "\n".join(lines)


def _files(lines: list[str]) -> list[dict[str, str]]:
    rows = []
    for line in lines:
        if not line.strip():
            continue
        status, *paths = line.split("\t")
        rows.append({"status": status[0], "path": paths[-1]})
    return rows


def commits(home: Path, limit: int = 500) -> list[dict[str, object]]:
    """Every commit, newest first, with what it touched — one git call, not one per run."""
    if not (home / ".git").is_dir():
        return []
    out = _git(home, "log", f"-n{limit}", "--format=%x1e%h%x1f%cI%x1f%s", "--name-status")
    found: list[dict[str, object]] = []
    for record in out.stdout.split("\x1e"):
        if not record.strip():
            continue
        head, *rest = record.strip("\n").split("\n")
        sha, at, subject = head.split("\x1f")
        found.append({"sha": sha, "at": at, "subject": subject, "changed": _files(rest)})
    return found


def log_entries(home: Path, limit: int = 500) -> dict[str, str]:
    """Per commit, the lines it added to log.md — the agent's own account of that run, in
    its own words, with no parsing or matching. One git call."""
    if not (home / ".git").is_dir():
        return {}
    out = _git(home, "log", f"-n{limit}", "--format=%x1e%h", "-p", "-U0", "--", "log.md")
    found: dict[str, str] = {}
    for record in out.stdout.split("\x1e"):
        if not record.strip():
            continue
        sha, *lines = record.strip("\n").split("\n")
        added = [ln[1:] for ln in lines if ln.startswith("+") and not ln.startswith("+++")]
        text = "\n".join(added).strip()
        if text:
            found[sha.strip()] = text
    return found


def pending(home: Path) -> list[dict[str, str]]:
    """Changes on disk that no commit holds yet — what people did since the last run."""
    if not (home / ".git").is_dir():
        return []
    out = _git(home, "status", "--porcelain", "--untracked-files=all")
    rows = []
    for line in out.stdout.splitlines():
        if len(line) < 4:
            continue
        code, path = line[:2].strip() or "?", line[3:].strip().strip('"')
        rows.append({"status": {"?": "A", "??": "A"}.get(code, code[0]), "path": path})
    return rows


def changed(home: Path, sha: str) -> list[dict[str, str]]:
    """What a commit touched: A added, M modified, D deleted, R renamed (the new path)."""
    out = _git(home, "show", "--name-status", "--format=", sha)
    return _files(out.stdout.splitlines())
