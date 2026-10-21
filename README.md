# Tuliprox Xtream Edge

A real **Xtream Codes API** served from Cloudflare Pages, built by GitHub Actions from an IPTV
provider's own Xtream API — no server, no VPS, no cold starts.

**Live:** https://tuliprox-edge-gateway.pages.dev

| | |
|---|---|
| Build pipeline | [Actions](https://github.com/travispflanz/tuliprox-cf-edge/actions) |
| Health alerts | [Issues labelled `edge-alert`](https://github.com/travispflanz/tuliprox-cf-edge/issues?q=label%3Aedge-alert) |
| **Operations runbook** | **[`docs/OPERATIONS.md`](docs/OPERATIONS.md)** |
| Architecture notes | [`ideas.md`](ideas.md) · [`kanban.md`](kanban.md) |

## Everything runs in the cloud

| Function | Runs on | Cadence |
|---|---|---|
| Fetch provider → filter → build indexes | GitHub Actions | every 6 hours |
| Serve Xtream / M3U / EPG | Cloudflare Pages | always on |
| Health monitoring + alerting | GitHub Actions (`Edge Monitor`) | every 6 hours |
| **Provider credential renewal** | GitHub Actions (inside the build) | only when the trial lapses |

No part of this project depends on a local machine. It is built, served and supervised entirely by
GitHub and Cloudflare, and it is operated from a browser — see the runbook.

The provider account renews itself on a **24-hour cadence**: a fresh trial is acquired straight from
the provider's API — **no email, no OTP, no browser, no manual rotation** — 6–12 hours before the
~36-hour trial it replaces dies (typically ~9h; builds run every 6h, so the cadence is evaluated in
6-hour steps), so the playlist never has a window with a dead account. That is ~1
signup/day rather than the ~4/day a signup-per-build would produce; the low rate is the point, since
request churn is what would risk the provider treating this as automated abuse. The credential and
its acquisition time are persisted across builds in the workflow's Actions cache.

## How it works

Tuliprox v3.3.0 runs one-shot in CI, fetching and filtering the provider's playlist. Its `xtream`
output is a binary B+Tree that only its own server can read, so `build/make_xtream.mjs` indexes the
M3U it emits alongside it into small static JSON. Cloudflare Pages Functions then answer the Xtream
surface (`/player_api.php`, `/panel_api.php`, `/get.php`, `/xmltv.php`, `/live|movie|series/:u/:p/:id.ts`)
straight from those prebuilt assets with ~0 CPU per request.

Stream URLs are rebuilt at request time from the channel id, so **provider credentials are never
written into any published file**.

## Repository layout

```
Makefile                make test / make test-live
config/                 Tuliprox inputs + filter templates
build/                  make_xtream.mjs (index builder), acquire_trial.py (credential renewal),
                        monitor_edge.py, run_tests.py (the suite), verify_edge.py,
                        verify_renewal_policy.py, verify_credential_persistence.py
functions/              Cloudflare Pages Functions implementing the Xtream API
.github/workflows/      tuliprox-edge.yml (build + deploy), edge-monitor.yml (cloud watchdog)
docs/OPERATIONS.md      the browser-only operations runbook
```

## Testing

```bash
make test        # offline: no network, no credentials, no provider contact. This gates every build.
make test-live   # the above, plus 27 assertions against the deployed production edge
```

`build/run_tests.py` is the single entrypoint and CI runs exactly it, so a local run and a build can
never check different things. It reports `pass / fail / skip` and only a **fail** breaks the suite —
a skip (a missing optional tool) is printed loudly so it can never pass itself off as success.

What the offline group pins:

| Check | Why it exists |
|---|---|
| `verify_renewal_policy.py` | the 24h-cadence / 12h-floor decision matrix, and that no case can reach the signup path |
| `verify_credential_persistence.py` | runs the workflow's **own** `run:` blocks, proving a credential's age survives between builds |
| `py_compile`, `node --check` (as ESM) | syntax, including the Pages Functions |
| workflows parse | a broken workflow can't be caught by CI, since CI is the thing that breaks |
| secret hygiene | `.cred/`, `.dev.vars`, `.env` are untracked and unstageable |

The live group runs `build/verify_edge.py`, the same 27 assertions used as the deploy's own smoke
test and by the cloud monitor. Point it anywhere:

```bash
BASE=https://tuliprox-edge-gateway.pages.dev CLIENT_USER=... CLIENT_PASS=... python3 build/verify_edge.py
```

> If your default `python3` lacks PyYAML, set `YAML_PYTHON=/path/to/python` rather than installing
> anything — the workflow-parse check will use it.