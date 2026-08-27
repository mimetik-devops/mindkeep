/**
 * A machine asking to be signed in: the desktop client opens
 * `/?connect=1&port=…&nonce=…&name=…` and listens on that port of localhost. The
 * request has to survive the identity provider's round trip, exactly like an invite
 * link does — same sessionStorage trick, same lifetime: this tab, this visit.
 */
const KEY = "mindstash.connect";

export type Connect = { port: number; nonce: string; name: string };

function parse(query: string): Connect | null {
  const q = new URLSearchParams(query);
  if (!q.get("connect")) return null;
  const port = Number(q.get("port"));
  const nonce = q.get("nonce") ?? "";
  // loopback only, and a nonce that cannot smuggle anything into the callback URL
  if (!Number.isInteger(port) || port < 1024 || port > 65535) return null;
  if (!/^[A-Za-z0-9_-]{8,64}$/.test(nonce)) return null;
  return { port, nonce, name: (q.get("name") ?? "").trim().slice(0, 80) || "this machine" };
}

export function rememberConnect(): void {
  const asked = parse(window.location.search);
  if (!asked) return;
  try {
    sessionStorage.setItem(KEY, JSON.stringify(asked));
  } catch {
    /* storage blocked: the address still carries it for a visit that needs no sign-in */
  }
}

export function pendingConnect(): Connect | null {
  const fromUrl = parse(window.location.search);
  if (fromUrl) return fromUrl;
  try {
    const kept = sessionStorage.getItem(KEY);
    return kept ? (JSON.parse(kept) as Connect) : null;
  } catch {
    return null;
  }
}

export function forgetConnect(): void {
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    /* nothing kept */
  }
  if (window.location.search.includes("connect=")) {
    window.history.replaceState(null, "", window.location.pathname);
  }
}

/** Where the machine is listening; the token rides in the query, over loopback only. */
export const callback = (asked: Connect, token: string) =>
  `http://127.0.0.1:${asked.port}/?${new URLSearchParams({ token, nonce: asked.nonce })}`;
