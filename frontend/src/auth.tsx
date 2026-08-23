import { KindeProvider, useKindeAuth } from "@kinde-oss/kinde-auth-react";
import { useEffect } from "react";

import { setTokenSource } from "./api";
import { App } from "./App";
import { Mark } from "./icons";

const env = import.meta.env;

/** Only ever rendered inside KindeProvider — the hook throws anywhere else. */
function Session() {
  const auth = useKindeAuth();
  setTokenSource(auth.getAccessToken);

  const { isLoading, isAuthenticated, register } = auth;

  // ALL-ORGS, as in Futuros and iclonic: every registration creates a Kinde org, and
  // the query string picks which kind. Plain visits fall through to the sign-in screen.
  //   /?account_type=org&org_name=Acme   a real team
  //   /?account_type=user                a personal org, named "Personal"
  //   /?invitation_code=…                joins the INVITER's org, never creates one
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

  if (auth.isLoading) return <div className="login" />;

  if (!auth.isAuthenticated) {
    return (
      <div className="login">
        <Mark size={64} />
        <h1>Mindstash</h1>
        <p>A second brain that reads what you feed it.</p>
        <button className="primary" onClick={() => auth.login()}>Sign in</button>
      </div>
    );
  }

  // The profile proper comes from GET /me, which reads Kinde live rather than from
  // claims minted when this session started. These are the fallback for when that read
  // cannot answer — a Kinde outage, or M2M credentials that are not authorised yet.
  const claims = {
    first_name: auth.user?.givenName ?? "",
    last_name: auth.user?.familyName ?? "",
    name: [auth.user?.givenName, auth.user?.familyName].filter(Boolean).join(" "),
    email: auth.user?.email ?? "",
    picture: auth.user?.picture ?? "",
  };

  return <App user={{ signOut: () => auth.logout(), claims }} />;
}

export function Gate() {
  const origin = window.location.origin;
  return (
    <KindeProvider
      domain={env.VITE_KINDE_DOMAIN}
      clientId={env.VITE_KINDE_CLIENT_ID}
      redirectUri={env.VITE_KINDE_REDIRECT_URI || origin}
      logoutUri={env.VITE_KINDE_LOGOUT_URI || origin}
      forceChildrenRender
    >
      <Session />
    </KindeProvider>
  );
}
