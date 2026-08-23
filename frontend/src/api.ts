const BASE = import.meta.env.VITE_API_URL ?? "/api";

// Kinde owns the browser session; auth.tsx hands us its token getter once it has one.
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
    let said = body;
    try {
      said = (JSON.parse(body) as { detail?: string }).detail ?? body;
    } catch {
      /* not JSON — a proxy or a crash, and the raw body is the best we have */
    }
    throw new Error(`${res.status} ${said}`);
  }
  const type = res.headers.get("content-type") ?? "";
  return (type.includes("json") ? res.json() : res.text()) as Promise<T>;
}

const segments = (path: string) => path.split("/").map(encodeURIComponent).join("/");

export const listBundles = () => call<string[]>("/bundles");

export const createBundle = (name: string) =>
  call<{ name: string }>("/bundles", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });

/** path -> sha256, for every file in the bundle. */
export const tree = (bundle: string) => call<Record<string, string>>(`/bundles/${bundle}/tree`);

export const readFile = (bundle: string, path: string) =>
  call<string>(`/bundles/${bundle}/files/${path}`);

/**
 * A source as the agent reads it — a .docx unzipped, anything UTF-8 as itself.
 *
 * Rejects with 415 for a format nothing can extract, which is the honest answer for a
 * PDF today. The bytes route stays for downloads.
 */
export const readAsText = (bundle: string, path: string) =>
  call<string>(`/bundles/${bundle}/text/${path}`);

export const writeFile = (bundle: string, path: string, body: string) =>
  call<{ path: string }>(`/bundles/${bundle}/files/${path}`, { method: "PUT", body });

/** The server stamps `verified` with the identity on the token — never one we send. */
export const verifyPage = (bundle: string, path: string) =>
  call<{ verified_by: string; at: string }>(`/bundles/${bundle}/verify/${path}`, {
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
    `/bundles/${bundle}/raw/${segments(rel || file.webkitRelativePath || file.name)}`,
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
};

/** Every raw source and whether the agent has folded it in yet. */
export const sources = (bundle: string) => call<Source[]>(`/bundles/${bundle}/sources`);

/** Only raw sources can be deleted. There is no route that removes a wiki page. */
/**
 * Folders under raw/, empty ones included.
 *
 * They cannot come from the tree, which maps files to hashes — an empty folder has no
 * file to carry it, and a folder you have just made is empty by definition.
 */
export const folders = (bundle: string) => call<string[]>(`/bundles/${bundle}/folders`);

export const addFolder = (bundle: string, path: string) =>
  call<{ folder: string }>(`/bundles/${bundle}/folders/${segments(path)}`, { method: "POST" });

export const removeFolder = (bundle: string, path: string) =>
  call<{ deleted: string }>(`/bundles/${bundle}/folders/${segments(path)}`, { method: "DELETE" });

/** Move a source within raw/. Both paths are bundle-relative, as everywhere else. */
export const moveRaw = (bundle: string, source: string, target: string) =>
  call<{ from: string; to: string }>(`/bundles/${bundle}/move`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source, target }),
  });

export const removeRaw = (bundle: string, path: string) =>
  call<{ deleted: string }>(`/bundles/${bundle}/raw/${segments(path)}`, { method: "DELETE" });

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

/** The hour value that means "never lint this bundle on a schedule". */
export const LINT_OFF = -1;

/** Choose the hour (UTC) for this bundle's nightly lint, or LINT_OFF to stop it. */
export const setLintHour = (bundle: string, hour: number) =>
  call<{ hour: number }>(`/bundles/${bundle}/lint`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ hour }),
  });

export const lintState = (bundle: string) => call<Lint>(`/bundles/${bundle}/lint`);

/** Run the maintenance pass now. The nightly one does exactly this, on a timer. */
export const startLint = (bundle: string) =>
  call<{ linting: string }>(`/bundles/${bundle}/lint`, { method: "POST" });

/** Composed from Kinde on every read — Mindstash keeps no user table of its own. */
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

/** An open question the wiki agent could not settle. `id` is its position in todo.md. */
export type Todo = { id: number; done: boolean; text: string; detail: string };

export const todos = (bundle: string) => call<Todo[]>(`/bundles/${bundle}/todos`);

export const setTodo = (bundle: string, id: number, done: boolean) =>
  call<{ done: boolean }>(`/bundles/${bundle}/todos/${id}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ done }),
  });

export type Said = { role: "user" | "assistant"; content: string };

/**
 * One turn with the assistant.
 *
 * The whole exchange goes up each time: the server keeps no conversation, so the browser
 * is where it lives. `changed` is the raw files it edited, each of which is being
 * re-ingested by the time this returns.
 */
export const ask = (bundle: string, question: string, messages: Said[]) =>
  call<{ reply: string; changed: string[] }>(`/bundles/${bundle}/assist`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, messages }),
  });

export const deviceToken = () => call<{ token: string }>("/device-token");
