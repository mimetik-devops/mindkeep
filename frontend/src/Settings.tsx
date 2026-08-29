import { useEffect, useState } from "react";

import {
  can,
  deleteBundle,
  addDevice,
  type Device,
  devices,
  removeDevice,
  LINT_OFF,
  moveBundle,
  renameBundle,
  setLintHour,
  startLint,
  type Team,
} from "./api";
import { Connections } from "./Connections";
import { Members } from "./Members";
import { TeamSettings } from "./TeamSettings";
import { confirm } from "./dialog";
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

// One page, three concerns: the bundle you are looking at, the team it belongs to, you.
const SECTIONS = ["Bundle", "Team", "Account"] as const;
type Section = (typeof SECTIONS)[number];

export function Settings({
  bundle,
  team,
  teams,
  onTeamChanged,
  onBundleMoved,
  onBundleRenamed,
  onBundleDeleted,
}: {
  bundle: string;
  team: Team;
  /** Every team you belong to — the ones you manage are where a bundle can go. */
  teams: Team[];
  /** After a rename, a leave or a delete: the app re-reads its teams. */
  onTeamChanged: () => void;
  /** The bundle now lives over there: the app follows it. */
  onBundleMoved: (team: string, bundle: string) => void;
  /** The bundle has a new name: the app's list and selection follow. */
  onBundleRenamed: (from: string, to: string) => void;
  /** The bundle is gone: the app drops it and opens another. */
  onBundleDeleted: (name: string) => void;
}) {
  const { lint, refresh } = useLint(bundle);
  const [section, setSection] = useState<Section>("Bundle");
  const [error, setError] = useState("");
  const [destination, setDestination] = useState("");
  const [newName, setNewName] = useState(bundle);
  const [confirmDelete, setConfirmDelete] = useState("");
  const manages = can(team, "bundles");
  const elsewhere = teams.filter((t) => t.id !== team.id && can(t, "bundles"));
  const [mine, setMine] = useState<Device[]>([]);
  const [deviceName, setDeviceName] = useState("");
  // a token is shown once, right after it is made; reloading the page loses it for good
  const [fresh, setFresh] = useState<{ name: string; token: string } | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    devices()
      .then(setMine)
      .catch(() => setMine([]));
  }, []);

  async function connectDevice() {
    const name = deviceName.trim();
    if (!name) return;
    setError("");
    try {
      const made = await addDevice(name);
      setFresh({ name: made.name, token: made.token });
      setCopied(false);
      setDeviceName("");
      setMine(await devices());
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function revoke(d: Device) {
    const sure = await confirm(
      `Sign ${d.name} out? Its token stops working at once; the files it synced stay where they are.`,
      { ok: "Sign out", danger: true },
    );
    if (!sure) return;
    setError("");
    try {
      await removeDevice(d.id);
      setMine((all) => all.filter((x) => x.id !== d.id));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const act = (work: Promise<unknown>) => {
    setError("");
    work.catch((e: Error) => setError(e.message)).finally(refresh);
  };

  return (
    <div className="settings">
      <h1>Settings</h1>
      <p className="lede">
        {section === "Bundle" && (
          <>
            Bundle <b>{bundle}</b> in <b>{team.name}</b>. Every bundle keeps its own schedule.
          </>
        )}
        {section === "Team" && (
          <>
            <b>{team.name}</b>: who is in it, and the team itself.
          </>
        )}
        {section === "Account" && <>You, on every team and every bundle.</>}
      </p>

      <nav className="subtabs">
        {SECTIONS.map((s) => (
          <button
            key={s}
            className="subtab"
            aria-current={section === s}
            onClick={() => {
              setError("");
              setSection(s);
            }}
          >
            {s}
          </button>
        ))}
      </nav>

      {error && <div className="banner">{error}</div>}

      {section === "Bundle" && (
        <div className="columns">
          <div className="column">
            <section className="card">
              <h2>Nightly lint</h2>
              <p>
                The agent re-reads the wiki, reports what has drifted — contradictions, stale
                drafts, entities with no page — deletes pages whose source you removed, and files
                every page where its type says it goes.
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

            {manages && (
              <section className="card">
                <h2>This bundle</h2>
                <p>
                  Rename it (lowercase letters, digits and hyphens — a desktop client pointed at the
                  old name will need <code>mindkeep login</code> again), move it to another team you
                  manage, history and all, or delete it: sources, wiki and history go, and there is
                  no undo. A team keeps at least one bundle.
                </p>
                <form
                  className="field"
                  onSubmit={(e) => {
                    e.preventDefault();
                    setError("");
                    renameBundle(bundle, newName.trim())
                      .then((r) => onBundleRenamed(bundle, r.name))
                      .catch((e: Error) => setError(e.message.replace(/^\d{3} /, "")));
                  }}
                >
                  <span>Name</span>
                  <input
                    aria-label="Bundle name"
                    value={newName}
                    onChange={(e) => setNewName(e.target.value)}
                  />
                  <button type="submit" className="lint" disabled={newName.trim() === bundle}>
                    Rename
                  </button>
                </form>
                {elsewhere.length > 0 && (
                  <div className="field">
                    <span>Move to</span>
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
                )}
                <form
                  className="field"
                  onSubmit={(e) => {
                    e.preventDefault();
                    if (confirmDelete !== bundle) return;
                    setError("");
                    deleteBundle(bundle)
                      .then(() => onBundleDeleted(bundle))
                      .catch((e: Error) => setError(e.message.replace(/^\d{3} /, "")));
                  }}
                >
                  <span>
                    Delete — type <b>{bundle}</b>
                  </span>
                  <input
                    aria-label="Type the bundle name to confirm"
                    placeholder={bundle}
                    value={confirmDelete}
                    onChange={(e) => setConfirmDelete(e.target.value)}
                  />
                  <button type="submit" className="lint danger" disabled={confirmDelete !== bundle}>
                    Delete
                  </button>
                </form>
              </section>
            )}
          </div>

          <div className="column">
            <Connections bundle={bundle} team={team} />
          </div>
        </div>
      )}

      {section === "Team" && (
        <>
          <TeamSettings team={team} onChanged={onTeamChanged} />
          <Members team={team} />
        </>
      )}

      {section === "Account" && (
        <section className="card">
          <h2>Your devices</h2>
          <p>
            The desktop app keeps a copy of your bundles that Claude can read directly, and uploads
            whatever you drop in a raw folder. Each machine signs in with a token of its own — the
            app asks for one through this website, or you make one here and paste it in.
          </p>
          {mine.length > 0 && (
            <ul className="devices">
              {mine.map((d) => (
                <li key={d.id}>
                  <b>{d.name}</b>
                  <span className="soft">
                    {d.last_seen ? `seen ${when(d.last_seen)}` : "never seen"}
                  </span>
                  <button className="more" onClick={() => revoke(d)}>
                    sign out
                  </button>
                </li>
              ))}
            </ul>
          )}
          <div className="row">
            <input
              value={deviceName}
              placeholder="Name this machine"
              onChange={(e) => setDeviceName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && connectDevice()}
            />
            <button className="primary" disabled={!deviceName.trim()} onClick={connectDevice}>
              Make a token
            </button>
          </div>
          {fresh && (
            <>
              <div className="command">
                <code>{fresh.token}</code>
                <button
                  onClick={() => {
                    navigator.clipboard.writeText(fresh.token);
                    setCopied(true);
                  }}
                  title={`Copy the token for ${fresh.name}`}
                >
                  {copied ? "copied" : <Copy />}
                </button>
              </div>
              <p className="soft">
                The token for {fresh.name}, shown this once. Paste it into the app, or into{" "}
                <code>mindkeep login</code>.
              </p>
            </>
          )}
        </section>
      )}
    </div>
  );
}
