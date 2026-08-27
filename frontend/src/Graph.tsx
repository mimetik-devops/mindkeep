import { useEffect, useMemo, useState } from "react";

import { graph as fetchGraph, type Graph as Data } from "./api";

/**
 * The wiki as the agent wired it: pages as dots, links as lines, coloured by the areas
 * the lint measures gaps between. It is the same graph `related` reads and the same
 * Louvain split the gap measurement uses, so what you see is what the agent is told.
 *
 * The layout is a plain force simulation run to rest before the first paint, seeded from
 * the page order — no library, and the same wiki always settles into the same shape.
 */

type Node = {
  id: string;
  kind: "page" | "source";
  title: string;
  description: string;
  area: number;
  x: number;
  y: number;
};

/**
 * The label a node wears: its slug, not its title. Titles are sentences — "Futuros —
 * pricing, plans & security" — and forty of them over a cluster is a smear; the file
 * stem is the page's name in the OKF sense and is short by construction. The full title
 * is the hover tooltip and the side panel's heading.
 */
const slug = (v: Node) => (v.id.split("/").pop() ?? v.id).replace(/\.md$/, "");

const AREAS = ["#c0603d", "#3d6f8c", "#5b7a5e", "#8a6d3b", "#6b5b95", "#a04a6b", "#2f7f7a", "#7a7a3d"];
const NONE = "#a09584"; // a page in no area, or a source
const W = 1000;
const H = 700;
const PAD = 40;

/** Fruchterman–Reingold, then scaled to fill the box. Mutates the nodes' x/y. */
export function layout(nodes: Node[], edges: [number, number][]) {
  const n = nodes.length;
  if (!n) return;
  let seed = 7;
  const rand = () => (seed = (seed * 48271) % 2147483647) / 2147483647;
  nodes.forEach((v, i) => {
    const a = (i / n) * 2 * Math.PI;
    v.x = W / 2 + (W / 3) * Math.cos(a) + (rand() - 0.5) * 40;
    v.y = H / 2 + (H / 3) * Math.sin(a) + (rand() - 0.5) * 40;
  });
  const k = Math.sqrt((W * H) / n) * 0.6;
  const dx = new Float64Array(n);
  const dy = new Float64Array(n);
  let temp = W / 8;
  for (let it = 0; it < 300; it++) {
    dx.fill(0);
    dy.fill(0);
    for (let i = 0; i < n; i++)
      for (let j = i + 1; j < n; j++) {
        const ex = nodes[i].x - nodes[j].x;
        const ey = nodes[i].y - nodes[j].y;
        const f = (k * k) / (ex * ex + ey * ey || 0.01);
        dx[i] += ex * f;
        dy[i] += ey * f;
        dx[j] -= ex * f;
        dy[j] -= ey * f;
      }
    for (const [a, b] of edges) {
      const ex = nodes[a].x - nodes[b].x;
      const ey = nodes[a].y - nodes[b].y;
      const f = Math.sqrt(ex * ex + ey * ey) / k;
      dx[a] -= ex * f;
      dy[a] -= ey * f;
      dx[b] += ex * f;
      dy[b] += ey * f;
    }
    for (let i = 0; i < n; i++) {
      // a little gravity, so pieces nothing links to stay in the picture
      dx[i] += (W / 2 - nodes[i].x) * 0.05;
      dy[i] += (H / 2 - nodes[i].y) * 0.05;
      const d = Math.sqrt(dx[i] * dx[i] + dy[i] * dy[i]) || 0.01;
      const step = Math.min(d, temp) / d;
      nodes[i].x += dx[i] * step;
      nodes[i].y += dy[i] * step;
    }
    temp *= 0.98;
  }
  const xs = nodes.map((v) => v.x);
  const ys = nodes.map((v) => v.y);
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const s = Math.min((W - 2 * PAD) / (x1 - x0 || 1), (H - 2 * PAD) / (y1 - y0 || 1));
  for (const v of nodes) {
    v.x = W / 2 + (v.x - (x0 + x1) / 2) * s;
    v.y = H / 2 + (v.y - (y0 + y1) / 2) * s;
  }
}

