import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app import accounts, devices, files, ingest, runs, schedule, teams
from app.auth import CurrentProfile, CurrentRole, CurrentUser, Person, device_token
from app.files import raw_path

# Without this, app logs vanish: uvicorn configures only its own loggers, and the root
# logger's fallback handler drops anything below WARNING. Ingest runs for minutes in the
# background — being unable to see it start and finish makes every failure look identical.
logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s - %(message)s")
log = logging.getLogger(__name__)


def migrate() -> None:
    """Bring the schema up to date before anything reads it.

    A fresh volume — a new deploy, a wiped database — otherwise leaves every request
    failing on a missing table until someone remembers to run alembic by hand.

    ponytail: safe because one replica starts at a time. With several, two would race
    here; move this to a Railway release command before scaling out.
    """
    from alembic.config import Config

    from alembic import command

    command.upgrade(Config(str(Path(__file__).parent.parent / "alembic.ini")), "head")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """A run still open at boot was killed by whatever stopped the last process."""
    try:
        migrate()
    except Exception:
        log.exception("could not migrate the database — expect failures on every read")
    try:
        root = Path(os.environ.get("WIKI_ROOT", "/data"))
        stranded = runs.sweep_interrupted()
        if stranded:
            log.warning("re-queueing %d ingest(s) interrupted by the last stop", len(stranded))
        # deduplicated: a source queued twice before the stop only needs ingesting once
        for tenant, bundle, source in dict.fromkeys(stranded):
            ingest.enqueue(root / tenant / bundle, source)
    except Exception:
        log.exception("could not sweep interrupted ingests")
    try:
        # behind the interrupted ones: what was waiting in the queue when the process died
        files.requeue_unread(Path(os.environ.get("WIKI_ROOT", "/data")))
    except Exception:
        log.exception("could not re-queue unread sources")
    try:
        runs.rekey_legacy_tenants(files.tenant_id)
    except Exception:
        log.exception("could not re-key legacy tenant rows")
    try:
        files.refresh_guides(Path(os.environ.get("WIKI_ROOT", "/data")))
        files.ensure_lists(Path(os.environ.get("WIKI_ROOT", "/data")))
        files.rebuild_indexes(Path(os.environ.get("WIKI_ROOT", "/data")))
    except Exception:
        log.exception("could not refresh the reader's guide or the lists in the bundles")
    schedule.start()
    yield


app = FastAPI(title="Mindkeep", lifespan=lifespan)
# In development the dev server proxies /api, so browser and API share an origin and
# nothing is needed. Deployed, the site and the API are two hosts, and the browser asks
# the API whether the site may call it: ALLOWED_ORIGINS, comma-separated, says yes.
if origins := [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
app.include_router(files.router)
app.include_router(teams.router)
if accounts.enabled():
    app.include_router(accounts.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/me")
def me(user: CurrentUser, role: CurrentRole, who: CurrentProfile) -> dict[str, str]:
    """Who is signed in, read off the token — there is no local user table."""
    return {
        "id": user,
        "name": who.name,
        "role": role,
        "first_name": who.first_name,
        "last_name": who.last_name,
        "email": who.email,
        "picture": who.picture,
    }


@app.post("/clean")
def clean_names(
    paths: Annotated[list[str], Body(embed=True)], _: CurrentUser
) -> dict[str, list[str]]:
    """The names these raw/ paths will be stored under.

    The desktop client asks before uploading a file that is new on its side and renames
    it on disk first, so the mirror and the server agree on what a file is called. The
    rule lives here only — a client with its own copy would drift.
    """
    return {"paths": [raw_path(p) for p in paths]}


@app.get("/about")
def about() -> dict[str, str]:
    """Where the website is, for a machine that only knows the API — the desktop client
    opens the connect page there to sign in. No auth: it is an address, not a secret."""
    return {"web": os.environ.get("WEB_URL", "")}


@app.get("/devices")
def my_devices(who: Person) -> list[dict[str, object]]:
    return [devices.as_dict(d) for d in devices.mine(who)]


@app.post("/devices", status_code=201)
def add_device(who: Person, name: Annotated[str, Body(embed=True)]) -> dict[str, object]:
    """A token for one machine, shown once. The website hands it to the desktop client
    over loopback, or a person pastes it."""
    device = devices.create(who, name)
    return {**devices.as_dict(device), "token": device_token(device.id)}


@app.delete("/devices/{device_id}")
def revoke_device(who: Person, device_id: str) -> dict[str, str]:
    """Out, from the next request on. Nobody else's device is touched."""
    if not devices.forget(who, device_id):
        raise HTTPException(404, "no such device")
    return {"revoked": device_id}
