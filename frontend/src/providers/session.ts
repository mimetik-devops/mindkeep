import type { ReactNode } from "react";

import type { Profile } from "../api";

/**
 * What Mindkeep needs from an identity provider — all of it.
 *
 * A provider adapter is a React `Provider` that owns the browser session, and a
 * `useSession` hook that reports it in these terms. Everything else in the app talks to
 * the backend with the bearer token; nothing else imports a provider's SDK. Adding a
 * provider is one file in this folder and one line in `auth.tsx`.
 */
export type Session = {
  /** Still finding out whether there is a session — show nothing yet. */
  loading: boolean;
  signedIn: boolean;
  /** The ID token's own profile claims: what the UI shows when GET /me cannot answer. */
  claims: Partial<Profile>;
  login: () => void;
  logout: () => void;
  /** The access token the backend verifies; undefined when signed out. */
  token: () => Promise<string | undefined>;
};

export type Adapter = {
  Provider: (props: { children: ReactNode }) => ReactNode;
  useSession: () => Session;
};

/** The settings every adapter reads. The names are the same whichever provider. */
export const settings = () => {
  const env = import.meta.env;
  const origin = window.location.origin;
  return {
    issuer: String(env.VITE_AUTH_ISSUER ?? ""),
    clientId: String(env.VITE_AUTH_CLIENT_ID ?? ""),
    redirectUri: String(env.VITE_AUTH_REDIRECT_URI || origin),
    logoutUri: String(env.VITE_AUTH_LOGOUT_URI || origin),
  };
};
