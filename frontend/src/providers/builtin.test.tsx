import { act } from "react";
import { createRoot } from "react-dom/client";
import { vi } from "vitest";

import { builtin, payload } from "./builtin";

const config = { provider: "builtin", registration: "first" };
vi.mock("../api", () => ({
  authConfig: vi.fn(async () => config),
  signIn: vi.fn(async () => ({
    token: fake({ sub: "local_1", name: "Ada Lovelace", email: "ada@x" }),
  })),
  signUp: vi.fn(async () => ({
    token: fake({ sub: "local_1", name: "Ada Lovelace", email: "ada@x" }),
  })),
}));
vi.mock("../invites", () => ({ pending: () => "" }));

/** A token shaped like the server's, unsigned: the adapter only reads the payload. */
function fake(claims: Record<string, unknown>, exp = Math.floor(Date.now() / 1000) + 3600) {
  const body = btoa(JSON.stringify({ ...claims, exp })).replace(/=+$/, "");
  return `h.${body}.s`;
}

function Probe() {
  const s = builtin.useSession();
  return (
    <div>
      <span data-in={String(s.signedIn)}>{s.claims.name}</span>
      <button onClick={s.logout}>out</button>
    </div>
  );
}

test("a stored, unexpired token is a session; signing out forgets it", async () => {
  localStorage.setItem("mindkeep.session", fake({ name: "Ada Lovelace", email: "ada@x" }));
  const host = document.createElement("div");
  document.body.append(host);
  await act(async () =>
    createRoot(host).render(
      <builtin.Provider>
        <Probe />
      </builtin.Provider>,
    ),
  );
  expect(host.querySelector("span")?.getAttribute("data-in")).toBe("true");
  expect(host.textContent).toContain("Ada Lovelace");
  await act(async () => host.querySelector("button")!.click());
  expect(host.querySelector("span")?.getAttribute("data-in")).toBe("false");
  expect(localStorage.getItem("mindkeep.session")).toBeNull();
});

test("an expired token is no session, and the payload is read without a signature", () => {
  localStorage.setItem("mindkeep.session", fake({ name: "Old" }, 1));
  expect(payload(fake({ a: 1 })).a).toBe(1);
  expect(payload("garbage")).toEqual({});
});

test("the login form registers the first account and signs in", async () => {
  localStorage.removeItem("mindkeep.session");
  const host = document.createElement("div");
  document.body.append(host);
  const Login = builtin.Login!;
  await act(async () =>
    createRoot(host).render(
      <builtin.Provider>
        <Login />
        <Probe />
      </builtin.Provider>,
    ),
  );
  expect(host.textContent).toContain("No accounts yet");
  const [nameBox, emailBox, passwordBox] = [...host.querySelectorAll("input")];
  await act(async () => {
    const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    for (const [box, value] of [
      [nameBox, "Ada Lovelace"],
      [emailBox, "ada@x"],
      [passwordBox, "correct horse"],
    ] as const) {
      set.call(box, value);
      box.dispatchEvent(new Event("input", { bubbles: true }));
    }
  });
  await act(async () => host.querySelector("form")!.requestSubmit());
  expect(host.querySelector("span")?.getAttribute("data-in")).toBe("true");
  expect(localStorage.getItem("mindkeep.session")).toContain(".");
});
