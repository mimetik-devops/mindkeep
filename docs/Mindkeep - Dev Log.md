# Mindkeep — Dev Log

*Architecture decisions and development notes, in the order they were made. An entry
says the decision, the reasoning, and what it replaced — so the next person can
disagree with the reason rather than the rule. Newest at the end of section 4. The
system as it is now is described in* Mindkeep - Dev Onboarding.md*; this is how it got
there. Entries 4.21–4.29 were lost with the folder that held them and are
reconstructed in brief.*

## 1. What it is

Mindkeep is a cloud-hosted second brain: an LLM-maintained wiki that ingests
material from the services where knowledge actually accumulates — uploads,
Gmail, Google Drive, Gemini meeting notes, transcripts — and folds each new
source into an interlinked set of markdown pages.

The reason for building it in the cloud rather than as a local folder is
ingestion: connectors can pull from Gmail and Drive continuously, which a local
wiki cannot. The wiki is then mirrored back down to a laptop so Claude Code can
be pointed at it directly.

It is **internal to Ruben's team for now**. Not a paid product, no public
release planned. Several decisions below are explicitly sized for that and are
marked with what would change if it were ever sold.

## 2. Where it stands (2026-08-22)

Working, with 42 passing backend tests and 9 frontend ones, plus the desktop
client verified end to end against a running server — download, edit a file in
`raw/`, re-upload, watch the wiki change:

- Kinde-authenticated multi-tenant file API, which also accepts device tokens
- OKF v0.2 bundle scaffolding, seeded per bundle
- Upload to a `raw/` the user owns and a `wiki/` only the agent may write
- A cloud ingest agent (Claude Sonnet 5) that folds a source into the wiki
- Run history in Postgres: how long each ingest took, and whether it finished
- A nightly lint per bundle, its hour set from Settings, plus a button to run one now
- An account menu (picture, name, role, email) reading a live Kinde profile
- Open questions in `todo.md`, and an assistant that answers them by fixing the sources
- Sources organised in folders, from the web or by making folders in the synced copy
- A stdlib-only desktop client with three-way sync — local edits and local
  deletions both propagate, without a resurrection or a clobber
- A working web UI — Library and Console tabs, wired to the real API

Not started: the Gmail/Drive connectors, MCP access, and PDF text extraction.

## 3. Architecture decisions

### 3.1 The wiki is files, not database rows

**Decided:** markdown on a Railway volume at `WIKI_ROOT/{kinde_sub}/{bundle}/`
is the canonical store. Postgres is deliberately *not* involved in the wiki —
it is reserved for user data, connector state and cursors.

**Why:** local sync becomes a directory copy rather than an export format,
backup becomes a volume snapshot, and tenant isolation becomes a path prefix you
can eyeball. Most importantly it removes the database/filesystem consistency
problem entirely — there is only one copy of anything.

**Rejected:** note bodies in Postgres with the filesystem as an export. It
creates two sources of truth that must be kept in agreement, and buys nothing
until search outgrows ripgrep.

**Binds:** no `documents` table should ever be proposed. Postgres earns its
place when the first connector needs to store an OAuth cursor, and Alembic is
deliberately not initialized until then.

### 3.2 Open Knowledge Format v0.2, with Karpathy's three-layer split

**Decided:** wiki pages follow OKF v0.2 — markdown with YAML frontmatter, one
required field (`type`). Layout per bundle is `CLAUDE.md`, `index.md`,
`log.md`, `raw/`, `wiki/`.

**Why:** OKF costs almost nothing to adopt (one required field, and conformance
explicitly forbids consumers from rejecting unknown keys, missing fields or
broken links) so it is a free wire format rather than a commitment. The fields
that earn their keep are `sources` for provenance, `generated`/`verified` to
separate LLM-written from human-confirmed, and `status`/`stale_after`, which is
exactly the input the weekly lint job needs.

**Worth recording precisely, because it is easy to overstate:** OKF itself only
defines `index.md`/`log.md` as reserved names, `okf_version` in the bundle-root
index, frontmatter with a required `type`, and bundle-absolute links. The
`raw/` + `wiki/` + `CLAUDE.md` split is *Karpathy's* three-layer
architecture, not OKF. OKF's own name for mirrored external material is
`references/`; Mindkeep uses `raw/` instead, matching this wiki's vocabulary.

**Sources:** Karpathy's LLM wiki notes
(gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) and Google's OKF
spec (github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md).

### 3.3 Kinde for identity, validated against JWKS with no SDK

**Decided:** Kinde issues RS256 JWTs; the backend verifies them against the
JWKS endpoint using PyJWT's `PyJWKClient`, checks issuer and audience, and uses
the `sub` claim as the tenant key. No local users table.

**Why:** house convention — Kinde for identity across every mimetik product.
Mindkeep independently landed on the same no-SDK JWKS validation iclonic uses.

**Correction to an open question on the Kinde decision page.** That page asks
"whether the no-SDK JWKS validation iclonic uses should replace the Kinde SDK in
Futuros". The premise is wrong: **Futuros' backend does not use the Kinde Python
SDK either.** Verified 2026-08-22 by direct inspection of
`Oliver/Futuros/futuros.io/foresight`:

- `backend/app/core/security.py` — `import jwt as pyjwt` and
  `from jwt import PyJWKClient`. Same approach as iclonic and Mindkeep.
- `backend/app/core/kinde.py` — a hand-rolled Kinde *Management API* client over
  `httpx` using OAuth2 client credentials. Its own docstring describes reads as
  best-effort and writes as raising. Not the SDK.
- `kinde_python_sdk 2.3.1` is installed in the backend `.venv` but is **not
  declared** in `pyproject.toml` or any requirements file — a leftover install,
  not a dependency. Grepping `app/` finds no `kinde_sdk`, `kinde_fastapi` or
  `from kinde` import anywhere.
- The **frontend** does use a Kinde SDK: `@kinde-oss/kinde-auth-react ^5.11.2`,
  declared in `frontend/package.json`.

So the backend convention is already uniform — no SDK, PyJWT plus
`PyJWKClient`, in Futuros as much as in iclonic, and now Mindkeep makes three.
The real distinction is frontend React SDK versus backend hand-rolled
validation, not one product against another. The open question as written should
be closed and replaced, if it is worth keeping at all, with the narrower
question of whether the *frontend* React SDK earns its weight.

**Also worth noting for the M2M thread:** Futuros already operates a Kinde M2M
application for Management API access, which is a separate mechanism from the
per-user API keys Mindkeep costed and rejected in §3.8.

**Binds:** the `sub` becomes a directory name, so it is regex-validated
(`^[A-Za-z0-9_-]{1,128}$`) before it is ever used as a path segment.

**Corrected 2026-08-22: the backend does run an M2M application.** The first pass
argued Mindkeep needed no M2M credentials, since verifying a JWT only requires the
public JWKS. Ruben pushed back, and he was right — the argument was too narrow. The
backend needs to resolve a Kinde `sub` into a person, because the wiki's `verified`
field is meaningless as a raw id, and that is a Management API read.

That produced a better design than the one it replaced: **the server owns the
`verified` stamp.** `POST /bundles/{b}/verify/{path}` takes the identity from the
verified token, resolves it through the Management API, and writes
`verified: { by: human:<email>, at: … }` itself. Previously the browser composed that
line and PUT it — which made the one field that distinguishes *the agent inferred this*
from *a person checked this* forgeable by whoever was holding the keyboard.

`app/kinde.py` mirrors the shape of Futuros' `app/core/kinde.py`: client_credentials
token with a locked check-and-refresh, best-effort reads that degrade to the raw `sub`
rather than failing the request, and blank credentials disabling the client so local
dev works without a Kinde tenant. Env: `KINDE_M2M_CLIENT_ID`, `KINDE_M2M_CLIENT_SECRET`,
optional `KINDE_MANAGEMENT_AUDIENCE` (defaults to `{issuer}/api` — for custom-domain
tenants the audience stays the canonical `*.kinde.com/api`).

### 3.3b Kinde configuration follows the house keys exactly

**Corrected 2026-08-22** after Ruben supplied the canonical env blocks from iclonic.
Mindkeep had invented its own names; it now uses the house ones.

Backend: `KINDE_ISSUER` (tenant root, pinned as the JWT `iss`), `KINDE_M2M_CLIENT_ID`,
`KINDE_M2M_CLIENT_SECRET`, `KINDE_MANAGEMENT_API_AUDIENCE` (custom domains only —
otherwise derived as `{issuer}/api`). Frontend: `VITE_KINDE_DOMAIN`,
`VITE_KINDE_CLIENT_ID`, `VITE_KINDE_REDIRECT_URI`, `VITE_KINDE_LOGOUT_URI`, the last
two defaulting to the app origin and needing to appear in Kinde's allowed-URL lists.

**No audience is verified, and there is no `KINDE_AUDIENCE`.** Futuros'
`app/core/security.py` states the reason: *Kinde access tokens carry no meaningful
audience*. Verification is signature, issuer and expiry — `verify_aud: False` plus
`require: ["exp", "sub", "iss"]`. Mindkeep had been checking an audience and had the
JWKS URL wrong as well (`/.well-known/jwks.json`; the house and Kinde use
`/.well-known/jwks`), which would have failed every request in production.

**Built and then removed on instruction:** an `AUTH_DISABLED` backend switch and a
matching frontend path where a blank `VITE_KINDE_CLIENT_ID` ran the app without login.
Both follow house precedent (Futuros has `auth_disabled` with a `user_local_dev`
identity), but Ruben decided against them for Mindkeep. The consequence, stated plainly:
there is now no way to run the app locally without a working Kinde tenant.

### 3.3c Registration is ALL-ORGS

**Decided:** every registration creates a Kinde organization, as in Futuros and iclonic.
The query string picks which kind, matching `ProtectedRoute.tsx` in Futuros:

- `/?account_type=org&org_name=Acme` — a real team-to-be
- `/?account_type=user` — a personal org named "Personal"
- `/?invitation_code=…` — joins the *inviter's* org and never creates one; wins over
  everything else
- no parameter — the ordinary sign-in screen

