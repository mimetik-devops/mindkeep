"""The app's config: one server and token, a root folder, and the bundles it watches.

Each watched bundle keeps the folder it was given when it was added — `<root>/<team>/
<bundle>` — rather than deriving it every run: a team renamed on the server would
otherwise quietly mirror into a fresh folder and abandon the old one, state and all.
"""

import json
import re
from pathlib import Path

CONFIG = Path.home() / ".mindstash" / "app.json"

UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def load(path: Path = CONFIG) -> dict:
    cfg = {"server": "http://localhost:8001", "token": "", "root": "", "watch": []}
    if path.exists():
        try:
            cfg.update(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass  # a damaged file is an empty one; the settings window asks again
    if not cfg["root"]:
        cfg["root"] = str(Path.home() / "Mindstash")
    return cfg


def save(cfg: dict, path: Path = CONFIG) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def team_folder(cfg: dict, team: str, name: str) -> Path:
    """Where this team's bundles go: the folder its other bundles already use, else the
    team's name under the root — with a number when another team took that name."""
    for entry in cfg["watch"]:
        if entry["team"] == team:
            return Path(entry["folder"]).parent
    root = Path(cfg["root"])
    taken = {Path(e["folder"]).parent for e in cfg["watch"]}
    clean = UNSAFE.sub("-", name).strip(" .") or "team"
    folder, n = root / clean, 2
    while folder in taken:
        folder, n = root / f"{clean} {n}", n + 1
    return folder


def watched(cfg: dict, team: str, bundle: str) -> dict | None:
    return next((e for e in cfg["watch"] if e["team"] == team and e["bundle"] == bundle), None)


def watch(cfg: dict, team: str, name: str, bundle: str) -> dict:
    """Add a bundle to the watch list, or return the entry it already has."""
    entry = watched(cfg, team, bundle)
    if entry is None:
        folder = team_folder(cfg, team, name) / bundle
        entry = {"team": team, "name": name, "bundle": bundle, "folder": str(folder)}
        cfg["watch"].append(entry)
    return entry


def unwatch(cfg: dict, team: str, bundle: str) -> None:
    cfg["watch"] = [e for e in cfg["watch"] if (e["team"], e["bundle"]) != (team, bundle)]


def sync_config(cfg: dict, entry: dict) -> dict:
    """What `mindstash.sync.sync()` takes: one bundle in one folder."""
    return {
        "server": cfg["server"],
        "token": cfg["token"],
        "folder": entry["folder"],
        "team": entry["team"],
        "bundle": entry["bundle"],
    }
