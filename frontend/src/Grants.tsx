import { useEffect, useState } from "react";

import {
  addGrant,
  type ConnectorKind,
  type Grant,
  listConnectors,
  listGrants,
  removeGrant,
  startOAuth,
} from "./api";
import { confirm } from "./dialog";

/** A day, short: "30 Aug", or "today". */
const dayOf = (iso: string) => {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  return at.toDateString() === new Date().toDateString()
    ? "today"
    : at.toLocaleDateString([], { day: "numeric", month: "short" });
};

/**
 * Your sign-ins: your standing with each provider, made once here and usable by any
 * connection you set up, on any bundle. The catalog says which connectors need one — a
 * token you paste, or (to come) a sign-in through the provider — and which need none.
 */
export function Grants({ notice = "" }: { notice?: string }) {
  const [kinds, setKinds] = useState<ConnectorKind[]>([]);
  const [mine, setMine] = useState<Grant[]>([]);
  const [error, setError] = useState("");
  const [adding, setAdding] = useState<string | null>(null); // the kind whose form is open
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);

  const load = () =>
    Promise.all([listConnectors(), listGrants()])
      .then(([known, own]) => {
        setKinds(known);
        setMine(own);
      })
      .catch((e: Error) => setError(e.message));

  useEffect(() => {
    load();
  }, []);

  const fail = (e: Error) => setError(e.message.replace(/^\d{3} /, ""));

  async function save(kind: ConnectorKind) {
    setSaving(true);
    setError("");
    try {
      await addGrant(kind.kind, secrets);
      setAdding(null);
      setSecrets({});
      await load();
    } catch (e) {
      fail(e as Error);
    } finally {
      setSaving(false);
    }
  }

  /** Off to the provider's consent page; it brings the browser back here. */
  function signIn(kind: ConnectorKind) {
    setError("");
    startOAuth(kind.kind)
      .then(({ url }) => window.location.assign(url))
      .catch(fail);
  }

  async function remove(g: Grant) {
    const uses =
      g.uses === 0
        ? "No connection uses it."
        : `${g.uses} connection${g.uses === 1 ? "" : "s"} use${g.uses === 1 ? "s" : ""} it ` +
          "and will stop syncing until given another sign-in.";
    const sure = await confirm(`Remove the sign-in ${g.label}? ${uses}`, {
      ok: "Remove",
      danger: true,
    });
    if (!sure) return;
    setError("");
    removeGrant(g.id).then(load, fail);
  }

  return (
    <section className="card">
      <h2>Connectors</h2>
      <p>
        Every source Mindkeep can read from, and your sign-in with each: a token, or a sign-in with
        the provider, made once here and usable by any connection you set up, on any bundle. A
        connection keeps syncing with your sign-in into the bundle it is on, for everyone who reads
        that bundle.
      </p>

      {error && <div className="banner">{error}</div>}
      {notice && !error && <p className="soft notice">{notice}</p>}

      <ul className="connections">
        {kinds.map((k) => {
          const own = mine.filter((g) => g.kind === k.kind);
          return (
            <li key={k.kind}>
              <div className="who">
                <b>{k.title}</b>
                <span className="grow" />
                {k.auth === "none" && <span className="soft">no sign-in needed</span>}
                {k.auth === "oauth2" && !k.available && (
                  <span className="soft" title="The server has no client id and secret for it">
                    not configured on this server
                  </span>
                )}
                {k.auth === "token" && adding !== k.kind && (
                  <button
                    className="more"
                    onClick={() => {
                      setError("");
                      setSecrets({});
                      setAdding(k.kind);
                    }}
                  >
                    {own.length ? "add another" : "add a sign-in"}
                  </button>
                )}
                {k.auth === "oauth2" && k.available && (
                  <button className="more" onClick={() => signIn(k)}>
                    {own.length ? "add another" : `sign in with ${k.title}`}
                  </button>
                )}
              </div>
              {own.map((g) => (
                <div className="signin" key={g.id}>
                  <span className={`dot ${g.error ? "failed" : "done"}`} />
                  <span className="label">{g.label}</span>
                  <span className={g.error ? "bad" : "soft"}>
                    {g.error
                      ? g.error
                      : `signed in ${dayOf(g.created_at)} · ${
                          g.uses === 0
                            ? "not used yet"
                            : `used by ${g.uses} connection${g.uses === 1 ? "" : "s"}`
                        }`}
                  </span>
                  <button className="more" onClick={() => remove(g)}>
                    remove
                  </button>
                </div>
              ))}
              {adding === k.kind && (
                <form
                  className="connection-form"
                  onSubmit={(e) => {
                    e.preventDefault();
                    save(k);
                  }}
                >
                  {k.grant_fields.map((f) => (
                    <label className="field" key={f.name} title={f.help}>
                      <span>{f.label}</span>
                      <input
                        aria-label={f.label}
                        type={f.secret ? "password" : "text"}
                        value={secrets[f.name] ?? ""}
                        placeholder={f.help}
                        onChange={(e) => setSecrets({ ...secrets, [f.name]: e.target.value })}
                      />
                    </label>
                  ))}
                  <div className="field">
                    <button type="button" className="more" onClick={() => setAdding(null)}>
                      cancel
                    </button>
                    <button type="submit" className="lint" disabled={saving}>
                      {saving ? "Trying it…" : "Sign in"}
                    </button>
                  </div>
                </form>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
