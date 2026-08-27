import { useEffect, useState } from "react";

import { acceptInvite, peekInvite, type Team } from "./api";
import { Mark } from "./icons";

/**
 * The page an invite link opens: `/?invite=<token>`. A query string rather than a
 * path, so it needs nothing from whatever serves the app. Says what the link is for,
 * joins on a click, and hands the team back so the app can open it.
 */
export function Invite({ token, onJoined }: { token: string; onJoined: (team: Team) => void }) {
  const [offer, setOffer] = useState<{ team: { id: string; name: string }; role: string } | null>(
    null,
  );
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    peekInvite(token)
      .then(setOffer)
      .catch((e) => setError(String(e).replace(/^Error: \d{3} /, "")));
  }, [token]);

  async function join() {
    setBusy(true);
    try {
      onJoined(await acceptInvite(token));
    } catch (e) {
      setError(String(e).replace(/^Error: \d{3} /, ""));
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <Mark size={64} />
      <h1>Mindstash</h1>
      {error ? (
        <p>{error}</p>
      ) : offer ? (
        <>
          <p>
            You are invited to join <b>{offer.team.name}</b> as {offer.role === "admin" ? "an" : "a"}{" "}
            {offer.role}.
          </p>
          <button className="primary" disabled={busy} onClick={join}>
            {busy ? "Joining…" : "Join"}
          </button>
        </>
      ) : (
        <p className="soft">Looking up the invite…</p>
      )}
    </div>
  );
}
