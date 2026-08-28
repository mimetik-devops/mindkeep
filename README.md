# Mindkeep

**A team knowledge base that an AI agent writes and keeps current, from the documents you
already have.**

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)
[![Desktop client](https://github.com/mimetik-devops/mindkeep/actions/workflows/app.yml/badge.svg)](https://github.com/mimetik-devops/mindkeep/actions/workflows/app.yml)
[![CI](https://github.com/mimetik-devops/mindkeep/actions/workflows/ci.yml/badge.svg)](https://github.com/mimetik-devops/mindkeep/actions/workflows/ci.yml)

People drop documents into a folder. An agent reads each one and folds it into a wiki of
short, linked, cited pages — one per person, company, project, concept, meeting. The wiki
syncs back down to every teammate's machine as plain markdown that any local tool can
open: Claude Code, Obsidian, an editor, `grep`.

The whole design follows from one rule:

> **The agent is the only writer of `wiki/`. People write only `raw/`.**

Nobody hand-edits a page, because a page is not a document — it is a view of the sources,
rewritten whenever they change. Correct the source and the page follows. Delete a source
and every claim that rested on it is withdrawn.

---

## What you get

- **A wiki nobody has to write.** Upload a PDF, a transcript, a contract, a `.docx`; the
  agent decides which pages it bears on and rewrites them, with citations.
- **Pages that stay current.** A nightly pass looks for contradictions, orphans, stale
  drafts and uncited sources. What it cannot settle it does not guess — it writes the
  question down for a person who knows, and the task for a person who can do it.
- **Files, not a database.** Markdown on disk with a git repository inside every knowledge
  base. Every agent run is two commits and can be undone.
- **Context your other AI tools can read.** A synced folder gives an agent a catalog, short
  pages, explicit links and a citation under every claim, instead of a pile of PDFs.
- **Multi-tenant from the first line.** Teams, bundles, roles, invite links, and per-device
  revocable tokens. A team you are not a member of is a 404, never a 403.
- **Sign-in that needs nothing else.** Built-in e-mail and password accounts by default, or
  point it at any OIDC provider (Keycloak, Auth0, Zitadel, Logto, Kinde…).

## Quick start

Requires Docker and an [Anthropic API key](https://console.anthropic.com/).

```bash
git clone https://github.com/mimetik-devops/mindkeep.git
cd mindkeep

cp backend/.env.example backend/.env      # set ANTHROPIC_API_KEY, DEVICE_SECRET, AUTH_SECRET
cp frontend/.env.example frontend/.env    # defaults are fine

docker compose up -d --build
```

Open <http://localhost:5163>, register an account, and drag a document onto the Library
tab. The first page appears when the run finishes.

Nothing else is required: with `AUTH_PROVIDER=builtin` (what `.env.example` ships) Mindkeep
runs on this server alone — no identity provider, no third-party account. The only outbound
calls are to the Anthropic API.

| Service | Where | Notes |
|---|---|---|
| Web app | <http://localhost:5163> | Vite dev server, proxies `/api` |
| API | <http://localhost:8001> | uvicorn with reload |
| Postgres | `localhost:5433` | user / password / db all `mindkeep` |

Compose builds the `dev` target of each Dockerfile. The final stage of each is the
production image: the built site behind Caddy, and uvicorn without the reloader.

## Configuration

Everything lives in `backend/.env` and `frontend/.env`; both `.env.example` files document
every key. The ones that matter:

| Variable | What |
|---|---|
| `ANTHROPIC_API_KEY` | the agent's credential — your API bill, no intermediary |
| `WIKI_ROOT` | where bundles live on disk (`/data`, a volume in production) |
| `DATABASE_URL` | Postgres; holds run metadata only, never wiki content |
| `AUTH_PROVIDER` | `builtin` for Mindkeep's own accounts, `oidc` for a provider |
| `AUTH_SECRET` | builtin only: signs session tokens. Rotating it signs everyone out |
| `DEVICE_SECRET` | signs desktop-client tokens. Rotating it revokes every device |
| `LINT_HOUR` | UTC hour for the nightly maintenance pass; out of range disables it |

## The desktop client

A sync engine, a CLI and a system-tray app that keep chosen bundles mirrored to folders on
a machine, both directions. Installers for Windows, macOS and Linux are attached to each
[release](https://github.com/mimetik-devops/mindkeep/releases).

**The installers are not code-signed.** Windows shows a SmartScreen warning ("More info" →
"Run anyway"); macOS needs a right-click → Open the first time; Linux does not care.

From source, without the tray app:

```bash
cd client
pip install -e .
mindkeep login          # opens the browser, stores a per-device token
mindkeep watch          # or `mindkeep sync` for a single pass
```

A file changed on both sides is never lost: your copy is kept under `.conflicts/` and the
server's lands in place.

## How it works

1. **A source arrives** — dropped in the web app, written into a synced folder, or produced
   by the assistant from a conversation. PDFs and `.docx` files go to the model as
   documents, so scans, charts and multi-column layouts survive.
2. **A run opens.** One worker thread per bundle, so a wiki has exactly one writer at a
   time. The agent reads `index.md` first, then rewrites only the pages the source bears on.
3. **Two commits are made** — what people changed since the last run, then what the agent
   wrote. `index.md` is rebuilt by the server from the pages' own frontmatter.
4. **Anything unresolved is written down** in `questions.md` (for someone who knows) or
   `todo.md` (for someone who can do it).
5. **Overnight, a lint** re-reads the whole bundle, fixes broken source links, and files
   what it cannot fix as questions. A link graph shows which areas are suspiciously
   disconnected from each other.

The agent's instructions are not a prompt buried in code — they are
[`backend/app/templates/manual.md`](backend/app/templates/manual.md), a versioned,
reviewed, tested document. If you want Mindkeep to behave differently, that is the file to
argue with.

## Repository layout

```
backend/     FastAPI + SQLAlchemy + Postgres. Routes, the ingest workers that run the
             agent, the nightly schedule, and a git repo inside every bundle.
  app/templates/manual.md    the agent's operating manual — the real spec
frontend/    React 19 + TypeScript + Vite. Library, graph, questions, activity, settings.
client/      The Python sync engine, the CLI, and the PySide6 tray app.
docs/        Developer onboarding and the architecture / decisions log.
```

## Development

Prerequisites: Docker, Node 22, Python 3.12+, git. Start with
[`docs/Mindkeep - Dev Onboarding.md`](docs/Mindkeep%20-%20Dev%20Onboarding.md) — it explains
the whole system in one sitting.

```bash
# backend — from backend/, in a venv with the dependency list + pytest ruff mypy
ruff check app tests && ruff format --check app tests && mypy app && pytest -q tests

# frontend — from frontend/
npx tsc --noEmit -p tsconfig.json && npx eslint src && npx vitest run

# client — from client/, `pip install -e ".[app,dev]"`
ruff check . && pytest -q
```

CI runs all three on every pull request.

## Documentation

| Document | What it covers |
|---|---|
| [Developer onboarding](docs/Mindkeep%20-%20Dev%20Onboarding.md) | the system end to end: concepts, backend, frontend, client, deployment |
| [Dev log](docs/Mindkeep%20-%20Dev%20Log.md) | every architecture decision, why it was made, and what it replaced |
| [The agent's manual](backend/app/templates/manual.md) | how the agent decides what a page is, where it goes, and what it may claim |
| [CONTRIBUTING.md](CONTRIBUTING.md) | how to propose a change, and the DCO sign-off |
| [SECURITY.md](SECURITY.md) | how to report a vulnerability |

## Contributing

Issues and pull requests are welcome. Commits carry a `Signed-off-by:` line certifying the
[Developer Certificate of Origin](DCO) — `git commit -s` adds it. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the conventions this codebase is held to; the
short version is that commit subjects are sentences, tests are the spec, and a change to
the agent's behaviour is a change to its manual.

## License

Copyright © 2026 mimetik. Mindkeep is free software under the
[GNU Affero General Public License v3.0](LICENSE).

You may run it, study it, modify it and share it. The AGPL adds one condition to the GPL
that matters here: **if you run a modified Mindkeep as a network service, you must offer
its users the source of your modifications.** Running it unmodified for your own team asks
nothing of you.

A fully managed, hosted Mindkeep is planned as a paid option from
[mimetik](https://mimetik.ai) — the same code, the same files, and the same right to take
them and leave. Self-hosting is a first-class way to use Mindkeep and stays that way.
