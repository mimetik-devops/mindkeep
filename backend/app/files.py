import html
import logging
import mimetypes
import os
import re
import shutil
import zipfile
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Annotated, cast

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response

from app import assist, gaps, graph, history, runs, schedule, teams, todos
from app.auth import CurrentIdentity, CurrentProfile, CurrentUser
from app.db import LINT_OFF, IngestRun
from app.ingest import (
    LINT,
    MAINTENANCE,
    REORGANISE,
    agent_owns,
    busy,
    enqueue,
    lock_for,
    pages_citing,
    user_owns,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/teams/{team}")

TEMPLATES = Path(__file__).parent / "templates"
TODO = "todo.md"  # shared: the wiki agent writes questions, the assistant ticks them off
# Keep human filenames: spaces and brackets are fine on every filesystem and in a URL.
# Path separators and traversal are handled by Path(...).name plus safe_path.
UNSAFE_NAME = re.compile(r"[^A-Za-z0-9 ()._-]")
BUNDLE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
FRONTMATTER = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)


def tenant_id(sub: str) -> str:
    """The directory a subject's bundles live in: a hash, not the subject.

    Subjects are the provider's to shape — `kp_…`, `user_…`, `auth0|…` — and a directory
    name has to be the same on every filesystem, in every URL and in every log line. A
    hash is; the subject is not. Thirty-two hex characters is more than enough to never
    collide and short enough to read.
    """
    return sha256(sub.encode()).hexdigest()[:32]


def tenant(team: str, user: CurrentUser, who: CurrentProfile) -> Path:
    """The team's directory of bundles. This path prefix is the isolation boundary, and
    membership is the gate: a team you are not in does not exist as far as you are
    concerned — 404, never 403. Your own team is made the first time you ask for it, so
    a desktop client can start with the personal id and no sign-in ever having happened
    in the browser."""
    root = Path(os.environ.get("WIKI_ROOT", "/data")).resolve()
    if team == tenant_id(user):
        teams.ensure_personal(user, who)
    elif teams.role_of(team, user) is None:
        raise HTTPException(404, "not found")
    home = root / team
    # Tenants from before the hash were named by the subject itself. Move one the first
    # time its owner shows up, and take its run history and settings along.
    legacy = root / user
    if team == tenant_id(user) and not home.exists() and legacy.is_dir():
        with lock_for(home):
            if not home.exists():
                legacy.rename(home)
                runs.rename_tenant(user, tenant_id(user))
                log.info("moved tenant %s to %s", user, tenant_id(user))
    if not home.exists():
        # A fresh sign-in fires several requests at once. Seeding in place is not enough
        # even under a lock: seed() creates the tenant directory as its first act, so a
        # concurrent request sees home.exists(), skips the lock, and reads a tree whose
        # files are not written yet. Build it aside and move it in one step — the
        # directory either does not exist or is complete, never halfway.
        with lock_for(home):
            if not home.exists():
                staging = root / f".{team}.seeding"
                shutil.rmtree(staging, ignore_errors=True)  # a crashed earlier attempt
                # ponytail: new tenants get one bundle, so the UI never opens on empty.
                seed(staging / "default")
                try:
                    staging.rename(home)
                except OSError:  # another worker got there first; theirs is as good
                    shutil.rmtree(staging, ignore_errors=True)
    return home


Tenant = Annotated[Path, Depends(tenant)]


# Every route that changes a bundle asks for `write`; the ones that change the team's
# shelf — a bundle made, moved, or scheduled — ask for `bundles`. Which roles carry
# those is teams.GRANTS, and nothing here knows.
Writer = Annotated[None, teams.needs("write")]
Manager = Annotated[None, teams.needs("bundles")]
# Taking a run back rewrites the wiki: admins and owners. Seeing the history is reading.
Historian = Annotated[None, teams.needs("history")]


def bundle(name: str, home: Tenant) -> Path:
    """One OKF bundle. `name` comes from the URL, so it is validated before it becomes a path."""
    if not BUNDLE_NAME.match(name):
        raise HTTPException(400, "bundle names are lowercase letters, digits and hyphens")
    target = home / name
    if not target.is_dir():
        raise HTTPException(404, "no such bundle")
    # raw/ and wiki/ are the bundle's shape rather than its content: a bundle with
    # nowhere to put a source is not a bundle. Emptying either one must not remove it,
    # so rather than trusting every path that deletes, guarantee it on the way in.
    for half in ("raw", "wiki"):
        (target / half).mkdir(exist_ok=True)
    # same argument, and it gives bundles made before the question list existed one too
    if not (target / TODO).exists():
        put_text(target / TODO, todos.EMPTY)
    return target


