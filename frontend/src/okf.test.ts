import { describe, expect, it } from "vitest";

import { render } from "./okf";

describe("render", () => {
  it("turns the agent's footnotes into references and a footnotes list", () => {
    const html = render("The figure is 85%.[^deck]\n\n[^deck]: The 2024 deck, slide 3.\n");
    expect(html).not.toContain("[^deck]");
    expect(html).toMatch(/<sup[^>]*>.*<a[^>]*href="#[^"]*deck[^"]*"/s);
    expect(html).toContain("The 2024 deck, slide 3.");
  });
});
