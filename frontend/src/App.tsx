import { useEffect, useState } from "react";

import { createTeam, listBundles, listTeams, type Profile as Person, setTeam, type Team } from "./api";
import { Bundles } from "./Bundles";
import { Console } from "./Console";
import { Graph } from "./Graph";
import { Invite } from "./Invite";
import { forget, pending } from "./invites";
import { Mark } from "./icons";
import { Library } from "./Library";
import { Picker } from "./Picker";
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
  const [teams, setTeams] = useState<Team[]>([]);
  const [team, setCurrentTeam] = useState<Team | null>(null);
  const [bundles, setBundles] = useState<string[]>([]);
  const [bundle, setBundle] = useState("default");
  const [tab, setTab] = useState<Tab>("Library");
  // from the address, or kept across a sign-in that started from an invite link
  const [invite, setInvite] = useState(pending);
  const [error, setError] = useState("");

  // The team comes first: every bundle URL carries it, so nothing loads until one is chosen.
  const choose = (t: Team) => {
    setTeam(t.id);
    setCurrentTeam(t);
    setBundle("default");
    setTab("Library");
  };

  useEffect(() => {
    listTeams()
      .then((all) => {
        setTeams(all);
        if (all.length && !invite) choose(all[0]); // personal first, so a first visit lands home
      })
      .catch((e) => setError(String(e)));
  }, []);

  // After a rename, a leave or a delete: the list is re-read, and if the team you were
  // looking at is no longer yours, you land back home.
  const reloadTeams = () =>
    listTeams()
      .then((all) => {
        setTeams(all);
        const still = all.find((t) => t.id === team?.id);
        if (still) setCurrentTeam(still);
        else if (all.length) choose(all[0]);
      })
      .catch((e) => setError(String(e)));

  useEffect(() => {
    if (!team) return;
    listBundles()
      .then((b) => {
        setBundles(b);
        if (b.length && !b.includes(bundle)) setBundle(b[0]);
      })
      .catch((e) => setError(String(e)));
  }, [team]);

  if (invite) {
    return (
      <Invite
        token={invite}
        onJoined={(joined) => {
          forget();
          setInvite("");
          setTeams((all) => (all.some((t) => t.id === joined.id) ? all : [...all, joined]));
          choose(joined);
        }}
        onDismiss={() => {
          forget();
          setInvite("");
          if (teams.length) choose(teams[0]);
        }}
      />
    );
  }

  return (
    <div className="app">
      <header className="header">
        <div className="wordmark">
          <Mark />
          Mindstash
        </div>

        {team && (
          <Picker
            items={teams.map((t) => ({ id: t.id, label: t.name }))}
            current={team.id}
            title="Teams"
            placeholder="New team"
            onPick={(id) => {
              const next = teams.find((t) => t.id === id);
              if (next) choose(next);
            }}
            onCreate={async (name) => {
              const made = await createTeam(name);
              setTeams((all) => [...all, made]);
              return made.id;
            }}
          />
        )}

        {team && (
          <Bundles
            bundles={bundles}
            current={bundle}
            onPick={setBundle}
            onCreate={(name) => {
              setBundles((b) => [...b, name].sort());
              setBundle(name);
            }}
          />
        )}

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

      {/* keyed by team: a change of team remounts every view, so nothing shows stale data */}
      {team && (
        <div className="body" key={team.id}>
          {tab === "Library" && <Library bundle={bundle} />}
          {tab === "Graph" && <Graph bundle={bundle} />}
          {tab === "Todo" && <Todo bundle={bundle} />}
          {tab === "Console" && <Console bundle={bundle} />}
          {tab === "Settings" && (
            <Settings
              bundle={bundle}
              team={team}
              teams={teams}
              onTeamChanged={reloadTeams}
              onBundleMoved={(id, moved) => {
                const next = teams.find((t) => t.id === id);
                if (next) {
                  choose(next);
                  setBundle(moved);
                }
              }}
            />
          )}
        </div>
      )}
    </div>
  );
}
