const BASE = import.meta.env.VITE_API_URL ?? "/api";

/**
 * Why VITE_API_URL cannot be used, or "" when it can. Checked at the gate so a site
 * built with a half-resolved value ("https://" when a Railway reference came up empty)
 * says so in one sentence instead of failing every request with a DNS error.
 */
export function apiProblem(base: string = BASE): string {
  if (base.startsWith("/")) return "";
  const url = /^https?:\/\/[^/]+/.test(base);
  return url
    ? ""
    : `VITE_API_URL is "${base}". It must be a path like /api or an absolute URL like https://api.example.com.`;
}

// The identity provider owns the browser session; auth.tsx hands us its token getter.
// The API also accepts a device token, which is what the desktop client sends.
let bearer: () => Promise<string | undefined> = async () => undefined;
export const setTokenSource = (getter: typeof bearer) => (bearer = getter);

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const token = await bearer();
  const res = await fetch(BASE + path, {
    ...init,
    headers: { Authorization: `Bearer ${token ?? ""}`, ...init?.headers },
  });
  if (!res.ok) {
    // FastAPI puts the sentence a person can read in `detail`; the JSON around it was
    // going straight into error banners, which helped nobody
    const body = await res.text();
    throw new Error(`${res.status} ${sentence(body)}`);
  }
  const type = res.headers.get("content-type") ?? "";
  return (type.includes("json") ? res.json() : res.text()) as Promise<T>;
}

/** The sentence a person can read out of an error body: FastAPI's `detail`, the title of
 * an HTML error page (a proxy's timeout, say), or the raw text when it is neither. */
export function sentence(body: string): string {
  try {
    const detail = (JSON.parse(body) as { detail?: string }).detail;
    if (detail) return detail;
  } catch {
    /* not JSON */
  }
  const title = /<title>([^<]*)<\/title>/i.exec(body);
  if (title) return title[1].trim();
  return body.slice(0, 300);
}

const segments = (path: string) => path.split("/").map(encodeURIComponent).join("/");

// Every bundle lives in a team, and the app looks at one team at a time. App.tsx sets it
// when the team changes and remounts everything below, so nothing else passes it around.
let current = "";
export const setTeam = (id: string) => (current = id);
const at = (bundle: string) => `/teams/${current}/bundles/${bundle}`;

export const listBundles = () => call<string[]>(`/teams/${current}/bundles`);

export const createBundle = (name: string) =>
  call<{ name: string }>(`/teams/${current}/bundles`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });

/** path -> sha256, for every file in the bundle. */
export const tree = (bundle: string) => call<Record<string, string>>(`${at(bundle)}/tree`);

export const readFile = (bundle: string, path: string) =>
  call<string>(`${at(bundle)}/files/${path}`);

/**
 * A source as the agent reads it — a .docx unzipped, anything UTF-8 as itself.
 *
 * Rejects with 415 for a format nothing can extract, which is the honest answer for a
 * PDF today. The bytes route stays for downloads.
 */
export const readAsText = (bundle: string, path: string) =>
  call<string>(`${at(bundle)}/text/${path}`);

/** `ifMatch` is the sha256 of the file as it was read: the server refuses with 412 when
 * it has moved on since, rather than let a stale copy win. Without it, last write wins. */
export const writeFile = (bundle: string, path: string, body: string, ifMatch = "") =>
  call<{ path: string }>(`${at(bundle)}/files/${path}`, {
    method: "PUT",
    body,
    headers: ifMatch ? { "If-Match": ifMatch } : {},
  });