Not carried over: `plan_interest` and `pricing_table_key` (Mindkeep has no billing),
`lang` (no i18n), and the `LOGOUT_IN_PROGRESS` guard. That last one exists in Futuros
because `ProtectedRoute` auto-calls `login()` whenever unauthenticated and therefore
races its own logout redirect; Mindkeep shows a manual **Sign in** button, so there is
nothing to race. If it ever auto-logs-in, that guard has to come with it.

**Worth knowing: the org is currently inert.** Tenancy keys on the Kinde `sub`
(`/data/{sub}/`) and nothing reads `org_code`, so registering as an org changes nothing
about where a wiki lives. It is the hook the team story needs — the earlier decision was
that a team is its own tenant rather than a shared bundle inside a personal repo, and
`org_code` is the natural tenant id for that. Open question for when teams arrive:
whether a personal org and a team org are the same tenant shape. Futuros says yes — the
plan code separates them, not the data model.

### 3.4 A tenant holds many bundles

**Decided:** the hierarchy is tenant → bundle → OKF bundle contents. Each
bundle is self-contained, with its own `CLAUDE.md`, so a work wiki and a
personal wiki can use different taxonomies. New tenants are auto-seeded with a
bundle named `default`.

**Why:** one person wants work, personal and per-client knowledge separated,
and the OKF bundle is the natural unit of separation.

**Binds:** bundles are the natural sharing unit when teams arrive, so any
ownership model should attach to a bundle or a tenant — never be implied by a
parent directory the way it currently is.

### 3.5 The cloud LLM is the only writer

**Decided:** a Claude Opus 5 agent in the backend is the sole writer of `wiki/`.
Local mirrors are read-only apart from dropping files into `raw/`. Uploads and
connectors write only to `raw/`, which is immutable.

**Why:** Ruben's stated reason — team-shared knowledge bases are coming, and
multiple writers would corrupt a shared wiki. This reverses an earlier plan in
which Claude Code, pointed at a synced folder, would have done the ingestion
locally and the backend would have run no LLM at all.

**Provenance sidecars were dropped 2026-08-22.** Uploads used to get a companion
`.md` holding `type: Source` and an upload timestamp. Ruben asked what it was for, and
the honest answer was: not much. Wiki pages cite sources through their own `sources:`
frontmatter, which needs no sidecar; OKF requires nothing of a PDF; and this wiki's own
convention is that a source's record is the **summary page the agent writes**, not a
mechanical file written before anything has read the document. The one real loss was the
original filename, and the fix for that was to stop mangling it — the upload sanitiser
now keeps spaces and brackets, so `Believe what repeats.md` stays itself. Nothing
records *who* uploaded a file now, but nothing did before either (the sidecar said
`process:upload`). This also removed the `.md.md` double-extension wart.

**Binds:** immutability of `raw/` is enforced in code, not just in prose —
the LLM's `write_file` tool refuses it with a message it can read, and
`PUT /files` returns 409. Ingests are serialized per bundle by a lock.

**Known ceiling:** the lock is in-process, so it holds for one worker only. A
second Railway replica requires `pg_advisory_lock(hashtext(tenant))` before it
is safe to scale out.

### 3.5b Two halves, one owner each

**Corrected 2026-08-22.** The model had drifted into something incoherent: `raw/` was
immutable and `wiki/` was writable through the API, so a user could edit an agent's page
but not correct their own upload. Ruben set it straight — **the user does CRUD on `raw/`,
the agent does CRUD on `wiki/`**, and the halves are exclusive.

What changed: `PUT /files/{path}` now accepts `raw/` and refuses `wiki/` (it did the
opposite); `DELETE /bundles/{b}/raw/{filename}` was added; and the agent gained a
`delete_file` tool restricted to everything except `raw/`. The predicate `writable()`
split into `agent_owns()` and `user_owns()`, because one boolean read backwards at half
its call sites.

**Deleting a source deliberately orphans pages, and that is the lint pass's job.** The
manual now tells the agent to fix orphans rather than only report them: delete a page
whose only source is gone, trim the claims when one source of several disappears, and
clean up links and `index.md` either way. Blocking a delete to protect a citation would
be the wrong trade — it is the user's material.

**The one deliberate exception:** `POST /bundles/{b}/verify/{path}` writes into `wiki/`
on a human's behalf. It stays because the server composes the stamp from the verified
token, which is the whole reason that endpoint exists (§3.3).

**Side effect worth noting:** the test suite could no longer use `PUT` to create wiki
pages, so it now seeds them on disk. That is more honest — it exercises the same path the
agent uses, rather than a route users are not allowed to take.

### 3.6 Local sync is a small HTTP client — git was tried and removed

**Decided:** the wiki syncs to a laptop over the ordinary API. `GET /bundles/{b}/tree`
returns a map of path to SHA-256; the client downloads whatever differs. Uploads go to
`POST /bundles/{b}/raw/{filename}`. **Git is gone entirely** — not demoted, removed:
`app/gitsync.py`, the HTTP serving route, the per-write commits, and git in the Docker
image.

**How it got here, because the reversal is the interesting part.** Git looked right at
first, and the argument was real: history for free, incremental transfer, and — the
decisive one — **no client to write, install or update**, since `git clone` is already on
every machine. It was built and verified end to end with a real clone and pull.

It came apart in two steps. First, the write direction turned out not to need git at all:
the only thing a client may write is `raw/`, and adding a raw document is a file upload,
not a merge. That deleted the whole push apparatus — a file-less repo, the real push
protocol, a hook to enforce the split, a second service, the backend becoming a git
client. Then the read direction fell too, once Ruben asked whether git was needed there
either: the moment a client exists for uploads, adding `pull` to it costs almost nothing —
and "no client to write" was git's entire remaining advantage.

**What was given up:** `git log` and `git diff` over your own wiki, and cheap per-ingest
undo. The first matters less than it looks — `log.md` is maintained by the agent on every
ingest because the format calls for it, so the change history exists in the product
already. The second is a real loss, now covered only by Railway volume snapshots, which
are coarser. If undo becomes important, the answer is server-side git that nobody has to
know about, not git as the transport.

**The general lesson worth keeping:** the argument for git was "the client is free". Once
something else forced a client into existence, that argument was worth nothing, and
keeping git would have been paying for a benefit already spent.

### 3.7 The client

**Decided:** `client/mindkeep.py` — one file, standard library only, three commands:
`login` (once: server, token, folder, bundle), `sync` (down then up), `watch` (sync every
30 seconds). You log in, choose where to save, it downloads the wiki, and you point Claude
Desktop at that folder. Drop a file into its `raw/` folder and it uploads automatically.
Uploading while away from the machine goes through the website, which already does it.

Stdlib-only is deliberate: `python mindkeep.py` runs with nothing installed.

The sync folder is a **mirror**: after uploads run, anything local that is not in
the server's tree has genuinely been deleted, so the client removes it. Keep your
own files somewhere else.

**Rejected:** a packaged desktop app with an installer and an update channel; a file-watch
library (a 30-second poll needs no dependency).

**Rejected — rsync and rclone**, considered 2026-08-22. rsync needs SSH or its own
daemon, which reintroduces the second transport and second credential that sank git
push, and it is absent on Windows. rclone is the serious contender — one static binary,
speaks WebDAV, real checksum sync — but it would cost a `PROPFIND` implementation on the
server (more code than the whole client), `rclone config` on every machine, and, most
importantly, it would make the write split a *filter setting* rather than a structural
fact: rclone would happily push `wiki/` back, and only a correctly-written rule would
stop it. Today no API accepts a client write to `wiki/` at all. The one thing they had
over the client — deletion propagation — turned out to be three lines.

### 3.8 Device tokens

**Decided:** a user's token is `sub.HMAC-SHA256(DEVICE_SECRET, sub)` — the token names its
own owner, so the server recomputes and compares, storing nothing. `GET /device-token`
hands a user theirs to paste into the client once. `current_user` accepts either a Kinde
JWT (browser — a JWT has two dots) or a device token (client — one dot), so the same API
serves both.

**Why not Kinde's own API keys**, which fit the requirement exactly (long-lived,
user-level, hashed at rest, self-serve revocation): they need a paid Kinde plan at
**$25/mo per business**, and only Futuros is on one — Mindkeep would be a fourth to pay
for. Ruben's explicit call: breaking the house authentication convention is acceptable
while Mindkeep is internal-only. **If it ever becomes a paid product, upgrade to the paid
Kinde subscription and use their API keys feature** rather than building a token table.

**Known ceiling:** revoking one person means rotating `DEVICE_SECRET`, which revokes
everyone. Acceptable for a small internal team.

**Tension worth recording:** the house decision "Kinde owns identity and billing" lists
*no credentials in the backend* as its headline benefit, and a single secret from which
every user's token derives is a concentrated target. Narrower than what that decision was
avoiding — read access to your own wiki, not passwords or card data — but a real
deviation, accepted knowingly.

### 3.9 Two-way sync, and why it stopped being a problem

**Where it landed:** sync is two-way, and it needed none of the machinery that
made it look hard.

The original objection assumed both sides write the same files — which is where
git's answer, conflict markers left in the file for a human to resolve, becomes
actively dangerous here, since the next reader is an agent that would ingest the
markers as content and cite them. Ruben pointed out the objection did not apply:
**the agent only ever writes `wiki/`, the client only ever writes `raw/`.**
Disjoint sets never collide.

Once the directions are disjoint, "push" means "add a raw document", which is a
file upload rather than a merge. The write path is one HTTP POST, and the split
is enforced structurally — the API exposes no way for a client to write `wiki/`
at all, so there is no rule for anyone to remember and no server-side hook needed
to police it.

**Still true, and worth not losing:** editing a wiki page locally and having that
stick is a different and much larger problem — it needs a merge policy and a
decision about what happens when the agent wants to rewrite the same page. Not
built. The way to correct the wiki is to add a source saying so, which gets cited
and logged like anything else.

### 3.10 Scheduled work is a thread, not a cron service and not a broker

**Decided:** the nightly lint runs in a daemon thread inside the app. No Celery,
no Redis, and — revising the earlier decision — no separate Railway cron service
either.

