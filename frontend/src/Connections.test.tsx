import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, test, vi } from "vitest";

import { Connections } from "./Connections";

const added: unknown[] = [];
const updated: unknown[] = [];
const synced: string[] = [];
let rows: unknown[] = [];

vi.mock("./api", () => ({
  REDACTED: "••••••••",
  can: (team: { permissions: string[] }, p: string) => team.permissions.includes(p),
  listConnectors: vi.fn(async () => [
    {
      kind: "website",
      title: "A website",
      blurb: "A page and the pages it links to.",
      auth: "none",
      available: true,
      fields: [
        { name: "url", label: "Address", secret: false, help: "https://…", required: true },
        { name: "pages", label: "Pages at most", secret: false, help: "", required: false },
      ],
    },
    {
      kind: "drive",
      title: "Google Drive",
      blurb: "",
      auth: "oauth2",
      available: false,
      fields: [],
    },
  ]),
  listConnections: vi.fn(async () => rows),
  addConnection: vi.fn(async (_b: string, body: unknown) => {
    added.push(body);
    return {};
  }),
  updateConnection: vi.fn(async (_b: string, id: string, patch: unknown) => {
    updated.push({ id, patch });
    return {};
  }),
  removeConnection: vi.fn(async () => ({})),
  syncConnection: vi.fn(async (_b: string, id: string) => {
    synced.push(id);
    return {};
  }),
}));

const owner = {
  id: "t",
  name: "Team",
  personal: false,
  role: "owner",
  permissions: ["read", "write", "bundles"],
};

async function mount(team = owner) {
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  await act(async () => root.render(<Connections bundle="default" team={team as any} />));
  return host;
}

const button = (host: HTMLElement, text: string) =>
  [...host.querySelectorAll("button")].find((b) => b.textContent?.trim() === text)!;
const field = (host: HTMLElement, label: string) =>
  host.querySelector(`[aria-label="${label}"]`) as HTMLInputElement | HTMLSelectElement;
const type = async (el: HTMLInputElement | HTMLSelectElement, value: string) => {
  const setter = Object.getOwnPropertyDescriptor(
    el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype,
    "value",
  )!.set!;
  setter.call(el, value);
  await act(async () =>
    el.dispatchEvent(
      new Event(el instanceof HTMLSelectElement ? "change" : "input", { bubbles: true }),
    ),
  );
};

test("a connection is set up from the connector's own fields", async () => {
  rows = [];
  const host = await mount();
  expect(host.textContent).toContain("No connections yet");
  await act(async () => button(host, "Add a connection").click());
  // the picker lists every connector, offering only the available ones
  const picker = field(host, "Connector") as HTMLSelectElement;
  expect([...picker.options].map((o) => o.disabled)).toEqual([false, true]);
  expect(picker.options[1].textContent).toContain("needs a sign-in");
  await type(field(host, "Connection name"), "mindkeep.io");
  await type(field(host, "Address"), "https://mindkeep.io/");
  await type(field(host, "Pages at most"), "1");
  await type(field(host, "Sync every"), "1440");
  await act(async () => button(host, "Connect").click());
  expect(added).toEqual([
    {
      kind: "website",
      name: "mindkeep.io",
      config: { url: "https://mindkeep.io/", pages: "1" },
      every: 1440,
    },
  ]);
});

test("a connection is listed with its state, synced now, and edited", async () => {
  rows = [
    {
      id: "c1",
      kind: "website",
      name: "mindkeep.io",
      folder: "raw/connectors/mindkeep.io",
      config: { url: "https://mindkeep.io/", pages: "1" },
      every: 60,
      enabled: true,
      syncing: false,
      synced_at: new Date().toISOString(),
      error: "",
      summary: "+1 ~0 -0",
      installed: true,
    },
  ];
  const host = await mount();
  expect(host.textContent).toContain("mindkeep.io");
  expect(host.textContent).toContain("+1 ~0 -0 · synced today");
  expect(host.textContent).toContain("every hour");
  await act(async () => button(host, "sync now").click());
  expect(synced).toEqual(["c1"]);

  await act(async () => button(host, "edit").click());
  expect((field(host, "Address") as HTMLInputElement).value).toBe("https://mindkeep.io/");
  await act(async () => (field(host, "Paused") as HTMLInputElement).click());
  await act(async () => button(host, "Save").click());
  expect(updated).toEqual([
    {
      id: "c1",
      patch: { config: { url: "https://mindkeep.io/", pages: "1" }, every: 60, enabled: false },
    },
  ]);
});

test("a viewer sees the list and none of the buttons", async () => {
  rows = [
    {
      id: "c1",
      kind: "website",
      name: "mindkeep.io",
      folder: "raw/connectors/mindkeep.io",
      config: {},
      every: 60,
      enabled: true,
      syncing: false,
      synced_at: "",
      error: "the site answered 503",
      summary: "",
      installed: true,
    },
  ];
  const host = await mount({ ...owner, role: "viewer", permissions: ["read"] });
  expect(host.textContent).toContain("the site answered 503");
  expect(host.querySelectorAll("button")).toHaveLength(0);
});
