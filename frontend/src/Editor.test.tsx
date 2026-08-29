import { render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { describe, expect, it, vi } from "vitest";

import Editor from "./Editor";

// CodeMirror blocks watch their own visibility; jsdom has no such thing
(globalThis as { IntersectionObserver?: unknown }).IntersectionObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

const PAGE =
  "# Jane\n\nA person.[^1]\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```py\nx = 1\n```\n\n[^1]: the deck\n";

describe("Editor", () => {
  it("mounts the page's body — twice, under StrictMode — without losing a node", async () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const { unmount } = render(
      <StrictMode>
        <Editor initial={PAGE} onChange={() => undefined} />
      </StrictMode>,
    );
    await waitFor(() => expect(screen.getByText("Jane")).toBeTruthy());
    await waitFor(() => expect(document.querySelectorAll(".ProseMirror")).toHaveLength(1));
    expect(screen.getByText("the deck")).toBeTruthy();
    expect(document.querySelector("table")).toBeTruthy();
    expect(errors).not.toHaveBeenCalled();
    unmount();
    await waitFor(() => expect(document.querySelectorAll(".ProseMirror")).toHaveLength(0));
    errors.mockRestore();
  });
});