**Why:** a broker is infrastructure to run, monitor and pay for, bought against a
problem that does not exist. A cron service is cheaper but still a second
deployment that needs its own image, its own env vars and its own auth back into
the API, to fire one HTTP call a day. The app is a single process on a single
service; a thread that wakes every fifteen minutes and asks "has this bundle been
linted today?" is the whole scheduler, in forty lines.

**What makes it safe to be that simple:** the answer comes from the run history in
Postgres, not from a timer's memory. So a restart at 02:59 does not double-lint,
and a server that was down all night lints when it comes back. `LINT_HOUR` (UTC,
default 3) sets the hour; anything outside 0–23 disables the pass entirely.

**Ceiling, named in the code:** one process. Two backend replicas would each run
the sweep and race for the same bundle. The upgrade is a row lock or an advisory
lock on the bundle, not a broker — and it is not needed until there is a second
replica.

**A second correction, found by the clock crossing midnight UTC mid-session.** `due()`
read `started_at.astimezone(UTC)`, which is right for an aware value and wrong for a naive
one: `astimezone` reads a naive datetime as *local* time. SQLite returns naive values, so
at 00:08 UTC on a UTC+2 machine a lint recorded minutes earlier was dated to the previous
day, the bundle read as due, and it linted twice. `runs.utc()` existed for exactly this and
was used everywhere except here. The tests caught it only because the session happened to
run past midnight — worth remembering that a date comparison is a time-dependent test, and
passes all day for the wrong reason.

**"Once a day" needed one correction, found by exercising the real sweep against the
real database with the agent stubbed out.** The first version asked "did a lint
*start* today?", which counts a run killed by a deploy at 02:59 as the night's pass.
It now asks "did a lint *finish cleanly* today?", so an interrupted or failed lint
leaves the bundle still due. The cost of that is a lint failing for a real reason —
no credit, say — retrying on each tick of its hour, four times a night at worst.
Worth it while failures are rare; the note in the code says to add a backoff if they
stop being.

**The hour is UTC and the UI says so in your own time.** `GET /bundles/{name}/lint`
returns `next` as an ISO-8601 instant, and the browser renders it — "today at 05:00",
"tomorrow at 05:00" — because the server has no idea where anyone is and a laptop
that crosses a timezone should not silently move when the wiki gets tidied. It is
also the honest answer to "is this thing actually going to run?", which a label
reading *every night* never was.

A lint is not a special kind of job: it is the same agent, with the same tools and
the same manual, given a different instruction (`LINT_TASK` instead of
`INGEST_TASK`). It records a run row like any ingest, and is serialised by the same
per-bundle lock, so a lint and an ingest can never write the wiki at once. The
manual's Lint section is the actual specification — report contradictions, orphaned
links, stale drafts and missing pages; *fix* only the orphans left by a deleted
source. The button is `POST /bundles/{name}/lint`, refused with a 409 while one is
already running.

## 4. What is built

**Backend** — FastAPI, Python 3.13, ruff + mypy strict, pytest.

| Endpoint | Purpose |
|---|---|
| `GET /health` | liveness |
| `GET/POST /bundles` | list and create bundles |
| `GET/PUT /bundles/{name}/files/{path}` | read and write a file |
| `GET /bundles/{name}/tree` | path to SHA-256 for every file — what the client diffs |
| `POST /bundles/{name}/raw/{filename}` | add a raw document, triggers ingest |
| `GET/PUT/POST /bundles/{name}/lint` | state and schedule; set the hour; run one now |
| `GET/POST/DELETE /bundles/{name}/folders/…` | folders under raw/, empty ones included |
| `POST /bundles/{name}/move` | move a source within raw/ |
| `GET /me` | the signed-in profile, composed from Kinde |
| `GET/POST /bundles/{name}/todos` | the open questions; tick one off |
| `POST /bundles/{name}/assist` | one turn with the assistant |
| `GET /device-token` | the token a user pastes into the client |

Modules: `auth.py` (Kinde JWKS plus device tokens), `files.py` (bundles, tree,
read/write, upload), `ingest.py` (the Claude agent), `db.py` + `runs.py` (the run
history), `schedule.py` (the nightly lint), `kinde.py` (the Management API).

**Client** — `client/mindkeep.py`, one standard-library file: `login`, `sync`,
`watch`.

**The ingest agent** uses the Anthropic SDK's `tool_runner`, which owns the
agentic loop, with three tools: `read_file`, `write_file`, `list_files`. The
tenant's own `CLAUDE.md` is passed as the system prompt, so the operating manual
and the agent's instructions are the same file — editing the manual changes
behaviour, with no code deploy.

**Isolation** is enforced by `safe_path()`: resolve, then require the result to
be under the bundle root. Tested against literal traversal, percent-encoded
traversal, absolute paths and cross-tenant reads.

**Frontend** — Vite + React + TS, Vitest and ESLint, talking to the backend
through a Vite dev proxy rather than CORS (one origin, nothing to configure;
`VITE_PROXY_TARGET` overrides the docker hostname for local runs).

Two tabs, built from the design canvas in the bone-and-clay language: **Library**
(tree, page, provenance rail) and **Console** (activity, counts, device token).
The design's third tab, **Ask**, was cut on Ruben's instruction to keep the first
cut minimal — the endpoint that would back it was written and then reverted in
the same session, so nothing speculative remains.

Notable pieces: `okf.ts` parses and re-serialises OKF frontmatter, so *Mark as
verified* appends a `verified:` line without disturbing the fields around it;
markdown is rendered through `marked` and sanitised with `DOMPurify`, because a
wiki page is agent-written and may quote a raw source verbatim. The Console's
activity feed is parsed from `log.md` — the agent's own record, which is why no
events table exists.

**Browser credential:** the UI currently authenticates with the same device
token as the client, pasted once and kept in `localStorage`. Kinde's React SDK is
the house convention and the intended replacement; the API already accepts either
credential, so that swap touches no server code.

**Infrastructure** — Docker Compose with Postgres 17, backend and frontend.
Host ports 5433 / 8001 / 5163.

### 4.1 Ingest state was derived, and that was wrong — REVISED 2026-08-23

**What follows is the original entry, kept because the reasoning failed in an
instructive way. The decision was reversed once folders shipped; see §4.10.**

### 4.1 (superseded) Ingest state is derived, not stored

`GET /bundles/{b}/sources` returns each raw source with `ingesting` and `pages`.
**"Ingested" is not a flag anyone sets — it is how many wiki pages cite the source.** A
status column would drift out of step with the files; a count derived from them cannot.
Sources currently in flight come from an in-memory set, which is honest: a restart forgets
them because a restart also kills the ingest.

The UI polls this every 2s while the agent is working and every 8s when it is not, and
reloads the file tree and the activity log whenever an ingest finishes. Before this
nothing on the server knew an ingest was happening, so the UI could not say — the symptom
Ruben reported was "hard to know what's going on at any given time".

A manual re-ingest endpoint and button were built and then removed at his instruction:
"it shouldn't be necessary if the system works well". The endpoint stays as a recovery
path; nothing in the UI offers it.

### 4.2 Reading a source is not the same as reading its bytes

A `.docx` uploaded on 2026-08-22 sent the agent into a slow, expensive loop: `read_file`
used `errors="replace"`, so it handed back 137KB of mojibake for what is really a zip of
XML. The agent could not tell it had failed, so it kept guessing, one API call at a time.

Now `readable_text()` decodes strictly — a `UnicodeDecodeError` means binary — and
`.docx` is extracted with `zipfile` plus a regex, no new dependency (39k characters of
real text out of the Futuros MSA). Anything genuinely binary returns *"not readable as
text… Do not guess at its contents. Note it in log.md and stop."* **PDFs are still
refused**, deliberately: they need a real parser (`pypdf`, or sending the file to the API
as a document block, which also handles scans), and that is a decision rather than a
stdlib trick.

The general lesson: a lossy read that *looks* like success is worse than a failure. The
agent had no way to distinguish a corrupt document from a badly written one.

### 4.3 The log is a timeline, not a report

Ingest entries were arriving as multi-paragraph essays, which made the activity feed
unreadable. Two fixes, because it had two causes: the manual now asks for **one line** in
`log.md` with the reasoning left on the pages it justifies, and the feed shows only the
first line with the rest behind a `more` toggle — rendered as markdown, since that is what
the agent writes.

### 4.4 Making ingest fast: three bottlenecks, measured not guessed

Ingests took four to six minutes and it was never obvious why. Logging each tool call
turned opinion into measurement, and the bottleneck moved twice — each time because of a
fix for the previous one.

1. **Output-bound.** `write_file` was the only mutation, so touching a page regenerated
   it whole. Pages grew on every touch (`resonaut.md` went 11,041 to 13,870 chars in one
   run), which made the next ingest slower. Measured at ~120 characters per second of
   wall clock: duration was simply *characters written ÷ 120*.
   Fixes: an `edit_file` tool, and a 400-word page cap in the manual.
2. **Round-trip-bound.** `edit_file` took a single replacement, so 21 edits became 21
   turns — 37 turns, 4m 3s — each resending the whole conversation. A fix for problem one
   created problem two. Fix: `edit_file` takes a *list* of edits, applied atomically.
   Result: 37 turns to 7, 4m 3s to 2m 6s.
3. **Model-bound.** What remained was ~18s per turn of the model composing considered
   text. Nothing structural left. Switched ingest to **claude-sonnet-5** with adaptive
   thinking still on.

**Why thinking stays on.** Ingest looks mechanical but the valuable half is judgement:
deciding what a source genuinely changes against a dozen existing pages, and catching a
claim that contradicts one (a run spotted that an 85% figure sat at the bottom of a
published 85–92% band and was measured on a different task). That is what the wiki is
for. A smaller model with thinking beats a larger one without.

**The regression signal, so this is falsifiable:** if ingests stop flagging
contradictions and merely summarise politely, Sonnet is too small and the model goes back
to `claude-opus-5`. That is visible in `log.md`, not a matter of taste.

### 4.5 Postgres holds metadata *about* files, never the files

