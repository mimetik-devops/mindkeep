import { act } from "react";
import { createRoot } from "react-dom/client";
import { vi } from "vitest";

import { Bundles } from "./Bundles";

const created: string[] = [];
vi.mock("./api", () => ({
  createBundle: vi.fn(async (name: string) => {
    if (name === "taken") throw new Error("409 bundle already exists");
    created.push(name);
    return { name };
  }),
}));

function mount() {
  const host = document.createElement("div");
  document.body.append(host);
  const picked: string[] = [];
  const made: string[] = [];
  const root = createRoot(host);
  const render = (bundles: string[], current: string) =>
    act(async () =>
      root.render(
        <Bundles
          bundles={bundles}
          current={current}
          onPick={(b) => picked.push(b)}
          onCreate={(b) => made.push(b)}
        />,
      ),
    );
  return { host, picked, made, render };
}

const input = (host: HTMLElement) => host.querySelector("input")!;
const button = (host: HTMLElement, text: string) =>
  [...host.querySelectorAll("button")].find((b) => b.textContent?.trim() === text)!;

test("lists the bundles and switches on a click", async () => {
  const { host, picked, render } = mount();
  await render(["default", "work"], "default");

  await act(async () => button(host, "default").click()); // the pill opens the menu
  expect(host.querySelector('[role="menuitemradio"][aria-checked="true"]')?.textContent).toBe("default");
  await act(async () => button(host, "work").click());

  expect(picked).toEqual(["work"]);
  expect(host.querySelector('[role="menu"]')).toBeNull(); // and the menu closed
});

test("creates a bundle from the field and hands it back", async () => {
  const { host, made, render } = mount();
  await render(["default"], "default");
  await act(async () => button(host, "default").click());

  await act(async () => {
    const field = input(host);
    const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    set.call(field, "notes");
    field.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => host.querySelector("form")!.requestSubmit());

  expect(created).toContain("notes");
  expect(made).toEqual(["notes"]);
  expect(host.querySelector('[role="menu"]')).toBeNull();
});

test("shows the server's reason when a name is refused", async () => {
  const { host, made, render } = mount();
  await render(["default"], "default");
  await act(async () => button(host, "default").click());

  await act(async () => {
    const field = input(host);
    const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    set.call(field, "taken");
    field.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => host.querySelector("form")!.requestSubmit());

  expect(host.textContent).toContain("bundle already exists");
  expect(host.textContent).not.toContain("409");
  expect(made).toEqual([]);
  expect(host.querySelector('[role="menu"]')).not.toBeNull(); // stays open to try again
});
