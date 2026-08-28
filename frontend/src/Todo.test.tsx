import { act } from "react";
import { createRoot } from "react-dom/client";
import { vi } from "vitest";

import { Todo } from "./Todo";

let answer = [{ id: 0, done: false, text: "Which figure?", detail: "used 85%" }];
const work = [{ id: 0, done: false, text: "Upload the pricing deck", detail: "" }];

vi.mock("./api", () => ({
  questions: vi.fn(async () => answer),
  tasks: vi.fn(async () => work),
  setQuestion: vi.fn(async () => ({ done: true })),
  setTask: vi.fn(async () => ({ done: true })),
  ask: vi.fn(async () => ({ reply: "done", changed: ["raw/x.md"] })),
}));

/** Answering the last question empties the list, and an empty list is a real screen. */
test("survives its own list going empty", async () => {
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);

  await act(async () => root.render(<Todo bundle="default" />));
  expect(host.textContent).toContain("Which figure?");
  expect(host.textContent).toContain("1 left");

  answer = []; // the tick lands, and nothing is open any more
  const tick = [...host.querySelectorAll("button")].find((b) =>
    b.textContent?.includes("Already answered"),
  );
  await act(async () => tick!.click());

  expect(host.textContent).toContain("all answered");
  expect(host.textContent).toContain("Nothing open");
});

/** Tasks are the other list: a checklist a person ticks, on the same tab. */
test("tasks are ticked in place", async () => {
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);

  await act(async () => root.render(<Todo bundle="default" />));
  const tab = [...host.querySelectorAll("button")].find((b) => b.textContent?.startsWith("Tasks"));
  await act(async () => tab!.click());
  expect(host.textContent).toContain("Upload the pricing deck");

  const box = host.querySelector("input[type=checkbox]") as HTMLInputElement;
  expect(box.checked).toBe(false);
  await act(async () => box.click());
  expect((host.querySelector("input[type=checkbox]") as HTMLInputElement).checked).toBe(true);
});
