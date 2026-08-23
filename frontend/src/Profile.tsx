/**
 * Avatar trigger plus the account dropdown, following Futuros' AccountMenu.
 *
 * The profile comes from the backend rather than from the Kinde claims in the browser's
 * token: the token is minted at sign-in and goes stale, while `GET /me` is composed from
 * Kinde on every read. Same reason Futuros reads its own `useCurrentUser`.
 */
import { useEffect, useRef, useState } from "react";

import { me, type Profile as Person } from "./api";

/** First letter of the first word plus the first of the last: "Ada Lovelace" → "AL". */
function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  const letters = parts.length === 1 ? parts[0][0] : parts[0][0] + parts[parts.length - 1][0];
  return letters.toUpperCase();
}

/** Drop the empty strings, so they do not overwrite a fallback that has a real value. */
function nonEmpty(person: Person | null): Partial<Person> {
  if (!person) return {};
  return Object.fromEntries(Object.entries(person).filter(([, v]) => v));
}

/** Picture, then initials, then a question mark — each tier covers the one above failing. */
function Avatar({ person, size }: { person: Partial<Person>; size: number }) {
  const [broken, setBroken] = useState(false);
  const style = { width: size, height: size };

  if (person.picture && !broken) {
    return (
      <img
        className="face"
        style={style}
        src={person.picture}
        alt={person.name ?? ""}
        onError={() => setBroken(true)}
      />
    );
  }
  return (
    <span className="face" style={style}>
      {initials(person.name || person.email || "")}
    </span>
  );
}

export function Profile({
  signOut,
  claims,
  onSettings,
}: {
  signOut?: () => void;
  claims: Partial<Person>;
  onSettings: () => void;
}) {
  const [fetched, setFetched] = useState<Person | null>(null);
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);

  useEffect(() => {
    me()
      .then(setFetched)
      .catch(() => setFetched(null)); // a profile that will not load is not worth an error
  }, []);

  // Field by field, not object by object: Kinde answers /me with blanks when the
  // Management API read fails, so "the request succeeded" is not the same as "we know
  // who this is". Anything still empty falls back to the ID token's own claims.
  const person = { ...claims, ...nonEmpty(fetched) } as Partial<Person>;

  // Dismiss on outside click or Escape, listeners only while open. `pointerdown` rather
  // than `click` so the menu closes before the thing underneath reacts.
  useEffect(() => {
    if (!open) return;
    const outside = (e: PointerEvent) => {
      const target = e.target as Node;
      if (!trigger.current?.contains(target) && !menu.current?.contains(target)) setOpen(false);
    };
    const escape = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        trigger.current?.focus();
      }
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  return (
    <div className="account">
      <button
        ref={trigger}
        className="facebutton"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Account"
        onClick={() => setOpen(!open)}
      >
        <Avatar person={person} size={32} />
      </button>

      {open && (
        <div ref={menu} role="menu" className="accountmenu">
          <div className="who">
            <Avatar person={person} size={44} />
            <div className="whotext">
              <b>{person?.name || "Signed in"}</b>
              {person?.role && <span className="role">{person.role}</span>}
              <span className="mail">{person?.email || person?.id}</span>
            </div>
          </div>
          <button
            role="menuitem"
            className="menuitem"
            onClick={() => {
              setOpen(false);
              onSettings();
            }}
          >
            Settings
          </button>
          {signOut && (
            <button
              role="menuitem"
              className="menuitem danger"
              onClick={() => {
                setOpen(false);
                signOut();
              }}
            >
              Sign out
            </button>
          )}
        </div>
      )}
    </div>
  );
}
