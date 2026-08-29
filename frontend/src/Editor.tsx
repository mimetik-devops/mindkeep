import "@milkdown/crepe/theme/common/style.css";
import "@milkdown/crepe/theme/frame.css";

import { Crepe } from "@milkdown/crepe";
import { remarkStringifyOptionsCtx } from "@milkdown/kit/core";
import { useEffect, useRef } from "react";

/**
 * A page's body in Milkdown's Crepe: what you see is the page, and what comes back out is
 * markdown — remark on both sides, so the agent's footnotes, tables and fences survive
 * the round trip. Checked against every page of the Mimetik wiki: nothing is lost, and
 * what changes is cosmetic (a `~` escaped, a table's columns padded). The one thing the
 * parser drops is an HTML block inside a footnote definition, which is why `forEditing`
 * strips the `</content>` an ingest sometimes leaves at the foot of a page.
 *
 * `initial` is read once; every change since comes back through `onChange` as markdown.
 */
export default function Editor({
  initial,
  onChange,
}: {
  initial: string;
  onChange: (markdown: string) => void;
}) {
  const root = useRef<HTMLDivElement>(null);
  const first = useRef(initial);
  const changed = useRef(onChange);
  changed.current = onChange;

  useEffect(() => {
    const crepe = new Crepe({
      root: root.current,
      defaultValue: first.current,
      // no images — a page has nowhere to put an upload; no maths, no top bar, no AI
      features: {
        [Crepe.Feature.ImageBlock]: false,
        [Crepe.Feature.Latex]: false,
        [Crepe.Feature.TopBar]: false,
        [Crepe.Feature.AI]: false,
      },
    });
    // the agent's bullets are dashes; remark's default is asterisks, and a page whose
    // every list changed on its first edit would read as rewritten
    crepe.editor.config((ctx) => ctx.set(remarkStringifyOptionsCtx, { bullet: "-" }));
    crepe.on((api) => api.markdownUpdated((_ctx, markdown) => changed.current(markdown)));
    // StrictMode mounts twice in development: the first instance is torn down while it
    // is still being made, so its destroy waits for its create
    const made = crepe.create();
    return () => {
      void made.then(() => crepe.destroy()).catch(() => undefined);
    };
  }, []);

  return <div className="editor" ref={root} />;
}
