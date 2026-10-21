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

The provider account renews itself: the build reuses the credential in force while it is healthy and,
once it's dead or nearly expired, acquires a fresh trial directly from the provider's API — **no
email, no OTP, no browser, no manual rotation**. It reuses rather than re-signing-up on every run on
purpose: ~4 trials/day of signup churn would risk the provider treating it as automated abuse.

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
config/                 Tuliprox inputs + filter templates
build/                  make_xtream.mjs (index builder), acquire_trial.py (credential renewal),
                        monitor_edge.py, verify_edge.py
functions/              Cloudflare Pages Functions implementing the Xtream API
.github/workflows/      tuliprox-edge.yml (build + deploy), edge-monitor.yml (cloud watchdog)
docs/OPERATIONS.md      the browser-only operations runbook
```

## Verifying

`build/verify_edge.py` performs 27 end-to-end assertions against any base URL and is used both as
the deploy's own smoke test and by the cloud monitor:

```bash
BASE=https://tuliprox-edge-gateway.pages.dev CLIENT_USER=... CLIENT_PASS=... python3 build/verify_edge.py
```