Bundle = Annotated[Path, Depends(bundle)]


def docx_text(path: Path) -> str | None:
    """A .docx is a zip of XML. Pull the body out with the standard library, no dependency."""
    try:
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml").decode("utf-8", "replace")
    except (zipfile.BadZipFile, KeyError, OSError):
        return None
    xml = re.sub(r"</w:p>", "\n", xml)  # paragraphs become newlines before tags are cut
    return html.unescape(re.sub(r"<[^>]+>", "", xml)).strip()


def readable_text(path: Path) -> str | None:
    """The file as text a model can actually use, or None when it is binary.

    Without this the agent is handed replacement characters for a PDF or a .docx, cannot
    tell that it failed, and burns turns and tokens guessing at mojibake.
    """
    if path.suffix.lower() == ".docx":
        return docx_text(path)
    try:
        return path.read_bytes().decode("utf-8")  # strict: a decode error means binary
    except UnicodeDecodeError:
        return None


def put_text(target: Path, text: str) -> None:
    """Always LF. Windows would translate to CRLF, and the wiki syncs to other machines."""
    target.write_text(text, encoding="utf-8", newline="\n")


def seed(home: Path) -> None:
    """A new bundle gets the OKF skeleton plus the reader's guide. The agent's manual
    stays on the server (templates/manual.md); it is a prompt, not content."""
    (home / "raw").mkdir(parents=True, exist_ok=True)
    (home / "wiki").mkdir(exist_ok=True)
    put_text(home / "CLAUDE.md", (TEMPLATES / "CLAUDE.md").read_text("utf-8"))
    put_text(home / "index.md", '---\nokf_version: "0.2"\n---\n\n# Index\n')
    put_text(home / "log.md", "# Log\n")
    put_text(home / TODO, todos.EMPTY)
    # the skeleton is the first commit, so the first answer in todo.md reads as an edit
    try:
        history.commit(home, "seeded")
    except Exception:
        log.exception("could not commit the seeded skeleton in %s", home)


def refresh_guide(home: Path) -> bool:
    """Bring one bundle's CLAUDE.md up to the guide that ships with the app. True if it
    changed. The guide is shipped code, not content: a bundle seeded last month would
    otherwise carry last month's, and so would every synced copy of it."""
    guide = (TEMPLATES / "CLAUDE.md").read_text(encoding="utf-8")
    target = home / "CLAUDE.md"
    if target.is_file() and target.read_text(encoding="utf-8") == guide:
        return False
    put_text(target, guide)
    return True


def refresh_guides(root: Path) -> int:
    """Every bundle, at startup — so a deploy is how a change to the guide reaches them,
    quiet bundles included. Sync clients see the new hash on their next pass."""
    changed = 0
    if not root.is_dir():
        return 0
    for tenant in root.iterdir():
        if not tenant.is_dir() or tenant.name.startswith("."):
            continue
        for home in tenant.iterdir():
            if home.is_dir() and (home / "index.md").is_file() and refresh_guide(home):
                changed += 1
    if changed:
        log.info("refreshed CLAUDE.md in %d bundle(s)", changed)
    return changed


def unchanged(target: Path, request: Request) -> None:
    """Honour If-Match. A client that names the hash it last saw is refused with 412 when
    the file has moved on since — the half of a sync race the tree fetch cannot see. No
    header, no check: the web UI and older clients keep last-write-wins."""
    expected = request.headers.get("if-match", "").strip().strip('"')
    if expected and target.is_file() and sha256(target.read_bytes()).hexdigest() != expected:
        raise HTTPException(412, "changed since you last saw it — fetch it again first")


def record(home: Path, message: str, *paths: str) -> None:
    """A person's change, committed as it happens. History is a convenience: a commit
    that fails is logged and the change stands."""
    try:
        history.record(home, message, *paths)
    except Exception:
        log.exception("could not record %s in %s", message, home)


def safe_path(home: Path, rel: str) -> Path:
    try:
        target = (home / rel).resolve()
    except ValueError:  # null bytes and friends
        raise HTTPException(400, "bad path") from None
    if not target.is_relative_to(home):
        raise HTTPException(400, "bad path")
    # dot directories — the bundle's history, for one — are not files anyone is served
    if any(part.startswith(".") for part in target.relative_to(home).parts):
        raise HTTPException(404, "not found")
    return target


def raw_path(rel: str) -> str:
    """A user-supplied path under raw/, made safe one segment at a time.

    Folders are the point — someone organising their own sources should keep their
    structure. `..` and `.` strip to nothing and are dropped, and UNSAFE_NAME cannot
    produce a separator, so no segment can climb out of raw/. safe_path still checks
    the result, because two guards on a path from the outside is not one too many.
    """
    parts = [p for segment in rel.split("/") if (p := UNSAFE_NAME.sub("-", segment).strip(" ."))]
    return "/".join(parts) or "upload"


