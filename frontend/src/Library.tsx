import { lazy, Suspense, useEffect, useRef, useState } from "react";

import {
  addFolder,
  addRaw,
  digest,
  folders,
  moveRaw,
  readAsText,
  readFile,
  removeRaw,
  retryIngest,
  tree,
  verifyPage,
  writeFile,
} from "./api";
import { confirm, prompt } from "./dialog";
import { filesIn, type Picked } from "./dropped";
import { build, FileTree } from "./FileTree";
import { Check, Pencil, Trash } from "./icons";
import { forEditing, parse, render, verifiedBy } from "./okf";
import { elapsed, useSources } from "./useSources";

// The editor carries ProseMirror, CodeMirror and Vue — as much again as the rest of the
// app — so it arrives only when someone clicks Edit.
const Editor = lazy(() => import("./Editor"));

/** Folders open by default: the two that matter, and nothing deeper. */
const OPEN = ["raw", "wiki"];

// Drawn whether or not they hold anything. The tree is built from file paths, so an empty
// half would simply not appear — and with it goes the only place to drop a file or make a
// folder, which is exactly when you need it most.
const HALVES = ["raw/", "wiki/"];

// Where you were, per bundle, across tab switches: the tab unmounts this component, and
// coming back to index.md with every folder closed every time was the cost of that.
const views = new Map<string, { selected: string; open: string[] }>();

export function Library({ bundle }: { bundle: string }) {
  const [paths, setPaths] = useState<string[]>([]);
  const [dirs, setDirs] = useState<string[]>([]);
  const [selected, setSelected] = useState(views.get(bundle)?.selected ?? "index.md");
  const [raw, setRaw] = useState("");
  const [error, setError] = useState("");
  const [unreadable, setUnreadable] = useState("");
  const [editing, setEditing] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  // what the editor holds now, and the file as it was read: the sha of that is the
  // If-Match on save, so a page the agent rewrote meanwhile is refused, not overwritten
  const draft = useRef("");
  const loaded = useRef("");
  const picker = useRef<HTMLInputElement>(null);
  // which folder the picker is filling. A ref, not state: the dialog outlives the render
  // that opened it, and the change event must read what was chosen at click time.
  const into = useRef("raw");
  const { sources, version } = useSources(bundle);
  const [open, setOpen] = useState(new Set(views.get(bundle)?.open ?? OPEN));

  useEffect(() => {
    views.set(bundle, { selected, open: [...open] });
  }, [bundle, selected, open]);

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
    setSelected(views.get(bundle)?.selected ?? "index.md");
    setOpen(new Set(views.get(bundle)?.open ?? OPEN));
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
    setEditing(false);
    setDirty(false);
    // markdown renders; anything else is asked for as text, which is how the agent got
    // it — a .docx included. Only a format nothing can extract falls through to a message.
    const wanted = selected.endsWith(".md") ? readFile : readAsText;
    wanted(bundle, selected)
      .then((text) => {
        loaded.current = text;
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
  // a page or a markdown source, in place; the files at the root are the agent's and
  // the server's, and a page is made by an ingest, never by hand
  const editable = isMarkdown && (isRaw || selected.startsWith("wiki/"));
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

  /** Leaving an unsaved edit — for another file, or by Cancel — asks first. */
  async function leave(): Promise<boolean> {
    if (!editing || !dirty) return true;
    return confirm("Leave without saving? The changes to this page are lost.", {
      ok: "Leave",
      danger: true,
    });
  }

  async function select(path: string) {
    if (!(await leave())) return;
    setEditing(false);
    setDirty(false);
    setSelected(path);
  }

  function edit() {
    draft.current = forEditing(loaded.current).body;
    setDirty(false);
    setEditing(true);
  }

  async function cancel() {
    if (!(await leave())) return;
    setEditing(false);
    setDirty(false);
  }

  /** The frontmatter as it was, the body as edited, one newline at the end. */
  async function save() {
    setSaving(true);
    const text = forEditing(loaded.current).head + draft.current.replace(/\n*$/, "\n");
    try {
      await writeFile(bundle, selected, text, await digest(loaded.current));
      loaded.current = text;
      setRaw(text);
      setEditing(false);
      setDirty(false);
      setError("");
      refresh();
    } catch (e) {
      // the editor stays open with the work in it: a stale copy is not lost, only not saved
      const stale = e instanceof Error && e.message.startsWith("412");
      setError(
        stale
          ? "This page changed while you were editing it — the agent, or someone else. " +
              "Copy what you changed, open the page again, and edit that."
          : String(e),
      );
    } finally {
      setSaving(false);
    }
  }

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
          onSelect={select}
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

        <div className="pagebar">
          <div className="crumbs">{selected.split("/").join(" / ")}</div>
          {editable && !editing && (
            <button className="quiet" onClick={edit}>
              <Pencil /> Edit
            </button>
          )}
          {editing && (
            <div className="actions">
              <button className="quiet" onClick={cancel} disabled={saving}>
                Cancel
              </button>
              <button className="primary" onClick={save} disabled={saving || !dirty}>
                {saving ? "Saving…" : "Save"}
              </button>
            </div>
          )}
        </div>

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

        {editing ? (
          <>
            <Suspense fallback={<p className="soft">Loading the editor…</p>}>
              <Editor
                initial={draft.current}
                onChange={(markdown) => {
                  draft.current = markdown;
                  setDirty(true);
                }}
              />
            </Suspense>
            <p className="soft">
              The title, description and tags above come from the page's frontmatter, which stays as
              it is.
            </p>
          </>
        ) : isMarkdown ? (
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
            <div className="row">
              {/* a run that read it and wrote nothing, or an old one you want redone — a
                  clean run is skipped by the queue, so this is the one way to ask again */}
              <button
                className="primary dark"
                disabled={status?.ingesting}
                onClick={() =>
                  retryIngest(bundle, selected).then(refresh, (e) => setError(String(e)))
                }
              >
                Ingest again
              </button>
              <button className="primary danger" onClick={remove}>
                <Trash /> Delete source
              </button>
            </div>
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
