<!--
  Commit subjects are sentences, not prefixes — see CONTRIBUTING.md.
  One concern per pull request: if the description needs the word "also", it is two.
-->

## What this changes, and why

<!-- The problem, then the change. Link the issue if there is one. -->

## How it was verified

<!-- The checks you ran, and anything you exercised by hand. If the agent's behaviour
     changed, say which source you ingested and what it wrote. -->

## Checklist

- [ ] Commits are signed off (`git commit -s`) — see [the DCO](../DCO)
- [ ] The checks in [CONTRIBUTING.md](../CONTRIBUTING.md) pass (backend, frontend, client — whichever this touches)
- [ ] Behaviour changes to the agent are changes to `backend/app/templates/manual.md`
- [ ] Tests cover it — and new access paths have traversal and cross-tenant tests
- [ ] `docs/Mindkeep - Dev Log.md` has an entry if this settles a decision
- [ ] The onboarding document is updated if this alters what it describes