def remove_tree(path: Path) -> None:
    """rmtree that gets past git's read-only object files on Windows."""

    def unlock(fn, target, _exc):  # type: ignore[no-untyped-def]
        os.chmod(target, 0o600)
        fn(target)

    shutil.rmtree(path, onexc=unlock)


def prune_empty(start: Path, stop: Path) -> None:
    """Walk up from `start`, removing empty folders until something is left or `stop`.

    An emptied folder is clutter in every synced copy of the bundle. A folder someone
    made on purpose is only ever empty until they put something in it, and this runs
    when a file leaves — so the one case it cannot distinguish, an intentionally empty
    folder that briefly held a file, costs one re-create.
    """
    folder = start
    while folder != stop and folder.is_dir() and not any(folder.iterdir()):
        folder.rmdir()
        folder = folder.parent


@router.get("/bundles")
def list_bundles(home: Tenant) -> list[str]:
    return sorted(p.name for p in home.iterdir() if p.is_dir() and BUNDLE_NAME.match(p.name))


@router.post("/bundles", status_code=201)
def create_bundle(
    home: Tenant, name: Annotated[str, Body(embed=True)], _: Manager
) -> dict[str, str]:
    if not BUNDLE_NAME.match(name):
        raise HTTPException(400, "bundle names are lowercase letters, digits and hyphens")
    if (home / name).exists():
        raise HTTPException(409, "bundle already exists")
    seed(home / name)
    return {"name": name}


@router.put("/bundles/{name}")
def rename_bundle(
    name: str, home: Bundle, _: Manager, to: Annotated[str, Body(embed=True)]
) -> dict[str, str]:
    """Rename a bundle: the directory, and its rows. Same rules as a move — not while
    anything is ingesting it, and the new name must be free. Mirrors pointed at the old
    name will 404 on their next pass and need `mindkeep login` again."""
    if not BUNDLE_NAME.match(to):
        raise HTTPException(400, "bundle names are lowercase letters, digits and hyphens")
    if to == name:
        return {"name": name}
    if (home.parent / to).exists():
        raise HTTPException(409, "bundle already exists")
    if busy(home):
        raise HTTPException(409, "this bundle is being ingested — rename it when that has finished")
    with lock_for(home):
        home.rename(home.parent / to)
    runs.rename_bundle(home.parent.name, name, to)
    log.info("renamed bundle %s to %s in %s", name, to, home.parent.name)
    return {"name": to}


@router.delete("/bundles/{name}")
def delete_bundle(name: str, home: Bundle, _: Manager) -> dict[str, str]:
    """Delete a bundle: its sources, its wiki, its history. The `bundles` permission; not
    while anything is ingesting it; and a team keeps at least one, since every view opens
    on a bundle. The UI asks for the name to be typed first — there is no undo yet."""
    if busy(home):
        raise HTTPException(409, "this bundle is being ingested — delete it when that has finished")
    others = [b for b in home.parent.iterdir() if b.is_dir() and b != home]
    if not others:
        raise HTTPException(409, "a team keeps at least one bundle")
    with lock_for(home):
        remove_tree(home)
    runs.forget_bundle(home.parent.name, name)
    log.info("deleted bundle %s in %s", name, home.parent.name)
    return {"deleted": name}


@router.put("/bundles/{name}/team")
def move_bundle(
    name: str,
    home: Bundle,
    user: CurrentUser,
    to: Annotated[str, Body(embed=True)],
    _: Manager,
) -> dict[str, str]:
    """Move a bundle to another team: a directory rename and its rows re-keyed.

    Owners and admins on both sides — it leaves one team's shelf and lands on another's.
    Not while anything is ingesting it: the worker holds the old path and would strand a
    run. The name has to be free over there; bundles are not renamed on the way.
    """
    leaving, joining = home.parent.name, to
    if not teams.allowed(teams.role_of(joining, user), "bundles"):
        raise HTTPException(404, "not found")  # a team you do not manage is not yours to see
    root = home.parent.parent
    target = root / joining / name
    if target.exists():
        raise HTTPException(409, "that team already has a bundle by that name")
    if busy(home):
        raise HTTPException(409, "this bundle is being ingested — move it when that has finished")
    with lock_for(home):
        (root / joining).mkdir(exist_ok=True)
        home.rename(target)
    runs.move_bundle(leaving, joining, name)
    log.info("moved bundle %s from %s to %s", name, leaving, joining)
    return {"team": joining, "bundle": name}