/** The sha256 the tree reports for a file, computed here from its text. */
export async function digest(text: string): Promise<string> {
  const hash = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(hash)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** The server stamps `verified` with the identity on the token — never one we send. */
export const verifyPage = (bundle: string, path: string) =>
  call<{ verified_by: string; at: string }>(`${at(bundle)}/verify/${path}`, {
    method: "POST",
  });

/**
 * Upload one source, at `rel` under raw/ — folders and all.
 *
 * The default covers the file picker: `webkitRelativePath` is filled in when the file
 * came from a directory picker ("papers/2026/x.md") and empty otherwise. A dropped
 * folder carries its shape somewhere else entirely, so that caller passes `rel` itself.
 * Each segment is encoded on its own, so the slashes survive as slashes while the spaces
 * and brackets in the names do not.
 */
export const addRaw = (bundle: string, file: File, rel = "") =>
  call<{ path: string }>(
    `${at(bundle)}/raw/${segments(rel || file.webkitRelativePath || file.name)}`,
    { method: "POST", body: file },
  );

export type Source = {
  path: string;
  ingesting: boolean;
  seconds: number;
  pages: number;
  error: string;
  took: number;
  /** The last thing the agent did, while it is still working. Empty otherwise. */
  note: string;
  /** Recorded when a run finished cleanly — not inferred from what cites the file. */
  ingested: boolean;
  /** Moved since the last lint, so the pages citing it still name the old path. */
  moved: boolean;
  /** The latest run's id when it wrote something, else 0 — what Undo takes back. */
  run: number;
  /** That run has been taken back; the source reads as not ingested until run again. */
  undone: boolean;
};

/**
 * One thing that happened to a bundle: an agent run (with the log entry it wrote),
 * changes by people between runs, or an undo. From the bundle's history.
 */
export type Entry = {
  kind: "run" | "people" | "undo" | "redo" | "pending";
  at: string;
  commit: string;
  changed: { status: string; path: string }[];
  id?: number;
  source?: string;
  finished_at?: string | null;
  seconds?: number;
  error?: string;
  undone?: boolean;
  note?: string;
  subject?: string;
  /** ingest, lint or retire — a deleted source's pages being taken out. */
  task?: string;
};

export const activity = (bundle: string) => call<Entry[]>(`${at(bundle)}/activity`);
export const runDetail = (bundle: string, run: number) =>
  call<{ changed: { status: string; path: string }[] }>(`${at(bundle)}/runs/${run}`);

/** Revert the undo of a run: the wiki and the source come back. `history`. */
export const redoRun = (bundle: string, run: number) =>
  call<{ redone: number; commit: string }>(`${at(bundle)}/runs/${run}/redo`, { method: "POST" });

/** The wiki and this run's source back to before it. `history`. */
export const undoRun = (bundle: string, run: number) =>
  call<{ undone: number; commit: string }>(`${at(bundle)}/runs/${run}/undo`, { method: "POST" });

/** Every raw source and whether the agent has folded it in yet. */
export const sources = (bundle: string) => call<Source[]>(`${at(bundle)}/sources`);

/** Only raw sources can be deleted. There is no route that removes a wiki page. */
/**
 * Folders under raw/, empty ones included.
 *
 * They cannot come from the tree, which maps files to hashes — an empty folder has no
 * file to carry it, and a folder you have just made is empty by definition.
 */
export const folders = (bundle: string) => call<string[]>(`${at(bundle)}/folders`);

export const addFolder = (bundle: string, path: string) =>
  call<{ folder: string }>(`${at(bundle)}/folders/${segments(path)}`, { method: "POST" });

export const removeFolder = (bundle: string, path: string) =>
  call<{ deleted: string }>(`${at(bundle)}/folders/${segments(path)}`, { method: "DELETE" });

/** Move a source within raw/. Both paths are bundle-relative, as everywhere else. */
export const moveRaw = (bundle: string, source: string, target: string) =>
  call<{ from: string; to: string }>(`${at(bundle)}/move`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source, target }),
  });

export const removeRaw = (bundle: string, path: string) =>
  call<{ deleted: string }>(`${at(bundle)}/raw/${segments(path)}`, { method: "DELETE" });

/** `at` is the day the last lint finished, empty if the bundle has never been linted. */
export type Lint = {
  linting: boolean;
  seconds: number;
  at: string;
  error: string;
  note: string;
  turns: number;
  /** ISO-8601 UTC of the next automatic lint. Empty when the nightly pass is off. */
  next: string;
  /** The hour (UTC) this bundle lints at, or LINT_OFF. */
  hour: number;
};

