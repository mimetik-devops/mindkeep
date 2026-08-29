import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, test, vi } from "vitest";

import { Connections } from "./Connections";

const added: unknown[] = [];
const updated: unknown[] = [];
const synced: string[] = [];
let rows: unknown[] = [];
let grants: unknown[] = [];
let driveAvailable = false;

const field = (name: string, label: string, extra = {}) => ({
  name,
  label,
  secret: false,
  help: "",
  required: true,
  multiline: false,
  options: [],
  rows: [],
  browse: false,
  ...extra,
});

vi.mock("./api", () => ({
  REDACTED: "••••••••",
  can: (team: { permissions: string[] }, p: string) => team.permissions.includes(p),
  listConnectors: vi.fn(async () => [
    {
      kind: "website",
      title: "Websites",
      blurb: "Pages at public addresses.",
      auth: "none",
      available: true,
      folder: "raw/connectors/website",
      tick: 15,
      fields: [
        field("sites", "Sites", {
          rows: [
            field("url", "Address", { help: "https://…" }),
            field("pages", "Pages at most", { required: false }),
            field("every", "Check for changes", {
              help: "1440",
              options: [
                ["15", "every 15 minutes"],
                ["1440", "every day"],
              ],
            }),
          ],
        }),
      ],
      grant_fields: [],
    },
    {
      kind: "notion",
      title: "Notion",
      blurb: "A workspace.",
      auth: "token",
      available: true,
      folder: "raw/connectors/notion",
      tick: 0,
      fields: [field("space", "Space")],
      grant_fields: [field("token", "Token", { secret: true })],
    },
    {
      kind: "drive",
      title: "Google Drive",
      blurb: "",
      auth: "oauth2",
      available: driveAvailable,
      folder: "raw/connectors/drive",
      tick: 15,
      fields: [
        field("folders", "Folders", {
          rows: [
            field("path", "Folder", { browse: true }),
            field("every", "Check for changes", {
              help: "60",
              options: [["60", "every hour"]],
            }),
          ],
        }),
      ],
      grant_fields: [],
    },
  ]),
  listConnections: vi.fn(async () => rows),
  listGrants: vi.fn(async () => grants),
  browseConnector: vi.fn(async (_b: string, _k: string, body: { at: string }) => ({
    at: body.at,
    choices:
      body.at === ""
        ? [
            { value: "Clients", label: "Clients", opens: true },
            { value: "Shared drives", label: "Shared drives", opens: true },
          ]
        : body.at === "Clients"
          ? [{ value: "Clients/Acme", label: "Acme", opens: true }]
          : [],
  })),
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
  [...host.querySelectorAll("button")].find((b) => b.textContent?.trim().startsWith(text))!;
const menuitem = (host: HTMLElement, title: string) =>
  [...host.querySelectorAll<HTMLButtonElement>(".menuitem")].find((b) =>
    b.textContent?.startsWith(title),
  )!;
const control = (host: HTMLElement, label: string) =>
  host.querySelector(`[aria-label="${label}"]`) as
    HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement;
const type = async (el: HTMLElement, value: string) => {
  const proto =
    el instanceof HTMLSelectElement
      ? HTMLSelectElement.prototype
      : el instanceof HTMLTextAreaElement
        ? HTMLTextAreaElement.prototype
        : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(el, value);
  await act(async () =>
    el.dispatchEvent(
      new Event(el instanceof HTMLSelectElement ? "change" : "input", { bubbles: true }),
    ),
  );
};

test("the picker offers what can be set up, and sites are rows with their own settings", async () => {
  rows = [];
  grants = [];
  const host = await mount();
  expect(host.textContent).toContain("No connections yet");
  await act(async () => button(host, "Add a connection").click());
  // every connector is listed; the ones that cannot be picked say why
  expect(menuitem(host, "Websites").disabled).toBe(false);
  expect(menuitem(host, "Notion").disabled).toBe(true);
  expect(menuitem(host, "Notion").textContent).toContain("sign in first");
  expect(menuitem(host, "Google Drive").disabled).toBe(true);
  expect(menuitem(host, "Google Drive").textContent).toContain("cannot do yet");
  await act(async () => menuitem(host, "Websites").click());
  expect(control(host, "Connection name")).toBeNull(); // the connector names it
  expect(control(host, "Sync every")).toBeNull(); // the sites keep their own clocks
  await type(control(host, "Address 1"), "https://mindkeep.io/");
  await type(control(host, "Pages at most 1"), "1");
  await act(async () => button(host, "add another").click());
  await type(control(host, "Address 2"), "https://mimetik.ai/");
  await type(control(host, "Check for changes 2"), "15");
  await act(async () => button(host, "Connect").click());
  expect(added).toEqual([
    {
      kind: "website",
      config: {
        sites: JSON.stringify([
          { url: "https://mindkeep.io/", pages: "1", every: "1440" },
          { url: "https://mimetik.ai/", pages: "", every: "15" },
        ]),
      },
      every: 60,
      grant: undefined,
    },
  ]);
});

test("a connector already connected is not offered again", async () => {
  rows = [
    {
      id: "w",
      kind: "website",
      name: "mindkeep.io",
      folder: "raw/connectors/website",
      config: {
        sites: JSON.stringify([{ url: "https://mindkeep.io/", pages: "", every: "1440" }]),
      },
      every: 15,
      enabled: true,
      syncing: false,
      synced_at: "",
      error: "",
      summary: "",
      installed: true,
      grant: null,
      grant_gone: false,
    },
  ];
  grants = [];
  const host = await mount();
  await act(async () => button(host, "Add a connection").click());
  expect(menuitem(host, "Websites").disabled).toBe(true);
  expect(menuitem(host, "Websites").textContent).toContain("already connected");
  // editing shows the rows as they are
  await act(async () => button(host, "edit").click());
  expect((control(host, "Address 1") as HTMLInputElement).value).toBe("https://mindkeep.io/");
  expect((control(host, "Check for changes 1") as HTMLSelectElement).value).toBe("1440");
});

test("a connector that needs a sign-in uses one of yours", async () => {
  rows = [];
  grants = [
    { id: "g1", kind: "notion", label: "Ada's workspace", created_at: "", error: "", uses: 0 },
  ];
  added.length = 0;
  const host = await mount();
  await act(async () => button(host, "Add a connection").click());
  expect(menuitem(host, "Notion").disabled).toBe(false);
  await act(async () => menuitem(host, "Notion").click());
  expect((control(host, "Sign-in") as HTMLSelectElement).value).toBe("g1");
  await type(control(host, "Space"), "docs");
  await act(async () => button(host, "Connect").click());
  expect(added).toEqual([{ kind: "notion", config: { space: "docs" }, every: 60, grant: "g1" }]);
});

test("a connection is listed with its state and sign-in, synced now, and edited", async () => {
  rows = [
    {
      id: "c1",
      kind: "notion",
      name: "docs",
      folder: "raw/connectors/notion/docs",
      config: { space: "docs" },
      every: 60,
      enabled: true,
      syncing: false,
      synced_at: new Date().toISOString(),
      error: "",
      summary: "+1 ~0 -0",
      installed: true,
      grant: { id: "g1", label: "Ada's workspace" },
      grant_gone: false,
    },
  ];
  grants = [
    { id: "g1", kind: "notion", label: "Ada's workspace", created_at: "", error: "", uses: 1 },
  ];
  const host = await mount();
  expect(host.textContent).toContain("Notion as Ada's workspace");
  expect(host.textContent).toContain("+1 ~0 -0 · synced today");
  await act(async () => button(host, "sync now").click());
  expect(synced).toEqual(["c1"]);

  await act(async () => button(host, "edit").click());
  expect((control(host, "Space") as HTMLInputElement).value).toBe("docs");
  await act(async () => (control(host, "Paused") as HTMLInputElement).click());
  await act(async () => button(host, "Save").click());
  expect(updated).toEqual([
    { id: "c1", patch: { config: { space: "docs" }, every: 60, enabled: false, grant: "g1" } },
  ]);
});

test("a viewer sees the list, a revoked sign-in, and none of the buttons", async () => {
  rows = [
    {
      id: "c1",
      kind: "notion",
      name: "docs",
      folder: "raw/connectors/notion/docs",
      config: {},
      every: 60,
      enabled: true,
      syncing: false,
      synced_at: "",
      error: "",
      summary: "",
      installed: true,
      grant: null,
      grant_gone: true,
    },
  ];
  grants = [];
  const host = await mount({ ...owner, role: "viewer", permissions: ["read"] });
  expect(host.textContent).toContain("the sign-in this connection used is gone");
  expect(host.querySelectorAll("button")).toHaveLength(0);
});

test("a browsable cell opens a browser and a pick writes the path", async () => {
  rows = [];
  driveAvailable = true;
  grants = [
    { id: "g9", kind: "drive", label: "ada@example.com", created_at: "", error: "", uses: 0 },
  ];
  const host = await mount();
  await act(async () => button(host, "Add a connection").click());
  await act(async () => menuitem(host, "Google Drive").click());
  expect(control(host, "Sync every")).toBeNull(); // Drive ticks on its own
  await act(async () => button(host, "browse").click());
  expect(host.textContent).toContain("Shared drives");
  await act(async () => [...host.querySelectorAll<HTMLButtonElement>(".choice")][0].click());
  expect(host.textContent).toContain("/ Clients");
  await act(async () => [...host.querySelectorAll<HTMLButtonElement>(".choice")][0].click());
  expect(host.textContent).toContain("Nothing inside");
  await act(async () =>
    (host.querySelector('[aria-label="Use Clients/Acme"]') as HTMLButtonElement).click(),
  );
  expect((control(host, "Folder 1") as HTMLInputElement).value).toBe("Clients/Acme");
  expect(host.querySelector(".browser")).toBeNull();
});
