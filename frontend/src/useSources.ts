import { useEffect, useState } from "react";

import { type Pass, type PassKind, passState, type Source, sources } from "./api";

export const elapsed = (s: number) => (s < 90 ? `${s}s` : `${Math.round(s / 60)}m`);

/** A finished duration, precise enough to compare two runs: 45s, 3m 10s. */
export const took = (s: number) => (s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`);

/**
 * A scheduled UTC instant, in the reader's own timezone: "tonight at 05:00", "Mon 05:00".
 *
 * The server schedules in UTC and says so in ISO-8601; the conversion belongs here,
 * where the browser already knows where it is.
 */
export function when(iso: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  const clock = at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  // compare calendar days, not 24-hour spans: 23:00 tonight and 05:00 tomorrow are six
  // hours apart but read very differently
  const midnight = new Date(at).setHours(0, 0, 0, 0);
  const days = Math.round((midnight - new Date().setHours(0, 0, 0, 0)) / 86400000);
  if (days <= 0) return `today at ${clock}`;
  if (days === 1) return `tomorrow at ${clock}`;
  return `${at.toLocaleDateString([], { weekday: "short" })} at ${clock}`;
}

const IDLE = 8000;
const BUSY = 2000;

/**
 * Poll a bundle's sources, quickly while the agent is working and slowly when it is not.
 *
 * `version` increments every time an ingest finishes, so a caller can reload the things
 * an ingest changes — the file tree, the log — instead of guessing when to refresh.
 */
export function useSources(bundle: string) {
  const [list, setList] = useState<Source[]>([]);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let timer: number;
    let stopped = false;
    let running: string[] = [];

    const tick = async () => {
      try {
        const next = await sources(bundle);
        if (stopped) return;
        setList(next);

        // Bump when a *particular* source stops running, not when the bundle falls idle.
        // A queue draining forty uploads always has something in flight, so waiting for
        // "nothing is busy any more" meant the log and the tree never reloaded at all.
        const done = running.some((path) => !next.find((s) => s.path === path)?.ingesting);
        if (done) setVersion((v) => v + 1);
        running = next.filter((s) => s.ingesting).map((s) => s.path);

        timer = window.setTimeout(tick, running.length ? BUSY : IDLE);
      } catch {
        if (!stopped) timer = window.setTimeout(tick, IDLE);
      }
    };

    tick();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [bundle]);

  return { sources: list, version, busy: list.some((s) => s.ingesting) };
}

/**
 * One overnight pass's state, polled on the same fast/slow rule as sources.
 *
 * A pass is not attached to any source, so useSources cannot see one running.
 */
export function usePass(bundle: string, kind: PassKind) {
  const [state, setState] = useState<Pass | null>(null);
  const [nudge, setNudge] = useState(0);

  useEffect(() => {
    let timer = 0;
    let gone = false;
    const tick = async () => {
      try {
        const next = await passState(bundle, kind);
        if (gone) return;
        setState(next);
        // nothing runs a pass but the nightly job and its button, so idle can be slow
        timer = window.setTimeout(tick, next.running ? BUSY : IDLE * 4);
      } catch {
        if (!gone) timer = window.setTimeout(tick, IDLE * 4);
      }
    };
    tick();
    return () => {
      gone = true;
      window.clearTimeout(timer);
    };
  }, [bundle, kind, nudge]);

  return { state, refresh: () => setNudge((n) => n + 1) };
}
