import { useEffect, useRef, useState } from "react";

import {
  addConnection,
  browseConnector,
  can,
  type Choice,
  type Connection,
  type ConnectorField,
  type ConnectorKind,
  type Grant,
  listConnections,
  listConnectors,
  listGrants,
  REDACTED,
  removeConnection,
  syncConnection,
  type Team,
  updateConnection,
} from "./api";
import { confirm } from "./dialog";
import { Chevron } from "./icons";
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
  name: string; // shown when editing; the connector chose it
  config: Record<string, string>;
  every: number;
  enabled: boolean;
  grant: string;
};

const blank = (kind: string, grant = ""): Form => ({
  kind,
  name: "",
  config: {},
  every: 60,
  enabled: true,
  grant,
});

/** Why a connector cannot be picked right now, or "" when it can. */
function blocked(k: ConnectorKind, grants: Grant[], rows: Connection[]): string {
  if (!k.available) return "needs a sign-in Mindkeep cannot do yet";
  if (rows.some((r) => r.kind === k.kind)) return "already connected — edit it";
  if (k.auth !== "none" && !grants.some((g) => g.kind === k.kind)) {
    return "sign in first — Settings → Account → Connectors";
  }
  return "";
}

type Row = Record<string, string>;

/** A rows field's value: the JSON the form keeps, as rows — an empty one to start. */
function rowsOf(value: string, f: ConnectorField): Row[] {
  try {
    const parsed = value ? (JSON.parse(value) as Row[]) : [];
    return parsed.length ? parsed : [blankRow(f)];
  } catch {
    return [blankRow(f)];
  }
}
const blankRow = (f: ConnectorField): Row =>
  Object.fromEntries(f.rows.map((r) => [r.name, r.options.length ? r.help : ""]));

/**
 * A bundle's connections: third-party sources pulled on schedule. The catalog of
 * connectors comes from the server — a plugin installed there shows up here with its own
 * form — so nothing in this file knows what a connector wants; it draws the fields it is
 * told about. A connector that needs a sign-in uses one of yours (Settings → Account →
 * Connectors).
 * Managing is the `bundles` permission, syncing now is `write`.
 */
