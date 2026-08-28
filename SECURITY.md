# Security Policy

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Report it privately through GitHub: go to the
[Security tab](https://github.com/mimetik-devops/mindkeep/security/advisories/new) and
choose **Report a vulnerability**. That opens a private advisory only the maintainers can
see, where we can discuss a fix and, if it warrants one, issue a CVE.

Useful in a report: what an attacker can do, the steps to reproduce it, the version or
commit you tested, and whether you were signed in and as what kind of member. A proof of
concept helps; it does not have to be weaponised.

We aim to acknowledge a report within a few working days. Mindkeep is maintained by a small
team, so please allow reasonable time for a fix before disclosing publicly. We will credit
you in the advisory unless you would rather we did not.

## What is in scope

The code in this repository: the backend API, the ingest pipeline and its agent tooling,
the web app, and the desktop client. The kinds of finding that matter most here:

- **Crossing a tenancy boundary** — reading or writing another team's or bundle's files.
  The path prefix plus the `tenant` column *is* the isolation model, so anything that
  escapes it is the most serious class of bug in this project.
- **Path traversal** through any route that takes a file path, or through the agent's own
  file tools.
- **Authentication or session flaws** in either provider (`builtin` accounts or `oidc`),
  in the per-device tokens, or in invite links.
- **Permission checks** that can be bypassed — reaching a `write`, `history`, `bundles`,
  `members` or `team` action without the grant.
- **Prompt injection with real consequences**: content in an ingested source that makes the
  agent write outside `wiki/`, exfiltrate another tenant's material, or misuse a tool. A
  source that merely persuades the agent to write something wrong is a quality bug, not a
  vulnerability — the wiki is derived from untrusted documents by design.
- **Cross-site scripting** in rendered wiki markdown, which quotes untrusted sources.

## What is not in scope

- Findings that require an operator to have already misconfigured their own deployment
  (a published `DEVICE_SECRET`, a database open to the internet, `ALLOWED_ORIGINS=*`).
- The **unsigned desktop installers**. That they are not code-signed is a known, documented
  trade-off, not a vulnerability — see the note in `.github/workflows/app.yml`.
- Anything that depends on already having filesystem or database access on the host.
- Missing hardening headers or a rate limit, absent a concrete attack.
- Automated scanner output with no demonstrated impact.

## Supported versions

Mindkeep is pre-1.0 and moves quickly. Only the current `main` is supported: fixes land
there, and a self-hosted deployment should track it. If you are running an older commit,
the first step of any fix is to update.

## For self-hosters

You run the deployment, so a few things are yours to get right:

- Generate real values for `AUTH_SECRET` and `DEVICE_SECRET` — 32+ random characters each.
  Rotating `AUTH_SECRET` signs everyone out; rotating `DEVICE_SECRET` revokes every device.
- Never expose the backend directly. In the shipped setup Caddy serves the site and proxies
  `/api` over a private network, which is also why there is no CORS to configure.
- Your `ANTHROPIC_API_KEY` is a spending credential. Every ingested document is sent to the
  Anthropic API; if that is not acceptable for a class of material, keep it out of `raw/`.
- Back up the wiki volume. It holds the knowledge; Postgres holds only run metadata.
