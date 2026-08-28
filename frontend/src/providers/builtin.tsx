import { createContext, type ReactNode, useContext, useEffect, useState } from "react";

import { signIn, signUp } from "../api";
import type { Adapter } from "./session";

/**
 * Mindkeep's own sign-in: an e-mail and a password, a session token the backend signed.
 *
 * The token lives in localStorage; the profile is read off its payload (no verification
 * here — the backend verifies every request). Signing out is forgetting the token. The
 * form is this adapter's own `Login`, which the gate renders in place of the "Sign in"
 * button the redirect-based providers need.
 */
const KEY = "mindkeep.session";

type Held = { token: string; setToken: (t: string | null) => void };
const Context = createContext<Held>({ token: "", setToken: () => {} });

function stored(): string {
  try {
    return localStorage.getItem(KEY) ?? "";
  } catch {
    return "";
  }
}

/** The token's payload, decoded but not verified: enough for a name in the header. */
export function payload(token: string): Record<string, unknown> {
  try {
    const body = token.split(".")[1] ?? "";
    const json = atob(body.replace(/-/g, "+").replace(/_/g, "/"));
    return JSON.parse(json) as Record<string, unknown>;
  } catch {
    return {};
  }
}

function live(token: string): boolean {
  const exp = payload(token).exp;
  return typeof exp === "number" && exp * 1000 > Date.now();
}

function Provider({ children }: { children: ReactNode }) {
  const [token, hold] = useState(stored);
  const setToken = (t: string | null) => {
    try {
      if (t) localStorage.setItem(KEY, t);
      else localStorage.removeItem(KEY);
    } catch {
      /* storage blocked: the session lasts as long as the tab */
    }
    hold(t ?? "");
  };
  useEffect(() => {
    if (token && !live(token)) setToken(null); // expired while the tab was closed
  }, [token]);
  return <Context.Provider value={{ token, setToken }}>{children}</Context.Provider>;
}

function useSession() {
  const { token, setToken } = useContext(Context);
  const signedIn = !!token && live(token);
  const who = signedIn ? payload(token) : {};
  const name = typeof who.name === "string" ? who.name : "";
  return {
    loading: false,
    signedIn,
    claims: {
      first_name: name.split(" ")[0] ?? "",
      last_name: name.split(" ").slice(1).join(" "),
      name,
      email: typeof who.email === "string" ? who.email : "",
      picture: "",
    },
    login: () => {}, // the form is the way in
    logout: () => setToken(null),
    token: async () => (signedIn ? token : undefined),
  };
}

function Login() {
  const { setToken } = useContext(Context);
  const [creating, setCreating] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    setError("");
    setBusy(true);
    try {
      const { token } = creating
        ? await signUp(email, password, name)
        : await signIn(email, password);
      setToken(token);
    } catch (e) {
      setError(String(e).replace(/^Error: \d{3} /, ""));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      className="signin"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      {creating && (
        <input
          value={name}
          placeholder="Your name"
          autoComplete="name"
          onChange={(e) => setName(e.target.value)}
        />
      )}
      <input
        value={email}
        type="email"
        placeholder="E-mail"
        autoComplete="email"
        required
        onChange={(e) => setEmail(e.target.value)}
      />
      <input
        value={password}
        type="password"
        placeholder={creating ? "Password (8 or more characters)" : "Password"}
        autoComplete={creating ? "new-password" : "current-password"}
        required
        minLength={creating ? 8 : undefined}
        onChange={(e) => setPassword(e.target.value)}
      />
      {error && <div className="banner">{error}</div>}
      <button className="primary" disabled={busy}>
        {busy ? "One moment…" : creating ? "Create account" : "Sign in"}
      </button>
      <button type="button" className="plain" onClick={() => setCreating(!creating)}>
        {creating ? "I have an account" : "Create an account"}
      </button>
    </form>
  );
}

export const builtin: Adapter = { Provider, useSession, Login };
