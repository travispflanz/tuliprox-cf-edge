# Project Tuliprox-Cloudflare-Edge — Ideas & Architecture

## Objective
Serve a **real Xtream Codes API** ($0/mo, no server, no cold starts) from Cloudflare Pages,
built by GitHub Actions running the official Tuliprox v3.3.0 CLI binary in one-shot mode.

## Architecture (what actually shipped)

```
GitHub Actions (cron 6h | push | dispatch | credentials-rotated)
  └─ build/acquire_trial.py     ensure a provider credential: REUSE while healthy, else acquire a
                                fresh trial from the provider's API (no email/OTP/browser/rotation)
  └─ tuliprox v3.3.0 one-shot   xtream in (provider) -> m3u + cat_live.json
  └─ build/probe_upstream.mjs   record the account's real health (auth/status/exp_date)
  └─ curl xmltv.php             provider's own guide
  └─ build/make_xtream.mjs      m3u + cat_live.json -> static Xtream JSON, edge-only URLs,
                                EPG ids lower-cased to match the playlist
  └─ leak gate                   fail if the provider host appears anywhere in dist/
  └─ wrangler pages deploy dist + ./functions

GitHub Actions — Edge Monitor (cron 6h, offset +30m)  [all-cloud supervision]
  └─ build/monitor_edge.py      reuses verify_edge.py (27 assertions), plus provider-account
                                expiry and deploy-freshness checks -> opens/updates/auto-closes a
                                labelled issue, and fails the run on real breakage so GitHub's own
                                notifications fire. No local machine involved.

Cloudflare Pages Functions
  /player_api.php   auth -> pipe prebuilt JSON (categories, streams, per-category splits)
  /panel_api.php    legacy panel variant
  /get.php          auth -> playlist.m3u (edge URLs only)
  /xmltv.php        auth -> epg.xml (plain XML; Cloudflare compresses it)
  /live|movie|series/:u/:p/:id.ts   auth -> proxy, else 302 to the provider
  /_data/*, /playlist.m3u           gated by _middleware.js (Pages serves real files directly)
```

### Two load-bearing design decisions

1. **Tuliprox's `xtream` output is a binary B+Tree** (`live.db`/`live.idx`/`video.db`) that only
   Tuliprox's own `-s` server can read — it cannot be shipped to a static host. The `m3u` it emits
   alongside already carries every field the Xtream API needs, and `cat_live.json` is a plain
   category map, so the build indexes those instead. Tuliprox still does all fetching/filtering.

2. **Stream URLs are deterministic** (`<host>/live/<user>/<pass>/<id>.ts`), so the edge rebuilds
   any stream URL from the id via env vars. No per-channel storage, and **no provider credentials
   are ever written into a published file** — they exist only in Worker env.

### Why the edge cannot proxy video (and what it does instead)
The provider **refuses datacenter IP ranges; Cloudflare's egress gets `403` on every channel**
(verified: the same request returns `200` from a residential IP). So the edge authenticates, then
returns a **302 to the provider** and the player fetches from its own IP. `X-Stream-Mode` reports
which path was taken; `STREAM_MODE=redirect` skips the doomed fetch.

### CPU model
Free tier is ~10 ms CPU/request, so the Worker **never parses the big indexes** — prebuilt assets
are piped through with `new Response(res.body, …)`, ~0 CPU for a 2 KB or 5 MB payload alike.

## AI Model Routing Matrix

| ID | Provider | Connection | Rule / Tier | Assigned Profile | Assigned Model |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | Claude Pro / Antigravity | OAuth / direct | Pro Subscription | edge-architect | anthropic/claude-sonnet-4.6 |
| 2 | Gemini Pro | Direct API | Google AI Pro | edge-architect | gemini-4-argon |
| 3 | Nous Portal Free | OAuth | Free catalog ($0) | edge-builder | stepfun/step-3.7-flash |
| 4 | TokenHarbor Free | Custom Endpoint | $0 Free allowance | edge-builder | stepfun/step-3.7-flash:free |
| 5 | OpenRouter Free | API Token | `:free` | edge-auditor | meta-llama/llama-4-maverick:free |
| 6 | Groq Free | API Token | Free Dev Tier | edge-auditor | llama-3.3-70b-versatile |
| 7 | NVIDIA NIM Free | API Token | Non-expiring Free Credits | edge-auditor | nvidia/nemotron-3-super-12 |
| 8 | Cloudflare Free | Workers AI | Free Workers AI Tier | edge-auditor | @cf/meta/llama-3.3-70b-instruct-fp8-fast |
| 9 | Hugging Face Free | API Token | Free Monthly Credits | edge-auditor | free-router-coding |
| 10 | Ollama Local/Cloud | Custom Endpoint | Local / $0 Cloud | edge-auditor | gemma4:31b |

## Day-2 operations (browser-only — full runbook in `docs/OPERATIONS.md`)
Everything runs in the cloud: GitHub Actions builds, Cloudflare Pages serves, and a scheduled
GitHub workflow supervises. No local machine, CLI, or Hermes profile is required for any of it.
1. Rebuild now: Actions -> "Build & Deploy Tuliprox Xtream Edge" -> Run workflow.
2. Provider credential: **fully automatic, 24h cadence**. The build renews once a day — ~12h of
   overlap on a ~36h trial, so there is never a window serving a dead account — and reuses the
   credential in between (~1 signup/day instead of ~4). Its age is carried between builds in
   `.cred/provider.json` via the Actions cache. `PROVIDER_*` are only the bootstrap and the
   fallback. No email/OTP is ever read, and client credentials and every playlist URL are unaffected.
3. Change filtering: edit `config/source.yml` or `config/template.yml` in the GitHub web editor and
   commit to `main` — the push triggers a rebuild.
4. Health: the Edge Monitor workflow opens, updates and auto-closes a labelled issue. It reuses the
   same `verify_edge.py` that gates deploys, so monitoring and deployment can never drift apart.
5. Clients connect by Xtream login (server URL + `CLIENT_USER`/`CLIENT_PASS`) or by M3U URL.

## Build notes
- Tuliprox v3.3.0 ships as a **bare musl binary** (~33.9 MB), not a tarball; the workflow also
  fetches the published `.sha256` and verifies it.
- Storage key is `working_dir:` — `storage_dir:` is not a real key.
- `filter` is a **required** field on every target.
- Pass `-H "$PWD"`, or Tuliprox derives its home from the binary's own directory.
- `wrangler pages deploy dist` bundles `./functions` from the **CWD** — run it from the repo root.
- Node 22 is required by wrangler; CI uses the runner's Node, local use needs `nvm use 22`.