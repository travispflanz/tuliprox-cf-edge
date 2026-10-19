# Project Tuliprox-Cloudflare-Edge Ideas & Architecture Spec

## Objective
Serverless Tuliprox v3.3.0 CLI Mode pipeline running on GitHub Actions scheduled cron/push events, publishing sanitized M3U playlists and EPG XMLTV directly to Cloudflare Pages/Workers edge delivery ($0.00/mo, zero cold starts, zero sleep timers).

## AI Model Routing Matrix
Below is the dynamic routing matrix established during Phase 0B:

| ID | Provider | Connection | Rule / Tier | Assigned Profile | Assigned Model |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | Claude Pro / Antigravity | Oauth / direct | Pro Subscription | edge-architect | anthropic/claude-sonnet-4.6 |
| 2 | Gemini Pro | Direct API | Google AI Pro | edge-architect | gemini-4-argon |
| 3 | Nous Portal Free | OAuth | Free catalog ($0) | edge-builder | stepfun/step-3.7-flash |
| 4 | TokenHarbor Free | Custom Endpoint | $0 Free allowance | edge-builder | stepfun/step-3.7-flash:free |
| 5 | OpenRouter Free | API Token | :free | edge-auditor | meta-llama/llama-4-maverick:free |
| 6 | Groq Free | API Token | Free Dev Tier | edge-auditor | llama-3.3-70b-versatile |
| 7 | NVIDIA NIM Free | API Token | Non-expiring Free Credits | edge-auditor | nvidia/nemotron-3-super-12 |
| 8 | Cloudflare Free | Workers AI | Free Workers AI Tier | edge-auditor | @cf/meta/llama-3.3-70b-instruct-fp8-fast |
| 9 | Hugging Face Free | API Token | Free Monthly Credits | edge-auditor | free-router-coding |
| 10 | Ollama Local/Cloud | Custom Endpoint | Local / $0 Cloud | edge-auditor | gemma4:31b |

## Day-2 Operations Workflow
1. Developer edits `config/source.yml` or `config/mapping.yml` locally or on GitHub.
2. Changes pushed to `main` branch OR `gh workflow run tuliprox-edge.yml` is triggered.
3. GitHub Action downloads pre-compiled Tuliprox v3.3.0 musl binary, parses input playlists into `dist/`.
4. Wrangler deploys `dist/` and `functions/` to Cloudflare Pages project (`tuliprox-edge-gateway`).
5. Clients (TiviMate / VLC) fetch edge playlist via `https://<project>.pages.dev/get.php?username=${CLIENT_USER}&password=${CLIENT_PASS}&type=m3u_plus&output=ts`.


---

## Build notes (what actually shipped)

- Tuliprox v3.3.0 is distributed as a **bare musl binary** (33,891,968 bytes), not a tarball.
  The workflow downloads it plus the published `.sha256` and verifies before running.
- Storage key is `working_dir:` — the spec's `storage_dir:` is not a real key.
- `wrangler pages deploy dist` bundles `./functions` from the **CWD**, so the deploy runs from the repo root.
- `wrangler.yml` deleted: an unvalidated `mapping.yml` was dropped rather than shipped.
- Node 22 is required by wrangler; CI uses the runner's Node, local use needs `nvm use 22`.
