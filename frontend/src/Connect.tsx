import { useState } from "react";

import { addDevice } from "./api";
import { callback, type Connect as Asked } from "./handoff";
import { Mark } from "./icons";

/**
 * The page the desktop client opens to sign a machine in. One click mints a device
 * token — its own, revocable from Settings — and sends the browser to the client's
 * loopback listener with it. Nothing is typed, nothing is pasted.
 */
export function Connect({ asked, onDismiss }: { asked: Asked; onDismiss: () => void }) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);

  async function connect() {
    setBusy(true);
    try {
      const device = await addDevice(asked.name);
      setSent(true);
      window.location.assign(callback(asked, device.token));
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
        <>
          <p>{error}</p>
          <button className="primary" onClick={onDismiss}>
            Continue
          </button>
        </>
      ) : sent ? (
        <p className="soft">Connected — you can go back to the app on {asked.name}.</p>
      ) : (
        <>
          <p>
            The Mindstash app on <b>{asked.name}</b> wants to sign in as you. It gets a token of its
            own, which you can revoke from Settings at any time.
          </p>
          <button className="primary" disabled={busy} onClick={connect}>
            {busy ? "Connecting…" : `Connect ${asked.name}`}
          </button>
          <button className="quiet" onClick={onDismiss}>
            Not now
          </button>
        </>
      )}
    </div>
  );
}
