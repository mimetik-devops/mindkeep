import { useState } from "react";

import { can, deleteTeam, me, removeMember, renameTeam, type Team } from "./api";

/**
 * The team itself, on the Settings page: its name, your place in it, and its end.
 *
 * Rename is owners. Leave is anyone who is not the last owner. Delete is an
 * owner typing the team's name, because it takes the bundles on disk with it and there
 * is no undo. None of it applies to a personal team, which is yours to keep — only the
 * name can change there.
 */
export function TeamSettings({ team, onChanged }: { team: Team; onChanged: () => void }) {
  const [name, setName] = useState(team.name);
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const owns = can(team, "team");

  const act = async (work: () => Promise<unknown>) => {
    setError("");
    setBusy(true);
    try {
      await work();
      onChanged();
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e).replace(/^\d{3} /, ""));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card">
      <h2>Team</h2>
      {error && <div className="banner">{error}</div>}

      <form
        className="field"
        onSubmit={(e) => {
          e.preventDefault();
          if (name.trim() && name.trim() !== team.name) act(() => renameTeam(team.id, name));
        }}
      >
        <span>Name</span>
        <input
          aria-label="Team name"
          value={name}
          disabled={!owns || busy}
          onChange={(e) => setName(e.target.value)}
        />
        {owns && (
          <button type="submit" className="lint" disabled={busy || name.trim() === team.name}>
            Rename
          </button>
        )}
      </form>

      {!team.personal && (
        <div className="field">
          <span>Leave the team. Its bundles stay with the others.</span>
          <button
            className="lint"
            disabled={busy}
            onClick={() => act(async () => removeMember(team.id, (await me()).id))}
          >
            Leave
          </button>
        </div>
      )}

      {!team.personal && owns && (
        <form
          className="field"
          onSubmit={(e) => {
            e.preventDefault();
            if (confirm === team.name) act(() => deleteTeam(team.id));
          }}
        >
          <span>
            Delete the team, its members and every bundle in it. There is no undo. Type{" "}
            <b>{team.name}</b> to confirm.
          </span>
          <input
            aria-label="Type the team name to confirm"
            placeholder={team.name}
            value={confirm}
            disabled={busy}
            onChange={(e) => setConfirm(e.target.value)}
          />
          <button type="submit" className="lint danger" disabled={busy || confirm !== team.name}>
            Delete
          </button>
        </form>
      )}

      {team.personal && (
        <p className="soft">Your personal team stays with you — it cannot be left or deleted.</p>
      )}
    </section>
  );
}
