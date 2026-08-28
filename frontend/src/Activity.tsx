import { useEffect, useState } from "react";

import {
  activity,
  can,
  type Entry,
  type Queue,
  queueState,
  redoRun,
  retryIngest,
  runDetail,
  type Team,
  tree,
  undoRun,
} from "./api";
import { confirm } from "./dialog";
import { render } from "./okf";
import { elapsed, took, useLint, useSources, when } from "./useSources";

/**
 * What happened to this bundle, newest first, from one record: the bundle's git history.
 *
 * Each agent run — ingest or lint — is a commit that carries its own log.md entry, so the
 * agent's words and the server's facts (when, how long, what it touched) are one row with
 * no matching. The commits between runs are what people did — uploads, edits, deletes —
 * which the log never narrated. What is happening right now sits on top. Undo is on a
 * run, for those with the `history` permission: it rewrites the wiki.
 */
const FILTERS = ["All", "Runs", "Changes"] as const;
type Filter = (typeof FILTERS)[number];

const WORD: Record<string, string> = { A: "added", M: "edited", D: "deleted", R: "renamed" };

function Changed({ files }: { files: { status: string; path: string }[] }) {
  if (!files.length) return <div className="step">Nothing changed.</div>;
  return (
    <div className="changes">
      {files.map((f) => (
        <div key={f.path} className="change">
          <span className={`mark ${f.status}`}>{WORD[f.status] ?? f.status}</span> {f.path}
        </div>
      ))}
    </div>
  );
}

