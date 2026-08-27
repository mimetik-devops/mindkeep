---
type: Playbook
title: Reading this knowledge base
description: What this folder is, how it is laid out, and what to do here and not do.
---

# Mindstash

This folder is a **bundle** from Mindstash: a wiki that an agent in the cloud maintains
from the documents its owner collects. It follows the Open Knowledge Format (OKF) v0.2 —
markdown with YAML frontmatter, readable without tooling — and you are reading a synced
copy of it.

**This copy is a mirror. Do not edit it in place.** The agent regenerates `wiki/` from the
sources, so a page edited here is overwritten the next time its source is read — and the
sync will remove anything the server does not have. Changes go through Mindstash:

- **To add material**, drop a file into `raw/`. The watcher uploads it and the agent folds
  it into the wiki.
- **To answer an open question** in `todo.md`, tick it off and write the answer there; the
  file syncs back up. Better still, correct or add the source it turns on, in `raw/`.
- **To fix a wrong page**, fix the source it cites, not the page. The page follows.

## Layout

- `raw/` — the owner's documents, under their own names. The only half a person writes.
- `wiki/` — everything the agent wrote: entity, concept, project and summary pages. Every
  page starts with frontmatter (`type`, `title`, `description`, `tags`, `sources`, and
  `verified` when a person has checked it), and cites its sources in footnotes.
- `index.md` — the catalog: one line per page. **Read this first**; it says what exists.
- `log.md` — what changed and why, newest last.
- `todo.md` — questions the agent could not settle. A person answers them.

## Answering a question from here

Read `index.md`, then open only the pages it points you to, and follow their links for
the neighbours. Prefer `stable` pages to `draft` ones, and a claim with a `verified` stamp
over one without. Quote the page's own citations when it matters where a claim came from.
If an answer takes real synthesis, do not write it into `wiki/` here — put it in a new
file under `raw/` so it is ingested and becomes a page the proper way.