@router.get("/bundles/{name}/tree")
def tree(home: Bundle) -> dict[str, str]:
    """path -> sha256. The client diffs this against its folder to decide what to fetch.

    ponytail: hashes every file per call. Fine for a wiki; cache by mtime if it ever bites.
    """
    files = (
        p
        for p in home.rglob("*")
        if p.is_file() and not any(s.startswith(".") for s in p.relative_to(home).parts)
    )
    # as_posix: these round-trip into URLs, so they are always forward-slashed.
    return {p.relative_to(home).as_posix(): sha256(p.read_bytes()).hexdigest() for p in files}


@router.get("/bundles/{name}/graph")
def link_graph(home: Bundle) -> dict[str, object]:
    """The wiki as pages and links, for the graph view, with the pairs of areas the lint
    would call gaps. Rebuilt from the files on every call — the same graph `related` and
    the gap measurement read, never a stored copy."""
    G = graph.build(home)
    U = G.to_undirected()
    out = graph.export(G)
    thin = gaps.pairs(U, graph.areas(U)) if U.number_of_edges() else []
    out["gaps"] = [
        {"a": p.a, "b": p.b, "links": p.links, "expected": round(p.expected, 1)} for p in thin
    ]
    return out


@router.get("/bundles/{name}/files/{path:path}")
def read(path: str, home: Bundle) -> Response:
    target = safe_path(home, path)
    if not target.is_file():
        raise HTTPException(404, "not found")
    # bytes, not text: raw/ holds PDFs and images as well as markdown.
    # .md explicitly, because Windows' mimetypes does not know it and Linux does.
    kind = "text/markdown" if target.suffix == ".md" else mimetypes.guess_type(target.name)[0]
    return Response(target.read_bytes(), media_type=kind or "application/octet-stream")


@router.get("/bundles/{name}/text/{path:path}")
def read_as_text(path: str, home: Bundle) -> Response:
    """A source as the agent reads it — .docx unzipped, anything UTF-8 as itself.

    The bytes route serves a .docx as a download, which is right for saving it and useless
    for looking at it. This is the same extraction the ingest agent gets, so what you read
    here is what it read, which is the version worth arguing with.

    415 when nothing can be extracted, rather than an empty page pretending to be the file.
    """
    target = safe_path(home, path)
    if not target.is_file():
        raise HTTPException(404, "not found")
    text = readable_text(target)
    if text is None:
        raise HTTPException(415, f"Mindkeep cannot read {target.suffix or 'this format'} yet")
    return Response(text, media_type="text/plain; charset=utf-8")


@router.put("/bundles/{name}/files/{path:path}")
async def write(path: str, request: Request, home: Bundle, _: Writer) -> dict[str, str]:
    target = safe_path(home, path)
    shared = target == home / TODO  # both agents and the owner write this one
    if not user_owns(home, target) and not shared:
        raise HTTPException(409, "wiki/ belongs to the agent — add a source instead")
    unchanged(target, request)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(await request.body())

    rel = target.relative_to(home).as_posix()
    record(home, f"{'answer' if shared else 'edit'} {rel}", rel)
    # a corrected source should correct the wiki: the pages built from it are now stale.
    # todo.md is a record *about* the knowledge, so nothing is re-read when it changes.
    if not shared:
        enqueue(home, rel)
    return {"path": rel}


@router.post("/bundles/{name}/verify/{path:path}")
def verify(path: str, home: Bundle, who: CurrentIdentity, _: Writer) -> dict[str, str]:
    """Stamp a page as human-checked.

    The server owns this, not the client: `verified` is the field that separates what
    the agent inferred from what a person confirmed, so the identity has to come from
    the token rather than from whatever the browser felt like sending.
    """
    target = safe_path(home, path)
    if not target.is_file() or not agent_owns(home, target):
        raise HTTPException(404, "not found")

    at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    text = target.read_text(encoding="utf-8")
    match = FRONTMATTER.match(text)
    if not match:
        raise HTTPException(409, "page has no frontmatter to stamp")

    # ponytail: drop any single-line `verified:` and append a fresh one, rather than
    # re-dumping the YAML, which would reformat every other field. OKF also allows a
    # list of verifications — write that when someone actually needs the history.
    kept = [ln for ln in match[1].split("\n") if not ln.startswith("verified:")]
    stamped = f"verified: {{ by: human:{who}, at: {at} }}"
    put_text(target, "---\n" + "\n".join([*kept, stamped]) + "\n---\n" + text[match.end() :])
    return {"verified_by": f"human:{who}", "at": at}


