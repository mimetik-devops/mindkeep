import { useEffect, useState } from "react";

import { deviceToken, readFile, tree } from "./api";
import { Copy } from "./icons";
import { render } from "./okf";
import { elapsed, took, useLint, useSources } from "./useSources";

type Event = { date: string; kind: string; title: string; detail: string; rest: string };

/** Compare titles on letters and digits alone: punctuation differs on the two sides. */
const normalise = (s: string) =>
  s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();

const FILTERS = ["All", "Ingests", "Uploads", "Edits"] as const;
type Filter = (typeof FILTERS)[number];

const MATCHES: Record<Filter, (kind: string) => boolean> = {
  All: () => true,
  Ingests: (k) => k.startsWith("ingest"),
  Uploads: (k) => k.startsWith("upload") || k.startsWith("add"),
  Edits: (k) => !k.startsWith("ingest") && !k.startsWith("upload") && !k.startsWith("add"),
};

/** log.md is the agent's own record: `## [2026-08-22] ingest | Title` then bullets. */
export function parseLog(markdown: string): Event[] {
  const heads: { date: string; kind: string; title: string; body: string[] }[] = [];

  // \r?\n, not \n: a CRLF file leaves a trailing \r that `$` will not match past.
  for (const line of markdown.split(/\r?\n/)) {
    const heading = line.match(/^##\s*\[([^\]]+)\]\s*([^|]*?)\s*(?:\|\s*(.*))?$/);
    if (heading) {
      heads.push({
        date: heading[1],
        kind: heading[2].trim() || "change",
        title: (heading[3] ?? "").trim(),
        body: [],
      });
    } else if (heads.length) {
      heads[heads.length - 1].body.push(line); // verbatim: it is markdown, not prose
    }
  }

  return heads.map(({ body, ...head }) => {
    const first = body.findIndex((l) => l.trim());
    return {
      ...head,
      // the first line summarises; the rest keeps its own markup so lists still render
      detail: first < 0 ? "" : body[first].replace(/^[-*]\s*/, "").trim(),
      rest: first < 0 ? "" : body.slice(first + 1).join("\n").trim(),
    };
  });
}

function Entry({ event, seconds }: { event: Event; seconds?: number }) {
  const [open, setOpen] = useState(false);
  return (
    <article className="event">
      <span className="when">{event.date}</span>
      <div className="what">
        <span className="kind">
          {event.kind}
          {event.title && <span className="soft"> · {event.title}</span>}
          {seconds ? <span className="state"> {took(seconds)}</span> : null}
        </span>
        {/* the agent writes markdown, so render it — sanitised, as everywhere else */}
        {event.detail && (
          <div className="detail md" dangerouslySetInnerHTML={{ __html: render(event.detail) }} />
        )}
        {event.rest && (
          <>
            {open && (
              <div
                className="detail md rest"
                dangerouslySetInnerHTML={{ __html: render(event.rest) }}
              />
            )}
            <button className="more" onClick={() => setOpen(!open)}>
              {open ? "less" : "more"}
            </button>
          </>
        )}
      </div>
    </article>
  );
}

export function Console({ bundle }: { bundle: string }) {
  const [events, setEvents] = useState<Event[]>([]);
  const [paths, setPaths] = useState<string[]>([]);
  const [token, setToken] = useState("");
  const [copied, setCopied] = useState(false);
  const [filter, setFilter] = useState<Filter>("All");
  const { sources, version } = useSources(bundle);
  const { lint } = useLint(bundle);

  useEffect(() => {
    deviceToken().then((r) => setToken(r.token));
  }, []);

  // reload whenever an ingest or a lint finishes — the log and the page count both changed
  useEffect(() => {
    tree(bundle).then((t) => setPaths(Object.keys(t)));
    readFile(bundle, "log.md").then((t) => setEvents(parseLog(t)));
  }, [bundle, version, lint?.linting]);

  const live = sources.filter((s) => s.ingesting);
  const done = sources.filter((s) => s.ingested).length;
  const waiting = sources.length - done - live.length;

  // the agent writes the log entry but cannot know how long it ran, so match its title
  // back to the source we timed. Punctuation differs on both sides — the upload sanitiser
  // turns an em dash into a hyphen, the agent writes the original — so compare on letters
  // and digits alone. No match still means no duration rather than a wrong one.
  const durationFor = (e: Event) => {
    const haystack = normalise(`${e.title} ${e.detail}`);
    const hit = sources.find((s) => {
      const stem = normalise(s.path.slice("raw/".length).replace(/\.[^.]+$/, ""));
      return stem.length > 6 && haystack.includes(stem);
    });
    return hit?.took;
  };
  const shown = events
    .map((e, i) => ({ e, i }))
    .filter(({ e }) => MATCHES[filter](e.kind.toLowerCase()))
    // newest first. Entries carry a date but no clock time, so within one day fall back
    // to file order — log.md is appended to, so later in the file is later in the day.
    .sort((a, b) => b.e.date.localeCompare(a.e.date) || b.i - a.i)
    .map(({ e }) => e);
  const wiki = paths.filter((p) => p.startsWith("wiki/")).length;

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

        {lint?.error && !lint.linting && (
          <div className="banner">
            <b>Lint failed.</b> {lint.error}
          </div>
        )}

        {/* live work is not in log.md yet — the agent writes its entry when it finishes */}
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
              {/* a lint reads for minutes before it changes anything — say which page */}
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

        {shown.map((e, i) => (
          <Entry key={i} event={e} seconds={durationFor(e)} />
        ))}

        {!shown.length && !live.length && !lint?.linting && (
          <p className="empty">
            {events.length
              ? "Nothing of that kind yet."
              : "Nothing yet. Add a source and the agent will write its first log entry."}
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

        <section>
          <h2>On your machine</h2>
          <p>A copy Claude can read directly. Drop files in its raw folder to upload them.</p>
          <div className="command">
            <code>python mindstash.py login</code>
            <button
              onClick={() => {
                navigator.clipboard.writeText(token);
                setCopied(true);
              }}
              title="Copy your device token"
            >
              {copied ? "copied" : <Copy />}
            </button>
          </div>
        </section>
      </aside>
    </div>
  );
}