export function Connections({ bundle, team }: { bundle: string; team: Team }) {
  const [kinds, setKinds] = useState<ConnectorKind[]>([]);
  const [grants, setGrants] = useState<Grant[]>([]);
  const [rows, setRows] = useState<Connection[]>([]);
  const [error, setError] = useState("");
  // "new", a connection's id, or nothing — one form at a time
  const [editing, setEditing] = useState<string | null>(null);
  const [form, setForm] = useState<Form>(blank(""));
  const [saving, setSaving] = useState(false);
  const [open, setOpen] = useState(false); // the picker of connectors
  // the browser, for one cell of one rows field: where it is, what is there
  const [browsing, setBrowsing] = useState<{
    field: string;
    row: number;
    col: string;
    at: string;
    choices: Choice[];
    busy: boolean;
  } | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const manages = can(team, "bundles");
  const writes = can(team, "write");

  const load = () =>
    Promise.all([listConnectors(), listConnections(bundle), listGrants()])
      .then(([known, mine, own]) => {
        setKinds(known);
        setRows(mine);
        setGrants(own);
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

  useEffect(() => {
    if (!open) return;
    const outside = (e: PointerEvent) => {
      const target = e.target as Node;
      if (!trigger.current?.contains(target) && !menu.current?.contains(target)) setOpen(false);
    };
    const escape = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  const fail = (e: Error) => setError(e.message.replace(/^\d{3} /, ""));
  const kind = kinds.find((k) => k.kind === form.kind);
  const usable = grants.filter((g) => g.kind === form.kind);

  function startNew(k: ConnectorKind) {
    setOpen(false);
    setError("");
    setForm(blank(k.kind, grants.find((g) => g.kind === k.kind)?.id ?? ""));
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
      grant: row.grant?.id ?? "",
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
          config: form.config,
          every: form.every,
          grant: form.grant || undefined,
        });
      } else if (editing) {
        await updateConnection(bundle, editing, {
          config: form.config,
          every: form.every,
          enabled: form.enabled,
          grant: form.grant || undefined,
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

  /** Open the browser on a cell, or move it to `at`: the connector says what is there. */
  async function look(field: string, row: number, col: string, at: string) {
    setError("");
    setBrowsing({ field, row, col, at, choices: [], busy: true });
    try {
      const got = await browseConnector(bundle, form.kind, {
        field: col,
        at,
        grant: form.grant || undefined,
      });
      setBrowsing({ field, row, col, at: got.at, choices: got.choices, busy: false });
    } catch (e) {
      setBrowsing(null);
      fail(e as Error);
    }
  }

  const input = (f: ConnectorField) => {
    const value = form.config[f.name] ?? "";
    const change = (v: string) => setForm({ ...form, config: { ...form.config, [f.name]: v } });
    if (f.rows.length) {
      const rows = rowsOf(value, f);
      const set = (next: Row[]) => change(JSON.stringify(next));
      const setCell = (i: number, name: string, v: string) =>
        set(rows.map((r, j) => (j === i ? { ...r, [name]: v } : r)));
      const up = (at: string) => at.split("/").slice(0, -1).join("/");
      return (
        <div className="rows">
          {rows.map((row, i) => (
            <div className="row" key={i}>
              {f.rows.map((col) =>
                col.browse ? (
                  <span className="browsable" key={col.name}>
                    <input
                      aria-label={`${col.label} ${i + 1}`}
                      title={col.label}
                      placeholder={col.label + (col.help ? ` — ${col.help}` : "")}
                      value={row[col.name] ?? ""}
                      onChange={(e) => setCell(i, col.name, e.target.value)}
                    />
                    <button
                      type="button"
                      className="more"
                      aria-label={`Browse ${i + 1}`}
                      onClick={() => look(f.name, i, col.name, up(row[col.name] ?? ""))}
                    >
                      browse
                    </button>
                    {browsing && browsing.field === f.name && browsing.row === i && (
                      <div className="browser" role="dialog" aria-label={`Browse ${col.label}`}>
                        <div className="where">
                          <button
                            type="button"
                            className="more"
                            onClick={() => look(f.name, i, col.name, "")}
                          >
                            top
                          </button>
                          {browsing.at && (
                            <>
                              <span className="soft"> / {browsing.at}</span>
                              <button
                                type="button"
                                className="more"
                                onClick={() => look(f.name, i, col.name, up(browsing.at))}
                              >
                                up
                              </button>
                            </>
                          )}
                        </div>
                        {browsing.busy ? (
                          <p className="soft">Looking…</p>
                        ) : browsing.choices.length === 0 ? (
                          <p className="soft">Nothing inside.</p>
                        ) : (
                          <ul>
                            {browsing.choices.map((c) => (
                              <li key={c.value}>
                                <button
                                  type="button"
                                  className="choice"
                                  onClick={() =>
                                    c.opens
                                      ? look(f.name, i, col.name, c.value)
                                      : (setCell(i, col.name, c.value), setBrowsing(null))
                                  }
                                >
                                  {c.label}
                                  {c.opens && " ›"}
                                </button>
                              </li>
                            ))}
                          </ul>
                        )}
                        <div className="where">
                          {browsing.at && (
                            <button
                              type="button"
                              className="lint"
                              aria-label={`Use ${browsing.at}`}
                              onClick={() => {
                                setCell(i, col.name, browsing.at);
                                setBrowsing(null);
                              }}
                            >
                              Use this folder
                            </button>
                          )}
                          <button type="button" className="more" onClick={() => setBrowsing(null)}>
                            close
                          </button>
                        </div>
                      </div>
                    )}
                  </span>
                ) : col.options.length ? (
                  <select
                    key={col.name}
                    aria-label={`${col.label} ${i + 1}`}
                    title={col.label}
                    value={row[col.name] ?? ""}
                    onChange={(e) =>
                      set(rows.map((r, j) => (j === i ? { ...r, [col.name]: e.target.value } : r)))
                    }
                  >
                    {col.options.map(([v, label]) => (
                      <option key={v} value={v}>
                        {label}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    key={col.name}
                    aria-label={`${col.label} ${i + 1}`}
                    title={col.label}
                    placeholder={col.label + (col.help ? ` — ${col.help}` : "")}
                    value={row[col.name] ?? ""}
                    onChange={(e) =>
                      set(rows.map((r, j) => (j === i ? { ...r, [col.name]: e.target.value } : r)))
                    }
                  />
                ),
              )}
              <button
                type="button"
                className="more"
                aria-label={`Remove ${i + 1}`}
                disabled={rows.length === 1}
                onClick={() => set(rows.filter((_, j) => j !== i))}
              >
                remove
              </button>
            </div>
          ))}
          <button type="button" className="more" onClick={() => set([...rows, blankRow(f)])}>
            add another
          </button>
        </div>
      );
    }
    if (f.multiline) {
      return (
        <textarea
          aria-label={f.label}
          rows={3}
          value={value}
          placeholder={f.help}
          onChange={(e) => change(e.target.value)}
        />
      );
    }
    return (
      <input
        aria-label={f.label}
        type={f.secret ? "password" : "text"}
        value={value}
        placeholder={f.help}
        onFocus={(e) => {
          // the marker stands for a secret that is set; typing replaces it
          if (f.secret && e.target.value === REDACTED) e.target.select();
        }}
        onChange={(e) => change(e.target.value)}
      />
    );
  };

  return (
    <section className="card">
      <h2>Connections</h2>
      <p>
        A source somewhere else — a website, a workspace — pulled into <code>raw/connectors/</code>{" "}
        on schedule and folded into the wiki whenever it changes. Each has its own depth and its own
        schedule. The folder is the connection's: a file edited there by hand is put back at the
        next sync.
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
                  {row.grant && ` as ${row.grant.label}`}
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
              <div className={`how${row.error || row.grant_gone ? " bad" : ""}`}>
                {row.error
                  ? row.error
                  : row.grant_gone
                    ? "the sign-in this connection used is gone — pick another"
                    : row.synced_at
                      ? `${row.summary} · synced ${when(row.synced_at)}`
                      : "never synced"}
                {row.enabled &&
                  !kinds.find((k) => k.kind === row.kind)?.tick &&
                  ` · ${EVERY.find(([m]) => m === row.every)?.[1] ?? `every ${row.every} min`}`}
              </div>
            </li>
          ))}
        </ul>
      )}
      {rows.length === 0 && !editing && <p className="soft">No connections yet.</p>}

      {manages && !editing && (
        <div className="picker">
          <button
            ref={trigger}
            className="lint"
            aria-haspopup="menu"
            aria-expanded={open}
            disabled={kinds.length === 0}
            onClick={() => setOpen(!open)}
          >
            Add a connection <Chevron />
          </button>
          {open && (
            <div ref={menu} role="menu" className="pickermenu wide">
              {kinds.map((k) => {
                const why = blocked(k, grants, rows);
                return (
                  <button
                    key={k.kind}
                    role="menuitem"
                    className="menuitem"
                    disabled={why !== ""}
                    title={why}
                    onClick={() => startNew(k)}
                  >
                    {k.title}
                    <span className="soft">{why || k.blurb}</span>
                  </button>
                );
              })}
            </div>
          )}
        </div>
      )}

      {editing && kind && (
        <form
          className="connection-form"
          onSubmit={(e) => {
            e.preventDefault();
            save();
          }}
        >
          <div className="field">
            <span>Source</span>
            <span className="soft">
              {kind.title}
              {editing !== "new" && ` · ${form.name}`}
            </span>
          </div>
          {editing === "new" && <p className="soft">{kind.blurb}</p>}

          {kind.auth !== "none" && (
            <label className="field">
              <span>Sign-in</span>
              <select
                aria-label="Sign-in"
                value={form.grant}
                onChange={(e) => setForm({ ...form, grant: e.target.value })}
              >
                {!usable.some((g) => g.id === form.grant) && <option value="">Choose one</option>}
                {usable.map((g) => (
                  <option key={g.id} value={g.id}>
                    {g.label}
                  </option>
                ))}
              </select>
            </label>
          )}

          {kind.fields.map((f) =>
            f.rows.length ? (
              <div className="field stacked" key={f.name}>
                <span>{f.label}</span>
                {input(f)}
              </div>
            ) : (
              <label className="field" key={f.name} title={f.help}>
                <span>
                  {f.label}
                  {!f.required && <span className="soft"> (optional)</span>}
                </span>
                {input(f)}
              </label>
            ),
          )}

          {!kind.tick && (
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
          )}

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
            <button
              type="submit"
              className="lint"
              disabled={saving || (kind.auth !== "none" && !form.grant)}
            >
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
