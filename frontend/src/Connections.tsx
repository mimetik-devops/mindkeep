import { useEffect, useState } from "react";

import {
  addConnection,
  can,
  type Connection,
  type ConnectorKind,
  listConnections,
  listConnectors,
  REDACTED,
  removeConnection,
  syncConnection,
  type Team,
  updateConnection,
} from "./api";
import { confirm } from "./dialog";
import { when } from "./useSources";

/** The intervals a person can pick. Minutes, as the server counts them. */
const EVERY = [
  [15, "every 15 minutes"],
  [60, "every hour"],
  [360, "every 6 hours"],
  [1440, "every day"],
  [10080, "every week"],
] as const;

type Form = {
  kind: string;
  name: string;
  config: Record<string, string>;
  every: number;
  enabled: boolean;
};

const blank = (kind: string): Form => ({ kind, name: "", config: {}, every: 60, enabled: true });

/**
 * A bundle's connections: third-party sources pulled on schedule. The catalog of
 * connectors comes from the server — a plugin installed there shows up here with its own
 * form — so nothing in this file knows what a connector wants; it draws the fields it is
 * told about. Managing is the `bundles` permission, syncing now is `write`.
 */
export function Connections({ bundle, team }: { bundle: string; team: Team }) {
  const [kinds, setKinds] = useState<ConnectorKind[]>([]);
  const [rows, setRows] = useState<Connection[]>([]);
  const [error, setError] = useState("");
  // "new", a connection's id, or nothing — one form at a time
  const [editing, setEditing] = useState<string | null>(null);
  const [form, setForm] = useState<Form>(blank(""));
  const [saving, setSaving] = useState(false);
  const manages = can(team, "bundles");
  const writes = can(team, "write");

  const load = () =>
    Promise.all([listConnectors(), listConnections(bundle)])
      .then(([known, mine]) => {
        setKinds(known);
        setRows(mine);
      })
      .catch((e: Error) => setError(e.message));

  useEffect(() => {
    load();
  }, [bundle]);

  // while something is syncing, its row is what tells you it finished
  const syncing = rows.some((r) => r.syncing);
  useEffect(() => {
    if (!syncing) return;
    const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, [syncing, bundle]);

  const fail = (e: Error) => setError(e.message.replace(/^\d{3} /, ""));
  const kind = kinds.find((k) => k.kind === form.kind);

  function startNew() {
    const first = kinds.find((k) => k.available);
    setError("");
    setForm(blank(first?.kind ?? ""));
    setEditing("new");
  }

  function startEdit(row: Connection) {
    setError("");
    setForm({
      kind: row.kind,
      name: row.name,
      config: { ...row.config },
      every: row.every,
      enabled: row.enabled,
    });
    setEditing(row.id);
  }

  async function save() {
    setSaving(true);
    setError("");
    try {
      if (editing === "new") {
        await addConnection(bundle, {
          kind: form.kind,
          name: form.name,
          config: form.config,
          every: form.every,
        });
      } else if (editing) {
        await updateConnection(bundle, editing, {
          config: form.config,
          every: form.every,
          enabled: form.enabled,
        });
      }
      setEditing(null);
      await load();
    } catch (e) {
      fail(e as Error);
    } finally {
      setSaving(false);
    }
  }

  async function remove(row: Connection) {
    const sure = await confirm(
      `Remove ${row.name}? Everything it pulled into ${row.folder} goes with it, and the pages ` +
        "resting on those files are retired.",
      { ok: "Remove", danger: true },
    );
    if (!sure) return;
    setError("");
    removeConnection(bundle, row.id).then(load, fail);
  }

  const sync = (row: Connection) => {
    setError("");
    syncConnection(bundle, row.id).then(load, fail);
  };

  const titleOf = (k: string) => kinds.find((x) => x.kind === k)?.title ?? k;

  return (
    <section className="card">
      <h2>Connections</h2>
      <p>
        A source somewhere else — a website, a workspace — pulled into <code>raw/connectors/</code>{" "}
        on schedule and folded into the wiki whenever it changes. The folder is the connection's: a
        file edited there by hand is put back at the next sync.
      </p>

      {error && <div className="banner">{error}</div>}

      {rows.length > 0 && (
        <ul className="connections">
          {rows.map((row) => (
            <li key={row.id}>
              <div className="who">
                <span
                  className={
                    row.syncing
                      ? "pulse"
                      : `dot ${row.error ? "failed" : row.synced_at ? "done" : "idle"}`
                  }
                />
                <b>{row.name}</b>
                <span className="soft">
                  {titleOf(row.kind)}
                  {!row.installed && " — not installed on this server"}
                  {!row.enabled && " · paused"}
                </span>
                {writes && row.installed && (
                  <button className="more" disabled={row.syncing} onClick={() => sync(row)}>
                    {row.syncing ? "syncing…" : "sync now"}
                  </button>
                )}
                {manages && (
                  <button className="more" onClick={() => startEdit(row)}>
                    edit
                  </button>
                )}
                {manages && (
                  <button className="more" onClick={() => remove(row)}>
                    remove
                  </button>
                )}
              </div>
              <div className={`how${row.error ? " bad" : ""}`}>
                {row.error
                  ? row.error
                  : row.synced_at
                    ? `${row.summary} · synced ${when(row.synced_at)}`
                    : "never synced"}
                {row.enabled &&
                  ` · ${EVERY.find(([m]) => m === row.every)?.[1] ?? `every ${row.every} min`}`}
              </div>
            </li>
          ))}
        </ul>
      )}
      {rows.length === 0 && !editing && <p className="soft">No connections yet.</p>}

      {manages && !editing && (
        <button className="lint" onClick={startNew} disabled={!kinds.some((k) => k.available)}>
          Add a connection
        </button>
      )}

      {editing && (
        <form
          className="connection-form"
          onSubmit={(e) => {
            e.preventDefault();
            save();
          }}
        >
          {editing === "new" ? (
            <label className="field">
              <span>Source</span>
              <select
                aria-label="Connector"
                value={form.kind}
                onChange={(e) => setForm({ ...blank(e.target.value), every: form.every })}
              >
                {kinds.map((k) => (
                  <option key={k.kind} value={k.kind} disabled={!k.available}>
                    {k.title}
                    {!k.available && " — needs a sign-in Mindkeep cannot do yet"}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <div className="field">
              <span>Source</span>
              <span className="soft">
                {titleOf(form.kind)} · {form.name}
              </span>
            </div>
          )}
          {kind && editing === "new" && <p className="soft">{kind.blurb}</p>}

          {editing === "new" && (
            <label className="field">
              <span>Name</span>
              <input
                aria-label="Connection name"
                value={form.name}
                placeholder="Where it lands under raw/connectors/"
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </label>
          )}

          {kind?.fields.map((f) => (
            <label className="field" key={f.name} title={f.help}>
              <span>
                {f.label}
                {!f.required && <span className="soft"> (optional)</span>}
              </span>
              <input
                aria-label={f.label}
                type={f.secret ? "password" : "text"}
                value={form.config[f.name] ?? ""}
                placeholder={f.help}
                onFocus={(e) => {
                  // the marker stands for a secret that is set; typing replaces it
                  if (f.secret && e.target.value === REDACTED) e.target.select();
                }}
                onChange={(e) =>
                  setForm({ ...form, config: { ...form.config, [f.name]: e.target.value } })
                }
              />
            </label>
          ))}

          <label className="field">
            <span>Check for changes</span>
            <select
              aria-label="Sync every"
              value={form.every}
              onChange={(e) => setForm({ ...form, every: Number(e.target.value) })}
            >
              {EVERY.map(([minutes, label]) => (
                <option key={minutes} value={minutes}>
                  {label}
                </option>
              ))}
            </select>
          </label>

          {editing !== "new" && (
            <label className="field">
              <span>Paused</span>
              <input
                type="checkbox"
                aria-label="Paused"
                checked={!form.enabled}
                onChange={(e) => setForm({ ...form, enabled: !e.target.checked })}
              />
            </label>
          )}

          <div className="field">
            <button type="button" className="more" onClick={() => setEditing(null)}>
              cancel
            </button>
            <button type="submit" className="lint" disabled={saving || !form.kind}>
              {saving
                ? editing === "new"
                  ? "Trying it…"
                  : "Saving…"
                : editing === "new"
                  ? "Connect"
                  : "Save"}
            </button>
          </div>
        </form>
      )}
    </section>
  );
}
