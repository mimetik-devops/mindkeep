import { act } from "react";
import { createRoot } from "react-dom/client";
import { vi } from "vitest";

import type { Graph as Data } from "./api";
import { Graph, wire } from "./Graph";

const jane = { path: "wiki/people/jane.md", title: "Jane", description: "", area: 0, sources: ["raw/a.md"] };
const x = { path: "wiki/projects/x.md", title: "Project X", description: "about x", area: 0, sources: ["raw/a.md"] };
const tax = ["vat", "payroll", "audit"].map((n) => ({
  path: `wiki/tax/${n}.md`,
  title: n,
  description: "",
  area: 1,
  sources: [],
}));

let served: Data = { pages: [jane, x], links: [[jane.path, x.path]], gaps: [] };

vi.mock("./api", () => ({
  graph: vi.fn(async () => served),
}));

test("the same wiki always settles into the same shape", () => {
  const data: Data = { pages: [jane, x], links: [[jane.path, x.path]], gaps: [] };
  const once = wire(data, false).nodes.map((v) => [v.x, v.y]);
  const again = wire(data, false).nodes.map((v) => [v.x, v.y]);
  expect(again).toEqual(once);
  expect(once[0]).not.toEqual(once[1]);
});

test("sources join the graph only when asked", () => {
  const data: Data = { pages: [jane, x], links: [], gaps: [] };
  expect(wire(data, false).nodes).toHaveLength(2);
  const { nodes, near } = wire(data, true);
  expect(nodes.map((v) => v.id)).toEqual([jane.path, x.path, "raw/a.md"]);
  expect(near.get("raw/a.md")).toEqual(new Set([jane.path, x.path]));
});

/** Clicking a page shows what the page itself cannot: who links to it. */
test("a click opens the page's connections", async () => {
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);

  await act(async () => root.render(<Graph bundle="default" />));
  expect(host.textContent).toContain("2 pages");
  expect(host.textContent).toContain("1 links");

  // labelled by slug, so a cluster of sentence-long titles stays readable
  const node = [...host.querySelectorAll("g.node")].find(
    (g) => g.querySelector("text")?.textContent === "x",
  );
  expect(node?.querySelector("title")?.textContent).toBe("Project X");
  await act(async () => node!.dispatchEvent(new MouseEvent("click", { bubbles: true })));

  expect(host.querySelector("aside")?.textContent).toContain("Project X");
  expect(host.querySelector("aside")?.textContent).toContain("about x");
  expect(host.querySelector("aside")?.textContent).toContain("Linked from");
  expect(host.querySelector("aside")?.textContent).toContain("Jane");
  expect(host.querySelector("aside")?.textContent).toContain("raw/a.md");
});

/** Gaps mode shows what the lint will be asked about: which two areas, and how thin. */
test("gaps mode lists each gap and lights only its two areas", async () => {
  const alone = { path: "wiki/alone.md", title: "Alone", description: "", area: -1, sources: [] };
  // a third area on no side of any gap: it fades, so the gaps are where the colour is
  const food = { path: "wiki/food.md", title: "Food", description: "", area: 2, sources: [] };
  served = {
    pages: [jane, x, ...tax, alone, food],
    links: [
      [jane.path, x.path],
      [tax[0].path, tax[1].path],
      [tax[1].path, tax[2].path],
    ],
    gaps: [{ a: 0, b: 1, links: 0, expected: 3 }],
  };
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  await act(async () => root.render(<Graph bundle="default" />));

  const toggle = [...host.querySelectorAll("label")].find((l) => l.textContent?.includes("Show gaps"));
  await act(async () => toggle!.querySelector("input")!.click());

  const aside = host.querySelector("aside")!;
  expect(aside.textContent).toContain("1 gap");
  expect(aside.textContent).toContain("Area 1 (2)");
  expect(aside.textContent).toContain("Area 2 (3)");
  expect(aside.textContent).toContain("0 of 3");
  expect(host.querySelectorAll("g.gap")).toHaveLength(1);
  const dimmed = () =>
    [...host.querySelectorAll("g.node.dim")].map((g) => g.querySelector("title")?.textContent);
  expect(dimmed()).toEqual(["Alone", "Food"]); // on no side of any gap

  await act(async () => host.querySelector("g.gap")!.dispatchEvent(new MouseEvent("click", { bubbles: true })));

  expect(host.querySelector("g.gap")?.classList.contains("picked")).toBe(true);
  expect(host.querySelector("g.gap text")?.textContent).toBe("0 links, 3 expected");
  expect(dimmed()).toEqual(["Alone", "Food"]);
});
