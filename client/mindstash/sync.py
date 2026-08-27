"""The sync itself: one bundle, one folder, both ways.

`sync(cfg)` pulls the wiki down and pushes anything new in raw/. `cfg` is a dict with
server, token, folder, team and bundle — the CLI keeps one in a file, the desktop app
keeps one per watched bundle. When you and someone else changed the same file, yours
is kept under .conflicts/ and theirs lands in place; for todo.md your ticks are merged
onto their list instead.

ponytail: stdlib only, so the CLI works with nothing installed and the app's only
dependency is Qt.
"""

import hashlib
import json
import shutil
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# The bundle's own shape. Recreated on every sync and never swept away: a mirror with
# nowhere to drop a file is not much use, and an empty directory has no files to carry
# it, so it cannot arrive by download.
LAYOUT = ("raw", "wiki")

# The one file outside raw/ that syncs both ways. The wiki agent writes the questions, the
# assistant ticks them off, and you can work through them here with Claude Code — which
# only works if what you write here goes back. Everything else under the mirror is the
# agent's and is overwritten from the server.
SHARED = "todo.md"

# Where your version of a file goes when the server has a different one: outside the
# synced tree — a dot name is neither uploaded nor swept — so it is never mistaken for a
# new source and ingested twice. Theirs lands in place; you merge by hand if you care.
CONFLICTS = ".conflicts"

# What the server had at the end of the last sync. Without it, a local file the server
# does not have is ambiguous: newly added here, or deleted over there? Guessing "new"
# resurrects deleted sources; guessing "deleted" destroys your originals. Kept beside the
# config rather than in the synced folder, so the mirror stays pure wiki.
STATE = Path.home() / ".mindstash-state.json"
INTERVAL = 30


def rename_new(cfg: dict, root: Path, remote: dict[str, str], seen: dict[str, str]) -> None:
    """Give every file that is new here the name the server will store it under.

    The server rewrites names it does not like, and a mirror that uploads first sees its
    own file come back under a different name — then sweeps the original away as deleted,
    and a twin already spelt the server's way collides into "-2", a duplicate source. So
    the server is asked (POST /clean, one call for all of them) and the file is renamed on
    disk *before* it goes up. The rule lives on the server only.

    Only new files: one the server already has is already spelt its way. A target that
    exists with the same bytes is a twin, and the new copy is dropped; with different
    bytes the new one gets "-2", the way the server would have done it.
    """
    fresh = [
        local.relative_to(root / "raw").as_posix()
        for local in sorted((root / "raw").rglob("*"))
        if local.is_file() and not local.name.startswith(".")
    ]
    fresh = [rel for rel in fresh if f"raw/{rel}" not in remote and f"raw/{rel}" not in seen]
    if not fresh:
        return
    body = json.dumps({"paths": fresh}).encode()
    stored = json.loads(call(cfg, "clean", body, kind="application/json"))["paths"]
    for rel, clean in zip(fresh, stored, strict=True):
        if clean == rel:
            continue
        local = root / "raw" / rel
        target = root / "raw" / clean
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and digest(target) == digest(local):
            local.unlink()
            print("dropped", f"raw/{rel}", "- same as", f"raw/{clean}")
            continue
        stem, suffix = target.stem, target.suffix
        for n in range(2, 1000):
            if not target.exists():
                break
            target = target.with_name(f"{stem}-{n}{suffix}")
        local.rename(target)
        print("renamed", f"raw/{rel}", "->", f"raw/{target.relative_to(root / 'raw').as_posix()}")


class Unreachable(Exception):
    """The server answered with something that is not the API."""


def url_for(cfg: dict, path: str) -> str:
    """Percent-encode each segment: wiki paths carry spaces, brackets and dashes."""
    encoded = "/".join(urllib.parse.quote(part, safe="") for part in path.split("/"))
    return cfg["server"].rstrip("/") + "/" + encoded


