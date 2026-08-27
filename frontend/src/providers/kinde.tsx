import { KindeProvider, useKindeAuth } from "@kinde-oss/kinde-auth-react";
import { type ReactNode, useEffect } from "react";

import { type Adapter, settings } from "./session";

/**
 * Kinde, through its own React SDK.
 *
 * The one Kinde-only behaviour lives here and nowhere else: ALL-ORGS registration, as in
 * Futuros and iclonic — every registration creates a Kinde org, and the query string
 * picks which kind. Plain visits fall through to the sign-in screen.
 *   /?account_type=org&org_name=Acme   a real team
 *   /?account_type=user                a personal org, named "Personal"
 *   /?invitation_code=…                joins the INVITER's org, never creates one
 */
function Registration() {
  const { isLoading, isAuthenticated, register } = useKindeAuth();
  useEffect(() => {
    if (isLoading || isAuthenticated) return;
    const params = new URLSearchParams(window.location.search);

    const invitationCode = params.get("invitation_code");
    if (invitationCode) {
      void register({ invitationCode });
      return;
    }

    const orgName = params.get("org_name");
    if (params.get("account_type") === "org") {
      void register({ isCreateOrg: true, ...(orgName ? { orgName } : {}) });
    } else if (params.get("account_type") === "user") {
      void register({ isCreateOrg: true, orgName: "Personal" });
    }
  }, [isLoading, isAuthenticated, register]);
  return null;
}

function Provider({ children }: { children: ReactNode }) {
  const { issuer, clientId, redirectUri, logoutUri } = settings();
  return (
    <KindeProvider
      domain={issuer}
      clientId={clientId}
      redirectUri={redirectUri}
      logoutUri={logoutUri}
      forceChildrenRender
    >
      <Registration />
      {children}
    </KindeProvider>
  );
}

function useSession() {
  const auth = useKindeAuth();
  return {
    loading: auth.isLoading,
    signedIn: auth.isAuthenticated,
    claims: {
      first_name: auth.user?.givenName ?? "",
      last_name: auth.user?.familyName ?? "",
      name: [auth.user?.givenName, auth.user?.familyName].filter(Boolean).join(" "),
      email: auth.user?.email ?? "",
      picture: auth.user?.picture ?? "",
    },
    login: () => void auth.login(),
    logout: () => void auth.logout(),
    token: auth.getAccessToken,
  };
}

export const kinde: Adapter = { Provider, useSession };
