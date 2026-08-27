import { useEffect, useRef, useState } from "react";

import { createBundle } from "./api";
import { Chevron } from "./icons";

/**
 * The bundle picker: which knowledge base you are looking at, and a way to start
 * another. One menu, same shape as the account menu — the list to switch, a field at
 * the bottom to create. The server owns the naming rule (lowercase, digits, hyphens) and
 * says so in its own words when a name is refused, so nothing here restates it.
 */
export function Bundles({
  bundles,
  current,
  onPick,
  onCreate,
}: {
  bundles: string[];
  current: string;
  onPick: (name: string) => void;
  /** Called once the server has made it, so the list and the selection can follow. */
  onCreate: (name: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);

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

  async function create() {
    const name = draft.trim();
    if (!name || busy) return;
    setBusy(true);
    setError("");
    try {
      await createBundle(name);
      setDraft("");
      setOpen(false);
      onCreate(name);
    } catch (e) {
      // the server's sentence, minus the status code the client wraps it in
      setError(String(e instanceof Error ? e.message : e).replace(/^\d{3} /, ""));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="bundles">
      <button
        ref={trigger}
        className="pill"
        aria-haspopup="menu"
        aria-expanded={open}
        title="Bundles"
        onClick={() => setOpen(!open)}
      >
        {current}
        <Chevron />
      </button>

      {open && (
        <div ref={menu} role="menu" className="bundlemenu">
          {bundles.map((b) => (
            <button
              key={b}
              role="menuitemradio"
              aria-checked={b === current}
              className="menuitem"
              onClick={() => {
                setOpen(false);
                onPick(b);
              }}
            >
              {b}
            </button>
          ))}
          <form
            className="newbundle"
            onSubmit={(e) => {
              e.preventDefault();
              create();
            }}
          >
            <input
              aria-label="New bundle"
              placeholder="new-bundle"
              value={draft}
              disabled={busy}
              onChange={(e) => {
                setDraft(e.target.value);
                setError("");
              }}
            />
            <button type="submit" className="menuitem" disabled={busy || !draft.trim()}>
              Create
            </button>
          </form>
          {error && <span className="error">{error}</span>}
        </div>
      )}
    </div>
  );
}