def call(
    cfg: dict,
    path: str,
    body: bytes | None = None,
    method: str = "",
    kind: str = "",
    headers: dict[str, str] | None = None,
) -> bytes:
    request = urllib.request.Request(
        url_for(cfg, path), data=body, method=method or ("POST" if body else "GET")
    )
    request.add_header("Authorization", "Bearer " + cfg["token"])
    if kind:
        request.add_header("Content-Type", kind)
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    with urllib.request.urlopen(request) as response:
        return response.read()


def call_json(cfg: dict, path: str):
    """As call(), but insist on JSON — an HTML page means we are talking to the wrong port."""
    raw = call(cfg, path)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise Unreachable(
            f"{cfg['server']} did not return JSON. That is usually the web address rather "
            "than the API - try the backend port (8001 by default)."
        ) from None


def keep_aside(root: Path, rel: str) -> Path:
    """Your version, kept under .conflicts/ at the same relative path."""
    kept = root / CONFLICTS / rel
    kept.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(root / rel, kept)
    return kept


def conflict(root: Path, rel: str, why: str) -> None:
    kept = keep_aside(root, rel)
    print(f"conflict {rel}: {why}; yours is kept in {kept.relative_to(root).as_posix()}")


def merge_todo(mine: str, theirs: str) -> str:
    """Their list — the agent may have added questions overnight — with your ticks and
    your added lines applied. One line per question makes this a text match rather than a
    merge: a line of yours they also have is theirs; a tick of yours on a question they
    still have open ticks it; anything else you wrote goes in after the last line of yours
    they do have, so an answer stays under its question."""
    lines = theirs.splitlines()
    at = 0  # where the next line of yours that they lack goes
    for line in mine.splitlines():
        if line in lines:
            at = lines.index(line) + 1
            continue
        if line.startswith("- [x] ") and ("- [ ] " + line[6:]) in lines:
            at = lines.index("- [ ] " + line[6:])
            lines[at] = line
            at += 1
            continue
        lines.insert(at, line)
        at += 1
    return "\n".join(lines) + "\n"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _key(cfg: dict) -> str:
    return f"{cfg['folder']}|{cfg['bundle']}"


def known(cfg: dict) -> dict[str, str]:
    """path -> hash, as the server had it when this machine last synced."""
    if not STATE.exists():
        return {}
    stored = json.loads(STATE.read_text(encoding="utf-8")).get(_key(cfg))
    # an older client wrote a bare list of paths; treat anything unexpected as no state,
    # which costs one conservative sync rather than a crash
    return stored if isinstance(stored, dict) else {}


def known_dirs(cfg: dict) -> set[str]:
    """The folders the server had at the last sync. Same three-way problem as files:
    a folder here and not there is either one you made or one someone else deleted."""
    if not STATE.exists():
        return set()
    stored = json.loads(STATE.read_text(encoding="utf-8")).get(_key(cfg) + "|dirs")
    return set(stored) if isinstance(stored, list) else set()


