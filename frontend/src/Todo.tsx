import { useEffect, useRef, useState } from "react";

import { ask, type Said, setTodo, type Todo as Item, todos } from "./api";
import { render } from "./okf";

/**
 * One open question at a time, with the assistant that answers it.
 *
 * No list. A queue of questions you can browse is a queue you never finish — the card is
 * the whole screen, the count says how much is left, and the only moves are answer it or
 * skip it. Answering ticks it off and the next one takes its place.
 *
 * The conversation lives here and nowhere else: the server is a plain turn-taking
 * endpoint. Moving to another question loses the thread, which is the honest cost of not
 * having a conversations table yet.
 */
export function Todo({ bundle }: { bundle: string }) {
  const [items, setItems] = useState<Item[]>([]);
  const [at, setAt] = useState(0);
  const [said, setSaid] = useState<Said[]>([]);
  const [draft, setDraft] = useState("");
  const [thinking, setThinking] = useState(false);
  const [changed, setChanged] = useState<string[]>([]);
  const [error, setError] = useState("");
  const foot = useRef<HTMLDivElement>(null);

  const refresh = () =>
    todos(bundle)
      .then(setItems)
      .catch((e) => setError(String(e)));

  useEffect(() => {
    refresh();
    setAt(0);
    setSaid([]);
  }, [bundle]);

  useEffect(() => {
    // braces, not an expression body: an effect's return value is its cleanup, and a
    // scrollIntoView patched by an extension or polyfill returns one — React then
    // crashed calling it when the question left the screen
    foot.current?.scrollIntoView({ behavior: "smooth" });
  }, [said, thinking]);

  const open = items.filter((i) => !i.done);
  // answering removes one, so the index can outrun the list; wrap rather than dead-end
  const here = open.length ? at % open.length : 0;
  const question = open[here];

  function move(by: number) {
    setSaid([]);
    setChanged([]);
    setError("");
    setAt((n) => (open.length ? (n + by + open.length) % open.length : 0));
  }

  async function send() {
    const text = draft.trim();
    if (!text || !question || thinking) return;
    const next: Said[] = [...said, { role: "user", content: text }];
    setSaid(next);
    setDraft("");
    setThinking(true);
    try {
      const { reply, changed: touched } = await ask(bundle, question.text, next);
      setSaid([...next, { role: "assistant", content: reply }]);
      if (touched.length) setChanged((was) => [...new Set([...was, ...touched])]);
      await refresh(); // it may have ticked this one off, or added another
    } catch (e) {
      setError(String(e));
      setSaid(next); // keep what was typed; the exchange is only in this tab
    } finally {
      setThinking(false);
    }
  }

  async function answered() {
    if (!question) return;
    try {
      await setTodo(bundle, question.id, true);
      setSaid([]);
      setChanged([]);
      await refresh();
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <div className="asking">
      <header className="asking-head">
        <h1>Questions</h1>
        <span className="left">
          {open.length ? `${open.length} left` : "all answered"}
          {open.length > 1 && <span className="soft"> · {here + 1} of {open.length}</span>}
        </span>
      </header>

      {error && <div className="banner">{error}</div>}

      {!question ? (
        <p className="empty">
          Nothing open. When the agent hits something it cannot settle from the sources —
          two documents disagreeing, a claim with nothing behind it — it writes the question
          here and carries on.
        </p>
      ) : (
        <>
          <article className="card question">
            <p className="asked">{question.text}</p>
            {question.detail && <p className="meanwhile">{question.detail}</p>}
            <div className="cardfoot">
              <button className="plain" onClick={answered}>
                Already answered
              </button>
              {open.length > 1 && (
                <button className="plain" onClick={() => move(1)}>
                  Skip for now
                </button>
              )}
            </div>
          </article>

          <div className="thread">
            {said.map((turn, i) => (
              <div key={i} className={`turn ${turn.role}`}>
                <div className="md" dangerouslySetInnerHTML={{ __html: render(turn.content) }} />
              </div>
            ))}
            {thinking && (
              <div className="turn assistant working">
                <span className="pulse" /> reading the sources
              </div>
            )}
            <div ref={foot} />
          </div>

          {changed.length > 0 && (
            <p className="soft">
              Rewritten and being re-ingested: {changed.join(", ")}. The pages catch up on
              their own.
            </p>
          )}

          <form
            className="say"
            onSubmit={(e) => {
              e.preventDefault();
              void send();
            }}
          >
            <textarea
              value={draft}
              placeholder={said.length ? "Reply" : "Answer it, or ask what the assistant needs"}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                // Enter sends, Shift+Enter is a newline — a chat, not a document
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void send();
                }
              }}
            />
            <button className="primary" disabled={thinking || !draft.trim()}>
              Send
            </button>
          </form>
        </>
      )}
    </div>
  );
}