export type Queue = {
  /** The worker is waiting to retry a source after the service failed — no credit, an outage. */
  held: { source: string; reason: string; until: string; attempts: number } | null;
  waiting: number;
  /** Sources whose latest run ended in an error. */
  failed: string[];
};
export const queueState = (bundle: string) => call<Queue>(`${at(bundle)}/queue`);
/** Ingest again: one source, or every failed one. Ends a hold early. */
export const retryIngest = (bundle: string, path = "") =>
  call<{ queued: string[] }>(`${at(bundle)}/retry`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });

/** The hour value that means "never lint this bundle on a schedule". */
export const LINT_OFF = -1;

/** Choose the hour (UTC) for this bundle's nightly lint, or LINT_OFF to stop it. */
export const setLintHour = (bundle: string, hour: number) =>
  call<{ hour: number }>(`${at(bundle)}/lint`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ hour }),
  });

export const lintState = (bundle: string) => call<Lint>(`${at(bundle)}/lint`);

/** Run the maintenance pass now. The nightly one does exactly this, on a timer. */
export const startLint = (bundle: string) =>
  call<{ linting: string }>(`${at(bundle)}/lint`, { method: "POST" });

const post = <T>(path: string, body: unknown) =>
  call<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
/** The built-in provider: a session token for an e-mail and a password. */
export const signIn = (email: string, password: string) =>
  post<{ token: string }>("/auth/login", { email, password });
export const signUp = (email: string, password: string, name: string) =>
  post<{ token: string }>("/auth/register", { email, password, name });

/** Composed from Kinde on every read — Mindkeep keeps no user table of its own. */
export type Profile = {
  id: string;
  name: string;
  first_name: string;
  last_name: string;
  email: string;
  picture: string;
  role: string;
};

export const me = () => call<Profile>("/me");

/** One checkbox line of a list the agent keeps: a question in questions.md, or a task in
 * todo.md. `id` is its position among the checkboxes. */
export type Item = { id: number; done: boolean; text: string; detail: string };