def remember(cfg: dict, tree: dict[str, str], dirs: set[str] | None = None) -> None:
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    state[_key(cfg)] = tree
    state[_key(cfg) + "|dirs"] = sorted(dirs or ())
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def sync(cfg: dict) -> None:
    root, bundle = Path(cfg["folder"]), cfg["bundle"]
    base = f"teams/{cfg['team']}/bundles/{bundle}"  # every bundle lives in a team
    remote: dict[str, str] = call_json(cfg, f"{base}/tree")
    seen = known(cfg)
    # before anything is compared, so a moved file is paired under its final name too
    rename_new(cfg, root, remote, seen)

    # Deleted here: in the last sync, on the server, gone from disk. Only the state file
    # makes that different from "not downloaded yet".
    gone = [
        rel
        for rel in seen
        if rel.startswith("raw/") and rel in remote and not (root / rel).exists()
    ]
    # Renames first: to a hash diff a moved file is a delete plus an upload, and sending
    # it as those two loses the fact that matters. The server records a move, and that is
    # what tells the lint to repoint the pages citing it rather than delete and rewrite
    # them. Same content, so the hash is the pairing.
    #
    # ponytail: two sources with identical bytes could pair the wrong way round. The files
    # end up in the right places either way — only the recorded old/new pairing would be
    # crossed, and the lint's own filesystem check covers that.
    arrived: dict[str, str] = {}
    for local in sorted((root / "raw").rglob("*")):
        if local.is_file() and not local.name.startswith("."):
            rel = f"raw/{local.relative_to(root / 'raw').as_posix()}"
            if rel not in remote:
                arrived.setdefault(digest(local), rel)

    moved = False
    for rel in list(gone):
        landed = arrived.pop(remote[rel], "")
        if not landed:
            continue
        call(
            cfg,
            f"{base}/move",
            json.dumps({"source": rel, "target": landed}).encode(),
            kind="application/json",
        )
        print("moved", rel, "->", landed)
        remote[landed] = remote.pop(rel)  # same bytes, so the same hash, at the new path
        gone.remove(rel)
        moved = True

    # A folder that has moved, or a drive that did not mount, looks exactly like someone
    # deleting every source. The two are told apart by the rest of the mirror: CLAUDE.md,
    # index.md and wiki/ belong to the agent and nobody empties those by hand, so if they
    # are still on disk the folder is plainly there and an empty raw/ was deliberate.
    #
    # Checking raw/ itself would not do, because sync recreates it — after a folder was
    # moved away, the freshly made empty raw/ would read as "they deleted everything".
    #
    # Checked after the renames above, so reorganising every source is not mistaken for
    # losing them all.
    everything = gone and len(gone) == len([r for r in seen if r.startswith("raw/")]) > 1
    intact = any(not rel.startswith("raw/") and (root / rel).exists() for rel in seen)
    if everything and not intact:
        print(f"skipping: the whole folder looks missing, not just its {len(gone)} sources")
        print(f"  nothing was deleted on the server. Check that {root} is where you left it.")
        gone = []
    elif everything:
        print(f"deleting all {len(gone)} sources - the rest of {root.name} is still here")
    for rel in gone:
        try:
            call(cfg, f"{base}/{rel}", method="DELETE", headers={"If-Match": seen[rel]})
        except urllib.error.HTTPError as e:
            if e.code != 412:
                raise
            print(f"conflict {rel}: rewritten over there since you deleted it; theirs comes back")
            continue  # still in the tree, so the download pass restores it
        print("deleted", rel)
        remote.pop(rel, None)

    # Up: raw/ is yours, so a change *you* made here wins. Three-way, not two: comparing
    # local against the server alone cannot tell "I edited this" from "someone changed it
    # over there", and pushing in the second case reverts their change and re-ingests it.
    sent = bool(gone) or moved
    # rglob: raw/ is the owner's to organise, so folders in it are theirs too and the
    # path they chose is the path the server stores.
    for local in sorted((root / "raw").rglob("*")):
        if not local.is_file() or local.name.startswith("."):
            continue
        rel, here = f"raw/{local.relative_to(root / 'raw').as_posix()}", digest(local)
        if remote.get(rel) == here or here == seen.get(rel):
            continue  # in step, or only the server moved — the download pass handles it
        if rel not in remote and rel in seen:
            continue  # deleted on the server since last sync; the sweep below removes it
        if rel in remote:
            # Both sides changed since you last synced: nobody's edit should silently win.
            # Yours is kept aside and theirs lands in place. The server's own check covers
            # the seconds between fetching the tree and this upload.
            if rel in seen and remote[rel] != seen[rel]:
                conflict(root, rel, "changed over there too")
                continue
            try:
                stamp = {"If-Match": seen[rel]} if rel in seen else {}
                call(cfg, f"{base}/files/{rel}", local.read_bytes(), method="PUT", headers=stamp)
            except urllib.error.HTTPError as e:
                if e.code != 412:
                    raise
                conflict(root, rel, "changed over there just now")
                continue
            print("updated", rel)
        else:
            call(cfg, f"{base}/{rel}", local.read_bytes())
            print("sent", rel)
        sent = True
    if sent:
        remote = call_json(cfg, f"{base}/tree")

    # todo.md is yours as much as theirs, so an answer written here goes up like a source
    # would — except that it starts no ingest, being a note about the wiki rather than in it.
    shared = root / SHARED
    if shared.is_file() and (here := digest(shared)) != remote.get(SHARED):
        if here != seen.get(SHARED):  # changed here, not merely changed over there
            mine = shared.read_bytes()
            if SHARED in remote and remote[SHARED] != seen.get(SHARED):
                # theirs moved too — the agent asked something overnight, or a teammate
                # ticked — so your ticks and lines go onto their list, not over it
                theirs = call(cfg, f"{base}/files/{SHARED}").decode("utf-8")
                mine = merge_todo(mine.decode("utf-8"), theirs).encode("utf-8")
                shared.write_bytes(mine)
                print("merged", SHARED)
            try:
                stamp = {"If-Match": remote[SHARED]} if SHARED in remote else {}
                call(cfg, f"{base}/files/{SHARED}", mine, method="PUT", headers=stamp)
            except urllib.error.HTTPError as e:
                if e.code != 412:
                    raise
                conflict(root, SHARED, "changed over there just now")
            else:
                print("sent", SHARED)
                remote[SHARED] = digest(shared)
                sent = True

    # Down: everything else is the agent's, so the server wins. Your raw/ edits were
    # pushed above, which is why this cannot overwrite them any more.
    for path, want in remote.items():
        local = root / path
        if local.is_file() and digest(local) == want:
            continue
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(call(cfg, f"{base}/files/{path}"))
        print("got", path)

    # Folders, which files cannot carry: an empty one has no file to arrive with, and
    # a folder made to be filled later is empty by definition. Read after the file work
    # above, so a move that created one is already reflected.
    dirs: set[str] = set(call_json(cfg, f"{base}/folders"))
    here = {
        p.relative_to(root / "raw").as_posix()
        for p in (root / "raw").rglob("*")
        if p.is_dir() and not p.name.startswith(".")
    }
    before = known_dirs(cfg)

    for rel in sorted(here - dirs):
        if rel in before:
            continue  # deleted on the server since the last sync; the sweep removes it
        if any(p.is_file() for p in (root / "raw" / rel).rglob("*")):
            continue  # its files were uploaded above, and they brought the folder with them
        call(cfg, f"{base}/folders/{rel}")
        print("made", f"raw/{rel}")
        dirs.add(rel)
    # deepest first, so a folder tree deleted here goes from the inside out
    for rel in sorted(before & dirs - here, reverse=True):
        try:
            call(cfg, f"{base}/folders/{rel}", method="DELETE")
            print("removed", f"raw/{rel}")
        except urllib.error.HTTPError as e:
            if e.code not in (404, 409):  # already pruned with its last file, or refilled
                raise
        dirs.discard(rel)

    # Gone: the uploads above already ran, so anything still missing from the tree
    # really was deleted on the server. This folder is a mirror — keep your own
    # files somewhere else, because they will not survive here.
    kept = set(LAYOUT) | {f"raw/{rel}" for rel in dirs}
    for local in sorted(root.rglob("*"), reverse=True):
        rel = local.relative_to(root).as_posix()
        if any(part.startswith(".") for part in rel.split("/")):
            continue  # .conflicts/ and friends: not part of the mirror
        if local.is_file() and rel not in remote:
            local.unlink()
            print("removed", rel)
        elif local.is_dir() and not any(local.iterdir()) and rel not in kept:
            local.rmdir()

    # last, so the sweep above cannot take them away again
    for name in (*LAYOUT, *(f"raw/{rel}" for rel in dirs)):
        (root / name).mkdir(parents=True, exist_ok=True)

    remember(cfg, remote, dirs)