@router.get("/bundles/{name}/sources")
def sources(home: Bundle) -> list[dict[str, object]]:
    """Every raw source with its ingest state.

    "Ingested" is not a flag anyone sets — it is whether any wiki page cites the source.
    That cannot drift out of step with reality, which a status column would.
    """
    last = runs.latest(home)
    done = runs.ingested_sources(home)
    moment = datetime.now(UTC)
    # a source moved since the last lint is still cited by the path it used to have
    was = {new for _, _, new in runs.pending_moves(home)}

    def state(rel: str) -> dict[str, object]:
        run = last.get(rel)
        if run is None:  # never attempted
            return {"ingesting": False, "seconds": 0, "error": "", "took": 0, "note": ""}
        # the latest run, so the UI can offer to take it back — and say when it has been
        undoable = {"run": run.id if run.commit else 0, "undone": run.undone_at is not None}
        if run.finished_at is None:
            return {
                "ingesting": True,
                "seconds": int((moment - runs.utc(run.started_at)).total_seconds()),
                "error": "",
                "took": 0,
                "note": run.note,  # the last thing the agent did, for the live card
            } | undoable
        return {
            "ingesting": False,
            "seconds": 0,
            "error": run.error,
            "took": run.seconds or 0,
            "note": "",
        } | undoable

    return [
        {
            "path": (rel := p.relative_to(home).as_posix()),
            "pages": pages_citing(home, rel),
            # recorded when a run finished cleanly, not inferred from what cites it
            "ingested": rel in done,
            # ...which is why a moved source still reads as ingested while its citations
            # are stale. The flag lets the UI say so rather than leaving it puzzling.
            "moved": rel in was,
        }
        | state(rel)
        for p in sorted((home / "raw").rglob("*"))
        if p.is_file()
    ]


def by_people(change: dict[str, str]) -> bool:
    """Whether a file in a before-run commit is something a person did. Sources are; an
    answered todo.md is; a seeded or refreshed file — the skeleton, the guide — is not."""
    path, status = change["path"], change["status"]
    return path.startswith("raw/") or (path == "todo.md" and status == "M")


@router.get("/bundles/{name}/activity")
def activity(home: Bundle) -> list[dict[str, object]]:
    """What happened to this bundle, newest first, from its history.

    Agent runs — ingests and lints — come with the log entry they wrote, taken from their
    own commit rather than parsed out of log.md and matched by title. The commits between
    runs are what people did: uploads, edits, deletes, answers — which the log never had.
    Reading the feed is reading; undoing a run is the `history` permission.
    """
    words = history.log_entries(home)
    by_commit = {r.commit: r for r in runs.recent(home) if r.commit}
    feed: list[dict[str, object]] = []
    told: set[int] = set()

    def run_entry(
        r: IngestRun, changed: list[dict[str, str]] | None, at: str = ""
    ) -> dict[str, object]:
        """A run in the feed. Its time is its commit's when it has one — the same clock and
        the same precision as every other entry, so a run and the undo of it sort right —
        and its start otherwise."""
        told.add(r.id)
        return {
            "kind": "run",
            "id": r.id,
            "source": r.source,
            "at": at or runs.utc(r.started_at).isoformat(),
            "finished_at": runs.utc(r.finished_at).isoformat() if r.finished_at else None,
            "seconds": r.seconds or 0,
            "error": r.error,
            "commit": r.commit,
            "undone": r.undone_at is not None,
            "note": words.get(r.commit, ""),
            # what kind of run: the agent's own log heading says, at no extra cost
            "task": r.source.strip("()")
            if r.source in MAINTENANCE
            else ("retire" if "] retire |" in words.get(r.commit, "") else "ingest"),
            "changed": changed or [],
        }

    for c in history.commits(home):
        sha, subject = str(c["sha"]), str(c["subject"])
        if sha in by_commit:
            # what it changed is fetched on demand
            feed.append(run_entry(by_commit[sha], None, at=str(c["at"])))
        elif subject.startswith(("undo run", "redo run")):
            feed.append(
                {
                    "kind": "undo" if subject.startswith("undo") else "redo",
                    "at": c["at"],
                    "commit": sha,
                    "subject": subject,
                    "changed": c["changed"],
                }
            )
        else:
            people = [x for x in cast(list[dict[str, str]], c["changed"]) if by_people(x)]
            if people:
                feed.append({"kind": "people", "at": c["at"], "commit": sha, "changed": people})
    # runs with no commit — still running, or read and wrote nothing — are history too
    for r in runs.recent(home):
        if r.id not in told:
            feed.append(run_entry(r, []))
    # people's changes through the app are committed as they happen; what is left here is
    # a change made straight on the volume, which the next run's commit will record
    waiting = [x for x in history.pending(home) if by_people(x)]
    if waiting:
        feed.append(
            {
                "kind": "pending",
                "at": datetime.now(UTC).isoformat(),
                "commit": "",
                "changed": waiting,
            }
        )
    # newest first, on real times: run rows carry microseconds, commits do not. Stable, so
    # entries from the same second keep git's own order.
    feed.sort(key=lambda e: datetime.fromisoformat(str(e["at"])), reverse=True)
    return feed


