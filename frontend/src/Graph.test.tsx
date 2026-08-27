import { act } from "react";
import { createRoot } from "react-dom/client";
import { vi } from "vitest";

import { Graph, wire } from "./Graph";

const jane = { path: "wiki/people/jane.md", title: "Jane", description: "", area: 0, sources: ["raw/a.md"] };
const x = { path: "wiki/projects/x.md", title: "Project X", description: "about x", area: 0, sources: ["raw/a.md"] };

vi.mock("./api", () => ({
  graph: vi.fn(async () => ({ pages: [jane, x], links: [[jane.path, x.path]] })),
}));

test("the same wiki always settles into the same shape", () => {
  const data = { pages: [jane, x], links: [[jane.path, x.path]] as [string, string][] };
  const once = wire(data, false).nodes.map((v) => [v.x, v.y]);
  const again = wire(data, false).nodes.map((v) => [v.x, v.y]);
  expect(again).toEqual(once);
  expect(once[0]).not.toEqual(once[1]);
});

test("sources join the graph only when asked", () => {
  const data = { pages: [jane, x], links: [] as [string, string][] };
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

  const node = [...host.querySelectorAll("g.node")].find((g) => g.textContent === "Project X");
  await act(async () => node!.dispatchEvent(new MouseEvent("click", { bubbles: true })));

  expect(host.querySelector("aside")?.textContent).toContain("about x");
  expect(host.querySelector("aside")?.textContent).toContain("Linked from");
  expect(host.querySelector("aside")?.textContent).toContain("Jane");
  expect(host.querySelector("aside")?.textContent).toContain("raw/a.md");
});
