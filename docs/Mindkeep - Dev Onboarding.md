# Mindkeep — Developer Onboarding

*Written 2026-08-28 against commit `153648c` on `main`. Everything here is checkable in
the repository; where this document and the code disagree, the code is right and this
document is stale.*

Mindkeep is a team knowledge base that an AI agent maintains. People drop documents into
a folder; a Claude agent in the cloud reads each one and folds it into a wiki of short,
linked, cited pages; the wiki syncs back down to every teammate's machine as plain
markdown that any local tool — Claude Code, Obsidian, `grep` — can read. Nobody writes
the wiki by hand. The product's one rule, from which most of the architecture follows:
**the cloud agent is the only writer of `wiki/`; people write only `raw/`.**

---

## 1. The shape of the system

```
                       ┌──────────────────────────────────────────────────────┐
   browser ──HTTPS──►  │ frontend  (React SPA, served by Caddy, proxies /api) │
                       └───────────────────────┬──────────────────────────────┘
                                               │ private network
   tray app / CLI ──HTTPS /api──► Caddy ──────►│
                                               ▼
                       ┌──────────────────────────────────────────────────────┐
                       │ backend (FastAPI, one process)                       │
                       │   • routes: teams, bundles, files, runs, devices     │
                       │   • per-bundle ingest worker threads (Claude agent)  │
                       │   • nightly lint scheduler thread                    │
                       │   • git repo inside every bundle (history, undo)     │
                       └───────────┬───────────────────────┬──────────────────┘
                                   │                       │
                          ┌────────▼────────┐    ┌─────────▼──────────┐
                          │ volume /data    │    │ Postgres            │
                          │ the wiki files  │    │ runs, teams,        │
                          │ (canonical)     │    │ devices — metadata  │
                          └─────────────────┘    └────────────────────┘
```

Three deployable things, one repository:

| Part | Directory | Stack | What it is |
|---|---|---|---|
| Backend | `backend/` | Python 3.13, FastAPI, SQLAlchemy 2, Alembic, Postgres 17, Anthropic SDK, git | The API, the ingest agent, the scheduler, the history, the built-in accounts |
| Frontend | `frontend/` | TypeScript, React 19, Vite 6, Vitest, react-oidc-context, Milkdown Crepe | The web app |
| Client | `client/` | Python 3.12+, stdlib (engine + CLI), PySide6 (tray app), Briefcase | The sync engine, the CLI, the desktop tray app |

`docker-compose.yml` at the root runs all three plus Postgres for development.

**Sizes, for orientation:** ~9,400 lines of source across the three parts; 151 backend
tests, 28 frontend tests, 32 client tests.

---

## 2. Core concepts

### Tenant, team, bundle

- A **team** is the unit of sharing and of tenancy. Every person has a **personal team**
  created on first sight, always named "Personal"; teams beyond that are made in the app
  and joined by invite link.
- A **bundle** is one knowledge base: one directory, one wiki, one git history. A team
  holds many bundles (`default` is seeded; more are created in Settings).
- On disk: `WIKI_ROOT/{team_id}/{bundle}/`. The personal team's id is
  `sha256(sub)[:32]` of the person's identity-provider subject; other teams get a random
  32-hex id. **The path prefix is the whole tenancy boundary** — every filesystem access
  goes through `files.safe_path()`, every DB row carries a `tenant` column, and a team you
  are not a member of is a 404, never a 403.
- Every bundle route is `/teams/{team}/bundles/{bundle}/…`.

### The bundle: an OKF bundle

Mindkeep bundles follow the Open Knowledge Format v0.2 — markdown with YAML frontmatter,
bundle-absolute links, `index.md`/`log.md` reserved. The layout:

```
{team}/{bundle}/
  CLAUDE.md       the reader's guide — pushed from the app at every startup; tells a
                  local agent this is a mirror, and where changes go
  index.md        the catalog: one line per page, rebuilt by the server from the pages'
                  frontmatter after every run. Agents read this first, nobody writes it.
  log.md          append-only history, one entry per run: "## [date] ingest | title"
  questions.md    open questions the agent could not settle — for someone who knows
  todo.md         tasks for a person, found by the agent — for someone who does
  raw/            the owner's documents, under their own names. The only human-written half.
    notes/<person>/…   findings contributed by local agents (see "Notes")
    connectors/<kind>/…   what a connection pulls from a third-party source (see "Connectors")
  wiki/           everything the agent wrote, filed by type:
    people/  companies/  projects/  concepts/  meetings/  summaries/ …
  .git/           history (hidden from every listing, never synced)
```

**Who writes what** (`backend/app/templates/manual.md`, *Who owns what*):

| File | Written by | Notes |
|---|---|---|
| `raw/**` | people (upload, sync, web) and the assistant | immutable to the agent; a deleted source retires its pages |
| `raw/connectors/<kind>/**` | the connection's sync | a mirror of the source: hand edits and deletes are put back at the next sync |
| `wiki/**` | the ingest/lint agent; a person may edit an existing page in the app | a page edit is a commit, never an ingest; the next ingest of a source the page cites may revise it — history keeps their version. Making pages stays the agent's |
| `log.md` | the ingest/lint agent only | its own account of every run |
| `index.md` | the server (`index.py`), after every run and undo | built from the pages' frontmatter; the agent's tools refuse it |
| `questions.md` | the agent (questions), the assistant (ticks, new questions) | answered through the assistant or a note in `raw/`; never edited by hand |
| `todo.md` | the agent and the assistant (tasks) | ticked by a person in the app; never edited by hand |
| `CLAUDE.md` | the app | overwritten from the template on every backend start |

**Where a page goes** (*manual.md → Where a page goes*): `wiki/<type-plural>/<title-slug>.md`.
The folder is the page's `type`, lowercase, plural; the slug is the title. The rule is
mechanical on purpose — a page's type is a fact about the page, its subject an opinion.
A bundle written before the rule is put in order by a **reorganise run**.

### Runs

Every piece of agent work is a **run**, a row in `ingest_run`, keyed by tenant + bundle +
source. Sources are:

- a path under `raw/` — an **ingest** (or a **retire**, when the file is gone);
- `(lint)` — the nightly maintenance pass;
- `(reorganise)` — the layout pass.

