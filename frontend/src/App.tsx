import { useEffect, useState } from "react";

import { listBundles, type Profile as Person } from "./api";
import { Console } from "./Console";
import { Graph } from "./Graph";
import { Chevron, Mark } from "./icons";
import { Library } from "./Library";
import { Profile } from "./Profile";
import { Settings } from "./Settings";
import { Todo } from "./Todo";

const TABS = ["Library", "Graph", "Todo", "Console"] as const;
// Settings is a page but not a tab — the account menu is its only entry point.
type Tab = (typeof TABS)[number] | "Settings";

export type User = {
  signOut?: () => void;
  /** Name, email and picture from the ID token, for when GET /me cannot answer. */
  claims: Partial<Person>;
};

export function App({ user }: { user: User }) {
  const [bundles, setBundles] = useState<string[]>([]);
  const [bundle, setBundle] = useState("default");
  const [tab, setTab] = useState<Tab>("Library");
  const [error, setError] = useState("");

  useEffect(() => {
    listBundles()
      .then((b) => {
        setBundles(b);
        if (b.length && !b.includes(bundle)) setBundle(b[0]);
      })
      .catch((e) => setError(String(e)));
  }, []);

  return (
    <div className="app">
      <header className="header">
        <div className="wordmark">
          <Mark />
          Mindstash
        </div>

        <button
          className="pill"
          title="Switch bundle"
          disabled={bundles.length < 2}
          onClick={() => setBundle(bundles[(bundles.indexOf(bundle) + 1) % bundles.length])}
        >
          {bundle}
          <Chevron />
        </button>

        <nav className="tabs">
          {TABS.map((t) => (
            <button key={t} className="tab" aria-current={tab === t} onClick={() => setTab(t)}>
              {t}
            </button>
          ))}
        </nav>

        <div className="grow" />
        {error && <span className="error">{error}</span>}
        <Profile
          signOut={user.signOut}
          claims={user.claims}
          onSettings={() => setTab("Settings")}
        />
      </header>

      {tab === "Library" && <Library bundle={bundle} />}
      {tab === "Graph" && <Graph bundle={bundle} />}
      {tab === "Todo" && <Todo bundle={bundle} />}
      {tab === "Console" && <Console bundle={bundle} />}
      {tab === "Settings" && <Settings bundle={bundle} />}
    </div>
  );
}
