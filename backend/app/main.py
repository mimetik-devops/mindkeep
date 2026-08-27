import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app import files, ingest, runs, schedule
from app.auth import CurrentProfile, CurrentRole, CurrentUser, device_token

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
    schedule.start()
    yield


app = FastAPI(title="Mindstash", lifespan=lifespan)
app.include_router(files.router)


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


@app.get("/device-token")
def my_device_token(user: CurrentUser) -> dict[str, str]:
    """What the website shows so someone can set up the desktop client."""
    return {"token": device_token(user)}
