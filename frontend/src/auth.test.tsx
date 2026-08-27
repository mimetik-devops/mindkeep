import { act } from "react";
import { createRoot } from "react-dom/client";
import { vi } from "vitest";

import { Gate } from "./auth";
import type { Adapter, Session } from "./providers/session";

vi.mock("./api", () => ({
  setTokenSource: vi.fn(),
  setTeam: vi.fn(),
  listTeams: vi.fn(async () => [{ id: "me", name: "Ada", personal: true, role: "owner", permissions: [] }]),
  createTeam: vi.fn(),
  listBundles: vi.fn(async () => ["default"]),
  me: vi.fn(async () => ({ id: "u", name: "", role: "", email: "", picture: "" })),
  tree: vi.fn(async () => ({})),
  sources: vi.fn(async () => []),
  folders: vi.fn(async () => []),
  lintState: vi.fn(async () => null),
  readFile: vi.fn(async () => ""),
  readAsText: vi.fn(async () => ""),
}));

/** An adapter that is just a state machine: what every real one boils down to. */
function fake(state: Partial<Session>): Adapter & { calls: string[] } {
  const calls: string[] = [];
  return {
    calls,
    Provider: ({ children }) => children,
    useSession: () => ({
      loading: false,
      signedIn: false,
      claims: {},
      login: () => calls.push("login"),
      logout: () => calls.push("logout"),
      token: async () => "t",
      ...state,
    }),
  };
}

async function mount(adapter: Adapter | string) {
  const host = document.createElement("div");
  document.body.append(host);
  await act(async () => createRoot(host).render(<Gate adapter={adapter} />));
  return host;
}

test("signed out: the sign-in screen, and the button asks the provider", async () => {
  const adapter = fake({ signedIn: false });
  const host = await mount(adapter);
  expect(host.textContent).toContain("Sign in");
  await act(async () => host.querySelector("button")!.click());
  expect(adapter.calls).toEqual(["login"]);
});

test("signed in: the app, with the provider's claims as the profile fallback", async () => {
  const host = await mount(fake({ signedIn: true, claims: { name: "Ada Lovelace" } }));
  expect(host.querySelector(".wordmark")?.textContent).toContain("Mindstash");
  expect(host.textContent).not.toContain("Sign in");
});

test("still loading: nothing yet, rather than a sign-in button that will vanish", async () => {
  const host = await mount(fake({ loading: true }));
  expect(host.textContent).toBe("");
});

test("a provider that is not configured says so instead of a blank page", async () => {
  expect((await mount("")).textContent).toContain("VITE_AUTH_PROVIDER is not set");
  expect((await mount("okta")).textContent).toContain('"okta"');
});
