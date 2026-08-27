"""Mindstash desktop client, from the command line.

    mindstash login    once, per machine
    mindstash sync     pull the wiki down, push anything new in raw/
    mindstash watch    the same, every 30 seconds

Point Claude at the folder this creates. Drop files in its raw/ folder and they upload.

Each command takes `--config PATH`: one config is one bundle in one folder, so a
second bundle is a second config, and a second `watch` — that is how the tray app
runs several.
"""

import argparse
import json
import sys
import time
import urllib.error
from pathlib import Path

from mindstash.sync import INTERVAL, LAYOUT, Unreachable, call_json, sync

CONFIG = Path.home() / ".mindstash.json"


def login(config: Path) -> None:
    default_folder = Path.home() / "Mindstash"
    server = input("API address [http://localhost:8001]: ").strip() or "http://localhost:8001"
    token = input("Device token (copy it from Settings): ").strip()
    folder = input(f"Save the wiki in [{default_folder}]: ").strip() or str(default_folder)

    # The teams you belong to, personal first — the server makes the personal one on
    # first sight, so there is always at least one to pick.
    probe = {"server": server, "token": token}
    try:
        teams = call_json(probe, "teams")
    except urllib.error.HTTPError as e:
        sys.exit(f"That did not work ({e.code}). Check the token.")
    except (urllib.error.URLError, Unreachable) as e:
        sys.exit(f"Could not reach the API: {e}")
    for n, t in enumerate(teams, 1):
        print(f"  {n}. {t['name']}{' (personal)' if t['personal'] else ''}")
    picked = input("Team [1]: ").strip() or "1"
    chosen = next(
        (t for n, t in enumerate(teams, 1) if picked in (str(n), t["id"], t["name"])), None
    )
    if chosen is None:
        sys.exit("That is not one of your teams.")
    team = chosen["id"]

    bundle = input("Bundle [default]: ").strip() or "default"
    cfg = {"server": server, "token": token, "folder": folder, "team": team, "bundle": bundle}
    try:
        call_json(cfg, f"teams/{team}/bundles/{bundle}/tree")
    except urllib.error.HTTPError as e:
        sys.exit(f"That did not work ({e.code}). Check the bundle name.")

    for name in LAYOUT:
        (Path(folder) / name).mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    print(f"Ready. Run `mindstash watch`, and point Claude at {folder}.")


def needs_team(cfg: dict) -> None:
    if "team" not in cfg:
        sys.exit("Bundles now live in teams: run `mindstash login` once more to pick yours.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="mindstash", description="Mindstash desktop client, from the command line.")
    parser.add_argument("command", nargs="?", default="sync", choices=["login", "sync", "watch"])
    parser.add_argument("--config", type=Path, default=CONFIG, help=f"default: {CONFIG}")
    args = parser.parse_args()
    command, config = args.command, args.config
    if command == "login":
        return login(config)
    if not config.exists():
        sys.exit(f"Run `mindstash login` first (config: {config}).")
    cfg = json.loads(config.read_text(encoding="utf-8"))
    needs_team(cfg)

    if command == "sync":
        sync(cfg)
    elif command == "watch":
        print(f"Watching {Path(cfg['folder']) / 'raw'} - drop files there. Ctrl-C to stop.")
        while True:
            try:
                sync(cfg)
            except Unreachable as e:
                sys.exit(str(e))  # a wrong address will not fix itself by waiting
            except Exception as e:  # a laptop closes, wifi drops; keep going
                print("retrying after:", e)
            time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
