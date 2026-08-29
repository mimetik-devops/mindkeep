import DOMPurify from "dompurify";
import { load } from "js-yaml";
import { marked } from "marked";
import markedFootnote from "marked-footnote";

// The agent cites with footnotes - `[^src]` in a claim, `[^src]: ...` at the foot - which
// marked alone leaves as literal text.
marked.use(markedFootnote());

export type Source = { id?: string; title?: string; resource?: string };
export type Actor = { by?: string; at?: string };

export type Frontmatter = {
  type?: string;
  title?: string;
  description?: string;
  status?: string;
  tags?: string[];
  sources?: Source[];
  generated?: Actor;
  verified?: Actor | Actor[];
};

const FENCE = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/;

/** Split an OKF page into its frontmatter and its body. Neither half is required. */
export function parse(raw: string): { meta: Frontmatter; body: string } {
  const match = raw.match(FENCE);
  if (!match) return { meta: {}, body: raw };
  try {
    return { meta: (load(match[1]) as Frontmatter) ?? {}, body: raw.slice(match[0].length) };
  } catch {
    return { meta: {}, body: raw }; // a page with unreadable frontmatter still reads
  }
}

// An ingest sometimes closes a page with the `</content>` of the document it was handed —
// invisible once rendered (sanitised away), but an HTML block inside the last footnote
// definition, which the editor's parser drops along with the definition.
const STRAY = /\n<\/content>\s*$/;

/** A page as the editor takes it: the frontmatter kept aside verbatim — the editor never
 * sees it, so it comes back byte for byte — and the body, less the stray tag. */
export function forEditing(raw: string): { head: string; body: string } {
  const head = raw.match(FENCE)?.[0] ?? "";
  return { head, body: raw.slice(head.length).replace(STRAY, "\n") };
}

/** Markdown from the wiki is agent-written and may quote a raw source, so sanitise it. */
export function render(markdown: string): string {
  return DOMPurify.sanitize(marked.parse(markdown, { async: false }) as string);
}

export function verifiedBy(meta: Frontmatter): Actor | undefined {
  const v = meta.verified;
  return Array.isArray(v) ? v[v.length - 1] : v;
}