**Decided:** every agent run — ingest or lint — opens a row in an `ingest_runs`
table and closes it when it finishes: tenant, bundle, source, start, end, seconds,
turns, characters written, model, error. Durations, status and errors are read from
there. None of it goes in the frontmatter.

**Why not frontmatter:** "this took 2m 6s" and "this run died" are facts about an
*operation*, not about the knowledge. Writing them into a page would mean the agent
rewriting a file to record how long it took to write that file, and would put
machine bookkeeping into a format meant to be read by humans and other OKF tools.

**Why not memory:** the first version tracked live runs in a dict. A deploy or a
`--reload` erased it, so a run killed mid-flight became indistinguishable from one
that never started, and every duration vanished on restart. Now `sweep_interrupted()`
runs at startup and closes anything still open as *interrupted* — a state the UI can
show, rather than a spinner that never stops.

**This does not contradict §3.1.** The wiki is still files; there is still no
`documents` table and nothing queries Postgres to read a page. The rule that came
out of it: **if deleting the row would lose knowledge, it belongs in a file; if
deleting the row would only lose bookkeeping, it belongs in Postgres.**

**What it bought immediately:** the Console shows the real duration of each run,
the nightly lint knows whether it has already run today without keeping a timer
alive, and a second lint can be refused while one is open — all three read the same
table.

**And then a `note` column, for the same reason one step further.** The first real
lint ran five and a half minutes and showed a spinner the whole time — a duration
you can only read *after* the thing you are worried about has finished. Now every
tool call writes one line to the run row ("`14 · reading wiki/concepts/x.md`") and
the UI shows it live, alongside the turn number, which is also written as the run
goes rather than only at the end. One small UPDATE per tool call, a few a minute,
against a row nobody else reads.

The general shape: **the same table answers "how did it go?" and "how is it going?",
and the second question is the one people actually ask.**

### 4.6 The agent had no clock, so it invented one

**Found by reading `log.md` after that first lint:** entries dated `2026-09-15`,
weeks in the future, on a wiki whose oldest entry is `2026-08-22`. Nineteen entries,
almost all fabricated. The manual asks for `## [YYYY-MM-DD]` headings and nothing
ever told the model what day it was, so it guessed, and each guess drifted further
forward from the last one it could see.

Three things this broke, none of them obvious from the code:

- The Console sorts newest-first *on that date*, so the ordering was fiction.
- "Stale" and `stale_after` are date comparisons the agent makes against its own
  invented today.
- A future-dated entry can never be corrected by a later run, which will read it and
  anchor its own guess even further out.

**Fix:** `"Today is {today}."` at the front of both task prompts — one line. Plus a
lint rule to report entries dated after today, correcting a heading only where the
entry's own text pins it to a real day. The existing nineteen stay wrong until a
lint gets to them; they are the agent's file, not mine to rewrite by hand.

**The lesson worth keeping:** a model with no clock will not say "I don't know what
day it is". It will produce a plausible date, and plausible is indistinguishable from
correct until you count them. Anything the agent must state as fact but cannot observe
— the date, the tenant, its own model name — belongs in the prompt, not in its head.

### 4.7 The manual is code, and now it is deployed like code

Editing `app/templates/CLAUDE.md` used to change nothing for anyone: it is copied
into a bundle at seed time, so every existing bundle ran forever on the manual it was
born with. The new lint rule would have reached exactly zero real bundles.

Now each run refreshes `CLAUDE.md` from the template if it differs — at the start of
the run, which is the only moment it is read. The manual says so about itself, since
`agent_owns()` would otherwise let the agent edit a file whose edits cannot survive.

The rule that falls out: **if it ships in the image, it is code, and it needs a path
to every copy already out there.** Seeding is not deployment.

### 4.7b Migrations run at startup, because a fresh volume is not an edge case

Deleting the Postgres volume left every request failing on `relation "ingest_run" does
not exist` — the app came up happily and then broke on the first read, because nothing
ran Alembic. Uploads still landed on disk; only the run row failed, so a source could be
uploaded and silently never ingested.

The app now runs `alembic upgrade head` in its lifespan, before the interrupted-run sweep
that reads the same tables. Verified by dropping all three tables and restarting: they
come back.

**ponytail ceiling, named in the code:** this is safe with one replica starting at a
time. Two replicas would race here, and the fix then is a Railway release command, not
a lock.

The general point, which is the same one as §4.7: **shipping the schema is not the same
as applying it.** A migration nobody runs is a migration that does not exist, and the
failure surfaces as a puzzling runtime error rather than as a deployment step that
obviously did not happen.

### 4.7c An empty directory cannot be downloaded

`GET /tree` is a map of path to hash, so it carries files and only files. A bundle's
`raw/` and `wiki/` exist on the server from the moment it is seeded, but an empty one
has nothing to carry it down, so it never appeared in the mirror — and the sync's own
tidy-up then deleted the `raw/` folder that `login` had just created. A fresh mirror
had nowhere to drop a file, which is the client's entire purpose.

Fixed in the client rather than the protocol: `LAYOUT = ("raw", "wiki")` is recreated
after every sweep and exempt from it. The client already knew the layout — the upload
pass walks `raw/` by name — so this adds no coupling that was not there.

**Rejected:** putting directories in the tree. They would need a sentinel hash and every
consumer would have to special-case entries that are not files, to express something the
client already knows.

### 4.7d Folders in `raw/`, because the half that is not ours should not be flat

`raw/` is the owner's half of the bundle (§3.5b), and someone with two hundred sources
will want them in folders. Uploads used to take a single path segment — a slash, encoded
or not, was rejected outright.

Now the upload, delete and re-ingest routes take a full path, sanitised one segment at a
time: each is run through the same filename filter, and `.` and `..` strip to nothing and
are dropped. `UNSAFE_NAME` cannot emit a separator, so no segment can invent one, and
`safe_path` still checks the assembled result. Two guards on a path that came from
outside is not one too many.

**The three places a folder has to survive**, all of which had to change together:
uploading (web and client), listing (`iterdir` → `rglob`), and the desktop client's
upload pass (`glob` → `rglob`, sending the relative path rather than the bare name).

**Deleting the last file in a folder removes the folder.** Otherwise every synced copy
accumulates empty directories nobody can delete through the API.

**Tested by where the bytes land, not by the status code.** An encoded `..` reaches the
handler and is stripped there; a literal one is normalised away by the URL before routing
and never arrives. Both outcomes are fine and they return different statuses, so the
assertion that actually means something is "the uploaded bytes are somewhere under
`raw/`".

### 4.7e Moving a source is free, and the lint is told what moved

Folders are only useful if things can be put in them, so `raw/` gained folder create and
delete routes and a move route. Moving is where it gets interesting: pages cite sources
by path, so every move breaks a citation, and the server will not edit a wiki page.

**First attempt, wrong:** fire an agent run per move with a "this moved, fix your links"
prompt. Correct, but it makes reorganising a folder of thirty sources cost thirty agent
runs — the same price as ingesting them. Ruben stopped it: *"maybe that is the job of the
linting"*. It is. The lint already exists, already runs nightly, and already owns broken
source links.

**But a lint that only sees the filesystem cannot tell a move from a deletion** — old path
gone, new path present, and its existing rule says "source gone, delete the page". So it
would delete a page and then re-summarise the same document from scratch. The manual now
splits that decision explicitly: a file of the same name elsewhere under `raw/` **moved**
(repoint the citations, change nothing else); nothing matching means it is **gone** (the
old orphan rule).

**Then Ruben asked the better question:** why make it rescan at all, when the server did
the moving? So a `source_move` row records old path, new path and time, and the lint's
prompt carries the list. Three details that make the row worth having:

- **Chains collapse.** `a.md` to `b.md` to `c.md` leaves one row, `a.md → c.md`, because
  that is the only fact a page citing `a.md` needs. A file moved back where it started
  leaves no row at all.
- **Settled only on success.** A lint that dies mid-run must not swallow the hints it
  never acted on, exactly as an interrupted lint does not count as the day's pass (§3.10).
- **A deleted source drops its pending move.** There is nowhere left to repoint to.

**Where the controls live decides what they can do.** The first pass put "new folder"
next to the upload button and a folder dropdown in the details rail. Both were wrong for
the same reason: a control detached from a folder can only ever act on one, so there was
no way to make a *sub*folder, and moving meant selecting a file, then finding a dropdown
somewhere else. The add-folder button is now a `+` on every folder row in `raw/`, and
moving is dragging a source onto a folder. The rule that generalises: **a control that
takes a target belongs on the target.**

Two details that only show up once it is built: the folder row had to stop being a single
`<button>`, since the add control cannot nest inside the toggle and the drop target should
be the whole row rather than the text; and `dragleave` fires when the pointer crosses into
a child, so the drop highlight flickers between the label and the `+` unless the handler
checks whether the pointer is still inside.

**The whole-collection guard was refusing a legitimate delete.** Emptying `raw/` locally
put every source back on the next sync, while deleting all but one worked fine — the guard
fires only when *every* remembered source is missing, and it cannot tell "I deleted them"
from "the drive did not mount".

The signal it was missing is the rest of the mirror. `CLAUDE.md`, `index.md` and `wiki/`
belong to the agent and nobody empties those by hand, so if they are still on disk the
folder is plainly there and an empty `raw/` was meant. Checking `raw/` itself would not
work, because sync recreates it: after the folder was moved away, the freshly made empty
`raw/` would read as a deliberate emptying and delete everything on the server.

Both branches verified against the real server — the mirror deleted outright refuses and
touches nothing; `raw/` emptied with the manual left in place deletes all three sources.
The refusal now also says which folder it could not find, rather than asking a question
and leaving.

**Empty folders had to be taught to the client separately**, for the same reason they
needed their own endpoint: no file carries one. The state file now remembers the server's
folder list alongside its file hashes, which makes the three-way question answerable for
folders too — a folder here and not there is either one you made or one someone else
deleted, and guessing wrong either resurrects it forever or throws it away. Files inside a
deleted folder always propagated, because they are files; it was only ever the empty case.