@router.get("/bundles/{name}/runs/{run_id}")
def run_detail(run_id: int, home: Bundle) -> dict[str, object]:
    """One run: what it touched, and whether it has been taken back."""
    run = runs.get(home, run_id)
    if run is None:
        raise HTTPException(404, "not found")
    return {
        "id": run.id,
        "source": run.source,
        "commit": run.commit,
        "undone": run.undone_at is not None,
        "changed": history.changed(home, run.commit) if run.commit else [],
    }


@router.post("/bundles/{name}/runs/{run_id}/undo")
def undo_run(run_id: int, home: Bundle, _: Historian) -> dict[str, object]:
    """Put the wiki — and the source this run read — back to what the wiki reflected
    before it. A source that was new at this run is removed; one that was edited goes
    back to the version the previous run read; one that had not changed is left alone.
    All in one revert commit, so the undo can be redone. Refused while an ingest runs,
    when a later run changed the same lines, and when the source has changed since —
    that later run is the one to undo first. A lint has no source: the wiki only.

    Taking the source back is the point: a bad source left in place would be ingested
    again the moment anyone touched it. Mirrors follow the server, and history keeps
    the file — nothing is lost that a redo cannot bring back.
    """
    run = runs.get(home, run_id)
    if run is None:
        raise HTTPException(404, "not found")
    if not run.commit:
        raise HTTPException(409, "this run wrote nothing, so there is nothing to undo")
    if run.undone_at is not None:
        raise HTTPException(409, "already undone")
    if busy(home):
        raise HTTPException(409, "an ingest is running — undo when it has finished")

    restore: tuple[str, str] | None = None
    remove = ""
    if run.source not in MAINTENANCE and run.based_on:
        read = history.blob(home, run.based_on, run.source)  # what this run read
        if history.blob(home, "HEAD", run.source) != read:
            raise HTTPException(
                409, "the source has changed since this run — undo the later run first"
            )
        before = runs.read_before(home, run)
        was = history.blob(home, before.based_on, run.source) if before and before.based_on else ""
        if was != read:
            if was:
                restore = (before.based_on, run.source)  # type: ignore[union-attr]
            else:
                remove = run.source  # new at this run, or read from before history began
    with lock_for(home):
        try:
            sha = history.take_back(
                home, run.commit, f"undo run {run_id}: {run.source}", restore, remove
            )
        except history.Conflict as e:
            raise HTTPException(409, str(e)) from None
    if remove:
        prune_empty((home / remove).parent, home / "raw")
        runs.forget_moves(home, remove)
    runs.mark_undone(run_id)
    log.info("undid run %d (%s) in %s", run_id, run.source, home)
    return {"undone": run_id, "commit": sha, "restored": bool(restore), "removed": remove}


@router.post("/bundles/{name}/runs/{run_id}/redo")
def redo_run(run_id: int, home: Bundle, _: Historian) -> dict[str, object]:
    """Revert the undo: the wiki and the source come back, and the run counts again."""
    run = runs.get(home, run_id)
    if run is None:
        raise HTTPException(404, "not found")
    if run.undone_at is None:
        raise HTTPException(409, "this run has not been undone")
    if busy(home):
        raise HTTPException(409, "an ingest is running — redo when it has finished")
    undo = next(
        (c for c in history.commits(home) if str(c["subject"]).startswith(f"undo run {run_id}:")),
        None,
    )
    if undo is None:
        raise HTTPException(409, "the undo is not in the history")
    with lock_for(home):
        try:
            sha = history.undo(home, str(undo["sha"]), f"redo run {run_id}: {run.source}")
        except history.Conflict as e:
            raise HTTPException(409, str(e)) from None
    runs.mark_redone(run_id)
    log.info("redid run %d (%s) in %s", run_id, run.source, home)
    return {"redone": run_id, "commit": sha}


@router.get("/bundles/{name}/todos")
def list_todos(home: Bundle) -> list[dict[str, object]]:
    """The open questions, as the agents left them in todo.md."""
    target = home / TODO
    return todos.parse(target.read_text(encoding="utf-8")) if target.is_file() else []


