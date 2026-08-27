import { act } from "react";
import { createRoot } from "react-dom/client";
import { vi } from "vitest";

import { Invite } from "./Invite";

const acme = { id: "t1", name: "Acme", personal: false, role: "member" as const };
vi.mock("./api", () => ({
  peekInvite: vi.fn(async (token: string) => {
    if (token === "spent") throw new Error("404 this invite is not open");
    return { team: { id: "t1", name: "Acme" }, role: "member" };
  }),
  acceptInvite: vi.fn(async () => acme),
}));

async function mount(token: string) {
  const host = document.createElement("div");
  document.body.append(host);
  const joined: unknown[] = [];
  await act(async () => createRoot(host).render(<Invite token={token} onJoined={(t) => joined.push(t)} />));
  return { host, joined };
}

test("says what the link is for, then joins on a click", async () => {
  const { host, joined } = await mount("ok");
  expect(host.textContent).toContain("join Acme as a member");
  await act(async () => host.querySelector("button")!.click());
  expect(joined).toEqual([acme]);
});

test("a spent or expired link says so, in the server's words", async () => {
  const { host } = await mount("spent");
  expect(host.textContent).toContain("this invite is not open");
  expect(host.querySelector("button")).toBeNull();
});
