import DOMPurify from "dompurify";
import { load } from "js-yaml";
import { marked } from "marked";

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

/** Markdown from the wiki is agent-written and may quote a raw source, so sanitise it. */
export function render(markdown: string): string {
  return DOMPurify.sanitize(marked.parse(markdown, { async: false }) as string);
}

export function verifiedBy(meta: Frontmatter): Actor | undefined {
  const v = meta.verified;
  return Array.isArray(v) ? v[v.length - 1] : v;
}
