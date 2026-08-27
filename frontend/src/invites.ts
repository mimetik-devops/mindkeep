/**
 * An invite link is `/?invite=<token>`. Someone who is not signed in yet goes off to the
 * provider and comes back to the bare origin, so the token has to survive that round
 * trip. sessionStorage lives exactly that long — this tab, this visit — and asks nothing
 * of the provider, which keeps the invite flow the app's own.
 */
const KEY = "mindkeep.invite";

/** Note the token in the address, if there is one. Runs before any sign-in redirect. */
export function remember(): void {
  const token = new URLSearchParams(window.location.search).get("invite");
  if (!token) return;
  try {
    sessionStorage.setItem(KEY, token);
  } catch {
    /* storage blocked: the address still carries it for a visit that needs no sign-in */
  }
}

/** The pending token — from the address, or from before the sign-in — or "". */
export function pending(): string {
  const fromUrl = new URLSearchParams(window.location.search).get("invite");
  if (fromUrl) return fromUrl;
  try {
    return sessionStorage.getItem(KEY) ?? "";
  } catch {
    return "";
  }
}

/** Done with it — joined, or could not — so nothing offers it again. */
export function forget(): void {
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    /* nothing kept */
  }
  if (new URLSearchParams(window.location.search).has("invite")) {
    window.history.replaceState({}, document.title, window.location.pathname);
  }
}
