# Contributing to Mindkeep

Thank you for considering it. This file covers the legal bit, the setup, and the
conventions this codebase is actually held to — the last part matters more than usual here,
because a lot of Mindkeep's behaviour is written in English rather than in code.

## Before a large change, open an issue

Small fixes can go straight to a pull request. For anything that changes how the agent
behaves, what a page is, or the shape of a bundle on disk, open an issue first. Those are
product decisions as much as technical ones, and it is cheaper to disagree before the work.

## Sign your commits (DCO)

Mindkeep uses the [Developer Certificate of Origin](DCO) rather than a contributor
licence agreement. There is nothing to sign and no form to fill in: you certify that you
have the right to submit your contribution by adding a `Signed-off-by:` line to each
commit, which git writes for you:

```bash
git commit -s -m "Your commit subject"
```

```
Signed-off-by: Jane Dev <jane@example.com>
```

The name and e-mail must be real and must match your git configuration. Forgot it? Fix the
last commit with `git commit --amend -s`, or a branch with
`git rebase --signoff main`.

Contributions are made under the [AGPL-3.0](LICENSE), the same licence as the project.

## Getting set up

Prerequisites: Docker, Node 22, Python 3.12+, git.

```bash
cp backend/.env.example backend/.env      # set ANTHROPIC_API_KEY, DEVICE_SECRET, AUTH_SECRET
cp frontend/.env.example frontend/.env
docker compose up -d --build
```

The web app is at <http://localhost:5163>, the API at <http://localhost:8001>, Postgres at
`localhost:5433`. [`docs/Mindkeep - Dev Onboarding.md`](docs/Mindkeep%20-%20Dev%20Onboarding.md)
explains the whole system in one sitting; read it before your first change.

You need an Anthropic API key to exercise anything that runs the agent. Ingestion costs
real money — a large PDF is a real bill — so develop against small sources.

## The checks

CI runs exactly these, and a pull request is expected to pass them:

```bash
# backend — from backend/, in a venv with the dependency list + pytest ruff mypy
ruff check app tests && ruff format --check app tests && mypy app && pytest -q tests

# frontend — from frontend/
npx tsc --noEmit -p tsconfig.json && npx eslint src && npx vitest run

# client — from client/, `pip install -e ".[app,dev]"`
ruff check . && pytest -q
```

`mypy` runs in strict mode. The backend package is not pip-installable from the host
(setuptools discovery trips over `tests/`, `alembic/`); install its dependency list
instead, or run the checks in the container.

## Conventions

These are not stylistic preferences; they are how the project stays legible.

**Commit subjects are sentences.** Not `fix: ingest bug` but *"Startup re-queues every
source uploaded but never read: a deploy mid-sync forgot 32 of 38"*. They double as the
desktop client's release notes, so a commit touching `client/` is written for someone who
will read it in a changelog.

**The manual is code.** [`backend/app/templates/manual.md`](backend/app/templates/manual.md)
is the agent's system prompt, and it is versioned, reviewed and tested like the rest. A
behaviour you want from the agent goes *there*, in prose it can follow — never as a prompt
tweak somewhere else.

**Tests are the spec** for the sync engine and for ingest behaviour. A change to either
starts with the test. New access paths get traversal and cross-tenant tests in
`backend/tests/test_files.py` — tenancy is the whole security model, so it is tested like it.

**Docstrings say why, not what.** A `ponytail:` comment marks a deliberate simplification
and the condition under which it should be revisited (`grep -rn ponytail`).

**Anything gated is gated by permission**, never by role name — `can(team, "history")`,
not `role === "admin"`.

**Decisions are logged.** A change that settles something goes in
[`docs/Mindkeep - Dev Log.md`](docs/Mindkeep%20-%20Dev%20Log.md) as a dated entry saying
what was decided, why, and what it replaced — so the next person can disagree with the
reason rather than the rule.

**Write files with LF endings.** Working copies are CRLF on Windows and LF in git.

## Pull requests

- One concern per pull request. If the diff needs the word "also", it is two.
- Say what changed and why, and how you verified it. Link the issue if there is one.
- Update the docs the change touches. The onboarding document is updated only when a change
  alters what it *describes*; history goes to the dev log.
- Expect review comments about naming and prose. That is the codebase, not pedantry.

## Reporting bugs and vulnerabilities

Bugs: open an issue with what you did, what happened, and what you expected. Include the
run's error from the Activity tab if the agent was involved.

Security vulnerabilities: **do not open a public issue.** Follow [SECURITY.md](SECURITY.md).

## Code of conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). Be decent; assume
competence.
