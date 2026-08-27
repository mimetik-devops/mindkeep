import type { ReactNode } from "react";
import { AuthProvider, useAuth } from "react-oidc-context";

import { type Adapter, settings } from "./session";

/**
 * Any OIDC provider, through the standard authorization-code flow with PKCE:
 * Keycloak, Auth0, Zitadel, Logto, Authentik, Kinde itself. Register the app as a
 * public single-page client with the redirect and logout URIs from the settings.
 *
 * Clerk is not on that list — it is not a generic OIDC login for single-page apps and
 * wants its own SDK — so it would be a third adapter, in this folder, in this shape.
 */
function Provider({ children }: { children: ReactNode }) {
  const { issuer, clientId, redirectUri, logoutUri } = settings();
  return (
    <AuthProvider
      authority={issuer}
      client_id={clientId}
      redirect_uri={redirectUri}
      post_logout_redirect_uri={logoutUri}
      scope="openid profile email"
      // the code and state in the URL after the round trip are spent; drop them
      onSigninCallback={() => window.history.replaceState({}, document.title, window.location.pathname)}
    >
      {children}
    </AuthProvider>
  );
}

function useSession() {
  const auth = useAuth();
  const who = auth.user?.profile;
  return {
    loading: auth.isLoading,
    signedIn: auth.isAuthenticated,
    claims: {
      first_name: who?.given_name ?? "",
      last_name: who?.family_name ?? "",
      name: who?.name ?? [who?.given_name, who?.family_name].filter(Boolean).join(" "),
      email: who?.email ?? "",
      picture: who?.picture ?? "",
    },
    login: () => void auth.signinRedirect(),
    logout: () => void auth.signoutRedirect(),
    token: async () => auth.user?.access_token,
  };
}

export const oidc: Adapter = { Provider, useSession };
