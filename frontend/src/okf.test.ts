import { describe, expect, it } from "vitest";

import { forEditing, render } from "./okf";

describe("render", () => {
  it("turns the agent's footnotes into references and a footnotes list", () => {
    const html = render("The figure is 85%.[^deck]\n\n[^deck]: The 2024 deck, slide 3.\n");
    expect(html).not.toContain("[^deck]");
    expect(html).toMatch(/<sup[^>]*>.*<a[^>]*href="#[^"]*deck[^"]*"/s);
    expect(html).toContain("The 2024 deck, slide 3.");
  });
});

describe("forEditing", () => {
  it("keeps the frontmatter aside verbatim and drops the stray </content> at the foot", () => {
    const raw = "---\ntype: Person\ntitle:  Jane\n---\n\n# Jane\n\n[^1]: a\n</content>\n";
    expect(forEditing(raw)).toEqual({
      head: "---\ntype: Person\ntitle:  Jane\n---\n",
      body: "\n# Jane\n\n[^1]: a\n",
    });
    expect(forEditing("no frontmatter\n")).toEqual({ head: "", body: "no frontmatter\n" });
  });
});
