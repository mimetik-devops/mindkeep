import { took, when } from "./useSources";
import { build } from "./FileTree";
import { parse } from "./okf";

test("reads OKF frontmatter, and survives a page that has none", () => {
  const page = "---\ntype: Concept\ntitle: Jane\ntags: [a, b]\n---\n\n# Body\n";
  expect(parse(page).meta).toMatchObject({ type: "Concept", title: "Jane", tags: ["a", "b"] });
  expect(parse(page).body.trim()).toBe("# Body");
  expect(parse("no frontmatter here").meta).toEqual({});
});

test("folds paths into a tree, folders before files", () => {
  const nodes = build(["index.md", "wiki/concepts/a.md", "wiki/b.md", "raw/x.pdf"]);
  expect(nodes.map((n) => n.name)).toEqual(["raw", "wiki", "index.md"]);

  const wiki = nodes.find((n) => n.name === "wiki")!;
  expect(wiki.children!.map((n) => n.name)).toEqual(["concepts", "b.md"]);
  expect(wiki.children![0].children![0].path).toBe("wiki/concepts/a.md");
});

test("formats a finished duration", () => {
  expect(took(9)).toBe("9s");
  expect(took(59)).toBe("59s");
  expect(took(190)).toBe("3m 10s");
});

test("a scheduled UTC instant reads as a local time the user recognises", () => {
  const at = (days: number, hour: number) => {
    const d = new Date();
    d.setDate(d.getDate() + days);
    d.setHours(hour, 0, 0, 0);
    return d.toISOString(); // local wall-clock, expressed in UTC, exactly as the server sends it
  };

  expect(when(at(0, 23))).toMatch(/^today at /);
  expect(when(at(1, 5))).toMatch(/^tomorrow at /);
  expect(when(at(3, 5))).toMatch(/^\w+ at /);
  expect(when("")).toBe(""); // the nightly pass is off
});

test("a folder with nothing in it still appears in the tree", () => {
  const nodes = build(["raw/note.md", "raw/papers/2026/"]);
  const raw = nodes.find((n) => n.name === "raw");
  const papers = raw?.children?.find((n) => n.name === "papers");

  expect(papers?.children?.[0]).toMatchObject({ name: "2026", path: "raw/papers/2026" });
  expect(papers?.children?.[0].children).toEqual([]); // a folder, not a file called ""
  expect(raw?.children?.map((n) => n.name)).toEqual(["papers", "note.md"]);
});

test("the two halves are drawn even with nothing in them", () => {
  const nodes = build(["index.md", "raw/", "wiki/"]);
  expect(nodes.map((n) => n.name)).toEqual(["raw", "wiki", "index.md"]);
  expect(nodes[0].children).toEqual([]); // a place to drop a file, not a file called ""
});
