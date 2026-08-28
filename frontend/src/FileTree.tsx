import { useState } from "react";

import type { Source } from "./api";
import { Chevron, ImportInto, NewFolder } from "./icons";

/** raw/ is the user's half — the only half they may reorganise. */
const mine = (path: string) => path === "raw" || path.startsWith("raw/");

export type Node = { name: string; path: string; children?: Node[] };

/**
 * Fold a flat list of paths into a nested tree. Folders sort before files.
 *
 * A trailing slash marks a folder that holds no files yet — the only way to express an
 * empty one in a list of paths, and empty is exactly what a folder you just made is.
 */
export function build(paths: string[]): Node[] {
  const root: Node[] = [];

  for (const path of paths) {
    const empty = path.endsWith("/");
    const parts = path.split("/").filter(Boolean);
    let level = root;
    parts.forEach((name, i) => {
      const here = parts.slice(0, i + 1).join("/");
      const isFile = i === parts.length - 1 && !empty;
      let node = level.find((n) => n.name === name && !n.children === isFile);
      if (!node) {
        node = isFile ? { name, path: here } : { name, path: here, children: [] };
        level.push(node);
      }
      if (node.children) level = node.children;
    });
  }

  const order = (nodes: Node[]): Node[] =>
    nodes
      .sort((a, b) => {
        const folders = Number(!!b.children) - Number(!!a.children);
        return folders || a.name.localeCompare(b.name);
      })
      .map((n) => (n.children ? { ...n, children: order(n.children) } : n));

  return order(root);
}

type Props = {
  nodes: Node[];
  depth?: number;
  selected: string;
  onSelect: (path: string) => void;
  open: Set<string>;
  toggle: (path: string) => void;
  sources: Source[];
  counts?: Record<string, number>;
  /** Make a folder inside this one. */
  onNewFolder: (parent: string) => void;
  /** Drop a source into a folder. Both are full paths. */
  onMove: (source: string, folder: string) => void;
  /** Files or folders dragged in from outside, dropped onto this folder. */
  onDrop: (transfer: DataTransfer, folder: string) => void;
  /** Open a file picker that uploads into this folder. */
  onImport: (folder: string) => void;
};

export function FileTree({
  nodes,
  depth = 0,
  selected,
  onSelect,
  open,
  toggle,
  sources,
  counts,
  onNewFolder,
  onMove,
  onDrop,
  onImport,
}: Props) {
  // which folder the pointer is over mid-drag, so there is somewhere obvious to let go
  const [over, setOver] = useState("");
  // 18px base, 13px per level — enough to read the nesting without losing filename width
  const pad = (extra = 0) => ({ paddingLeft: 18 + depth * 13 + extra });

  return (
    <>
      {nodes.map((node) =>
        node.children ? (
          <div key={node.path}>
            {/* a row, not one button: the add-a-folder control cannot nest inside the
                toggle, and dropping onto the row has to mean the folder, not the label */}
            <div
              className={over === node.path ? "folderrow over" : "folderrow"}
              onDragOver={(e) => {
                if (!mine(node.path)) return;
                e.preventDefault(); // without this the drop never fires
                setOver(node.path);
              }}
              onDragLeave={(e) => {
                // dragleave also fires when the pointer crosses into a child, which would
                // make the highlight flicker between the label and the add button
                // `Node` here is this module's own type, so the DOM one is spelled out
                if (e.currentTarget.contains(e.relatedTarget as HTMLElement | null)) return;
                setOver((o) => (o === node.path ? "" : o));
              }}
              onDrop={(e) => {
                setOver("");
                if (!mine(node.path)) return;
                e.preventDefault();
                // two kinds of drop land here: a source dragged from this tree, which is
                // a move, and files or folders from outside, which are new sources
                const source = e.dataTransfer.getData("text/plain");
                if (source) onMove(source, node.path);
                else if (e.dataTransfer.items.length) onDrop(e.dataTransfer, node.path);
              }}
            >
              <button
                className="folder"
                style={pad()}
                onClick={() => toggle(node.path)}
                aria-expanded={open.has(node.path)}
              >
                <span className={open.has(node.path) ? "twisty open" : "twisty"}>
                  <Chevron size={11} />
                </span>
                <span className="grow">{node.name}</span>
                {node.path === "wiki" && <span className="badge">read-only</span>}
                <span className="count">{counts?.[node.path] ?? node.children.length}</span>
              </button>
              {mine(node.path) && (
                <>
                  <button
                    className="rowbtn"
                    title={`Add sources to ${node.name}`}
                    onClick={() => onImport(node.path)}
                  >
                    <ImportInto />
                  </button>
                  <button
                    className="rowbtn"
                    title={`New folder in ${node.name}`}
                    onClick={() => onNewFolder(node.path)}
                  >
                    <NewFolder />
                  </button>
                </>
              )}
            </div>
            {open.has(node.path) && (
              <FileTree
                nodes={node.children}
                depth={depth + 1}
                selected={selected}
                onSelect={onSelect}
                open={open}
                toggle={toggle}
                sources={sources}
                counts={counts}
                onNewFolder={onNewFolder}
                onMove={onMove}
                onDrop={onDrop}
                onImport={onImport}
              />
            )}
          </div>
        ) : (
          <Leaf
            key={node.path}
            node={node}
            style={pad(16)}
            selected={selected}
            onSelect={onSelect}
            source={sources.find((s) => s.path === node.path)}
            draggable={mine(node.path)}
          />
        ),
      )}
    </>
  );
}

function Leaf({
  node,
  style,
  selected,
  onSelect,
  source,
  draggable,
}: {
  node: Node;
  style: React.CSSProperties;
  selected: string;
  onSelect: (path: string) => void;
  source?: Source;
  draggable: boolean;
}) {
  return (
    <button
      className="entry"
      style={style}
      aria-current={selected === node.path}
      onClick={() => onSelect(node.path)}
      title={node.path}
      draggable={draggable}
      onDragStart={(e) => e.dataTransfer.setData("text/plain", node.path)}
    >
      {source?.ingesting && <span className="pulse" />}
      {source && !source.ingesting && !source.ingested && (
        <span className={source.error ? "pending failed" : "pending"} />
      )}
      {node.name}
    </button>
  );
}
