import { useEffect, useRef, useState } from "react";

import {
  addFolder,
  addRaw,
  folders,
  moveRaw,
  readAsText,
  readFile,
  removeRaw,
  retryIngest,
  tree,
  verifyPage,
} from "./api";
import { confirm, prompt } from "./dialog";
import { filesIn, type Picked } from "./dropped";
import { build, FileTree } from "./FileTree";
import { Check, Trash } from "./icons";
import { parse, render, verifiedBy } from "./okf";
import { elapsed, useSources } from "./useSources";

/** Folders open by default: the two that matter, and nothing deeper. */
const OPEN = ["raw", "wiki"];

// Drawn whether or not they hold anything. The tree is built from file paths, so an empty
// half would simply not appear — and with it goes the only place to drop a file or make a
// folder, which is exactly when you need it most.
const HALVES = ["raw/", "wiki/"];

export function Library({ bundle }: { bundle: string }) {
  const [paths, setPaths] = useState<string[]>([]);
  const [dirs, setDirs] = useState<string[]>([]);
  const [selected, setSelected] = useState("index.md");
  const [raw, setRaw] = useState("");
  const [error, setError] = useState("");
  const [unreadable, setUnreadable] = useState("");
  const picker = useRef<HTMLInputElement>(null);
  // which folder the picker is filling. A ref, not state: the dialog outlives the render
  // that opened it, and the change event must read what was chosen at click time.
  const into = useRef("raw");
  const { sources, version } = useSources(bundle);
  const [open, setOpen] = useState(new Set(OPEN));

  // clear on success: a transient failure used to stay on screen until a hard refresh.
  const refresh = () =>
    Promise.all([tree(bundle), folders(bundle)])
      .then(([files, made]) => {
        setPaths(Object.keys(files));
        setDirs(made);
        setError("");
      })
      .catch((e) => setError(String(e)));

  useEffect(() => {
    refresh();
    setSelected("index.md");
  }, [bundle]);

  useEffect(() => {
    if (version) refresh(); // an ingest just wrote pages — the tree is stale
  }, [version]);

  // ...and the desktop client can add or remove sources with no ingest involved at all,
  // which nothing else would tell the tree about. `sources` is already polled; this just
  // reacts when the set of paths in it changes.
  const listed = sources.map((s) => s.path).join("|");
  useEffect(() => {
    if (listed) refresh();
  }, [listed]);

  useEffect(() => {
    setRaw("");
    setUnreadable("");
    // markdown renders; anything else is asked for as text, which is how the agent got
    // it — a .docx included. Only a format nothing can extract falls through to a message.
    const wanted = selected.endsWith(".md") ? readFile : readAsText;
    wanted(bundle, selected)
      .then((text) => {
        setRaw(text);
        setError("");
      })
      .catch((e: Error) => {
        if (e.message.startsWith("415")) setUnreadable(e.message.replace(/^415\s*/, ""));
        else setError(String(e));
      });
  }, [bundle, selected]);

  const { meta, body } = parse(raw);
  const isMarkdown = selected.endsWith(".md");
  const isRaw = selected.startsWith("raw/");
  const status = sources.find((s) => s.path === selected);
  const nodes = build([...paths, ...dirs.map((d) => `raw/${d}/`), ...HALVES]);
  // a folder counts every file beneath it, not just its direct children
  const counts = paths.reduce<Record<string, number>>((acc, path) => {
    const parts = path.split("/");
    for (let i = 1; i < parts.length; i++) {
      const dir = parts.slice(0, i).join("/");
      acc[dir] = (acc[dir] ?? 0) + 1;
    }
    return acc;
  }, {});

  const toggle = (path: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (!next.delete(path)) next.add(path);
      return next;
    });

  async function markVerified() {
    try {
      await verifyPage(bundle, selected);
      setRaw(await readFile(bundle, selected));
    } catch (e) {
      setError(String(e));
    }
  }

  async function remove() {
    const name = selected.slice("raw/".length);
    const sure = await confirm(
      `Delete ${name}? The pages that rest on it are retired right away. The file itself ` +
        "stays in the history.",
      { ok: "Delete", danger: true },
    );
    if (!sure) return;
    try {
      await removeRaw(bundle, name);
      setSelected("index.md");
      refresh();
    } catch (e) {
      setError(String(e));
    }
  }

  /** Every folder on the way to a file, so the tree opens onto what was just added. */
  const ancestors = (path: string) =>
    path
      .split("/")
      .slice(0, -1)
      .map((_, i, parts) => parts.slice(0, i + 1).join("/"));

  /** `parent` is a full path — "raw", or any folder inside it. */
  async function newFolder(parent: string) {
    const name = await prompt(`New folder in ${parent}`, { ok: "Create" });
    if (!name?.trim()) return;
    const inside = parent === "raw" ? "" : parent.slice("raw/".length);
    try {
      const { folder } = await addFolder(bundle, [inside, name.trim()].filter(Boolean).join("/"));
      await refresh();
      setOpen((prev) => new Set([...prev, ...ancestors(`raw/${folder}/`)]));
    } catch (e) {
      setError(String(e));
    }
  }

  /** Dropped a source onto a folder. Both are full paths; the filename comes along. */
  async function moveInto(source: string, folder: string) {
    const name = source.split("/").pop() ?? "";
    const target = `${folder}/${name}`;
    if (target === source) return; // dropped back where it already was
    try {
      const { to } = await moveRaw(bundle, source, target);
      await refresh();
      setOpen((prev) => new Set([...prev, ...ancestors(to)]));
      if (selected === source) setSelected(to);
    } catch (e) {
      setError(String(e));
    }
  }

  /** `into` is a full folder path; "" means raw/ itself. */
  async function upload(picked: Picked[], into = "") {
    if (!picked.length) return;
    const prefix = into.startsWith("raw/") ? into.slice("raw/".length) : "";
    try {
      let last = "";
      // one at a time: each upload starts an ingest, and they queue on the same lock
      // anyway, so a burst of parallel requests would only make the failures harder to read
      for (const { file, path } of picked) {
        last = (await addRaw(bundle, file, [prefix, path].filter(Boolean).join("/"))).path;
      }
      await refresh();
      setOpen((prev) => new Set([...prev, "raw", ...ancestors(last)]));
      setSelected(last); // land on the last source so its progress is visible
    } catch (e) {
      setError(String(e));
    }
  }

  const importInto = (folder: string) => {
    into.current = folder;
    picker.current?.click();
  };

  /** The picker fills in webkitRelativePath itself, so the path can stay empty here. */
  const onPicked = (e: React.ChangeEvent<HTMLInputElement>) => {
    const chosen = [...(e.target.files ?? [])].map((file) => ({ file, path: "" }));
    e.target.value = ""; // so picking the same file twice still fires a change
    void upload(chosen, into.current);
  };

  /** Files or folders dragged in from outside. Anything else on the row is a move. */
  const onDropped = async (transfer: DataTransfer, into: string) => {
    try {
      await upload(await filesIn(transfer), into);
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <div className="body">
      <nav className="sidebar">
        <input ref={picker} type="file" multiple hidden onChange={onPicked} />

        <FileTree
          nodes={nodes}
          selected={selected}
          onSelect={setSelected}
          open={open}
          toggle={toggle}
          sources={sources}
          counts={counts}
          onNewFolder={newFolder}
          onMove={moveInto}
          onDrop={onDropped}
          onImport={importInto}
        />
      </nav>

      <main className="page">
        {/* failures belong where there is room to read them, not squeezed into the rail */}
        {status?.error && !status.ingesting && (
          <div className="banner">
            <b>Ingest failed.</b> {status.error}{" "}
            <button
              className="more"
              onClick={() =>
                retryIngest(bundle, selected).then(refresh, (e) => setError(String(e)))
              }
            >
              retry
            </button>
          </div>
        )}
        {error && <div className="banner">{error}</div>}

        <div className="crumbs">{selected.split("/").join(" / ")}</div>

        {meta.title && <h1>{meta.title}</h1>}
        {meta.description && <p className="lede">{meta.description}</p>}

        {(meta.type || meta.status || meta.tags) && (
          <div className="tags">
            {meta.type && <span className="tag strong">{meta.type}</span>}
            <span className="tag">{meta.status ?? "stable"}</span>
            {(meta.tags ?? []).map((t) => (
              <span className="tag" key={t}>
                {t}
              </span>
            ))}
          </div>
        )}

        {isMarkdown ? (
          <div className="prose" dangerouslySetInnerHTML={{ __html: render(body) }} />
        ) : unreadable ? (
          <p className="empty">
            {unreadable}. It is stored and synced, but nothing has read it — including the agent,
            which is why no page cites it.
          </p>
        ) : (
          <>
            <p className="soft extracted">Text as the agent reads it.</p>
            <pre className="extract">{raw}</pre>
          </>
        )}
      </main>

      <aside className="rail">
        {isRaw && (
          <section>
            <h2>Source</h2>
            <div className="srcstate">
              {/* pages appear while it is still working — "Reading" alone reads as stuck */}
              {status?.ingesting ? (
                <>
                  <span className="pulse" />
                  <span className="grow">{status.pages ? "Writing" : "Reading"}</span>
                  <span className="state">
                    {status.pages ? `${status.pages} pages · ` : ""}
                    {elapsed(status.seconds)}
                  </span>
                </>
              ) : status?.ingested ? (
                <>
                  <span className="dot done" />
                  <span className="grow">Ingested</span>
                  {/* right after a move the pages still name the old path, so the count
                      reads zero until the lint repoints them — say that rather than 0 */}
                  <span className="state">
                    {status.moved ? "links update at the next lint" : `${status.pages} pages`}
                  </span>
                </>
              ) : status?.error ? (
                <>
                  <span className="dot failed" />
                  <span className="grow">Failed</span>
                </>
              ) : (
                <>
                  <span className="dot idle" />
                  <span className="grow">Not ingested</span>
                </>
              )}
            </div>
            {status?.ingesting && status.note && <div className="step">{status.note}</div>}
            {status?.undone && (
              <div className="step">Its last ingest was undone. Ingest it again when ready.</div>
            )}
            <button className="primary danger" onClick={remove}>
              <Trash /> Delete source
            </button>
          </section>
        )}

        <section>
          <h2>Provenance</h2>
          {(meta.sources ?? []).map((s, i) => (
            <div className="source" key={s.id ?? i}>
              {s.id && <span className="id">{s.id}</span>}
              <span className="title">{s.title ?? s.resource}</span>
              {s.resource && <span className="where">{s.resource}</span>}
            </div>
          ))}
          {!meta.sources?.length && <p>No sources cited on this page.</p>}
        </section>

        {/* Trust is about a page the agent wrote — a raw document has no author to vouch */}
        {!isRaw && (
          <section>
            <h2>Trust</h2>
            {/* unverified needs no sentence — the Mark as verified button below says it */}
            {verifiedBy(meta) && <p>Checked by {verifiedBy(meta)?.by}.</p>}
            {meta.generated && (
              <div className="meta">
                {`generated\n  by: ${meta.generated.by}\n  at: ${meta.generated.at}`}
              </div>
            )}
            {/* only a real OKF page can be stamped — index.md and log.md have no `type` */}
            {meta.type && !verifiedBy(meta) && (
              <button className="primary dark" onClick={markVerified}>
                <Check /> Mark as verified
              </button>
            )}
          </section>
        )}
      </aside>
    </div>
  );
}