**Every source control ended up on the folder it acts on.** The sidebar's "Add sources"
button and its drop hint are gone; each `raw/` folder row carries its own pair, revealed
on hover, to the right of the file count: import files into *this* folder, and make a
folder inside *this* folder. Same reasoning as the move control (§4.7e) — a control that
takes a target belongs on the target — but followed all the way, so there is no longer a
global "add" that can only ever mean the root.

One implementation note worth keeping: the folder being filled is held in a ref, not
state. The file dialog outlives the render that opened it, so the change handler has to
read what was chosen at click time rather than whatever the last render captured. The
input's value is also cleared after each use, or picking the same file twice fires no
event at all.

**One picker for files and folders is not something a browser will do.** `webkitdirectory`
turns the dialog into a folder-only picker and there is no flag that makes one dialog take
either. A drop target has no such split, so dropping is the single gesture: files and
folders both, onto any folder row in `raw/`, which picks the destination in the same
motion. A dropped folder arrives as a tree of `FileSystemEntry` objects rather than a list
of files and has to be walked — and `readEntries` returns at most a hundred at a time, so
the walk loops until it gets an empty batch. The button stays as the fallback for people
who would rather click.

**The client pairs its own renames back up.** A local rename reaches a hash diff as a
delete plus an upload, and sending it as those two loses the fact that matters. So before
issuing any deletion, sync matches each disappeared path's hash against the files that
have appeared, and calls the move route for the pairs. Two consequences, one of them a
bug fixed by accident: reorganising every source into a folder used to trip the
whole-collection guard ("all N sources are missing locally") — now they are recognised as
moves before anything is counted as missing.

**Not a file watcher, and it should not be one.** `mindkeep watch` is a thirty-second
poll, not an OS event feed. A real watcher would report renames directly, but it needs a
dependency in a deliberately stdlib-only client, and it is blind to anything done while
it was not running — which is most reorganising. Hash matching costs a dozen lines, needs
nothing, and catches a rename made with the laptop shut.

**The scan rule stays as the backstop anyway.** Identical files could pair the wrong way
round, and a move made anywhere but these two paths has no row at all, so the prompt says
the list may be incomplete rather than letting the agent trust it as final.

### 4.10 "Ingested" is recorded, not inferred — reversing §4.1

Moving a source made it read as **not ingested**, in the tree and in the details rail.
Nothing had gone wrong with the ingest: `pages_citing()` counted wiki pages containing
the source's path, and after a move no page contained the new one yet.

The first fix was to count through the old path as well, using the move rows from §4.7e.
It worked, and it was wrong — patching a proxy to keep agreeing with the thing it was
standing in for. Ruben called it: *"the ingested state shouldn't be determined by if
pages reference it, it should be saved to the DB after successful ingestion."*

**§4.1 argued the opposite** — that a derived state "cannot drift out of step with
reality, which a status column would". The flaw is in the word *reality*. What was
derived was not whether the source had been ingested; it was whether any page currently
names that path, which is a different question that usually gives the same answer. It
gives the wrong one in both directions: a source the agent read and correctly decided
not to write about looks unread, and a moved source looks like it was never touched.

**What it is now:** a source is ingested if the run history holds a run for it that
finished with no error. No new column — `ingest_run` already recorded exactly this and
was only ever being read for durations. The one thing that had to change is that a
source's history follows it: a move repoints its rows, so the record stays attached to
the document rather than to where the document happens to be filed.

**And this is the honest version of the §4.1 worry, which was not baseless.** A stored
state can go stale, and it did, immediately: the four sources Ruben had already moved
were stranded, because the move route learned to repoint history only after he had used
it. That needed a one-off repair, driven from the `source_move` rows the server had
recorded anyway. Stored state needs migrating when its meaning changes; derived state
does not. That is the real trade, rather than the one §4.1 claimed.

**`pages` survives as a number, not a status.** It still says how many pages cite the
file right now, which after a move is zero until the lint — true, and no longer mistaken
for "never read". The `moved` flag lets the rail say *links update at the next lint*
instead of showing a bare zero.

### 4.11 A bundle's shape is not its content

Deleting every source from the web left the Library with no `raw/` folder and no `wiki/`
folder — and therefore nowhere to drop a file and no `+` to make a folder, exactly when
you most need both. Nothing had been lost: the directories were still on the volume. The
tree is built from file paths, and a directory holding no files contributes none.

Fixed on both sides, because they fail differently:

- **The web tree always draws both halves**, however empty. They are the bundle's shape,
  not a summary of what happens to be in it.
- **The server asserts they exist on every bundle access.** Two `mkdir(exist_ok=True)`
  calls, which is cheaper than auditing every path that can delete something and trusting
  the audit to stay true. It also heals a bundle whose folders were removed by an older
  client or by hand — verified by deleting `raw/` on the volume behind the app's back and
  watching the next request put it back.

The general rule, which the client had already learned the hard way with `LAYOUT`: **an
empty container is a fact about the design, not a gap in the data.** Anything that derives
structure from content will delete it the moment the content is gone, and it will do it at
the worst possible time.

### 4.12 The dev server was not reloading, and nothing said so

Vite in the container never picked up an edit made on the Windows host: Docker Desktop's
bind mount does not forward the host's filesystem events, so chokidar's inotify watch sat
silent. `server.watch.usePolling` fixes it, at the cost of some idle CPU — the going rate
for editing on one OS and serving from another.

**What made it hard to see** is that the failure is silent in both directions. Nothing
errors; the dev server starts normally and serves pages. And a full refresh often *does*
show new code, because Vite re-transforms on request — so it looks like HMR is merely
slow rather than absent, until an edit that should have been picked up plainly is not.

**And it nearly produced a wrong conclusion.** Testing it, the log showed an `hmr update`
line with polling switched off, which said the watcher was fine. It was a stale line from
before the container restart, caught by a `--since 1m` filter whose window reached back
past it. Reading the actual timestamps against the restart banner showed nothing after the
restart at all. The lesson is narrow and worth keeping: **when a test's evidence is log
lines, the restart boundary is part of the evidence** — a count is not a reading.

The check that finally settled it needed a browser attached. Vite logs an `hmr update`
only when a client is connected to push it to, so the first few probes with no page open
proved nothing either way.

### 4.13 Forty-four files at once took the whole API down

Copying 44 sources into the synced folder left the UI frozen for half a minute before it
showed any activity. The run history said exactly what happened: **39 runs open, one with
a `note`, 37 with none at all** — thirty-seven ingests that had recorded a start row and
then never executed a single tool call.

The cause is a shape mistake, not a slow ingest. Every upload became a Starlette
`BackgroundTask`, and `ingest_safely` is a sync function, so each one ran in the anyio
threadpool. They serialise on the per-bundle lock — but a task blocked on a lock **holds
its thread for the entire wait**. The pool defaults to 40. Sync route handlers come from
that same pool, and nearly every route here is sync, so once the uploads had taken all
forty threads there was nothing left to answer `/sources` or `/tree` with. The server was
not busy; it was fully occupied doing nothing.

**Fix:** one worker thread per bundle, fed by a queue. Uploads enqueue and return; waiting
now costs a queue slot rather than a thread. `BackgroundTasks` is gone from the ingest
paths entirely, and the scheduler hands lints to the same queue, so a nightly lint takes
its turn behind whatever is already waiting instead of competing for the lock.

**The incident also showed the recovery path was missing.** Every restart — including the
`--reload` that fires on any backend edit — marked in-flight runs interrupted and dropped
them. A source could be uploaded, have its ingest killed seconds later, and sit there
forever, with nothing in the UI to retry it. `sweep_interrupted()` now returns which runs
it closed and startup puts them back in the queue: whatever stopped the process is not the
source's fault. Deduplicated, because a source queued twice before the stop still only
needs ingesting once.

**What generalises:** a lock does not make concurrent work safe if waiting on it is
expensive. The queue is not an optimisation — it is the difference between *this work is
serialised* and *this work serialises everything else too*.

### 4.14 A second agent, with the opposite permissions

Ingesting turns up things no source settles: two documents disagree, a claim rests on
nothing, a first name could be two people. The wiki agent had nowhere to put those — it
either guessed, or buried the doubt in a log entry nobody reads.

Now it writes them to `todo.md`, and a **second agent** works through them with whoever
knows the answer. Its permissions are the wiki agent's mirror image: it may write `raw/`
and `todo.md`, and it may not touch a single page.

**Why it must not edit the wiki, which is the whole design.** A page is derived from a
source. Editing the page and leaving the source alone puts the two out of step — and the
next ingest of that source, which any later correction will cause, regenerates the page
and throws the edit away, question intact. Worse, the contradiction is still in the source,
so the agent raises it again. Correcting the source instead means the fix survives and the
wiki catches up the way it always does. **The answer goes where the question came from.**

**Why a file rather than a table.** Ruben's reason, and the better one: `todo.md` syncs to
the laptop like everything else, so the questions can be worked through in Claude Code
directly, with no Mindkeep involved. That made it the first file outside `raw/` that syncs
*both* ways — a third ownership class after "the owner's" and "the agent's", shared by
both and by the person with the folder open. Writing to it starts no ingest: it is a
record *about* the knowledge, not knowledge.

**One question at a time, and no list of them.** The first pass gave it a sidebar of
questions like every other tab; Ruben cut it. A queue you can browse is a queue you never
finish — so the card is the screen, a count says how many are left, and the only moves are
answer it or skip it. It also removes a question the sidebar could not answer: what does
selecting a *second* question do to the conversation you were having about the first.

**The conversation is not stored.** Each turn posts the whole exchange; the browser holds
it, the server holds none of it. Leaving a question loses the thread, which is the honest
cost of not having a conversations table — and the moment someone needs to come back to
one, it has stopped being a chat and become a ticket, which is when to build that.

**The manual carries the rules for the file** — one line per question, name the files it
turns on, never reword or delete someone else's line, never repeat one that is already
there — because both agents and a human all write to it, and the only thing keeping it
coherent is that they agree on the format.

### 4.15 A blank page is the worst possible error report

