import { apiProblem, setTokenSource } from "./api";
import { App } from "./App";
import { Mark } from "./icons";
import { pending, remember } from "./invites";
import { pendingConnect, rememberConnect } from "./handoff";
import { builtin } from "./providers/builtin";
import { oidc } from "./providers/oidc";
import type { Adapter } from "./providers/session";

/**
 * The gate: whoever VITE_AUTH_PROVIDER names owns the session, and this file only
 * asks it the five things the app needs. A provider's SDK is imported by its adapter
 * under providers/ and by nothing else.
 */
const ADAPTERS: Record<string, Adapter> = { builtin, oidc };

function chosen(): Adapter | string {
  // unset means Mindkeep's own accounts: the build works with no provider configured
  const name = String(import.meta.env.VITE_AUTH_PROVIDER || "builtin");
  return ADAPTERS[name] ?? name;
}

/** Only ever rendered inside the adapter's Provider — its hook throws anywhere else. */
function Session({ adapter }: { adapter: Adapter }) {
  const session = adapter.useSession();
  setTokenSource(session.token);

  if (session.loading) return <div className="login" />;

  if (!session.signedIn) {
    // the address may carry an invite; keep it across the provider's round trip
    remember();
    rememberConnect();
    return (
      <div className="login">
        <Mark size={64} />
        <h1>Mindkeep</h1>
        <p>
          {pendingConnect()
            ? `The Mindkeep app on ${pendingConnect()?.name} wants to sign in as you. Sign in to connect it.`
            : pending()
              ? "You have been invited to a team. Sign in, or register, to join it."
              : "A second brain that reads what you feed it."}
        </p>
        {adapter.Login ? (
          <adapter.Login />
        ) : (
          <button className="primary" onClick={session.login}>
            Sign in
          </button>
        )}
      </div>
    );
  }

  return <App user={{ signOut: session.logout, claims: session.claims }} />;
}

export function Gate({ adapter = chosen() }: { adapter?: Adapter | string }) {
  const api = apiProblem();
  if (api) {
    return (
      <div className="login">
        <Mark size={64} />
        <h1>Mindkeep</h1>
        <p>{api}</p>
      </div>
    );
  }
  if (typeof adapter === "string") {
    return (
      <div className="login">
        <Mark size={64} />
        <h1>Mindkeep</h1>
        <p>
          VITE_AUTH_PROVIDER is "{adapter}". It names the identity provider: one of{" "}
          {Object.keys(ADAPTERS).join(", ")}.
        </p>
      </div>
    );
  }
  return (
    <adapter.Provider>
      <Session adapter={adapter} />
    </adapter.Provider>
  );
}
