import { useEffect, useRef, useState } from "react";

import { Chevron } from "./icons";

/**
 * A pill that opens a menu: the things to switch between, and a field at the bottom
 * to make another. Bundles and teams are both this — one list, one current, one
 * "new" — so they share it. The server owns the naming rule for either and says so
 * in its own words when a name is refused, which is shown as it came.
 */
export function Picker({
  items,
  current,
  title,
  placeholder,
  onPick,
  onCreate,
}: {
  items: { id: string; label: string }[];
  current: string;
  title: string;
  placeholder: string;
  onPick: (id: string) => void;
  /** Makes it on the server and resolves to its id; the picker then switches to it. */
  onCreate: (name: string) => Promise<string>;
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
      const id = await onCreate(name);
      setDraft("");
      setOpen(false);
      onPick(id);
    } catch (e) {
      // the server's sentence, minus the status code the client wraps it in
      setError(String(e instanceof Error ? e.message : e).replace(/^\d{3} /, ""));
    } finally {
      setBusy(false);
    }
  }

  const shown = items.find((i) => i.id === current)?.label ?? current;

  return (
    <div className="picker">
      <button
        ref={trigger}
        className="pill"
        aria-haspopup="menu"
        aria-expanded={open}
        title={title}
        onClick={() => setOpen(!open)}
      >
        {shown}
        <Chevron />
      </button>

      {open && (
        <div ref={menu} role="menu" className="pickermenu">
          {items.map((i) => (
            <button
              key={i.id}
              role="menuitemradio"
              aria-checked={i.id === current}
              className="menuitem"
              onClick={() => {
                setOpen(false);
                onPick(i.id);
              }}
            >
              {i.label}
            </button>
          ))}
          <form
            className="newitem"
            onSubmit={(e) => {
              e.preventDefault();
              create();
            }}
          >
            <input
              aria-label={placeholder}
              placeholder={placeholder}
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