The Questions tab went white after the last question was ticked off. A render error
unmounts the whole React tree, so what you get is a blank page: nothing in the UI, nothing
in the server log, and the console gone the moment you reload to see what happened.

Two mounted tests of the exact path — the empty list, and the transition from one question
to none — both passed, and the API answered fine, which points at a hot update that failed
to apply rather than the code. Not something to prove, so the useful move was to stop the
next one being invisible: an error boundary at the root that renders the message and the
stack, with a reload button.

Kept the transition test anyway. Its value is not that it caught this one — it did not —
but that an empty list is a real screen, and it is the screen you reach exactly when
someone has just finished all their work.

### 4.16 Knowledge gaps: the server measures, the agent asks

Ruben asked for what InfraNodus does — find the places a body of knowledge is thin and
formulate the question that would fill them — and whether to buy its API or build it.
Decided 2026-08-26: **build**, in the lint, on the link graph.

**What InfraNodus actually does.** Tokenise text into a word co-occurrence graph, run
Louvain community detection to get topics, rank nodes by betweenness, call two topics
with few edges between them a *structural gap*, and hand the top terms of each side to an
LLM for a bridging question. Steps one to four are a few dozen lines of `networkx`; the
product's real value is the visualisation and the years of tuning, neither of which an API
caller uses.

**Why not the API.** Three reasons, any one sufficient. It is priced for one researcher —
API access on the ~€19–32/month tier with a per-hour AI-credit cap, and the commercial
backend path is self-hosted Enterprise at €9,900/year — not for N tenants' nightly lints.
It would mean sending every tenant's full wiki text to a third party, for a product whose
premise is *your material, your wiki*. And it would throw away the better signal: InfraNodus
infers structure from word adjacency because raw text is all it has, whereas Mindkeep has
the links the agent deliberately wrote, plus `sources`, `tags` and `type`.

**What was built: `app/gaps.py`,** ~100 lines and one dependency. Wiki pages are nodes; a
markdown link or bare `/wiki/…` path from one page to another is an edge (relative and
bundle-absolute both resolve; links to pages that do not exist are dropped, since the lint
already reports those). Louvain groups the pages into areas of three or more; betweenness
centrality picks each area's hub pages. A **gap** is a pair of areas with under a third of
the links the configuration null model would give them — the same yardstick Louvain used
to draw the areas. At most three per lint, thinnest first, each side described by up to
four hubs with their `title` and `description`.

**Why relative, not a count.** The first version flagged pairs with at most one link
between them, and found nothing on the mimetik wiki: `mimetik.md` links to every project,
so every pair of areas has a link or two that says nothing about how the areas relate. The
observed-over-expected ratio is scale-free — a hub inflates *expected*, so it cannot mask a
gap. On the real wiki the ratio ranks Futuros × Resonaut thinnest (6 links where random
wiring would give 22; the bridges are people and house decisions, not a single concept
page) and nothing else under the threshold. That is the one question a foresight-and-
research studio should be asked, and it was the only one asked.

**Wired exactly like source moves (§4.7e).** `ingest_safely` measures before a lint and
passes the gaps into the task alongside `MOVED`, as a `GAPS` hint. The server knows where
the graph is thin the way it knows what moved, so it says so rather than making the agent
read every page to find out. The manual's Lint section gained a *Knowledge gaps* paragraph:
for each pair, judge whether the areas genuinely bear on each other; where they do, one
question in `todo.md` naming the pages on each side, specific enough that the owner can
answer it or drop in a source that does; where they do not — cooking and tax law — a few
words in the log and nothing added. So the question flows through the same channel every
other open question does (§4.14): the assistant asks the owner, the answer corrects or
adds a source, the ingest links the two sides, and the next night's measurement no longer
finds the gap.

**Rejected:** a separate LLM call for the question — the lint already has the manual, the
tools and the `todo.md` rules, and a second prompt would have to restate all three.
Co-occurrence edges as a second signal — deferred until a link graph proves too sparse to
cluster, which a wiki whose agent is told to link liberally should not be.

**Not done, and known:** the same gap is re-measured every night until something links the
sides, and the only thing stopping it being asked twice is the agent reading `todo.md`
before writing, which is a rule rather than a mechanism. If it repeats, record asked gaps
in Postgres the way settled moves are.

### 4.17 `related`: the graph answers the one question a page cannot

Follow-up to §4.16, 2026-08-27. Ruben asked whether the link graph could improve how the
agent finds related pages during an ingest, and search when querying; then whether the
graph was redundant with the frontmatter. It is not: the frontmatter holds no page-to-page
links — those live in the body — and it holds `sources`, which point at raw documents, a
second edge type the gap measurement had ignored. Decided: no `related:` field in the
frontmatter, ever (it would be the actual duplication, a list to keep in step with the
body links); instead one tool, built from both edge types, given to both agents.

**What was built: `app/graph.py`,** and `gaps.py` shrank to use it. `build(home)` reads
the wiki into a directed graph — pages as nodes carrying `title`, `description` and the
sources their frontmatter cites (relative, bundle-absolute and URL forms all resolve);
body links as edges. Nothing is persisted: it is rebuilt on every call, 113 ms for the
35-page mimetik wiki, so a page written a turn ago is already in it. `related(G, path)`
returns text in three sections — *Links to*, *Linked from*, *Cites the same source* —
each page as its index line. Given a path under `raw/` instead, the pages that cite it.

**Why this is the question worth answering.** A page knows what it links to; it cannot
know what links to it, and `index.md` does not say either. So when a source changed what
Futuros *is*, the pages describing it from the outside stayed as they were — the only way
to find them was to read everything, which the manual forbids. On the real wiki,
`related` on `futuros.md` surfaces `commands-over-tools.md`, `invariant-checks.md` and
`named-command-surface.md`: none linked from the page, all resting on the same source or
pointing at it, all pages a change to Futuros can put out of date. The manual's ingest
section now says to call it before changing a page; the assistant's role says to call it
on a source before editing one, so it can say what the correction will reach.

**What it does not do.** Find the entry point. The graph carries structure, not content,
so matching a new source or a question to pages still rests on the `index.md` descriptions
— which are read whole on every run, and at 135 bytes a page will be ~17k tokens at 500
pages. The next lever is a lexical index (BM25 over pages) so `index.md` can go back to
being a catalog for people; `related` then widens from whatever search finds. Deliberately
left out of this version: a personalised-PageRank "nearby" section. With a company page
linking to every project, two hops from anywhere is everywhere, and a ranked top-five of
that would have looked like information without being any.

### 4.18 The log went quiet the day migrations moved into startup