A run records when it started and finished, turns, characters written, the model, the
error if any, the agent's own log entry (`note`), and two git commits: `based_on` (what it
read from) and `commit` (what it wrote). Runs are what the Activity feed, undo, the
sources' "ingested" state and the queue all read.

### History

A git repository lives inside each bundle (`backend/app/history.py`). Around each run the
server makes two commits — `before run N` (people's changes since the last run) and
`run N: <source>` (what the agent wrote) — and every action a person takes through the
app is its own commit (`upload …`, `edit …`, `delete …`, `move …`).
Undo of a run reverses **what it did under `wiki/`** and takes the source back (a new file
is removed, an edited one restored to the previous clean read), rebuilds `index.md`, and
appends an `undo` entry to `log.md` — one commit; redo reverses that. Only pages are
reverted, so later runs' edits to the index and the log never block an undo; a later
run that rewrote the *same page* is a real conflict, and the refusal names it. The `.git` directory is
excluded from the tree endpoint, from the agent's `list_files`, and from `safe_path`, so
neither the mirror nor the agent ever sees it.

### Concurrency: optimistic, never locks

`PUT /files/{path}` and `DELETE /raw/{path}` honour `If-Match` with the sha256 the client
last saw; a mismatch is a 412. The sync client keeps a both-sides-changed file under
`.conflicts/` (a dot directory: never uploaded, never swept) and lets the server's copy
land in place. The agent side is serialised by
design: **one worker thread per bundle**, so only one run ever writes a wiki at a time.

---

## 3. Backend

### Modules (`backend/app/`)

| Module | Role |
|---|---|
| `main.py` | FastAPI app, lifespan (migrate → sweep interrupted runs → re-queue unread sources → re-key legacy tenants → push the reader's guide, ensure the lists, rebuild every index → start the scheduler), `/health`, `/me`, `/about`, `/clean`, `/devices` |
| `auth.py` | Who is asking: a session token (browser) or a device token (client). `CurrentUser`, `Person` (session only), `CurrentRole`, `CurrentProfile`, `CurrentIdentity`. Dispatches once, on `AUTH_PROVIDER`, to the provider's RS256 path or to `accounts.py` |
| `accounts.py` | The built-in identity provider: e-mail + scrypt password hash, HS256 session tokens; register and log in, nothing else |
| `teams.py` | Teams, memberships, invites, roles → permissions; `needs(permission)` dependencies |
| `files.py` | Everything under `/teams/{team}/bundles/…`: bundles CRUD, tree, read/write, raw upload/move/delete, sources, activity, undo/redo, todos, assistant, lint schedule, queue/retry, reorganise, folders. Also `safe_path`, `tenant()`, guide refresh, startup re-queue |
| `ingest.py` | The agent: task texts, the tool set, the per-bundle worker, `ingest_safely`, holds and retries, `busy()` |
| `runs.py` | The `ingest_run` table and everything derived from it |
| `history.py` | git inside the bundle: commit, record, reverse (path-scoped), take_back (undo), put_back (redo), diff, log entries, pending changes |
| `index.py` | `index.md` built from the pages' frontmatter, after every run and undo |
| `graph.py` / `gaps.py` | The link graph built from the files in memory; Louvain areas; structural gaps (thin pairs of areas) |
| `assist.py` | The assistant: a second agent with the mirror-image permissions (writes `raw/` and `todo.md`, never `wiki/`) |
| `todos.py` | the two lists — `questions.md`, `todo.md` — as checkbox lines: parse, tick, append; `ensure` seeds both and migrates a pre-split `todo.md` |
| `schedule.py` | Nightly lint: a daemon thread, per-bundle hour, decided from run history; the same sweep syncs every connection that is due |
| `connectors/` | The plugin contract (`base.py`: `Connector`, `Field`, `Item`, `Pull`, `Grant`, `OAuth`, `ConnectorError`), the registry (built-ins + the `mindkeep.connectors` entry-point group), the `website` and `drive` built-ins |
| `connections.py` | A connector configured on a bundle: catalog, CRUD, sync now; a connection that needs a sign-in references one of the caller's grants |
| `grants.py` | A person's standing with a provider — a pasted token, or a sign-in with the provider: the OAuth dance (`start`, `callback`, PKCE, signed state), `fresh` (refresh before use, revoked marked), `configured` — made once, usable by any connection they set up. User-facing: a sign-in, under Settings → Account → *Connectors* |
| `syncing.py` | One sync: pull, diff against `connector_item`, write, commit, queue; mirror semantics; `due()`; `disconnect()` |
| `vault.py` | Secret fields sealed at rest (Fernet, key from `DEVICE_SECRET`), redacted on the way out, kept when the marker comes back |
| `devices.py` | Per-machine tokens: create, holder, mine, forget |
| `db.py` | SQLAlchemy models and session; `now()` |
| `templates/manual.md` | The agent's system prompt. Never leaves the server |
| `templates/CLAUDE.md` | The reader's guide seeded into every bundle |

### Authentication

Two credentials share one `Authorization: Bearer` header — and the first has two sources,
chosen **explicitly** by `AUTH_PROVIDER` (`oidc`, the default when unset, or `builtin`;
never inferred from a missing setting, so a deploy that loses a variable fails closed):

- **A browser** sends the identity provider's access token. Any OIDC provider that issues
  RS256 tokens and publishes its keys works: verification is signature against the
  issuer's JWKS (via discovery, or `AUTH_JWKS_URL`), `iss`, expiry, and `aud` only when
  `AUTH_AUDIENCE` is set. Profile (`email`, `given_name`, `family_name`, `picture`) and
  role (`AUTH_ROLE_CLAIM`, default Kinde's `roles`) are read off the claims. **There is no
  user table** — a person is their `sub`.
- **With `AUTH_PROVIDER=builtin`, the browser sends Mindkeep's own session token.** No
  provider at all: `accounts.py` keeps e-mail + password (scrypt, parameters stored with
  the hash) in the `account` table and signs HS256 tokens with `AUTH_SECRET` (30 days,
  claims `sub` = `local_…`, `email`, `name`). Two routes — register, log in — and that is
  the whole of it: registration is open, there are no roles, no throttle, no reset, no
  e-mail verification; logout is the browser forgetting the token; revoking every session
  is rotating `AUTH_SECRET`. Kept to the bone by decision — anything more is added when
  someone needs it. The HS256 path lives entirely in `accounts.py`; the RS256 pin in
  `auth.py` never sees those tokens.
- **A machine** sends a device token, `<device id>.<hmac>`: the HMAC (`DEVICE_SECRET`)
  proves it was minted here, the `device` row says whose it is, and its absence means
  revoked. Devices are minted and revoked only with a browser session (`auth.Person`);
  a device may never mint devices. Rotating `DEVICE_SECRET` revokes everything.

The **connect flow** signs a machine in without typing: the client listens on a loopback
port with a nonce and opens `https://<site>/?connect=1&port=…&nonce=…&name=…`; the signed-in
person clicks *Connect*; the site mints a device and sends the browser to
`http://127.0.0.1:<port>/?token=…&nonce=…`. The request survives the provider's redirect
through sessionStorage (`frontend/src/handoff.ts`), the same trick invite links use.

### Teams and permissions

Roles are named sets of permissions (`teams.GRANTS`):

| Role | Permissions |
|---|---|
| viewer | read |
| contributor | read, write |
| admin | + history, bundles, members |
| owner | + team |

Routes ask for a **permission**, never a role — `Writer`, `Manager`, `Historian` in
`files.py` are `teams.needs("write" / "bundles" / "history")`. `GET /teams` returns each
team's `permissions`, and the UI gates with `can(team, "write")`. Personal teams cannot be
left, deleted, renamed or invited to. Invites are one-use links, 7 days, states
open/used/expired. Teams are Mindkeep's own tables, never the provider's organisations —
that is what keeps auth provider-agnostic.

### The ingest pipeline

1. **Trigger.** An upload (`POST …/raw/{path}`), an edit of a source (`PUT …/files/raw/…`),
   a deletion of a cited source, a manual `POST …/ingest/{path}` or `…/retry`, a lint's
   hour, a reorganise request — each calls `ingest.enqueue(home, source)`.
2. **Queue.** One `queue.Queue` and one daemon worker thread per bundle, created on first
   use. A source already *waiting* is not queued twice; a source *running* is queued
   again (it may have changed since the run read it). The queue is in-process memory.
3. **Worker.** `ingest_safely(home, source)`:
   - a source byte-for-byte what the last clean, not-undone run read is **skipped** before
     a run row opens (`history.same` against the run's `based_on`) — unless a person asked
     for it (*Ingest again*, *retry*: `enqueue(…, force=True)`), which runs it regardless;
   - opens the run row; commits people's changes as `before run N`; records `based_on`;
   - for a re-ingest, computes `git diff` of the source since the last clean read and hands
     it to the agent as a `CHANGED` hint (removed lines = withdrawn claims);
   - calls `ingest()`, which runs Claude (`MODEL`, adaptive thinking, medium effort) with
     the manual as system prompt and the task text for the source kind;
   - commits `run N: <source>`, closes the row (turns, chars, error, note).
4. **Failure classes.** An error that is the **file's** (the agent could not parse it, a
   tool misuse) marks the run failed and the worker moves on. An error that is the
   **service's** — credit balance, rate limit, overloaded, connection, auth, 4xx/5xx
   (`ingest.service_error`) — **holds** the bundle: the source stays at the front and is
   retried after 1, 2, 4, 8, then 15 minutes; `held(home)` exposes the hold to the UI;
   `resume(home)` ends the wait early (the *retry now* button, or any `…/retry` call).
5. **Restart amnesia.** Because the queue is memory, startup repairs what a stop lost:
   `runs.sweep_interrupted` closes runs that were open and re-queues them;
   `files.requeue_unread` re-queues every raw file with no run at all and every source
   whose latest failure was the service's.

**PDF and .docx sources** ride on the task message as a `document` block
(`ingest.attachment`) rather than through `read_file`: a PDF as itself, which the API
reads as text *and* as page images — scans, charts and multi-column layouts survive; a
`.docx` as its text (the API takes PDF and plain text, not Word), under the same shape.
`read_file` on that path points at the attachment. The API's ceiling is 32 MB and 600
pages; a larger PDF is refused with a sentence rather than guessed at. Other binaries are
reported as binary.

**The agent's tools** (closures in `ingest.ingest`): `read_file`, `write_file`,
`edit_file` (batched exact-match edits, all-or-nothing), `move_file` (rename on disk,
content never passes through the model — what a reorganise uses), `delete_file` (prunes
emptied folders), `list_files`, `related` (what a page links to, what links to it, what shares a
source — the pages a change can put out of date). The agent may not touch `raw/`
(`agent_owns`); the assistant may not touch `wiki/`.

**Task kinds** (`ingest.py` constants): `INGEST_TASK` (+ `CHANGED` hint),
`RETIRE_TASK` (source deleted while pages cite it: drop the claims that rested on it),
`LINT_TASK` (+ `MOVED` list of server-recorded source moves, + `GAPS` pairs of thinly
connected areas), `REORGANISE_TASK` (+ `MISFILED`: the server names every page outside
its type's folder and where it goes, so the run moves rather than reads). A reply cut off
at `max_tokens` fails the run with a message rather than ending it silently with nothing
applied — the output budget is 16k tokens, so a turn that re-emits many whole pages
cannot finish; the tools are shaped to keep content on the server. The manual (`templates/manual.md`) is the
authoritative description of each; read it end to end once — it is the spec the agent is
held to, and the tests of ingest behaviour are tests of that text.

### Lint

`schedule.py` wakes every fifteen minutes and asks, per bundle, "linted today?" — decided
from run history, so a restart at 02:59 does not double-lint and a server down all night
lints when it comes back. Each bundle picks its hour in Settings (`bundle_setting`);
`LINT_HOUR` is the default; hours are UTC. A lint reports (contradictions, orphans, stale
drafts, uncited sources, misfiled pages, future-dated log entries), **fixes only broken
source links** (moved vs. gone), puts what a person must *answer* in `questions.md` and
what a person must *do* in `todo.md`, and turns knowledge gaps into questions. When
it finishes cleanly and the server finds pages outside their type's folder
(`ingest.misfiled`), a reorganise run is queued behind it automatically.

### Connectors

A **connector** is code that reads one kind of third-party source and hands back files
(`app/connectors/`). A **connection** is a connector set up on one bundle with its own
settings, secrets and schedule (`app/connections.py`): "the team wiki in Notion", "the
pricing sheet at this URL". Everything a connector does not have to do is the plumbing's,
the same for every connector:

- **Where files land.** Under `raw/connectors/<folder>/` — the connector's `folder`, its kind
  unless it says — through the same road an upload
  takes — `raw_path` for safe names, a scoped commit (`sync <name>: +a ~c -r`), an ingest
  queued per changed file. The connection's folder is the connection's (**mirror
  semantics**): a file in it edited, deleted or moved out of band — the web app, the
  desktop client, whose `raw/` syncs both ways — is put back at the next sync; the
  person's version is in the history. A removed source retires its pages, as a delete does.
- **What changed.** `connector_item` holds one row per file the connection wrote, keyed by
  the source's own id: unchanged is skipped, renamed is a move, missing is removed. A
  connector may return the whole set each time (`Pull(complete=True)`, the default) or
  only what changed plus the ids that went (`complete=False`, `removed=[…]`), with its own
  opaque `cursor` handed back on the next pull.
- **Grants — sign-ins.** A connector's `auth` is `none`, `token` or `oauth2`. A *grant* is a
  person's standing with the provider: for a `token` kind, the secrets of `grant_fields`,
  tried by `check_grant` (which names it — an e-mail, a workspace); for an `oauth2` kind,
  the tokens of the provider's sign-in, which `grants.py` runs from the connector's
  `OAuth` declaration (below). A grant is the person's, not a bundle's: made
  once in Settings → Account → *Connectors*, usable by any connection they set up in any
  team, and passed to `check` and `pull` as a `Grant` with its secrets in the clear for
  that call only. A connection keeps syncing with its maker's grant into a bundle other
  people read — the person put their credential to work for that bundle. Deleting a
  grant never cascades: connections that used it keep their rows and files and report
  *the sign-in this connection used is gone* at their next sync, until given another.
- **One connection of a kind per bundle; the form holds the plural.** A connection's
  `fields` are its scope. A field may be `rows` — a list of rows, each with sub-fields
  (the sites of a website connection: address, depth, frequency), sent as JSON — or
  `multiline` (a list, one per line), or have `options` (a choice). A connector that
  keeps its own clock (each site on its own frequency) sets `tick` — minutes — and the
  plumbing lets it look that often, with no interval on the connection; it decides what
  is due, remembers it in its cursor, and returns `complete=False` with only what changed.
  The connector `name(config)`s the connection — the hosts of a website connection —
  never a person; the name follows the settings.
- **The provider sign-in (OAuth 2).** A connector declares `OAuth(provider, authorize_url,
  token_url, scopes, params)`; the app's own client id and secret are `<PROVIDER>_CLIENT_ID`
  / `<PROVIDER>_CLIENT_SECRET` in the environment (`GOOGLE_…` for Drive), and a kind whose
  provider is not configured is listed but not offered. `GET /grants/oauth/{kind}/start`
  (signed in) answers the consent-page URL: PKCE (S256), and a `state` that is a signed
  note — HMAC on `DEVICE_SECRET` — of who asked and for what, good for ten minutes. The
  provider sends the browser to `GET /grants/oauth/{kind}/callback` (no bearer token: the
  state is the proof), which trades the code for tokens, asks the connector's
  `check_grant` what to call the grant, keeps the tokens sealed with `expires_at`, and
  sends the browser back to `WEB_URL/?connected=<kind>` (or `?connect_error=…`); the app
  lands on Settings → Account → Connectors and says so. The redirect URI is
  `<API_PUBLIC_URL>/grants/oauth/{kind}/callback`, `API_PUBLIC_URL` defaulting to
  `WEB_URL` + `/api` (right when Caddy proxies /api). Before every use — a sync, or a
  `check` — `grants.fresh` renews an access token within 90 s of expiring and reseals
  the grant; a refresh the provider refuses (`invalid_grant`: revoked) sets the grant's
  `error` and the connection reports it. Google needs `access_type=offline` and
  `prompt=consent` to hand over a refresh token — one consent screen per sign-in.
  Grants are per kind: a Drive grant will not serve a Gmail connector (its scopes and
  consent differ), by decision, not accident.
- **Secrets.** Fields the connector marks `secret` are Fernet-encrypted at rest
  (`app/vault.py`, key derived from `DEVICE_SECRET`), never sent to a browser (a marker
  says one is set; the marker sent back means "keep it"), tried by the connector's `check`
  before they are saved.
- **Schedule.** `schedule.py`'s sweep also runs every enabled connection whose interval
  (`every`, minutes, 15 min to 7 days) has passed since its last attempt, each in its own
  thread; a failing source is retried at its own pace. *Sync now* is a route.
- **Permissions.** Managing connections is `bundles` (owners and admins — they hold
  credentials and decide what flows in); *sync now* is `write`.
- **Cost.** Each changed file is one ingest, as an upload is: a first sync of 500 files is
  500 agent runs in a row. The changed-file list in `syncing.apply` is where a batching
  window goes when that is decided.

**Writing a connector.** A subclass of `app.connectors.Connector` with a `kind`:

```python
from app.connectors import Connector, ConnectorError, Field, Item, Pull

class NotionConnector(Connector):
    kind = "notion"                      # stable, lowercase: it names the rows
    title = "Notion"
    blurb = "The pages a Notion integration can see."
    auth = "token"                       # "none" | "token" | "oauth2" (declared, not yet done)
    grant_fields = (Field("token", "Integration token", secret=True, help="Settings → Integrations"),)
    fields = (Field("pages", "Pages", multiline=True, help="Page links, one per line; empty for all", required=False),)

    def check_grant(self, secrets):      # tried before a sign-in is kept; returns its name
        me = notion(secrets["token"]).users.me()   # raises ConnectorError when refused
        return me["name"]

    def check(self, config, grant):      # tried before a connection is saved
        ...

    def pull(self, config, cursor, grant):   # files, and a cursor for next time
        api = notion(grant.token)
        return Pull(items=[Item(id=page_id, path=f"{title}.md", content=markdown)])
```

Built-ins are found in the package; anyone else's is a Python package installed into the
backend image that names its class under the entry-point group `mindkeep.connectors`
(`[project.entry-points."mindkeep.connectors"] notion = "mindkeep_notion:NotionConnector"`).
Both show up in `GET /teams/{t}/connectors` on the next start; a plugin with a built-in's
kind replaces it. `app/connectors/website.py` is the worked example and the first real
connector: a list of sites, each the page at an address and the pages it links to on the
same site — under the address's path when it has one, so `x.com/docs` is that section —
up to its own `pages` (20 unless said), on its own frequency, each kept as **Markdown**:
whole-page HTML→Markdown (`markdownify`; scripts, styles, nav and footer dropped; links
made absolute; `title`, `source` and `description` up top), not readability-style
extraction, which drops the headings and lists of any page that is not an article. An
address that is not HTML — a PDF, a feed — is kept as the file it is. Conversion is
byte-stable across fetches, so a page is folded in again only when it actually changed.

`app/connectors/drive.py` is the first provider connector: a Google sign-in
(`drive.readonly`, nothing more — the grant's name comes from Drive's own `about`), then
folders as rows, each on its own frequency — named as a person sees them from the top of
My Drive (`Clients/Acme`; a name matching two folders is refused with the count, never
guessed) or as the folder's link or id (which reaches a shared drive). Docs, Sheets and
Slides are exported as Markdown, CSV and text; other files come as they are up to 20 MB;
forms, shortcuts and sites are skipped, and so is a file that fails to download (tried
next time). The cursor holds each file's `modifiedTime` per folder, so a tick fetches
only what changed; the Drive id is the item's identity, so a rename is a move; a file
reached through two folders of the list is taken once; bounded at 500 files and 100
folders per row per tick. REST over httpx, no Google SDK.

The app manages connections in Settings → Bundle, where they have the right-hand column
(`Connections.tsx`).

### Graph and gaps

Nothing is stored: `graph.build(home)` reads every page's links and `sources` and builds
a NetworkX graph in milliseconds. `graph.areas()` runs a seeded Louvain; `gaps.find()`
names pairs of sizeable areas with fewer links than chance would give them, and
`describe()` names the most central pages on each side — what the lint is told, and what
the Graph tab draws. `graph.related(path)` is the agent's `related` tool.

### The assistant

`POST …/assist` starts one turn as a **job** and returns its id at once; the browser polls
`GET …/assist/{job}` every two seconds until it is done. A turn can run for minutes and
anything in front of the API (Cloudflare cuts a request at 100 s) would give up on it. The
browser sends the whole conversation each time; the server holds only the turn in flight,
in memory, for an hour (no conversation table — deliberately, until someone needs to
leave a thread and come back). It may write `raw/`, tick and add to `questions.md`, and
add tasks to `todo.md` (`task` tool); writing a source triggers
an ingest like any upload. It may not write a wiki page, because a page is derived from
its source and the next ingest would throw the edit away.

### Database

Postgres holds **metadata only** — the wiki is files. Tables (`db.py`):

| Table | What |
|---|---|
| `ingest_run` | every run: tenant, bundle, source, timing, model, error, note, `based_on`, `commit`, `undone_at` |
| `bundle_setting` | per-bundle lint hour |
| `source_move` | moves the server performed, handed to the next lint, settled when it acts |
| `connection` | a connector on a bundle: kind, name, sealed config, cursor, interval, enabled, last attempt and how it went |
| `connector_item` | one row per file a connection wrote: the source's id, the path, the digest |
| `grant` | a person's standing with a provider: kind, label, sealed secrets, `expires_at` for OAuth; keyed by `sub`, like a device |
| `team`, `membership`, `invite` | teams |
| `device` | per-machine tokens |

Migrations are Alembic (`backend/alembic/versions/`), run **at startup** by `main.migrate()`
— safe because one replica starts at a time; move it to a release command before scaling
out. Every query on a tenant table filters on `tenant` (`runs._where`).

### Environment (`backend/.env.example`)

`GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` (a Google Cloud OAuth client of type *Web application*, the Drive API enabled, redirect URI `<API_PUBLIC_URL>/grants/oauth/drive/callback`), `API_PUBLIC_URL` (blank: `WEB_URL` + `/api`),
`DATABASE_URL` (`postgresql+psycopg://…`), `WIKI_ROOT` (`/data`), `LINT_HOUR`,
`ANTHROPIC_API_KEY`, `DEVICE_SECRET`, `AUTH_PROVIDER` (`builtin` | `oidc`), builtin only:
`AUTH_SECRET`; oidc only: `AUTH_ISSUER`,
`AUTH_AUDIENCE`, `AUTH_JWKS_URL`, `AUTH_ROLE_CLAIM`; `WEB_URL` (where the site is, for the connect page), `ALLOWED_ORIGINS`
(CORS, only for a split-origin deploy), `HOST`/`PORT` (the deploy image's bind).

### API reference

Unprefixed:

| Method | Path | Who | What |
|---|---|---|---|
| GET | `/health` | — | liveness |
| GET | `/about` | — | `{web: WEB_URL}` |
| GET | `/me` | any | id, name, role, profile from the token |
| POST | `/auth/register` · `/auth/login` | — | built-in only: `{token}` (201 / 200); 409 for an e-mail already taken, 401 for a wrong password |
| POST | `/clean` | any | the names `raw/` paths will be stored under (the client asks before uploading) |
| GET/POST | `/devices` | person | list / mint (token returned once) |
| DELETE | `/devices/{id}` | person | revoke |
| GET/POST | `/teams` | any | list mine (with permissions) / create |
| PUT/DELETE | `/teams/{team}` | owner | rename / delete (typed-name confirm in the UI) |
| GET | `/teams/{team}/members` | member | |
| PUT/DELETE | `/teams/{team}/members/{sub}` | members | set role / remove (or leave) |
| GET/POST/DELETE | `/teams/{team}/invites[/{token}]` | members | list / make / revoke |
| GET/POST | `/invites/{token}[/accept]` | any | peek / accept |

Under `/teams/{team}`, membership required:

| Method | Path | Perm | What |
|---|---|---|---|
| GET/POST | `/bundles` | read / bundles | list / create |
| PUT/DELETE | `/bundles/{b}` | bundles | rename / delete (keeps ≥1) |
| PUT | `/bundles/{b}/team` | bundles | move to another team |
| GET | `/bundles/{b}/tree` | read | `path → sha256`, dot dirs excluded |
| GET | `/bundles/{b}/files/{path}` · `/text/{path}` | read | raw bytes · readable text (`.docx` extracted with the stdlib; other binaries are reported as binary — a PDF reaches the agent as a document instead, see the ingest pipeline) |
| PUT | `/bundles/{b}/files/{path}` | write | write a source (queues an ingest), or rewrite a wiki page that exists (commits, queues nothing); `If-Match` honoured. A new page, anything but `.md` under `wiki/`, and the root files are refused (409) |
| POST | `/bundles/{b}/raw/{path}` | write | upload (name cleaned, ingest queued) |
| DELETE | `/bundles/{b}/raw/{path}` | write | delete; a cited source starts a retire run |
| POST | `/bundles/{b}/move` | write | move a source; recorded for the lint |
| GET/POST/DELETE | `/bundles/{b}/folders[/{path}]` | read/write | folders under `raw/` |
| GET | `/bundles/{b}/sources` | read | every source with its ingest state |
| POST | `/bundles/{b}/ingest/{path}` · `/retry` | write | ingest again · one or every failed source, ends a hold |
| GET | `/bundles/{b}/queue` | read | `{held, waiting, failed}` |
| GET | `/bundles/{b}/activity` · `/runs/{id}` | read | the feed (runs, people's changes, undo/redo, pending) · a run's changed files |
| POST | `/bundles/{b}/runs/{id}/undo` · `/redo` | history | |
| GET/PUT/POST | `/bundles/{b}/lint` | read / write | state · set hour · lint now |
| POST | `/bundles/{b}/reorganise` | write | file every page by its type |
| GET | `/connectors` | signed in | the catalog: every connector installed, its fields, whether it is available |
| GET/POST | `/bundles/{b}/connections` | read / bundles | list · set one up (credentials tried first; first sync started) |
| PUT/DELETE | `/bundles/{b}/connections/{id}` | bundles | settings, secrets, interval, enabled · remove it and everything it wrote |
| POST | `/bundles/{b}/connections/{id}/sync` | write | sync now (202; 409 while one runs) |
| GET/POST | `/grants` | signed in | your sign-ins, with how many connections use each · add one (a token, tried first; the connector names it) |
| GET | `/grants/oauth/{kind}/start` | signed in | the provider's consent-page URL (PKCE, signed state) |
| GET | `/grants/oauth/{kind}/callback` | the state | where the provider sends the browser; trades the code, keeps the grant, redirects to the app |
| DELETE | `/grants/{id}` | signed in | gone at once; connections that used it stay and say so at their next sync |
| GET/POST | `/bundles/{b}/questions[/{index}]` · `/todos[/{index}]` | read / write | the questions · the tasks: list, tick |
| POST / GET | `/bundles/{b}/assist[/{job}]` | write / read | start one assistant turn (202, `{job}`) · poll it: `{done:false}`, then the reply and what changed, or an error |
| POST | `/bundles/{b}/verify/{path}` | write | stamp a page `verified` by the caller's identity |
| GET | `/bundles/{b}/graph` | read | nodes, edges, areas, gaps |

---

## 4. Frontend

React 19 + TypeScript + Vite; no router (the app is tabs and query-string pages); no state
library; no CSS framework — `index.css` with the marketing site's tokens (`--clay`,
`--cream`, `--warm`, `--ink`…) and faces: Instrument Serif for titles, Space Grotesk for
everything else, mono for paths. The header and the login page are the site's clay field.

### Structure (`frontend/src/`)

| File | Role |
|---|---|
| `main.tsx` | mounts `<Boundary><Gate/></Boundary>` and the app-wide `<Dialogs/>` |
| `auth.tsx` | the gate: picks the adapter named by `VITE_AUTH_PROVIDER`, checks `VITE_API_URL`, shows sign-in, remembers invite/connect requests across the redirect |
| `providers/session.ts` · `builtin.tsx` · `oidc.tsx` | the adapter contract (`Provider` + `useSession()`, optional `Login` form) and two adapters. `builtin` (the default when `VITE_AUTH_PROVIDER` is unset) keeps the session token in localStorage and renders its own sign-in / register form; `oidc.tsx` is the standard code flow with PKCE (react-oidc-context) for any provider — Kinde in production, with the `offline` scope for a refresh token |
| `api.ts` | every call to the backend; `setTeam`/`at(bundle)` build the prefixed URLs; `can(team, permission)`; types (`Team`, `Entry`, `Queue`, `Device`, `Lint`…) |
| `App.tsx` | header (wordmark, team & bundle pickers, tabs, account menu) and the pages |
| `Library.tsx` + `FileTree.tsx` + `dropped.ts` | the tree, drag-and-drop upload, folders, the page view with provenance and trust, verify, delete, *Re-ingest* (a clean run is skipped by the queue; this is the one way to ask for it), *Edit* on a wiki page or a markdown source |
| `Grants.tsx` | Settings → Account: *Connectors* — every connector and what it needs (none, a token, a provider sign-in not yet possible), your sign-ins per connector with how many connections use each, add (the connector's `grant_fields`) and remove |
| `Connections.tsx` | Settings → Bundle, the right-hand column: the bundle's connections — list with state, *sync now*, add (*Add a connection ▾*, a menu of the server's connectors with what they do or why they cannot be picked — a sign-in missing, or one Mindkeep cannot do yet; then the form drawn from the connector's `fields` — no name: the connector names it — a `rows` field as a small table with *add another*, a `multiline` field as a textarea, no interval when the connector ticks, a sign-in picked from yours; a connector already connected is not offered again), edit (secrets as the marker, interval, paused), remove. Nothing here knows what a connector wants |
| `Editor.tsx` | the WYSIWYG editor: Milkdown's Crepe (remark in, remark out — footnotes, tables and fences round-trip), loaded lazily on *Edit*. Frontmatter is kept aside verbatim by `forEditing`; saves carry `If-Match` (sha256 of the text as read) and a 412 keeps the editor open |
| `Graph.tsx` | the force-laid-out link graph, areas coloured, *Show gaps* mode |
| `Todo.tsx` | two panels: Questions (one at a time, with the assistant chat) and Tasks (a checklist) |
| `Activity.tsx` | the feed from git history + runs; undo/redo; the ingest-paused and failed banners |
| `Settings.tsx` (+ `TeamSettings.tsx`, `Members.tsx`) | tabs Bundle / Team / Account: lint hour, reorganise, rename/move/delete bundle; team rename/delete, members, invites; devices |
| `Picker.tsx`, `Bundles.tsx`, `Invite.tsx`, `Connect.tsx`, `Profile.tsx` | pickers and the two link-driven pages |
| `dialog.tsx` | the app's own `confirm()`/`prompt()` — promise-based, one host at the root |
| `okf.ts` | frontmatter split, markdown render (sanitised — wiki text may quote sources; `marked-footnote` turns the agent's `[^src]` citations into references and a footnotes list), `forEditing` (frontmatter aside, the stray `</content>` an ingest leaves at the foot dropped) |
| `useSources.ts` | polls sources fast while the agent works and slowly when not; `version` bumps when an ingest finishes so views reload; `useLint` |
| `invites.ts`, `handoff.ts` | sessionStorage stashes for invite and connect links |

### Conventions

- `api.ts` throws `Error("<status> <detail>")`; views strip the status for display.
- Anything gated is gated by **permission** (`can(team, "history")`), never by role name.
- `VITE_*` values are **baked at build time**. The Dockerfile's `build` stage declares each
  as an `ARG`; a new variable needs a new `ARG` line or Railway's value never reaches
  `vite build`.
- In development the Vite dev server proxies `/api` → `http://backend:8000`, so the site and
  API share an origin and there is no CORS to configure.
- Tests: Vitest + Testing Library + jsdom. `npx tsc --noEmit -p tsconfig.json`, `npx eslint
  src`, `npx vitest run`, `npx prettier --print-width 100`.

---

## 5. The desktop client (`client/`)

One Python package, `mindkeep/`, three faces:

| Module | What |
|---|---|
| `sync.py` | the engine, **stdlib only**: `sync(cfg)` for one bundle in one folder |
| `cli.py` | `mindkeep login \| sync \| watch [--config PATH]` — one config = one bundle |
| `connect.py` | the loopback browser sign-in (`about`, `sign_in`), shared by CLI and app |
| `app/` | the PySide6 tray app |
| `__init__.py` | `__version__` (0.0.0 in git, stamped from the tag) and `USER_AGENT` |

### The sync engine

`cfg = {server, token, folder, team, bundle}`. Each pass:

1. `GET …/tree` → `path → sha256` for the whole bundle (dot dirs excluded).
2. **Rename new files first** — `POST /clean` gives the names the server would store them
   under; the file is renamed on disk before upload so both sides agree.
3. **Deletions here** — a file that was on the server at the last sync and is gone from
   disk is deleted over there. The **state file** (`~/.mindkeep-state.json`, keyed by
   folder|bundle) is what tells "new here" from "deleted there"; without it, guessing
   either way loses data. A whole-folder disappearance (unmounted drive, moved folder) is
   detected by the rest of the mirror being gone too, and nothing is deleted.
4. **Renames** — a local file whose bytes match a server file at another path is sent as a
   *move* (`POST …/move`), so the lint repoints citations instead of the pages being
   deleted and rewritten.
5. **Up** — `raw/` is yours: a file changed *here* since the last sync is uploaded, with
   `If-Match` of the last-seen hash. If it also changed *there*, yours is kept under
   `.conflicts/<path>` and theirs lands in place. Nothing outside `raw/` goes up.
6. **Down** — everything else (`wiki/`, `index.md`, `log.md`, `questions.md`, `todo.md`,
   `CLAUDE.md`) is overwritten from the server, and files the server no longer has are swept (dot paths excepted).

Hooks `sync.say(*parts)` and `sync.notify(cfg, kind, text)` are module attributes the tray
app replaces; the CLI prints. Every request carries `User-Agent: Mindkeep/<version>` —
Cloudflare's browser check, in front of `mindkeep.io`, drops Python's default agent.

### The tray app (`mindkeep/app/`)

| Module | What |
|---|---|
| `main.py` | `QApplication`, single instance via `QLocalServer` (`io.mindkeep.app`), AppUserModelID on Windows, wires tray ↔ worker ↔ window |
| `config.py` | `~/.mindkeep/app.json`: `{server, token, root, watch:[{team, name, bundle, folder}]}`; a folder is fixed when a bundle is added (`<root>/<team>/<bundle>`, suffixed on a name clash) and never re-derived |
| `worker.py` | **one** `QThread` looping over the watched bundles sequentially (the state file is read-modify-write; parallel syncs would drop each other's state), then `GET …/sources` for failures |
| `alerts.py` | pure dedupe: a failing ingest once until it stops failing; unreachable once after three misses; dead token once per sign-out |
| `tray.py` | `QSystemTrayIcon`, menu: status · Sync now · Pause · Open folder ▸ · Settings… · Log… · Start at login · Quit |
| `settings.py` | the window: Settings tab (API address, sign in via browser or paste a token, root folder, a tree of teams with a checkbox per bundle) and Log tab |
| `log.py` | the in-memory log buffer (last 500 lines, stamped) |
| `autostart.py` | Run key / LaunchAgent plist / autostart `.desktop` |
| `mark.py` | the one icon, drawn with QPainter — tray, windows, taskbar and the installer icons all come from `paint()` |

Balloons appear only when a person is needed: a conflict kept aside, an ingest that
failed, a token that stopped working, the server unreachable.

**The API address** in the app is where the API answers *from the machine*. Deployed
behind the site's proxy that is `https://main.mindkeep.io/api`; in development
`http://localhost:8001`.

### Packaging and release

- `pyproject.toml`: the package (`mindkeep`), scripts `mindkeep` and `mindkeep-app`, the
  `app` extra (PySide6), ruff config, and `[tool.briefcase]` for app `mindkeep-app`
  (module `mindkeep_app/`, a two-line shim). Windows installs per user to
  `%LocalAppData%\Programs\Mimetik\Mindkeep`.
- **The tag is the version.** `version` is `0.0.0` in git; `stamp.py vX.Y.Z` writes it into
  `pyproject.toml` and `mindkeep/__init__.py`. `changelog.py` writes `CHANGELOG.md` from the
  commits that touched `client/` since the previous tag — so commit subjects on the client
  are written as sentences a user could read. Neither file is typed by hand.
- `.github/workflows/app.yml`: on a `v*` tag (or by hand), a matrix builds
  `.msi` / `.dmg` (ad-hoc signed) / `.deb` (the Linux leg runs Briefcase from the *system*
  Python, as its `.deb` backend requires) and attaches them to a GitHub release with the
  generated changelog as notes. **Unsigned**: Windows warns, macOS needs right-click → Open.
- Releasing: `git tag -a v0.2.0 -m "0.2.0" && git push origin v0.2.0`. That is all.

---

## 6. Local development

**Prerequisites:** Docker Desktop, Node 22 (for running frontend checks on the host),
Python 3.12+ (for the client and backend tests), git.

```
cp backend/.env.example backend/.env      # fill ANTHROPIC_API_KEY, DEVICE_SECRET, AUTH_*
cp frontend/.env.example frontend/.env    # VITE_AUTH_* for your identity provider
docker compose up -d --build
```

- Frontend: http://localhost:5163 (Vite dev server, hot reload, proxies `/api`).
- Backend: http://localhost:8001 (uvicorn `--reload`; the `dev` Dockerfile stage).
- Postgres: localhost:5433, user/db/password `mindkeep`. Volumes `mindkeep_pgdata`,
  `mindkeep_wikidata` (the wiki, at `/data` in the container).
- Compose builds the `dev` **target** of each Dockerfile; the last stage of each is the
  deploy image (built site behind Caddy; uvicorn without the reloader).

**Checks** (what CI and every commit in the log ran):

```
# backend — from backend/, in a venv with the dependency list + pytest ruff mypy
ruff check app tests && ruff format --check app tests && mypy app && pytest -q tests
# frontend — from frontend/
npx tsc --noEmit -p tsconfig.json && npx eslint src && npx vitest run
# client — from client/, `pip install -e ".[app,dev]"`
ruff check . && pytest -q
```

**Gotchas**

- The backend package is not pip-installable from the host (setuptools discovery sees
  `tests/`, `alembic/`…); install its dependency list instead, or run tests in the
  container. The Docker copy installs fine.
- After a frontend dependency change, `docker compose up -d -V frontend` — `node_modules`
  is an anonymous volume that survives image rebuilds.
- Files are CRLF in working copies on Windows and LF in git (`core.autocrlf`); write
  files with LF.
- The dev database was renamed from `mindstash` in place; the old volumes may still exist
  as `mindstash_*` on a machine that predates the rename.
- Tests use `tenant_id("alice")` for directories, never a literal name, and `x-test-user`
  headers to switch identity; new access paths get traversal and cross-tenant tests in
  `backend/tests/test_files.py`.

---

## 7. Deployment (Railway)

Three services in one project: `db` (Postgres), `backend`, `frontend`. The env files
`backend/.env.railway` and `frontend/.env.railway` (git-ignored) are the variables to paste.

- **The backend has no public domain.** The frontend image is Caddy (`frontend/Caddyfile`):
  it serves the built site with SPA fallback and reverse-proxies `/api/*` to
  `API_UPSTREAM` (`http://<backend service>.railway.internal:8000`) over the private
  network. One origin, no CORS. The backend binds `HOST=::` there, because Railway's
  private network is IPv6-only and uvicorn binds one family; `PORT=8000` is fixed so the
  proxy knows where to find it.
- `DATABASE_URL` is assembled from `${{db.PGUSER}}` etc. with the `+psycopg` scheme.
- A volume mounted at `/data` on the backend holds every bundle.
- The site is behind **Cloudflare**; its Browser Integrity Check drops unknown user
  agents, which is why every client request names itself.
- Redeploys kill in-flight work: the running run is re-queued at startup, and so is
  everything that was waiting (see *restart amnesia*). Migrations run at startup.
- The identity provider (Kinde today) needs the site's origin in its allowed callback and
  logout URLs.

---

## 8. How we work

- **Commits are atomic and their subjects are sentences** — "Startup re-queues every
  source uploaded but never read: a deploy mid-sync forgot 32 of 38". They double as the
  client's release notes.
- **Docstrings say why, not what.** A `ponytail:` comment marks a deliberate simplification
  and the condition under which it should be revisited (`grep -rn ponytail`).
- **Tests are the spec** for the sync engine and the ingest behaviour; a change to either
  starts with the test.
- **The manual is code.** `templates/manual.md` is versioned, reviewed and tested like the
  rest; a behaviour you want from the agent goes there, in prose it can follow, not in a
  prompt tweak somewhere else.
- **Decisions are logged** in the architecture and decisions log (a Mindkeep source itself,
  §4.x entries) — with the reasoning, so the next person can disagree with the reason
  rather than the rule.

---

## 9. Glossary

| Term | Meaning |
|---|---|
| bundle | one knowledge base: a directory with `raw/`, `wiki/`, `index.md`, `log.md`, `todo.md`, `.git` |
| source | a file under `raw/`; the unit of ingestion |
| run | one piece of agent work over a bundle: ingest, retire, lint, reorganise |
| retire | the run that removes what rested on a deleted source |
| lint | the nightly maintenance pass |
| reorganise | the run that files pages by their type |
| hold | the worker waiting to retry a source after a service failure |
| area | a Louvain community of the link graph |
| gap | two sizeable areas with fewer links than chance would give them |
| note | a finding a local agent contributes, one per file, under `raw/notes/<person>/` |
| device | a machine's own revocable token |
| mirror | a synced copy of a bundle on someone's machine |
| conflict copy | your version of a file both sides changed, under `.conflicts/` |
| the manual | `templates/manual.md`, the agent's system prompt |
| the guide | `templates/CLAUDE.md`, seeded into every bundle for local readers |

---

## 10. Known gaps and where to look next

- The ingest queue is per-process memory: one backend replica. Scaling out means moving
  the queue (and the migration step) out of the process.
- No global cap on concurrent runs across bundles, and no fairness between teams.
- The `oidc` adapter has only been exercised against a fake; a Keycloak check is owed.
  Clerk would be a fourth adapter in `providers/`, same shape.
- The built-in provider is register and log in only: no password change or reset, no
  e-mail verification, no roles, no throttle — by decision, until someone needs one.
- Each hold retry opens a failed run row; a long outage leaves a few per hour in Activity.
- `edit_file` is occasionally called with `edits` as a JSON string; the SDK rejects it and
  the model retries — a wasted turn.
- PDFs are not text-extracted: a PDF source reaches the agent as "binary", so a page is
  written from its name at best. `.docx` is handled; PDF needs an extractor.

---

The decisions behind all of this, in the order they were made, are in
*Mindkeep - Dev Log.md* beside this document.
