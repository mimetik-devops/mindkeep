import { afterEach, describe, expect, it } from "vitest";

import { callback, forgetConnect, pendingConnect, rememberConnect } from "./handoff";

const visit = (search: string) => window.history.replaceState(null, "", "/" + search);

describe("a machine asking to be connected", () => {
  afterEach(() => {
    forgetConnect();
    visit("");
  });

  it("is read off the address, and only with a loopback port and a sane nonce", () => {
    visit("?connect=1&port=51234&nonce=abcdefgh1234&name=Laptop");
    expect(pendingConnect()).toEqual({ port: 51234, nonce: "abcdefgh1234", name: "Laptop" });
    visit("?connect=1&port=80&nonce=abcdefgh1234");
    expect(pendingConnect()).toBeNull(); // not a port a client would listen on
    visit("?connect=1&port=51234&nonce=<script>");
    expect(pendingConnect()).toBeNull();
    visit("?invite=xyz");
    expect(pendingConnect()).toBeNull();
  });

  it("survives the sign-in round trip, and is forgotten on purpose", () => {
    visit("?connect=1&port=51234&nonce=abcdefgh1234&name=Laptop");
    rememberConnect();
    visit(""); // the provider sent us back without the query
    expect(pendingConnect()?.port).toBe(51234);
    forgetConnect();
    expect(pendingConnect()).toBeNull();
  });

  it("hands the token to the listener over loopback with the nonce it started with", () => {
    expect(callback({ port: 51234, nonce: "n0nce-n0nce", name: "x" }, "id.digest")).toBe(
      "http://127.0.0.1:51234/?token=id.digest&nonce=n0nce-n0nce",
    );
  });
});
