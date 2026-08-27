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


def undo(home: Path, sha: str, message: str) -> str:
    """Revert one commit as a new one. History is kept, so an undo can itself be undone."""
    out = _git(home, "revert", "--no-edit", sha)
    if out.returncode:
        _git(home, "revert", "--abort")
        raise Conflict("later runs changed the same lines — undo those first")
    _git(home, "commit", "-q", "--amend", "-m", message)
    return _git(home, "rev-parse", "--short", "HEAD").stdout.strip()


def changed(home: Path, sha: str) -> list[dict[str, str]]:
    """What a commit touched: A added, M modified, D deleted, R renamed (the new path)."""
    out = _git(home, "show", "--name-status", "--format=", sha)
    rows = []
    for line in out.stdout.splitlines():
        if not line.strip():
            continue
        status, *paths = line.split("\t")
        rows.append({"status": status[0], "path": paths[-1]})
    return rows
