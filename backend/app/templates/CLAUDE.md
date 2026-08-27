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
If an answer takes real synthesis, do not write it into `wiki/` here — write it up as a
note, below, so it is ingested and becomes a page the proper way.

## Contributing what you worked out

When work here settles something the team should keep — why a thing fails and the fix,
a decision and its reason, a fact that took effort to establish — write it as a **note**:
one finding per file, under `raw/notes/<your name>/`, named by date and subject:

```
raw/notes/ruben/2026-08-27 — why the frontend container lost its packages.md
```

```yaml
---
type: Note
author: ruben          # the person, not the tool
via: claude-code       # or whatever wrote it
about: [docker, frontend]
supersedes: raw/notes/ruben/2026-08-20 — rebuild the frontend image.md   # optional
---
```

Then the finding, in a few paragraphs, with what it rests on. The watcher uploads the
note and the agent folds it into the wiki as a **draft** page — inferred by a tool, not
yet checked by a person — until someone verifies it.

Rules that keep notes true:

- **One finding per file.** A file that grows goes stale as a whole; a fact has its own
  life. Do not keep a running memory file here.
- **Do not edit history into a note.** When you learn better, write a new note that names
  the old one in `supersedes:`; the wiki retires the old claims. Delete a note only when it
  was simply wrong.
- **Team knowledge only.** What you learned about working with *this person or this
  machine* — preferences, paths, habits — is your own memory and stays in your own memory,
  not in the shared wiki.
