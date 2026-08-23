import { useState } from "react";

import { LINT_OFF, setLintHour, startLint } from "./api";
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

export function Settings({ bundle }: { bundle: string }) {
  const { lint, refresh } = useLint(bundle);
  const [error, setError] = useState("");

  const act = (work: Promise<unknown>) => {
    setError("");
    work.catch((e: Error) => setError(e.message)).finally(refresh);
  };

  return (
    <div className="settings">
      <h1>Settings</h1>
      <p className="lede">
        For <b>{bundle}</b>. Every bundle keeps its own schedule.
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
    </div>
  );
}