export const questions = (bundle: string) => call<Item[]>(`${at(bundle)}/questions`);
export const tasks = (bundle: string) => call<Item[]>(`${at(bundle)}/todos`);
const flip = (bundle: string, list: string, id: number, done: boolean) =>
  call<{ done: boolean }>(`${at(bundle)}/${list}/${id}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ done }),
  });
/** A question answered some other way; or put back. */
export const setQuestion = (bundle: string, id: number, done: boolean) =>
  flip(bundle, "questions", id, done);
/** A task done; or put back. */
export const setTask = (bundle: string, id: number, done: boolean) =>
  flip(bundle, "todos", id, done);

export type Said = { role: "user" | "assistant"; content: string };

/**
 * One turn with the assistant.
 *
 * The whole exchange goes up each time: the server keeps no conversation, so the browser
 * is where it lives. `changed` is the raw files it edited, each of which is being
 * re-ingested by the time this returns.
 */
export async function ask(bundle: string, question: string, messages: Said[]) {
  // a turn is a job: started at once, polled until done — a request that waited for the
  // whole turn would be cut off by whatever sits in front of the API
  const { job } = await call<{ job: string }>(`${at(bundle)}/assist`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, messages }),
  });
  for (;;) {
    await new Promise((r) => setTimeout(r, 2000));
    const state = await call<{ done: boolean; reply?: string; changed?: string[]; error?: string }>(
      `${at(bundle)}/assist/${job}`,
    );
    if (!state.done) continue;
    if (state.error) throw new Error(state.error);
    return { reply: state.reply ?? "", changed: state.changed ?? [] };
  }
}

/** A wiki page as the graph view draws it. `area` is -1 for a page in no area. */
export type Page = {
  path: string;
  title: string;
  description: string;
  area: number;
  sources: string[];
};

/**
 * Two areas that barely connect: `links` observed between them against `expected`, what
 * random wiring with the same degrees would give. `a` and `b` are area indices.
 */
export type Gap = { a: number; b: number; links: number; expected: number };

/** Pages, the links between them, and the gaps — as the agent is told them. Rebuilt per call. */
export type Graph = { pages: Page[]; links: [string, string][]; gaps: Gap[] };

export const graph = (bundle: string) => call<Graph>(`${at(bundle)}/graph`);

/** A team you belong to, with what you are in it. Personal teams come first. */
export type Team = {
  id: string;
  name: string;
  personal: boolean;
  role: Role;
  /** What the role lets you do here — gate on these, never on the role's name. */
  permissions: Permission[];
};
export type Role = "owner" | "admin" | "contributor" | "viewer";
export type Permission = "read" | "write" | "history" | "bundles" | "members" | "team";
export const can = (team: Team, p: Permission) => team.permissions.includes(p);
export type Member = { sub: string; name: string; email: string; role: Role; since: string };
export type Invite = {
  token: string;
  role: Role;
  created_by: string;
  expires_at: string;
  accepted_by: string | null;
  accepted_at: string | null;
  /** The member's name (or email) when the link was used, else "". */
  accepted_name: string;
  /** A used link stays on the list, marked, rather than vanishing. */
  state: "open" | "used" | "expired";
};

const json = (body: unknown, method = "POST") => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const listTeams = () => call<Team[]>("/teams");
// ---- connections: a connector (a plugin on the server) configured on a bundle ----

/** One field of a connector's form, as the server describes it. */
export type ConnectorField = {
  name: string;
  label: string;
  secret: boolean;
  help: string;
  required: boolean;
  /** a list, one per line */
  multiline: boolean;
  /** a choice: [value, label] pairs */
  options: [string, string][];
  /** a list of rows, each with these sub-fields, sent back as JSON */
  rows: ConnectorField[];
  /** the app offers a browse button; the choices come from the connector, level by level */
  browse: boolean;
};
/** One thing to pick while browsing: what to store, what to show, whether it opens. */
export type Choice = { value: string; label: string; opens: boolean };
/** What a browsable field offers one level down from `at` ("" is the top), asked with
 * the caller's own sign-in. The `bundles` permission. */
export const browseConnector = (
  bundle: string,
  kind: string,
  body: { field: string; at: string; grant?: string },
) => call<{ at: string; choices: Choice[] }>(`${at(bundle)}/connectors/${kind}/browse`, json(body));
/** A connector the server has: what a person can set up. `auth` says whether it needs a
 * sign-in — `grant_fields` is the form for a token; `available` is false for one whose
 * sign-in the server cannot do yet. `fields` is the scope of one connection. */
export type ConnectorKind = {
  kind: string;
  title: string;
  blurb: string;
  auth: "none" | "token" | "oauth2";
  available: boolean;
  /** where its files land in the bundle */
  folder: string;
  /** minutes, when the connector keeps its own clock — then a connection has no interval */
  tick: number;
  fields: ConnectorField[];
  grant_fields: ConnectorField[];
};
/** Your standing with a provider — a sign-in — made once, usable by any connection. */
export type Grant = {
  id: string;
  kind: string;
  label: string;
  created_at: string;
  error: string;
  /** how many connections use it, anywhere */
  uses: number;
};
export const listGrants = () => call<Grant[]>("/grants");
/** Tried first; the connector names it. A 400 says what was wrong. */
export const addGrant = (kind: string, secrets: Record<string, string>) =>
  call<Grant>("/grants", json({ kind, secrets }));
/** The provider's consent page, for the browser to go to; it comes back to the app with
 * `?connected=<kind>` or `?connect_error=…` in the address. */
export const startOAuth = (kind: string) => call<{ url: string }>(`/grants/oauth/${kind}/start`);
/** Connections that used it keep their rows and say so at their next sync. */
export const removeGrant = (id: string) =>
  call<{ deleted: string; orphaned: number }>(`/grants/${id}`, { method: "DELETE" });

/** A connection: secrets come back as REDACTED, and sent back as REDACTED mean "keep". */
export type Connection = {
  id: string;
  kind: string;
  name: string;
  folder: string;
  config: Record<string, string>;
  every: number;
  enabled: boolean;
  syncing: boolean;
  synced_at: string;
  error: string;
  summary: string;
  installed: boolean;
  /** the sign-in it syncs with, or null for a kind that needs none */
  grant: { id: string; label: string } | null;
  /** it had one, and the person revoked it */
  grant_gone: boolean;
};
export const REDACTED = "••••••••";

export const listConnectors = () => call<ConnectorKind[]>(`/teams/${current}/connectors`);
export const listConnections = (bundle: string) => call<Connection[]>(`${at(bundle)}/connections`);
/** The `bundles` permission. The credentials are tried first; a 400 says what was wrong. */
export const addConnection = (
  bundle: string,
  body: { kind: string; config: Record<string, string>; every: number; grant?: string },
) => call<Connection>(`${at(bundle)}/connections`, json(body));
export const updateConnection = (
  bundle: string,
  id: string,
  patch: { config?: Record<string, string>; every?: number; enabled?: boolean; grant?: string },
) => call<Connection>(`${at(bundle)}/connections/${id}`, json(patch, "PUT"));
/** Everything it pulled goes with it; pages resting on those files are retired. */
export const removeConnection = (bundle: string, id: string) =>
  call<{ deleted: string; removed: number }>(`${at(bundle)}/connections/${id}`, {
    method: "DELETE",
  });
/** The `write` permission; 409 while one runs. */
export const syncConnection = (bundle: string, id: string) =>
  call<{ syncing: string }>(`${at(bundle)}/connections/${id}/sync`, { method: "POST" });

/** The `bundles` permission; the name must be free; not mid-ingest. Resolves to the new name. */
export const renameBundle = (bundle: string, to: string) =>
  call<{ name: string }>(at(bundle), json({ to }, "PUT"));

/** The `bundles` permission; not mid-ingest; a team keeps at least one. No undo. */
export const deleteBundle = (bundle: string) =>
  call<{ deleted: string }>(at(bundle), { method: "DELETE" });

/** Owners and admins on both sides; the name must be free there; not mid-ingest. */
export const moveBundle = (bundle: string, to: string) =>
  call<{ team: string; bundle: string }>(`${at(bundle)}/team`, json({ to }, "PUT"));
export const createTeam = (name: string) => call<Team>("/teams", json({ name }));
export const renameTeam = (team: string, name: string) =>
  call<Team>(`/teams/${team}`, json({ name }, "PUT"));
/** Owners only, never a personal team: the members, the invites and the bundles on disk go too. */
export const deleteTeam = (team: string) =>
  call<{ deleted: string }>(`/teams/${team}`, { method: "DELETE" });
export const members = (team: string) => call<Member[]>(`/teams/${team}/members`);
export const setRole = (team: string, sub: string, role: Role) =>
  call<{ role: Role }>(`/teams/${team}/members/${segments(sub)}`, json({ role }, "PUT"));
export const removeMember = (team: string, sub: string) =>
  call<{ removed: string }>(`/teams/${team}/members/${segments(sub)}`, { method: "DELETE" });
export const invites = (team: string) => call<Invite[]>(`/teams/${team}/invites`);
export const createInvite = (team: string, role: Role) =>
  call<Invite>(`/teams/${team}/invites`, json({ role }));
export const revokeInvite = (team: string, token: string) =>
  call<{ revoked: string }>(`/teams/${team}/invites/${token}`, { method: "DELETE" });
/** What an invite is for, before joining. 404 when it is spent or expired. */
export const peekInvite = (token: string) =>
  call<{ team: { id: string; name: string }; role: Role }>(`/invites/${token}`);
export const acceptInvite = (token: string) =>
  call<Team>(`/invites/${token}/accept`, { method: "POST" });

export type Device = { id: string; name: string; created_at: string; last_seen: string | null };
export const devices = () => call<Device[]>("/devices");
/** The token comes back once, here, and never again. */
export const addDevice = (name: string) =>
  call<Device & { token: string }>("/devices", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
export const removeDevice = (id: string) => call(`/devices/${id}`, { method: "DELETE" });