/** Nodes, edges by index, and each node's neighbours — sources as nodes when asked. */
export function wire(data: Data, withSources: boolean) {
  const nodes: Node[] = [];
  const edges: [number, number][] = [];
  const at = new Map<string, number>();
  const near = new Map<string, Set<string>>();
  for (const p of data.pages) {
    at.set(p.path, nodes.length);
    nodes.push({ id: p.path, kind: "page", ...p, x: 0, y: 0 });
  }
  for (const [a, b] of data.links) edges.push([at.get(a)!, at.get(b)!]);
  if (withSources)
    for (const p of data.pages)
      for (const s of p.sources) {
        if (!at.has(s)) {
          at.set(s, nodes.length);
          const title = s.split("/").pop() ?? s;
          nodes.push({ id: s, kind: "source", title, description: "", area: -1, x: 0, y: 0 });
        }
        edges.push([at.get(p.path)!, at.get(s)!]);
      }
  for (const [a, b] of edges) {
    near.set(nodes[a].id, (near.get(nodes[a].id) ?? new Set()).add(nodes[b].id));
    near.set(nodes[b].id, (near.get(nodes[b].id) ?? new Set()).add(nodes[a].id));
  }
  layout(nodes, edges);
  return { nodes, edges, near };
}

export function Graph({ bundle }: { bundle: string }) {
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState("");
  const [withSources, setWithSources] = useState(false);
  const [selected, setSelected] = useState("");
  const [hover, setHover] = useState("");
  const [box, setBox] = useState({ x: 0, y: 0, w: W, h: H });
  const [drag, setDrag] = useState<{ px: number; py: number; x: number; y: number } | null>(null);

  useEffect(() => {
    setData(null);
    setSelected("");
    setBox({ x: 0, y: 0, w: W, h: H });
    fetchGraph(bundle)
      .then(setData)
      .catch((e) => setError(String(e)));
  }, [bundle]);

  const { nodes, edges, near } = useMemo(
    () => (data ? wire(data, withSources) : { nodes: [], edges: [], near: new Map() }),
    [data, withSources],
  );

  const focus = hover || selected;
  const lit = (id: string) => !focus || id === focus || near.get(focus)?.has(id);
  const colour = (v: Node) => (v.area < 0 ? NONE : AREAS[v.area % AREAS.length]);
  const radius = (v: Node) =>
    v.kind === "source" ? 3 : 3 + Math.sqrt(near.get(v.id)?.size ?? 0) * 1.4;
  const page = data?.pages.find((p) => p.path === selected);
  const out = data?.links.filter(([a]) => a === selected).map(([, b]) => b) ?? [];
  const into = data?.links.filter(([, b]) => b === selected).map(([a]) => a) ?? [];
  const titled = (path: string) => data?.pages.find((p) => p.path === path)?.title ?? path;
  const areas = data ? new Set(data.pages.map((p) => p.area).filter((a) => a >= 0)).size : 0;

  // Text in an SVG grows with the zoom, so zooming in spreads the nodes and enlarges the
  // labels at the same rate and the smear never clears. Labels are kept at screen size
  // instead — and at any zoom only the ones that do not collide are drawn, hubs first,
  // so the rest surface as you zoom in. A hovered or selected node's neighbourhood is
  // always labelled: that is what you are looking at.
  const scale = box.w / W;
  const labelled = useMemo(() => {
    const placed: { x0: number; y0: number; x1: number; y1: number }[] = [];
    const show = new Set<string>();
    const busiest = [...nodes].sort(
      (a, b) =>
        (near.get(b.id)?.size ?? 0) - (near.get(a.id)?.size ?? 0) || a.id.localeCompare(b.id),
    );
    for (const v of busiest) {
      const h = 12 * scale;
      const x0 = v.x + radius(v) + 3;
      const y0 = v.y - h / 2;
      const x1 = x0 + slug(v).length * 6 * scale;
      const y1 = y0 + h;
      if (placed.some((b) => x0 < b.x1 && x1 > b.x0 && y0 < b.y1 && y1 > b.y0)) continue;
      placed.push({ x0, y0, x1, y1 });
      show.add(v.id);
    }
    return show;
  }, [nodes, near, scale]); // radius() only reads `near`

  function wheel(e: React.WheelEvent<SVGSVGElement>) {
    const r = e.currentTarget.getBoundingClientRect();
    const f = e.deltaY > 0 ? 1.15 : 1 / 1.15;
    const cx = box.x + ((e.clientX - r.left) / r.width) * box.w;
    const cy = box.y + ((e.clientY - r.top) / r.height) * box.h;
    setBox({ x: cx - (cx - box.x) * f, y: cy - (cy - box.y) * f, w: box.w * f, h: box.h * f });
  }

  function move(e: React.PointerEvent<SVGSVGElement>) {
    if (!drag) return;
    const r = e.currentTarget.getBoundingClientRect();
    setBox({
      ...box,
      x: drag.x - ((e.clientX - drag.px) * box.w) / r.width,
      y: drag.y - ((e.clientY - drag.py) * box.h) / r.height,
    });
  }

  return (
    <div className="graph">
      <svg
        viewBox={`${box.x} ${box.y} ${box.w} ${box.h}`}
        onWheel={wheel}
        onPointerDown={(e) => setDrag({ px: e.clientX, py: e.clientY, x: box.x, y: box.y })}
        onPointerMove={move}
        onPointerUp={() => setDrag(null)}
        onPointerLeave={() => setDrag(null)}
        onClick={() => setSelected("")}
      >
        {edges.map(([a, b], i) => {
          const touches = nodes[a].id === focus || nodes[b].id === focus;
          return (
            <line
              key={i}
              x1={nodes[a].x}
              y1={nodes[a].y}
              x2={nodes[b].x}
              y2={nodes[b].y}
              className={
                (nodes[b].kind === "source" ? "source " : "") + (focus && !touches ? "dim" : "")
              }
            />
          );
        })}
        {nodes.map((v) => (
          <g
            key={v.id}
            className={`node ${v.id === selected ? "selected" : ""} ${lit(v.id) ? "" : "dim"}`}
            transform={`translate(${v.x} ${v.y})`}
            onPointerDown={(e) => e.stopPropagation()}
            onClick={(e) => {
              e.stopPropagation();
              setSelected(v.id === selected ? "" : v.id);
            }}
            onPointerEnter={() => setHover(v.id)}
            onPointerLeave={() => setHover("")}
          >
            <title>{v.title}</title>
            {v.kind === "source" ? (
              <rect x={-3} y={-3} width={6} height={6} fill={NONE} />
            ) : (
              <circle r={radius(v)} fill={colour(v)} />
            )}
            {(labelled.has(v.id) || (focus && lit(v.id))) && (
              <text x={radius(v) + 3} y={4 * scale} fontSize={10 * scale}>
                {slug(v)}
              </text>
            )}
          </g>
        ))}
      </svg>

      <aside className="side">
        {error && <p className="error">{error}</p>}
        {data && !data.pages.length && <p className="soft">No pages yet — ingest a source.</p>}
        {page ? (
          <>
            <h2>{page.title}</h2>
            <span className="path">{page.path}</span>
            {page.description && <p>{page.description}</p>}
            <p className="soft">
              {page.area < 0 ? "In no area" : `Area ${page.area + 1}`}
              {" · "}
              {near.get(page.path)?.size ?? 0} connections
            </p>
            {(
              [
                ["Links to", out],
                ["Linked from", into],
              ] as [string, string[]][]
            ).map(([label, list]) =>
              list.length ? (
                <section key={label}>
                  <h3>{label}</h3>
                  {list.map((p) => (
                    <button key={p} className="link" onClick={() => setSelected(p)}>
                      {titled(p)}
                    </button>
                  ))}
                </section>
              ) : null,
            )}
            {page.sources.length > 0 && (
              <section>
                <h3>Cites</h3>
                {page.sources.map((s) => (
                  <span key={s} className="path">
                    {s}
                  </span>
                ))}
              </section>
            )}
          </>
        ) : (
          data &&
          data.pages.length > 0 && (
            <>
              <h2>{data.pages.length} pages</h2>
              <p className="soft">
                {data.links.length} links · {areas} {areas === 1 ? "area" : "areas"}
              </p>
              <p>
                Each colour is an area the wiki has grown: pages that link among themselves far
                more than outward. The lint looks for pairs of areas with almost nothing between
                them, and asks the question that would connect them.
              </p>
              <label className="toggle">
                <input
                  type="checkbox"
                  checked={withSources}
                  onChange={(e) => setWithSources(e.target.checked)}
                />
                Show sources
              </label>
              <p className="soft">
                Click a page for what links to it. Scroll to zoom — more labels appear as
                there is room for them — and drag to pan.
              </p>
            </>
          )
        )}
      </aside>
    </div>
  );
}
