import { type FormEvent, useEffect, useRef, useSyncExternalStore } from "react";

/**
 * The app's own confirm and prompt, in place of the browser's.
 *
 * `confirm()` and `prompt()` return a promise, so a call site reads as it did with the
 * native ones: `if (!(await confirm("Delete it?"))) return;`. One `<Dialogs />` near the
 * root draws whichever question is open. A second question while one is open cancels
 * the first — there is no queue, because nothing in the app asks two at once.
 */
type Question =
  | { kind: "confirm"; message: string; ok: string; danger: boolean }
  | { kind: "prompt"; message: string; ok: string; initial: string };
type Open = Question & { settle: (answer: boolean | string | null) => void };

let open: Open | null = null;
const listeners = new Set<() => void>();

function show(q: Open | null) {
  open?.settle(q?.kind === "prompt" ? null : false);
  open = q;
  listeners.forEach((l) => l());
}

export function confirm(
  message: string,
  { ok = "OK", danger = false }: { ok?: string; danger?: boolean } = {},
): Promise<boolean> {
  return new Promise((resolve) => {
    show({ kind: "confirm", message, ok, danger, settle: (a) => resolve(a === true) });
  });
}

export function prompt(
  message: string,
  { ok = "OK", initial = "" }: { ok?: string; initial?: string } = {},
): Promise<string | null> {
  return new Promise((resolve) => {
    show({
      kind: "prompt",
      message,
      ok,
      initial,
      settle: (a) => resolve(typeof a === "string" ? a : null),
    });
  });
}

function subscribe(l: () => void) {
  listeners.add(l);
  return () => listeners.delete(l);
}

export function Dialogs() {
  const q = useSyncExternalStore(subscribe, () => open);
  const focus = useRef<HTMLInputElement & HTMLButtonElement>(null);

  useEffect(() => {
    if (!q) return;
    focus.current?.focus();
    focus.current?.select?.();
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") show(null);
    };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, [q]);

  if (!q) return null;

  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const answer = q!.kind === "prompt" ? new FormData(e.currentTarget).get("answer") : true;
    const settle = q!.settle;
    open = null;
    listeners.forEach((l) => l());
    settle(typeof answer === "string" ? answer : answer === true);
  }

  return (
    <div className="veil" onMouseDown={(e) => e.target === e.currentTarget && show(null)}>
      <form className="dialog" role="dialog" aria-modal="true" onSubmit={submit}>
        <p>{q.message}</p>
        {q.kind === "prompt" && (
          <input ref={focus} name="answer" defaultValue={q.initial} autoComplete="off" />
        )}
        <div className="actions">
          <button type="button" className="quiet" onClick={() => show(null)}>
            Cancel
          </button>
          <button
            type="submit"
            ref={q.kind === "confirm" ? focus : undefined}
            className={`primary${q.kind === "confirm" && q.danger ? " danger" : ""}`}
          >
            {q.ok}
          </button>
        </div>
      </form>
    </div>
  );
}
