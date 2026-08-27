import { fireEvent, render, screen } from "@testing-library/react";
import { act } from "react";
import { describe, expect, it } from "vitest";

import { confirm, Dialogs, prompt } from "./dialog";

describe("dialogs", () => {
  it("answers a confirm with true on OK, false on Cancel or Escape", async () => {
    render(<Dialogs />);
    let asked!: Promise<boolean>;
    act(() => void (asked = confirm("Delete it?", { ok: "Delete", danger: true })));
    expect(screen.getByRole("dialog").textContent).toContain("Delete it?");
    fireEvent.click(screen.getByText("Delete"));
    expect(await asked).toBe(true);
    expect(screen.queryByRole("dialog")).toBeNull();

    act(() => void (asked = confirm("Again?")));
    fireEvent.click(screen.getByText("Cancel"));
    expect(await asked).toBe(false);

    act(() => void (asked = confirm("Once more?")));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(await asked).toBe(false);
  });

  it("answers a prompt with the text, or null when dismissed", async () => {
    render(<Dialogs />);
    let asked!: Promise<string | null>;
    act(() => void (asked = prompt("Folder name", { initial: "notes" })));
    const input = screen.getByRole("textbox") as HTMLInputElement;
    expect(input.value).toBe("notes");
    fireEvent.change(input, { target: { value: "papers" } });
    fireEvent.submit(screen.getByRole("dialog"));
    expect(await asked).toBe("papers");

    act(() => void (asked = prompt("Folder name")));
    fireEvent.click(screen.getByText("Cancel"));
    expect(await asked).toBeNull();
  });
});
