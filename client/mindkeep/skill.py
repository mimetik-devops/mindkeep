"""The agent skill: one file that tells any coding agent where this machine's bundles are.

Agents that follow the Agent Skills standard (agentskills.io) keep every skill's name and
description in context and read the rest only when a question calls for it. With this
file installed, a person no longer hands the bundle folder to each conversation: pi,
Codex and Gemini CLI read `~/.agents/skills`, Claude Code reads `~/.claude/skills`.

It says where, not how. The rules live in each bundle's AGENTS.md, which the server
refreshes, so the skill never carries a second copy of them to drift.
"""

import json
import re
from pathlib import Path

NAME = "mindkeep"  # the standard wants the name to match the folder
MARK = "generated-by: mindkeep-app"  # a skill without it is someone else's: left alone
HOMES = [Path.home() / ".agents" / "skills", Path.home() / ".claude" / "skills"]

MESSY = re.compile(r"[\s\x00-\x1f\x7f]+")
BREAKS_PATH = re.compile(r"[`\x00-\x1f\x7f]")


def clean(text: str, limit: int = 80) -> str:
    """Team and bundle names come from the server and land in every agent's prompt:
    one line, no control characters, and short."""
    text = MESSY.sub(" ", str(text)).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def describe(teams: list[str]) -> str:
    """What the agent sees in every conversation; it decides from this alone whether to
    open the skill. The standard caps it at 1024 characters, whatever the team count."""
    head = "The team knowledge base kept by Mindkeep, mirrored on this machine: "
    tail = (
        " — wiki pages on the team's projects, decisions, people, customers and how things"
        " work, each claim cited to its source. Use it when a question touches that work,"
        " before guessing, searching the web or asking the person."
    )
    names, room = [], 1024 - len(head) - len(tail) - len(", and more")
    for team in teams:
        if len(", ".join([*names, team])) > room:
            return head + ", ".join(names) + ", and more" + tail
        names.append(team)
    return head + ", ".join(names) + tail


def render(watch: list[dict]) -> str | None:
    """The SKILL.md for the bundles this app keeps, or None when none is on disk yet."""
    # a path goes in verbatim, since a tidied one would point somewhere else; one that
    # could break out of its code span is left out instead
    here = [
        e for e in watch if Path(e["folder"]).is_dir() and not BREAKS_PATH.search(str(e["folder"]))
    ]
    if not here:
        return None
    teams = list(dict.fromkeys(clean(e["name"]) for e in here))
    bundles = "\n".join(
        f"- **{clean(e['name'])} / {clean(e['bundle'])}** — `{e['folder']}`" for e in here
    )
    return f"""---
name: {NAME}
description: {json.dumps(describe(teams), ensure_ascii=False)}
metadata:
  {MARK}
---

# Mindkeep

These folders are bundles: wikis an agent in the cloud maintains from the documents a
team collects, synced to this machine by the Mindkeep app. The app wrote this file and
rewrites it when the bundles change, so an edit here does not last.

## Bundles

{bundles}

## Using one

1. Read `AGENTS.md` in the bundle's folder first. It is the bundle's own guide, kept
   current by Mindkeep, and its rules apply. Above all: the folder is a mirror, so do not
   edit it in place.
2. Then read `index.md`, the catalog, and open only the pages it points you to.
3. When the work settles something the team should keep, write it as a note the way
   `AGENTS.md` describes. The app uploads it and the wiki takes it in.
"""


def keep(watch: list[dict], homes: list[Path] = HOMES) -> list[str]:
    """Bring each home's skill in line with the watched bundles: write it when it is
    missing or out of date, remove it when there is nothing to point at. Returns what it
    did, for the log; quiet when nothing changed."""
    text = render(watch)
    done = []
    for home in homes:
        file = home / NAME / "SKILL.md"
        if file.exists() and MARK not in file.read_text(encoding="utf-8"):
            continue
        if text is None:
            if file.exists():
                file.unlink()
                if not any(file.parent.iterdir()):
                    file.parent.rmdir()
                done.append(f"removed {file}")
            continue
        if file.exists() and file.read_text(encoding="utf-8") == text:
            continue
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding="utf-8", newline="\n")
        done.append(f"wrote {file}")
    return done
