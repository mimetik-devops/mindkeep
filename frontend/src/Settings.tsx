import { useEffect, useState } from "react";

import { can, deviceToken, LINT_OFF, moveBundle, setLintHour, startLint, type Team } from "./api";
import { Members } from "./Members";
import { TeamSettings } from "./TeamSettings";
import { Copy } from "./icons";
import { took, useLint, when } from "./useSources";

/**
 * The 24 UTC hours, labelled in the reader's own time and ordered by it.
 *
 * The server schedules in UTC — it has no idea where anyone is, and a laptop that
 * crosses a timezone should not silently move when the wiki gets tidied. Built from
 * real Date objects rather than by adding an offset, so the half-hour zones land right.
 */
function localHours() {
  const now = new Date();
  return Array.from({ length: 24 }, (_, utcHour) => {
    const at = new Date(
      Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), utcHour),
    );
    return {
      utcHour,
      sort: at.getHours() * 60 + at.getMinutes(),
      label: at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
    };
  }).sort((a, b) => a.sort - b.sort);
}

const HOURS = localHours();

export function Settings({
  bundle,
  team,
  teams,
  onTeamChanged,
  onBundleMoved,
}: {
  bundle: string;
  team: Team;
  /** Every team you belong to — the ones you manage are where a bundle can go. */
  teams: Team[];
  /** After a rename, a leave or a delete: the app re-reads its teams. */
  onTeamChanged: () => void;
  /** The bundle now lives over there: the app follows it. */
  onBundleMoved: (team: string, bundle: string) => void;
}) {
  const { lint, refresh } = useLint(bundle);
  const [error, setError] = useState("");
  const [destination, setDestination] = useState("");
  const manages = can(team, "bundles");
  const elsewhere = teams.filter((t) => t.id !== team.id && can(t, "bundles"));
  const [token, setToken] = useState("");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    deviceToken().then((r) => setToken(r.token));
  }, []);

  const act = (work: Promise<unknown>) => {
    setError("");
    work.catch((e: Error) => setError(e.message)).finally(refresh);
  };

  return (
    <div className="settings">
      <h1>Settings</h1>
      <p className="lede">
        <b>{team.name}</b> · bundle <b>{bundle}</b>. Every bundle keeps its own schedule.
      </p>

      {error && <div className="banner">{error}</div>}

      <section className="card">
        <h2>Nightly lint</h2>
        <p>
          The agent re-reads the wiki, reports what has drifted — contradictions, stale
          drafts, entities with no page — and deletes pages whose source you removed.
        </p>

        <label className="field">
          <span>Run automatically at</span>
          <select
            value={lint?.hour ?? LINT_OFF}
            disabled={!lint}
            onChange={(e) => act(setLintHour(bundle, Number(e.target.value)))}
          >
            <option value={LINT_OFF}>Never</option>
            {HOURS.map((h) => (
              <option key={h.utcHour} value={h.utcHour}>
                {h.label}
              </option>
            ))}
          </select>
        </label>

        <div className="field">
          <span>
            {lint?.linting
              ? "Running now — watch it in the Console."
              : lint?.next
                ? `Next ${when(lint.next)}`
                : "Nothing scheduled."}
          </span>
          <button
            className="lint"
            disabled={!lint || lint.linting}
            onClick={() => act(startLint(bundle))}
          >
            {lint?.linting ? "Linting…" : "Lint now"}
          </button>
        </div>

        {lint && !lint.linting && (
          <p className="soft">
            {lint.at
              ? `Last run ${lint.at}, took ${took(lint.seconds)}.`
              : "This bundle has never been linted."}
            {lint.error && ` It ended badly: ${lint.error}`}
          </p>
        )}
      </section>

      {manages && elsewhere.length > 0 && (
        <section className="card">
          <h2>Move this bundle</h2>
          <p>
            <b>{bundle}</b> leaves <b>{team.name}</b> and lands on another team you manage,
            history and all. Not while it is being ingested, and the name must be free there.
          </p>
          <div className="field">
            <span>To</span>
            <select value={destination} onChange={(e) => setDestination(e.target.value)}>
              <option value="">Choose a team</option>
              {elsewhere.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
            <button
              className="lint"
              disabled={!destination}
              onClick={() => {
                setError("");
                moveBundle(bundle, destination)
                  .then((r) => onBundleMoved(r.team, r.bundle))
                  .catch((e: Error) => setError(e.message.replace(/^\d{3} /, "")));
              }}
            >
              Move
            </button>
          </div>
        </section>
      )}

      <TeamSettings team={team} onChanged={onTeamChanged} />

      <Members team={team} />

      <section className="card">
        <h2>On your machine</h2>
        <p>
          The desktop client keeps a copy of a bundle that Claude can read directly, and
          uploads whatever you drop in its raw folder. It signs in with your device token —
          yours, not the bundle's, so one login covers every bundle.
        </p>
        <div className="command">
          <code>python mindstash.py login</code>
          <button
            disabled={!token}
            onClick={() => {
              navigator.clipboard.writeText(token);
              setCopied(true);
            }}
            title="Copy your device token"
          >
            {copied ? "copied" : <Copy />}
          </button>
        </div>
        <p className="soft">Paste the token when the login asks for it.</p>
      </section>
    </div>
  );
}