@router.post("/bundles/{name}/todos/{index}")
def set_todo(
    index: int, home: Bundle, done: Annotated[bool, Body(embed=True)], _: Writer
) -> dict[str, bool]:
    """Tick a question off by hand, or put it back."""
    target = home / TODO
    if not target.is_file():
        raise HTTPException(404, "nothing to tick")
    text = target.read_text(encoding="utf-8")
    if index >= len(todos.parse(text)):
        raise HTTPException(404, "no such question")
    put_text(target, todos.tick(text, index, done))
    return {"done": done}


@router.post("/bundles/{name}/assist")
def ask(
    home: Bundle,
    question: Annotated[str, Body()],
    messages: Annotated[list[dict[str, str]], Body()],
    _: Writer,
) -> dict[str, object]:
    """One turn with the assistant. The browser holds the conversation; the server does not.

    Synchronous on purpose: this is a chat, and the person is waiting for the answer. The
    ingests it may trigger are the part that goes on the queue.
    """
    if not messages:
        raise HTTPException(400, "nothing to say")
    return assist.reply(home, question, messages)


@router.get("/bundles/{name}/lint")
def lint_state(home: Bundle) -> dict[str, str | int | bool]:
    """Whether a lint is running, and how the last one went."""
    run = runs.last_lint(home)
    nxt = schedule.next_run(home)  # empty when the nightly pass is switched off
    hour = schedule.hour_for(home)
    if run is None:
        return {
            "linting": False,
            "seconds": 0,
            "at": "",
            "error": "",
            "note": "",
            "turns": 0,
            "next": nxt,
            "hour": hour,
        }
    if run.finished_at is None:
        return {
            "linting": True,
            "seconds": int((datetime.now(UTC) - runs.utc(run.started_at)).total_seconds()),
            "at": "",
            "error": "",
            # a lint reads for minutes before it writes anything, so the live card needs
            # something more specific than a spinner
            "note": run.note,
            "turns": run.turns or 0,
            "next": nxt,
            "hour": hour,
        }
    return {
        "linting": False,
        "seconds": run.seconds or 0,
        "at": runs.utc(run.finished_at).strftime("%Y-%m-%d"),
        "error": run.error,
        "note": "",
        "turns": run.turns or 0,
        "next": nxt,
        "hour": hour,
    }


@router.put("/bundles/{name}/lint")
def set_lint_schedule(
    home: Bundle, hour: Annotated[int, Body(embed=True)], _: Manager
) -> dict[str, int]:
    """Choose the hour (UTC) this bundle is linted, or -1 to stop linting it nightly."""
    if hour != LINT_OFF and not 0 <= hour <= 23:
        raise HTTPException(400, "hour is 0-23, or -1 to switch the nightly lint off")
    runs.set_lint_hour(home, hour)
    return {"hour": hour}


@router.post("/bundles/{name}/lint")
def lint(home: Bundle, _: Writer) -> dict[str, str]:
    """Run a maintenance pass now. The nightly one does exactly this, on a timer."""
    if LINT in runs.running_sources(home):
        raise HTTPException(409, "a lint is already running")
    enqueue(home, LINT)
    return {"linting": home.name}


@router.post("/bundles/{name}/reorganise")
def reorganise(home: Bundle, _: Writer) -> dict[str, str]:
    """File every page where the manual's layout rule puts it. A run like a lint: one
    commit, undoable. For a bundle written before the rule, or after it changed."""
    if REORGANISE in runs.running_sources(home):
        raise HTTPException(409, "a reorganise is already running")
    enqueue(home, REORGANISE)
    return {"reorganising": home.name}


@router.get("/bundles/{name}/folders")
def list_folders(home: Bundle) -> list[str]:
    """Folders under raw/, including empty ones.

    They cannot come from `tree`, which maps files to hashes — an empty folder has no
    file to carry it. The web UI and the desktop client both need them to exist before
    anything is in them, which is the whole point of making one.
    """
    raw = home / "raw"
    if not raw.is_dir():
        return []
    return sorted(
        p.relative_to(raw).as_posix()
        for p in raw.rglob("*")
        if p.is_dir() and not p.name.startswith(".")
    )


@router.post("/bundles/{name}/folders/{path:path}", status_code=201)
def create_folder(path: str, home: Bundle, _: Writer) -> dict[str, str]:
    rel = raw_path(path)
    target = safe_path(home, f"raw/{rel}")
    if target.exists():
        raise HTTPException(409, "already there")
    target.mkdir(parents=True)
    return {"folder": rel}