export function Activity({ bundle, team }: { bundle: string; team: Team }) {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [paths, setPaths] = useState<string[]>([]);
  const [filter, setFilter] = useState<Filter>("All");
  const [open, setOpen] = useState("");
  const [changed, setChanged] = useState<Record<number, { status: string; path: string }[]>>({});
  const [busy, setBusy] = useState(0);
  const [error, setError] = useState("");
  const { sources, version } = useSources(bundle);
  const { lint } = useLint(bundle);
  const mayUndo = can(team, "history");
  const mayWrite = can(team, "write");
  const [queue, setQueue] = useState<Queue | null>(null);

  const refresh = () =>
    Promise.all([activity(bundle), tree(bundle), queueState(bundle)])
      .then(([feed, t, q]) => {
        setEntries(feed);
        setPaths(Object.keys(t));
        setQueue(q);
      })
      .catch((e) => setError(String(e).replace(/^Error: \d{3} /, "")));

  // a hold ends on its own timer, so the banner has to keep looking
  useEffect(() => {
    const tick = setInterval(
      () =>
        queueState(bundle)
          .then(setQueue)
          .catch(() => {}),
      15000,
    );
    return () => clearInterval(tick);
  }, [bundle]);

  async function retry(path = "") {
    setError("");
    try {
      await retryIngest(bundle, path);
      setQueue(await queueState(bundle));
    } catch (err) {
      setError(String(err).replace(/^Error: \d{3} /, ""));
    }
  }

  // reload whenever an ingest or a lint finishes — the history and the page count changed
  useEffect(() => {
    refresh();
  }, [bundle, version, lint?.linting]);

  const live = sources.filter((s) => s.ingesting);
  const done = sources.filter((s) => s.ingested).length;
  const waiting = sources.length - done - live.length;
  const wiki = paths.filter((p) => p.startsWith("wiki/")).length;

  async function expand(e: Entry) {
    const key = `${e.kind}:${e.id ?? e.commit}`;
    if (open === key) return setOpen("");
    setOpen(key);
    if (e.kind === "run" && e.id && e.commit && !changed[e.id]) {
      try {
        const detail = await runDetail(bundle, e.id);
        setChanged((c) => ({ ...c, [e.id!]: detail.changed }));
      } catch (err) {
        setError(String(err).replace(/^Error: \d{3} /, ""));
      }
    }
  }

  async function undo(e: Entry) {
    if (!e.id) return;
    const what =
      e.source === "(lint)"
        ? "Undo this lint? The wiki goes back to how it was before."
        : `Undo the ingest of ${e.source}? The wiki goes back to how it was before, and so ` +
          "does the source — a file new at this run is removed. Both stay in the history, " +
          "and the undo can be redone.";
    if (!(await confirm(what, { ok: "Undo", danger: true }))) return;
    setBusy(e.id);
    setError("");
    try {
      await undoRun(bundle, e.id);
      await refresh();
    } catch (err) {
      setError(String(err).replace(/^Error: \d{3} /, ""));
    } finally {
      setBusy(0);
    }
  }

  async function redo(e: Entry) {
    if (!e.id) return;
    setBusy(e.id);
    setError("");
    try {
      await redoRun(bundle, e.id);
      await refresh();
    } catch (err) {
      setError(String(err).replace(/^Error: \d{3} /, ""));
    } finally {
      setBusy(0);
    }
  }

  // a run still open already has a live card above, with its progress; its row in the
  // history would be the same run twice. A reorganise has no live card, so it stays.
  const livePaths = new Set(live.map((s) => s.path));
  const shownLive = (e: Entry) =>
    e.kind === "run" &&
    !e.finished_at &&
    ((e.source === "(lint)" && !!lint?.linting) || livePaths.has(e.source ?? ""));
  const shown = entries.filter(
    (e) =>
      !shownLive(e) &&
      (filter === "All" ? true : filter === "Runs" ? e.kind === "run" : e.kind !== "run"),
  );

  return (
    <div className="console">
      <main className="feed">
        <div className="feed-head">
          <h2>Activity</h2>
          <div className="filters">
            {FILTERS.map((f) => (
              <button
                key={f}
                className="chipfilter"
                aria-current={filter === f}
                onClick={() => setFilter(f)}
              >
                {f}
              </button>
            ))}
          </div>
        </div>

        {error && <div className="banner">{error}</div>}
        {queue?.held ? (
          <div className="banner">
            <b>Ingest paused.</b> {queue.held.reason} — retrying {when(queue.held.until)}
            {queue.waiting > 0 && `, ${queue.waiting + 1} waiting`}.{" "}
            {mayWrite && (
              <button className="more" onClick={() => retry()}>
                retry now
              </button>
            )}
          </div>
        ) : queue && queue.failed.length > 0 ? (
          <div className="banner">
            <b>
              {queue.failed.length === 1
                ? "One ingest failed."
                : `${queue.failed.length} ingests failed.`}
            </b>{" "}
            {queue.failed.length === 1 ? queue.failed[0] : "See the sources in the Library."}{" "}
            {mayWrite && (
              <button className="more" onClick={() => retry()}>
                {queue.failed.length === 1 ? "retry" : "retry them all"}
              </button>
            )}
          </div>
        ) : null}
        {lint?.error && !lint.linting && (
          <div className="banner">
            <b>Lint failed.</b> {lint.error}
          </div>
        )}

        {/* live work is not in the history yet — the run is committed when it finishes */}
        {lint?.linting && (
          <article className="event running">
            <span className="when">{elapsed(lint.seconds)}</span>
            <div className="what">
              <span className="kind">
                Linting
                <span className="soft">
                  {lint.turns ? ` · turn ${lint.turns}` : " · checking the wiki over"}
                </span>
              </span>
              {lint.note && <div className="step">{lint.note}</div>}
              <div className="bar">
                <span />
              </div>
            </div>
          </article>
        )}
        {live.map((s) => (
          <article className="event running" key={s.path}>
            <span className="when">{elapsed(s.seconds)}</span>
            <div className="what">
              <span className="kind">
                {s.pages ? "Writing" : "Reading"}
                <span className="path"> {s.path}</span>
                {s.pages > 0 && <span className="soft"> · {s.pages} pages so far</span>}
              </span>
              {s.note && <div className="step">{s.note}</div>}
              <div className="bar">
                <span />
              </div>
            </div>
          </article>
        ))}

        {shown.map((e) => {
          const key = `${e.kind}:${e.id ?? e.commit}`;
          const isRun = e.kind === "run";
          const state = !isRun
            ? ""
            : e.undone
              ? "undone"
              : !e.finished_at
                ? "running"
                : e.error
                  ? "failed"
                  : "";
          return (
            <article key={key} className={`event kind-${e.kind} ${state}`}>
              <span className="when" title={e.at}>
                {e.kind === "pending" ? "now" : when(e.at)}
              </span>
              <div className="what">
                <button className="kind" aria-expanded={open === key} onClick={() => expand(e)}>
                  {isRun ? (
                    <>
                      {e.task ?? (e.source?.startsWith("(") ? e.source.slice(1, -1) : "ingest")}
                      {!e.source?.startsWith("(") && <span className="soft"> · {e.source}</span>}
                      {e.seconds ? <span className="state"> {took(e.seconds)}</span> : null}
                      {state && <span className="state"> · {state}</span>}
                    </>
                  ) : e.kind === "pending" ? (
                    <>
                      changes
                      <span className="soft">
                        {" "}
                        · by people, {e.changed.length} files, waiting for the next run
                      </span>
                    </>
                  ) : e.kind === "undo" || e.kind === "redo" ? (
                    <>
                      {e.kind}
                      <span className="soft"> · {e.subject}</span>
                    </>
                  ) : (
                    <>
                      changes<span className="soft"> · by people, {e.changed.length} files</span>
                    </>
                  )}
                </button>
                {/* the agent's own words for the run, straight from its commit */}
                {isRun && e.note && (
                  <div className="detail md" dangerouslySetInnerHTML={{ __html: render(e.note) }} />
                )}
                {isRun && e.error && <div className="step">It ended badly: {e.error}</div>}
                {open === key &&
                  (isRun ? (
                    e.commit ? (
                      changed[e.id!] ? (
                        <Changed files={changed[e.id!]} />
                      ) : (
                        <div className="step">Looking up what it changed…</div>
                      )
                    ) : (
                      <div className="step">
                        {e.finished_at
                          ? "This run wrote nothing."
                          : "Still running — what it changed appears when it finishes."}
                      </div>
                    )
                  ) : (
                    <Changed files={e.changed} />
                  ))}
                {isRun && mayUndo && e.commit && !e.undone && e.finished_at && (
                  <button className="more" disabled={busy === e.id} onClick={() => undo(e)}>
                    {busy === e.id ? "undoing…" : "undo"}
                  </button>
                )}
                {isRun && mayUndo && e.undone && (
                  <button className="more" disabled={busy === e.id} onClick={() => redo(e)}>
                    {busy === e.id ? "redoing…" : "redo"}
                  </button>
                )}
              </div>
            </article>
          );
        })}

        {!shown.length && !live.length && !lint?.linting && (
          <p className="empty">
            {entries.length
              ? "Nothing of that kind yet."
              : "Nothing yet. Add a source and the agent's first run will appear here."}
          </p>
        )}
      </main>

      <aside className="panel">
        <section>
          <h2>This bundle</h2>
          <div className="stat">
            <b>{sources.length}</b>
            <span>{sources.length === 1 ? "source" : "sources"}</span>
          </div>
          {/* the interesting number while a queue drains: how many are actually in yet */}
          <div className="stat">
            <b>{done}</b>
            <span>ingested{waiting ? `, ${waiting} to go` : ""}</span>
          </div>
          <div className="stat">
            <b>{wiki}</b>
            <span>{wiki === 1 ? "wiki page" : "wiki pages"}</span>
          </div>
        </section>
      </aside>
    </div>
  );
}