Found 2026-08-27 when Ruben ingested a document and saw nothing in the backend log but
alembic's two lines. The runs had worked — rows closed, `log.md` written — but nothing
after "Will assume transactional DDL" was ever printed: no "Application startup complete",
no access lines, no ingest steps. Cause: alembic's generated `env.py` calls
`logging.config.fileConfig(alembic.ini)`. From the command line that is the right thing;
called from inside `lifespan()` by migrate-on-boot it does two things to a process that
already has logging: `disable_existing_loggers=True` switches off every logger created so
far (uvicorn's, `app.ingest`), and the file's `[logger_root] level = WARNING` replaces the
app's root configuration, so even a logger that survives has its INFO dropped. The first
fix — passing `disable_existing_loggers=False` — brought back uvicorn and the access log
and looked complete; the next ingest ran nine turns with no trace, and the Kinde warning
came out in alembic's `WARNI [app.kinde]` format, which was the tell. The real fix is a
guard: `env.py` only calls `fileConfig` when the root logger has no handlers, i.e. when
nobody has configured logging yet, which is only ever the CLI. Same shape as the seeding
race: a library that assumes it owns the process is safe until it is called from inside one.

### 4.19 The Graph tab: the same graph the agents are told about, drawn

Added 2026-08-27, after the graph module gained its second user. `GET /bundles/{name}/graph`
returns pages (title, description, cited sources, Louvain area or -1) and links as pairs,
built from the files on each call like `related` and the gap measurement; there is still
nothing stored. The tab draws it in SVG with a ~60-line Fruchterman–Reingold layout run to
rest before first paint and seeded from page order, so the same wiki always settles into
the same picture and there is no dependency to carry. Pages are coloured by area — the
split the lint measures gaps between, so a visible hole between two colours is a question
the next lint will ask — sized by connections, with sources drawn as squares on request.
Clicking a page lists links out, links in and citations: what `related` tells the agent.
Left out on purpose: jumping from a node into the Library, which needs the Library's
selection lifted into App; labels for every node past a few hundred pages will need
thinning by zoom level. The commit history on branch `graph` splits the day's work in
four: gaps, `related`, the alembic logging fix, the view.

### 4.20 The client renames before it uploads

Found 2026-08-27 by copying six folders and 51 files into a fresh mirror with `watch`
running: 56 `sent`, 15 `got` under different names, 15 `removed`, and 13 `-2` files. The
server rewrites a source's name on upload — `UNSAFE_NAME` allows only ASCII letters,
digits, space, parentheses, dot, underscore and dash — and the client ignored the path the
server returned. So an em dash in a title went up, came back as a hyphen, and the sweep
took the original for deleted; and where the folder already held both spellings (the
SecondBrain raw folder does, for 13 documents — the leftovers of an earlier round trip
through the same rewrite), the second upload collided into `-2`, a duplicate source
queued for its own ingest. Nothing was lost, but the mirror deleted files the owner had
just put there, which is the one thing it must never do.

Ruben's call: sanitise on the client, before upload, for files that are new there — and
ask the server what the name will be rather than copy its rule. `POST /clean` takes raw/
paths and answers with the names the server would store them under; `sync()` now opens with
`rename_new()`, which sends every file the server does not yet know in one call, renames
each on disk to the answer, and only then uploads. So what goes up is what stays, and the
rule lives in `files.py` alone. A twin with identical bytes under the clean name is
dropped with a note; a different document under that name gets `-2` locally, as the server
would have done. Files the server already has are by definition spelt its way and are left
alone. The first cut carried a copy of the regex in the client; it lasted one commit. Not
changed: the rule itself, which is narrower than it needs to be (every accented letter
becomes a hyphen); loosening it is a separate decision, and with the client renaming first
it no longer causes damage, only ugliness.

### 4.8 The profile is composed from Kinde, never mirrored

`GET /me` returns name, first name, last name, email, picture and role, read from
Kinde's Management API on every call. There is still no user table — following
Futuros' `app/users/router.py`, which keeps only an id and composes the rest per
read.

**Why not the token's claims,** which the browser already has for free: the claims
are minted at sign-in and go stale the moment someone edits their profile in Kinde's
portal. Futuros hit this and its `AccountMenu` reads the backend for the same reason.
Role is the one exception — it comes from the verified JWT's `roles` claim, because
it is display-only and reading it costs nothing.

**Degradation is the whole design.** Kinde being unreachable returns blank strings,
never an error: the avatar falls back to initials, then to the user id. Which is more
than theoretical — see §6.

### 4.9 The lint schedule moved out of the environment and into the database

`LINT_HOUR` was a single env var, so every tenant and every bundle shared one hour,
and changing it meant recreating the container. That is the wrong shape for a
multi-tenant app: an env var cannot be per-user by construction.

Now a `bundle_setting` row holds the hour per bundle, set from Settings. Three states,
deliberately: **no row** follows the server default, **0–23** is a chosen UTC hour, and
**-1** is off. The env var survives as the default for bundles nobody has configured —
which keeps the zero-config path working without pretending it was ever a per-user
setting.

**Hours are stored in UTC and chosen in local time.** The dropdown builds its 24 options
from real `Date` objects rather than by adding an offset, so half-hour zones (India,
Nepal) land on the right minute. The server never learns where anyone is.

**The scheduler thread now always starts.** It used to skip starting when `LINT_HOUR`
was off, which would have made a bundle's own "lint at 22:00" silently do nothing.
Per-bundle settings mean the global switch can no longer decide whether the machinery
runs at all.

### 4.21–4.29 Recovered in brief (2026-08-27)

The mirror that held these entries was lost with the folder it lived in; what follows is
what the code and the session record still say. The reasoning behind each is in the
commit messages of the day.

- **4.21 Graph tab labels and gaps mode.** Nodes wear their slug, not their title; labels
  stay legible across zoom and never collide; *Show gaps* fades everything but the areas
  the lint would ask about.
- **4.22 Teams, provider-agnostic.** App-owned `team`/`membership`/`invite` tables keyed by
  the provider's `sub`; personal team = the tenant hash; invites are one-use links that
  survive the sign-in redirect; a personal team cannot be left, deleted, renamed or
  invited to. Never build teams on Kinde/Clerk organisations.
- **4.23 Roles are named sets of permissions.** viewer/contributor/admin/owner over
  `read/write/history/bundles/members/team`; routes ask for a permission, never a role.
- **4.24 Bundles from the UI**: create, rename, delete (never the last), move between
  teams — all refused while a run is busy. The device token moved to Settings; Settings
  became tabs.
- **4.25 Auth is any OIDC provider.** `app/auth.py` verifies against the issuer's JWKS;
  `kinde.py` and the M2M lookup deleted; frontend adapters (`kinde`, `oidc`) behind one
  contract; no Kinde fallbacks — "commit to the final form".
- **4.26 Two prompt texts, two readers.** `manual.md` is the agent's system prompt and
  stays on the server; `CLAUDE.md` is the reader's guide seeded into every bundle and
  re-pushed at every startup.
- **4.27 Concurrency is optimistic.** `If-Match` on writes (412), conflict copies under
  `.conflicts/` (a dot dir: never uploaded, never swept), `todo.md` ticks merged. No locks,
  no staging folder — Ruben weighed both and chose this.
- **4.28 Git in every bundle.** Two commits per run (people's changes, then the agent's);
  every person's action its own commit; undo reverts the run *and takes the source back*
  (Ruben's call — a bad source left in place is re-ingested at the next touch); redo;
  `based_on` and a diff hint on re-ingest; notes from local agents under
  `raw/notes/<person>/` instead of memory files.
- **4.29 Activity replaces Console.** One feed from git history — runs, people's changes,
  undo/redo, pending — with a `history` permission gating undo; a deleted cited source
  starts a retire run at once; a data migration must never key on a directory's
  existence (legacy run rows were re-keyed by value after one did).

### 4.30 Custom dialogs, a queue that dedupes, and the desktop app (2026-08-27/28)

- **Dialogs.** The browser's `confirm()`/`prompt()` replaced by the app's own
  (`frontend/src/dialog.tsx`): promise-based, one `<Dialogs />` at the root.
- **Ingest queue.** Measured on the live runs (median 57 s, p90 112 s per source): a
  source already waiting is not queued twice, and a source byte-for-byte what the last
  clean run read is skipped before a run row opens (`history.same`). The per-bundle serial
  worker stays — the bundle is the unit of shared state. Missing still: a global cap and
  fairness across teams; the queue is in-process (one replica).
- **Desktop app: Python + PySide6, not Tauri.** Reuses the sync engine verbatim; Briefcase
  builds msi / dmg (ad-hoc) / deb via a GitHub Actions matrix on `v*` tags; unsigned by
  decision. Phases: the client became a package (`sync.py`, `cli.py --config`); per-device
  revocable tokens (`device` table, `<id>.<hmac>`, `auth.Person` for minting) and the
  loopback **connect flow** (`/?connect=1&port&nonce&name` → `127.0.0.1:<port>`); the tray
  app (`mindkeep/app/`: one worker thread over the watched bundles, balloons only for
  conflicts / failed ingests / dead token / unreachable; folder fixed at add-time; single
  instance; autostart); packaging with a `mindkeep_app` shim and generated icons. Three
  forks built the phases in parallel on disjoint files.

### 4.31 Renamed: Mindstash is now Mindkeep (2026-08-28)

Everywhere in the codebase in one commit — packages, Briefcase app and bundle id,
installer folder `Programs\Mimetik\Mindkeep`, config paths `~/.mindkeep*`, the Postgres
role/database (migrated in place, 66 runs intact), the URL. Not renamed on purpose: the
Kinde issuer host, the GitHub repo, the checkout directory. Existing clients sign in
again. The reader's guide stopped inviting edits to `todo.md`: the cloud agent is the only
writer of the wiki.

### 4.32–4.33 Railway: images, CORS, then a private API (2026-08-28)

Env files for Railway (`.env.railway`, git-ignored via `.env.*`). Both Dockerfiles became
multi-stage: a `dev` target for compose, a last stage for deploys. The first deploy ran
the Vite dev server, which refuses unknown hosts. `VITE_*` values are build args the
Dockerfile must declare, or Railway's values never reach `vite build`. Then Ruben: no
need to expose the backend — so the frontend image is **Caddy**, serving the built site
and proxying `/api/*` to `API_UPSTREAM` (`http://<service>.railway.internal:8000`) over the
private network: one origin, no CORS (the `ALLOWED_ORIGINS` middleware stays for a
split-origin deploy). Railway's private network is IPv6-only and uvicorn binds one
family, so the image takes `HOST` (Railway sets `::`) and a fixed `PORT`. The client
names itself in every request: Cloudflare's browser check, in front of mindkeep.io, drops
`Python-urllib`. The API address for a client is the site plus `/api`.

### 4.34 Where a page goes (2026-08-28)

A production tree with `people/`, `concepts/` and thirty flat prefix-named pages: nothing
said where a page goes. Decision: `wiki/<type-plural>/<title-slug>.md`, mechanical on
purpose — a page's type is a fact, its subject an opinion. A **reorganise run**
(`(reorganise)`, beside `(lint)` in `MAINTENANCE`) files an existing bundle; the lint
reports misfiled pages and the server queues the reorganise after it (`misfiled`). The
first run re-emitted forty pages through `write_file` and hit `max_tokens` with nothing
applied — so `move_file` (rename on disk), the server hands the run the list of moves,
and a reply cut at `max_tokens` fails the run visibly. The reader's guide gained "the
mirror changes under you": start from `index.md` per question, re-read before relying.

### 4.35–4.36 Restart amnesia, and holding when the service fails (2026-08-28)

Only 7 of 38 files ingested: a redeploy mid-sync killed the process and the 32 waiting
lived only in the in-memory queue. Startup now re-queues every raw file no run has ever
touched (`requeue_unread`), and every source whose latest failure was the service's.
Then the account ran out of credit and each remaining file got its own failed run in
seconds: `service_error()` classifies credit/rate-limit/overload/connection/auth/5xx
failures, and on one the worker **holds** the source and retries after 1, 2, 4, 8, then
15 minutes; `GET …/queue`, `POST …/retry` (one source or all failed, ends a hold), banners
in Activity, a *retry* button in the Library. A 400 is the request's fault and never holds.

### 4.37 Two lists: questions.md and todo.md (2026-08-28)

`questions.md` — what a person must *answer*; `todo.md` — what a person must *do*. Both
the agent's: `PUT /files` refuses them, the client's upward sync of `todo.md` is gone,
ticks are made in the app; the assistant ticks questions and adds tasks (`task` tool).
`todos.ensure` migrates a pre-split bundle (its questions move over) at startup and on
any bundle access. Todo tab: Questions (one at a time + assistant) and Tasks panels. The
assistant answers as a polled job — Cloudflare cuts a request at 100 s (524).

### 4.38 Documents go to the model as documents (2026-08-28)

A PDF reached the agent as "not readable as text". The API takes a PDF as a `document`
block (32 MB, 600 pages) and reads it as text and as page images, so no extractor: the
file rides on the task message (`ingest.attachment`) and `read_file` on it says so. A
`.docx` goes the same way as its extracted text (the API takes PDF and plain text). *Ingest
again* on any source in the Library — and a person's ask (`force=True`) bypasses the
unchanged-source skip, which had silently swallowed the first retries.

### 4.39 Undo past later runs: index.md is the server's (2026-08-28)

A retire run could not be undone — later runs had touched `index.md` and `log.md`, and
every run does. Decision: `index.md` is built by the server from the pages' frontmatter
(`app/index.py`) after every run, undo and startup; the agent's tools refuse it. `log.md`
is a timeline: an undo appends its own entry. `history.take_back` reverses only what the
run did under `wiki/` (`git diff sha^ sha -- wiki | git apply -R --index`, all-or-nothing,
bytes end to end — Windows text mode had rewritten the patch's line ends), then restores
or removes the source, rebuilds the index, commits once; redo (`put_back`) the same for
`wiki/` and `raw/`. A conflict is now only a later run rewriting the same page, named.
Also: the site renders the agent's `[^src]` footnotes (`marked-footnote`).

### 4.40 A built-in identity provider, so the product works out of the box (2026-08-28)

Ruben: "a basic default built-in auth system so the product works out of the box without
the need for third party dependencies (other than hosting)". Built as a third adapter
behind the existing contract, not a special case: `AUTH_PROVIDER=builtin` (explicit — a
deploy that loses `AUTH_ISSUER` must fail closed, not fall into open registration; unset
still means `oidc`, so production did not move) turns on `accounts.py`: an `account`
table (sub `local_<hex>`, e-mail, scrypt hash with its parameters stored in the string so
they can be raised later, `admin` for the first account), HS256 session tokens signed
with `AUTH_SECRET` (30 days), verified in that module alone — the RS256 pin in `auth.py`
dispatches once and never accepts both algorithms. Registration is open only until the
first account exists, then `AUTH_REGISTRATION=invite` (an invite link from a team lets
someone in) or `open`. Five wrong passwords in five minutes lock the address out. The
frontend gained `providers/builtin.tsx` — token in localStorage, profile read off the
payload, its own sign-in / register form — and the adapter contract an optional `Login`
component the gate renders instead of the redirect button; `VITE_AUTH_PROVIDER` unset
now means `builtin`. Settings → Account has a password change. Decided, not omitted: no
reset, no e-mail verification, logout is forgetting the token, revoke-all is rotating the
secret. Verified end to end on the local stack switched to builtin (first-account
registration, sign out, sign in), then switched back to Kinde.

### 4.41 …and then to the bone (2026-08-28)

Ruben: "keep it as simple and as basic as possible, strip out all functionality to keep
it down to its bones." Gone from the first cut: the registration policy and the
first-account-is-admin rule (registration is simply open), the login throttle, the
password change route and its Settings card, `GET /auth/config`, the `admin` and
`last_seen` columns (a follow-up migration drops them). What is left is two routes —
register, log in — a scrypt hash with its parameters, and an HS256 session token; the
frontend form has a sign-in / create-account toggle and nothing else. Each thing removed
is a decision recorded here so it can be added back when someone actually needs it.

### 4.42 The personal team is always "Personal" (2026-08-28)

Signing in through the built-in provider, Ruben's personal team came out as "Ruben"; on
Kinde it had been "Personal". Same rule, different tokens: the team was named after the
person when the token carried a name, and Kinde's access token carries none unless a
setting says so. Ruben: remove the logic, always call it Personal. One line in
`ensure_personal`; the team is named once, on creation, so existing teams keep whatever
name they have.

### 4.43 The Kinde adapter goes; `oidc` is the one redirect adapter (2026-08-28)

Ruben switched the local frontend to `VITE_AUTH_PROVIDER=oidc` against Kinde and saw no
difference — which was the finding. Checked in the browser: discovery and the token
exchange come from `oidc-client-ts`, sign-in lands on the team, sign-out ends Kinde's own
session (the next sign-in shows Kinde's form), the access token lives 24 hours. The one
gap was no refresh token — the scope lacked `offline`, Kinde's spelling of
`offline_access` — so the generic adapter now asks for it. `providers/kinde.tsx` and
`@kinde-oss/kinde-auth-react` are deleted; what the SDK did beyond OIDC was the
`?account_type=org` registration hook from Futuros, which Mindkeep's own teams replaced.
Two adapters remain: `builtin` (default) and `oidc`. Production's frontend moves to
`oidc` with the same issuer and client id. Not yet seen: a session crossing the 24-hour
mark on the refresh token, and a first-time registration through Kinde's "Create one".

### 4.44 The client workflow only packages (2026-08-28)

A CI workflow (`ci.yml`, arriving with the open-source release files) runs the backend
and frontend checks on every push. Ruben: the test job in the desktop client workflow
does much the same — remove it. `app.yml` now runs on a `v*` tag or by hand and only
builds the installers; the client's `ruff check . && pytest -q` belongs in `ci.yml` as a
third job beside backend and frontend, so one workflow answers "is main green".

## 5. Open questions and known gaps

- **The M2M application is not authorised for the Management API**, so every
  Kinde read fails and degrades to blanks. Found 2026-08-23 while wiring `GET /me`;
  the token endpoint answers `400 invalid_request — Requested audience
  'https://mindkeep-dev.eu.kinde.com/api' has not been whitelisted by the OAuth 2.0
  Client`. Fixed in the Kinde dashboard, not in code: authorise the M2M application
  against the Kinde Management API. Until then the account menu shows initials from
  a blank name, and — the part that actually matters — every `verified` stamp
  written so far records a raw `kp_…` id instead of a person, because `who_is()` has
  been falling back the whole time. Nothing about it was visible until a screen
  displayed the result: the degradation is deliberate and silent by design, which
  is right for a page write and wrong for the one call whose entire job is
  identity.

- **Connectors are the biggest remaining piece.** Gmail and Drive are one
  integration, not two — same Google OAuth, same incremental-cursor pattern
  (Gmail `historyId`, Drive `changes.startPageToken`). Gemini meeting notes land
  in Drive as Docs, so they are covered by the Drive half rather than being a
  third connector. This is what finally justifies Postgres and Alembic.
- **Ingest cost is unmodelled.** Each ingest is an agentic loop touching 10–15
  pages. At connector volume — every email from a watched sender — that adds up
  quickly. A batching window before ingest is probably the next real decision.
- **Ingest is not durable, and the first lint proved it.** It runs in FastAPI
  `BackgroundTasks`, so it dies with the container — and a `--reload` counts. The
  first real lint ran 5m 28s, edited six pages, and was killed mid-run by a code
  edit before it could write its log entry. It is now at least *visible*: the run
  row is closed as interrupted at startup, the failure shows in the UI, and the
  `note` says how far it got. But nothing retries, and a run killed after it has
  edited four pages and before it has written the fifth leaves the wiki in a state
  nothing records. That is the strongest argument for making ingest resumable, and
  the reason it is above connectors in priority now.
- **`GET /tree` hashes every file on every call.** Fine for a wiki; cache by
  mtime if it ever bites.
- **No undo.** Dropping git removed cheap per-ingest rollback; a bad ingest is
  now recoverable only from a Railway volume snapshot, which is much coarser. If
  this bites, the answer is server-side git nobody has to know about, not git as
  the transport.
- **A `.docx` was extractable and unviewable at the same time.** The ingest agent had been
  reading `.docx` since §4.2 — unzipped with the standard library — but the web viewer only
  ever asked for the file's bytes, so it showed "not markdown, open it from your synced
  folder" for a document the agent had already summarised. Two ways of asking for the same
  file, and only one of them knew the trick. `GET /bundles/{name}/text/{path}` now serves
  exactly what the agent gets, which makes it worth reading for its own sake: what the
  model saw is the version you want to argue with. 415 for a format nothing can extract,
  rather than an empty page pretending to be the file.
- **PDFs are refused rather than mangled.** `readable_text()` decodes strictly and
  returns nothing for a binary, and `.docx` is unzipped with the standard library.
  A PDF needs `pypdf` or the API's document blocks; until then the agent is told it
  cannot read the file, which is far better than the alternative — a lenient decode
  handed it 137KB of mojibake and it burned seven turns trying to make sense of it.
- **MCP access** was considered as an alternative to local sync and deferred.
  They are not alternatives: sync serves a local Claude (grep beats tool calls
  for "find every note mentioning X"), MCP serves every other client. Worth building after the API stabilizes; FastMCP mounts into the same
  app, so it is not a second deployment.
- **Sources can be deleted, bundles cannot.** Deleting a source is allowed and
  deliberately does *not* protect the pages citing it — the lint pass cleans up
  the orphans afterwards, which is why the lint had to exist before the delete was
  comfortable. Deleting a whole bundle still needs thought, and probably an
  export-first step rather than an `rmtree`. Nothing can be renamed yet.
- **Whether a real OKF consumer accepts the bundles** has not been tested. The
  format was implemented from the spec, not validated against Google's tooling.
- **The UI has Kinde login, org registration, an account menu and a Settings page**,
  but still no bundle *creation* (the picker cycles through existing bundles) and no
  rename.
- **The nightly lint assumes one process.** Two replicas would both sweep and race
  for the same bundle. Named in `schedule.py`; the fix is an advisory lock, not a
  broker.
- **Tenant seeding raced twice, and the second race hid behind the fix for the first.**
  A fresh sign-in fires several requests at once. Round one: two of them both seeded and
  the loser died on `mkdir` — fixed with a per-tenant lock. Round two, visible only in
  production logs: `seed()` creates the tenant directory as its *first* act, so a
  concurrent request saw `home.exists()`, skipped the lock entirely, and read a tree with
  no files in it yet — a 404 on `index.md` seconds after startup. Now the tenant is built
  in a staging directory and moved into place with one `rename`, so it either does not
  exist or is complete. The lesson: a lock makes the *write* safe, not the *observation*;
  the guard everyone else checks has to flip atomically.
- **A third bug, this one found by a test:** `Path.write_text` translates `
` to
  `

` on Windows, so the server was writing CRLF into a wiki that syncs to other
  machines and gets parsed by both the UI and the agent. Every text write now goes
  through `put_text()`, which pins `newline="
"`. Note the pattern: line endings bit
  three times in one session, in three different places.
- **Two bugs found by running the UI rather than by its tests**, both worth
  remembering as a pattern: the file endpoint served every file as text, so the
  first PDF would have thrown; and the log parser split on `
`, which fails on a
  CRLF file because a JS regex `$` will not match past the stray `
`. Both unit
  tests had passed — they used LF fixtures and markdown. Exercising the real thing
  found both in minutes.