@router.delete("/bundles/{name}/folders/{path:path}")
def remove_folder(path: str, home: Bundle, _: Writer) -> dict[str, str]:
    """Only an empty one. Deleting sources is the other route, and it asks first."""
    target = safe_path(home, f"raw/{path}")
    if not user_owns(home, target) or not target.is_dir() or target == home / "raw":
        raise HTTPException(404, "not found")
    if any(target.iterdir()):
        raise HTTPException(409, "the folder is not empty")
    target.rmdir()
    prune_empty(target.parent, home / "raw")
    return {"deleted": target.relative_to(home).as_posix()}


@router.post("/bundles/{name}/move")
def move_raw(
    home: Bundle,
    source: Annotated[str, Body()],
    target: Annotated[str, Body()],
    _: Writer,
) -> dict[str, str]:
    """Move a source. Both ends are under raw/, which is the half the user owns.

    Pages cite a source by path, so a move leaves those citations pointing at nothing.
    Repointing them is the lint's job, not this route's: the server never edits a wiki
    page, and firing an agent run per move would make reorganising a folder cost as much
    as ingesting it. The manual tells the lint that a moved source is not a missing one.
    """
    old = safe_path(home, source)
    if not user_owns(home, old) or not old.is_file():
        raise HTTPException(404, "not found")

    # Both ends are spelled the same way, bundle-relative. Accepting a bare "papers/x.md"
    # as well would quietly turn a mistyped "wiki/x.md" into "raw/wiki/x.md" rather than
    # refusing it, and a silent reinterpretation of a path is worse than an error.
    if not target.startswith("raw/"):
        raise HTTPException(400, "a source can only be moved within raw/")
    new = safe_path(home, f"raw/{raw_path(target.removeprefix('raw/'))}")
    if new.exists():
        raise HTTPException(409, "something is already there")

    was = old.relative_to(home).as_posix()
    new.parent.mkdir(parents=True, exist_ok=True)
    old.rename(new)
    prune_empty(old.parent, home / "raw")

    now = new.relative_to(home).as_posix()
    runs.rename_source(home, was, now)  # its history is about the document, not the path
    runs.record_move(home, was, now)  # and the next lint is told, rather than left to notice
    record(home, f"move {was} -> {now}", was, now)
    return {"from": was, "to": now}


@router.post("/bundles/{name}/ingest/{path:path}")
def reingest(path: str, home: Bundle, _: Writer) -> dict[str, str]:
    """Run the agent over a source again.

    Ingests are long and not durable — a deploy, a crash, or `--reload` in development
    kills one mid-run, and the only trace is a source with no pages citing it. Rather than
    build a queue, give the source a retry button.
    """
    target = safe_path(home, f"raw/{path}")
    if not user_owns(home, target) or not target.is_file():
        raise HTTPException(404, "not found")
    rel = target.relative_to(home).as_posix()
    enqueue(home, rel)
    return {"ingesting": rel}


@router.delete("/bundles/{name}/raw/{path:path}")
def remove_raw(path: str, home: Bundle, request: Request, _: Writer) -> dict[str, object]:
    """Delete a raw source. Only ever raw/ — never a wiki page.

    Deleting a source orphans the pages derived from it. That is fine and expected: the
    lint pass notices pages whose only source is gone and removes them, using the agent's
    own delete tool. Blocking the delete to protect a citation would be the wrong trade.
    """
    target = safe_path(home, f"raw/{path}")
    if not user_owns(home, target) or not target.is_file():
        raise HTTPException(404, "not found")
    unchanged(target, request)  # deleting what someone has since rewritten is a conflict
    rel = target.relative_to(home).as_posix()
    cited = pages_citing(home, rel) > 0
    target.unlink()
    prune_empty(target.parent, home / "raw")
    runs.forget_moves(home, rel)  # a deleted source has nowhere to be repointed to
    record(home, f"delete {rel}", rel)
    # pages that rested on it are retired now, by a run over the gone source — the lint
    # would find them tonight, and nobody wants to wait for a lint to stop citing a file
    if cited:
        enqueue(home, rel)
    return {"deleted": rel, "retiring": cited}


@router.post("/bundles/{name}/raw/{path:path}")
async def add_raw(path: str, home: Bundle, request: Request, _: Writer) -> dict[str, str]:
    """Raw documents land here as sent, keeping their own name.

    No provenance sidecar: the ingest agent writes a summary page under wiki/ that cites
    the source, and that page is the real record. A second mechanical file written before
    anything had read the document only duplicated it.
    """
    target = home / "raw" / raw_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    stem, suffix = target.stem, target.suffix
    for n in range(2, 1000):  # a new upload never silently replaces an existing source
        if not target.exists():
            break
        target = target.with_name(f"{stem}-{n}{suffix}")
    target.write_bytes(await request.body())

    rel = target.relative_to(home).as_posix()
    record(home, f"upload {rel}", rel)
    enqueue(home, rel)
    return {"path": rel}
