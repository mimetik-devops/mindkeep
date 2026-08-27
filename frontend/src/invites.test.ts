import { forget, pending, remember } from "./invites";

/** A signed-out visitor leaves for the provider and comes back to the bare origin. */
test("an invite in the address survives the sign-in round trip", () => {
  window.history.replaceState({}, "", "/?invite=abc");
  remember();
  window.history.replaceState({}, "", "/"); // what the provider sends us back to
  expect(pending()).toBe("abc");

  forget();
  expect(pending()).toBe("");
});

test("the address wins over what was kept, and forget clears both", () => {
  window.history.replaceState({}, "", "/?invite=old");
  remember();
  window.history.replaceState({}, "", "/?invite=new");
  expect(pending()).toBe("new");

  forget();
  expect(window.location.search).toBe("");
  expect(pending()).toBe("");
});
