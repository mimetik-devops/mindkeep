# Two images from one file. `dev` is what docker-compose runs: uvicorn reloading over a
# bind mount. The last stage is what a deploy gets: the same app, no reloader, on the
# port the platform hands it.

FROM python:3.13-slim AS base
# git: every ingest is a commit in the bundle, and an undo is a revert (app/history.py)
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir .
COPY . .

FROM base AS dev
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

FROM base
# shell form so $PORT expands: Railway sets it, compose does not
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
