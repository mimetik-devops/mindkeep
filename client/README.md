# Mindkeep on your machine

Keeps a bundle in a folder Claude can read, and uploads whatever you drop in its `raw/`.
Two ways to run it: a command-line client with no dependencies, and a tray app.

## The command line

Python 3.12 or newer, nothing to install:

```
cd client
python -m mindkeep login           # once per machine: API address, sign in, pick a team and bundle
python -m mindkeep sync            # pull the wiki down, push what is new in raw/
python -m mindkeep watch           # the same, every 30 seconds — leave it running
```

One config is one bundle in one folder. To keep a second bundle, give it its own config
and its own `watch`:

```
python -m mindkeep login --config ~/.mindkeep-research.json
python -m mindkeep watch --config ~/.mindkeep-research.json
```

`login` opens the website so you can connect the machine with a click; if there is no
browser where you are, paste a device token from **Settings → Account** instead.

## The tray app

Sits in the taskbar, keeps every bundle you tick in one root folder, and shows a
notification when something needs you — a file kept aside after a conflict, an ingest
that failed, a sign-in that stopped working.

From a checkout:

```
cd client
pip install -e ".[app]"
mindkeep-app
```

Or take an installer from the releases page. They are **unsigned**: Windows will warn
(*More info → Run anyway*), and macOS needs a right-click → *Open* the first time.

Right-click the icon for *Sync now*, the folders, *Settings…* (server, sign in, root
folder, which bundles to keep) and *Start at login*.

## Where things live

| What | Where |
|---|---|
| CLI config (one per bundle) | `~/.mindkeep.json`, or whatever `--config` names |
| Tray app config | `~/.mindkeep/app.json` |
| Sync state — what the server had at the last sync, per folder | `~/.mindkeep-state.json` |
| Your copy of a file that conflicted | `<bundle folder>/.conflicts/…` |

The state file is what tells "new here" from "deleted over there"; delete it and the next
sync is conservative, not destructive.

## Building installers

[Briefcase](https://briefcase.beeware.org) builds for the OS it runs on; the GitHub
workflow in `.github/workflows/app.yml` runs it on all three when a `v*` tag is pushed.

```
cd client
pip install briefcase
briefcase create && briefcase build && briefcase package        # Windows: .msi
briefcase create macOS app && briefcase build macOS app && briefcase package macOS app --adhoc-sign
briefcase create linux system && briefcase build linux system && briefcase package linux system   # .deb / .rpm
```

## Releasing

Tag and push the tag: `git tag -a v0.2.0 -m "0.2.0" && git push origin v0.2.0`. That is
the whole of it.

The tag is the only place a version is typed. `pyproject.toml` says `0.0.0` in git —
what a local build gets — and the workflow stamps the tag's number over it
(`stamp.py`) before Briefcase reads it, so the installers, the package metadata and
the changelog all carry it. The release notes — and the `CHANGELOG.md` Briefcase ships
inside the Linux package — are written by `changelog.py`: the subjects of the commits
that touched `client/` since the previous tag, so commit subjects are written as
sentences a user could read. Nobody types a version or a changelog. For a local
`briefcase build linux`, run `python changelog.py` first. *Run workflow* on the Actions
page builds `0.0.0` installers without a tag, as artifacts — the way to try a change to
the workflow itself.

`briefcase dev` runs the app straight from the source tree. The icons come from
`icons/make.py` and are checked in; rerun it if the mark changes.

## Tests

```
cd client
pip install pytest ruff
pytest -q && ruff check .
```

Only the sync logic is tested — it is the part that can delete your files.
