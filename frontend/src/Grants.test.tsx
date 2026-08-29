import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, test, vi } from "vitest";

import { Grants } from "./Grants";

const added: unknown[] = [];
const removed: string[] = [];
const started: string[] = [];
let mine: unknown[] = [];
let driveAvailable = false;

vi.mock("./api", () => ({
  listConnectors: vi.fn(async () => [
    {
      kind: "website",
      title: "Websites",
      blurb: "",
      auth: "none",
      available: true,
      fields: [],
      grant_fields: [],
    },
    {
      kind: "notion",
      title: "Notion",
      blurb: "",
      auth: "token",
      available: true,
      fields: [],
      grant_fields: [
        { name: "token", label: "Integration token", secret: true, help: "ntn_…", required: true },
      ],
    },
    {
      kind: "drive",
      title: "Google Drive",
      blurb: "",
      auth: "oauth2",
      available: driveAvailable,
      fields: [],
      grant_fields: [],
    },
  ]),
  startOAuth: vi.fn(async (kind: string) => {
    started.push(kind);
    return { url: "https://accounts.google.com/consent" };
  }),
  listGrants: vi.fn(async () => mine),
  addGrant: vi.fn(async (kind: string, secrets: unknown) => {
    added.push({ kind, secrets });
    return {};
  }),
  removeGrant: vi.fn(async (id: string) => {
    removed.push(id);
    return {};
  }),
}));

vi.mock("./dialog", () => ({ confirm: vi.fn(async () => true) }));

async function mount() {
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  await act(async () => root.render(<Grants />));
  return host;
}

const button = (host: HTMLElement, text: string) =>
  [...host.querySelectorAll("button")].find((b) => b.textContent?.trim() === text)!;
const type = async (el: HTMLInputElement, value: string) => {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(el, value);
  await act(async () => el.dispatchEvent(new Event("input", { bubbles: true })));
};

test("every connector is listed with what it needs, and a token is added as a sign-in", async () => {
  mine = [];
  const host = await mount();
  expect(host.textContent).toContain("Websites");
  expect(host.textContent).toContain("no sign-in needed");
  expect(host.textContent).toContain("not configured on this server");
  expect(button(host, "sign in with Google Drive").disabled).toBe(true);
  await act(async () => button(host, "add a sign-in").click());
  await type(host.querySelector('[aria-label="Integration token"]')!, "ntn_secret");
  await act(async () => button(host, "Sign in").click());
  expect(added).toEqual([{ kind: "notion", secrets: { token: "ntn_secret" } }]);
});

test("a sign-in shows how many connections use it, and can be removed", async () => {
  mine = [
    {
      id: "g1",
      kind: "notion",
      label: "Ada's workspace",
      created_at: new Date().toISOString(),
      error: "",
      uses: 2,
    },
  ];
  const host = await mount();
  expect(host.textContent).toContain("Ada's workspace");
  expect(host.textContent).toContain("2 connections");
  await act(async () => button(host, "remove").click());
  expect(removed).toEqual(["g1"]);
});

test("a provider sign-in goes to the consent page the server names", async () => {
  mine = [];
  driveAvailable = true;
  const gone: string[] = [];
  Object.defineProperty(window, "location", {
    value: { ...window.location, assign: (url: string) => gone.push(url) },
    writable: true,
  });
  const host = await mount();
  expect(button(host, "sign in with Google Drive").disabled).toBe(false);
  await act(async () => button(host, "sign in with Google Drive").click());
  expect(started).toEqual(["drive"]);
  expect(gone).toEqual(["https://accounts.google.com/consent"]);
});

test("what a sign-in left in the address is said on the card", async () => {
  mine = [];
  const host = document.createElement("div");
  document.body.append(host);
  await act(async () =>
    createRoot(host).render(<Grants notice="Signed in — the connection can use it now." />),
  );
  expect(host.textContent).toContain("Signed in");
});
