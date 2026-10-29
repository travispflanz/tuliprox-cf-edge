# Tuliprox → Cloudflare Xtream Edge

A self-hosted IPTV gateway with **no server, no hosting bill, and no machine of your own involved.**

GitHub Actions fetches your provider's channel list every 6 hours, filters and reshapes it, and
publishes it to Cloudflare Pages, which serves a real **Xtream Codes API** that any IPTV player can
log into. The build also **renews its own provider trial account**, so there is no recurring manual
step at all. A second scheduled workflow watches everything and opens a GitHub issue if it breaks.

| | |
|---|---|
| **Live edge** | https://tuliprox-edge-gateway.pages.dev |
| **Builds** | [Actions → Build & Deploy Tuliprox Xtream Edge](https://github.com/travispflanz/tuliprox-cf-edge/actions/workflows/tuliprox-edge.yml) |
| **Pull requests** | [Actions → CI](https://github.com/travispflanz/tuliprox-cf-edge/actions/workflows/ci.yml) — the gate every code change passes before it can deploy |
| **Change process** | `CONTRIBUTING.md` — and `AGENTS.md` if you are an AI agent |
| **Health alerts** | [Issues labelled `edge-alert`](https://github.com/travispflanz/tuliprox-cf-edge/issues?q=label%3Aedge-alert) |
| **Secrets** | [Settings → Secrets → Actions](https://github.com/travispflanz/tuliprox-cf-edge/settings/secrets/actions) |
| **Cloudflare** | [dash.cloudflare.com](https://dash.cloudflare.com) → Workers & Pages → `tuliprox-edge-gateway` |
| **Currently serving** | 10,312 channels across 163 categories, with EPG |

> **Everything in Part 1 is done in a web browser.** No terminal, no CLI, no local install.
> Part 2 is for developers working on the code, and **Part 3 is for whoever — human or AI
> agent — has to take it over.**

---

# Part 1 — Operating it

## 1.1 The one idea you need

**There is no playlist file to edit.** Cloudflare stores no editable state — it serves a frozen
copy of whatever the last build produced.

```
   YOUR PROVIDER              GITHUB ACTIONS               CLOUDFLARE PAGES           YOUR PLAYER
   source of truth   ──►  fetch → filter → reshape  ──►  static snapshot + API  ──►  TiviMate / VLC
   (trial account)            every 6 hours                    always on
```

So "updating the playlist" always means **re-running the build**, or changing an input to it. The
provider is the source of truth; the edge is a rebuildable cache.

| You want to change… | What you actually do | Where |
|---|---|---|
| Channels (the provider's lineup shifted) | Re-run the build | [§1.4](#14-force-a-rebuild) |
| The provider account | **Nothing — it renews itself** | [§1.5](#15-the-provider-account--automatic) |
| Which categories are included | Edit one config file in the browser | [§1.6](#16-change-what-the-playlist-contains) |
| The login your players use | Change two GitHub secrets, re-run | [§1.7](#17-change-the-login-your-players-use) |

## 1.2 Connecting a player

**Xtream login** — preferred, because it gives you categories, logos and EPG automatically:

```
Server URL:  https://tuliprox-edge-gateway.pages.dev
Username:    <your CLIENT_USER>
Password:    <your CLIENT_PASS>
```

**M3U playlist** — for players that only accept a URL:

```
https://tuliprox-edge-gateway.pages.dev/get.php?username=<CLIENT_USER>&password=<CLIENT_PASS>&type=m3u_plus&output=ts
```

**EPG (TV guide)** — normally detected from the playlist automatically; the explicit URL is:

```
https://tuliprox-edge-gateway.pages.dev/xmltv.php?username=<CLIENT_USER>&password=<CLIENT_PASS>
```

`CLIENT_USER` / `CLIENT_PASS` are credentials **you** chose, stored as GitHub secrets. They are
deliberately not written down in this repository. GitHub cannot show a secret back to you once
saved — if you've lost them, set new ones ([§1.7](#17-change-the-login-your-players-use)).

> **After any change, refresh the playlist inside your player.** Players cache aggressively, so an
> update can look like it didn't happen until you reload or re-add the playlist.

**Tested players:** TiviMate, iMPlayer, OTT Navigator, VLC. Only **one viewer at a time** — the
provider trial permits a single connection (`max_connections=1`).

## 1.3 Is it working?

Three ways, fastest first.

**1 — The alerts page.** [Issues labelled `edge-alert`](https://github.com/travispflanz/tuliprox-cf-edge/issues?q=label%3Aedge-alert).
A workflow checks the live edge every 6 hours and:

- **healthy** → no issue at all (and any previous alert is **closed automatically**)
- **warning** → *"Edge warning — upstream trial expiring soon"*; it still works, but renewal is overdue
- **broken** → *"Edge DOWN"*, and the run goes red so GitHub emails you

Issues are updated in place rather than duplicated, so the comment trail is the history.

**2 — The Actions tab.** A green tick on the latest run means the build succeeded *and* smoke-tested
the live site. Each run's summary carries the full health report.

**3 — Ask the edge directly.** Open this in any browser tab, filling in your own credentials:

```
https://tuliprox-edge-gateway.pages.dev/player_api.php?username=<CLIENT_USER>&password=<CLIENT_PASS>
```

Healthy looks like `"auth":1` and `"status":"Active"`. If you see `"auth":0` or `"status":"Expired"`,
the provider trial lapsed — it self-heals on the next build ([§1.5](#15-the-provider-account--automatic)).
An anonymous visitor to that same URL correctly gets `401`.

### What the monitor checks

1. **The whole edge** — reuses `build/verify_edge.py`, the same 27 assertions that gate every deploy:
   auth on every endpoint, catalogue size, Xtream field completeness, no provider host leaked into
   the playlist, EPG integrity, and a real channel returning MPEG-TS bytes.
2. **The provider account** — `auth`, `status`, expiry. This matters because a lapsed account still
   serves the full catalogue while *every stream fails*.
3. **Deploy freshness** — if the newest build is over 14 hours old, the schedule has stopped.
   GitHub silently disables cron in repositories it considers inactive, and this is the only way
   you'd ever find out.

## 1.4 Force a rebuild

Nothing to edit; just re-run the pipeline.

1. Open [Actions](https://github.com/travispflanz/tuliprox-cf-edge/actions)
2. Click **Build & Deploy Tuliprox Xtream Edge** in the sidebar
3. **Run workflow ▾** → **Run workflow**
4. ~4 minutes. The run smoke-tests the live edge before going green.

Use this when the provider's lineup changed and you don't want to wait for the next 6-hour cycle.

## 1.5 The provider account — automatic

**There is nothing to do here.** This used to be the one recurring chore; it isn't any more.

Trials last ~36 hours. The build renews on a **24-hour cadence**, so the replacement arrives
**6–12 hours before the old one dies** (typically ~9h — builds run every 6h, so the cadence is
evaluated in 6-hour steps). There is never a window where the playlist is backed by a dead account.

It also deliberately **reuses** a healthy credential rather than signing up each build. That keeps
the rate near **1 signup/day** instead of ~4/day, and that restraint is the point: request churn is
what would make the provider treat this as automated abuse and withdraw free trials altogether.

| Setting | Default | Meaning |
|---|---|---|
| `RENEW_EVERY_HOURS` | `24` | replace the credential once it's a day old |
| `MIN_HOURS_LEFT` | `12` | safety floor — renew regardless of age if life drops below this |

The floor is a net, not the engine: it covers a lost timestamp, a shorter-than-expected trial, or a
previous renewal that failed. If a renewal does fail, the next build retries 6 hours later while the
current credential still has hours left. Want more runway? Lower `RENEW_EVERY_HOURS` to `18` — it
buys overlap at the cost of more signups. Set it as a
[repository variable](https://github.com/travispflanz/tuliprox-cf-edge/settings/variables/actions).

Acquisition needs **no email, no OTP, no captcha and no browser** — the provider's own API returns
the credentials in its completion poll. An email *address* is required as an input (the provider
dedupes permanently per address, so a fresh `+alias` is minted each time) but **nothing ever reads
that inbox**.

**To force a brand-new trial:** Actions → Build & Deploy → **Run workflow ▾** → tick
**`force_trial`** → **Run workflow**.

**To pin a specific account by hand:** set `PROVIDER_URL`, `PROVIDER_USER`, `PROVIDER_PASS` in
[Secrets](https://github.com/travispflanz/tuliprox-cf-edge/settings/secrets/actions). They act as
the *bootstrap* and the *fallback* — a healthy seeded credential is reused, and any acquisition
failure falls back to it, so automation can never turn a working build into a broken one.

**Your players are unaffected by any of this.** The client credentials and every playlist URL stay
identical across provider rotations. That separation is the core of the design.

## 1.6 Change what the playlist contains

Edit the file in GitHub's web editor, commit to `main`, and the push triggers a rebuild.

**Which categories are kept** — `config/source.yml`, the `filter:` line:

```yaml
filter: '!ALL_CHAN! AND !DROP_NONENG!'
```

**What those names mean** — `config/template.yml`:

```yaml
templates:
  - name: ALL_CHAN
    value: 'Group ~ ".*"'
  - name: DROP_NONENG
    value: 'Not (Group ~ "(?i)^(DK|CH|ARG|AR|PL|TR|PT|ES|BR|MX|IN|SE|IT|NL|FR|DE|…):")'
```

The playlist is currently **filtered to English-language groups**: `DROP_NONENG` removes any
category whose name starts with one of those region prefixes. Add a prefix to drop that region;
remove one to bring it back. This filter currently yields **10,312 channels / 163 categories** out
of the provider's ~301 raw categories.

Patterns are regular expressions matched against the category name: `(?i)` makes it
case-insensitive and `^…:` anchors to the start of the name.

> A template that nothing references has **no effect**. To add a rule you must create the template
> *and* reference it from `filter:`.

**Include movies and series** — in `config/source.yml` set both to `false`:

```yaml
options:
  xtream_skip_vod: false
  xtream_skip_series: false
```

> ⚠️ Expect a **multi-hundred-megabyte** payload. The build is live-only on purpose: Cloudflare
> Pages caps individual files at 25 MiB, and staying live-only is what keeps the edge fast and free.

**Editing in the browser, step by step:**

1. Open the file, e.g. [`config/template.yml`](https://github.com/travispflanz/tuliprox-cf-edge/edit/main/config/template.yml)
2. Click the ✏️ pencil
3. **Commit changes…** → commit directly to `main`
4. The push triggers a rebuild — watch it in **Actions**

## 1.7 Change the login your players use

Do this if your `edge…` credentials leak, or you just want new ones.

1. Open [Secrets](https://github.com/travispflanz/tuliprox-cf-edge/settings/secrets/actions)
2. Update `CLIENT_USER` and `CLIENT_PASS`
3. Re-run the build ([§1.4](#14-force-a-rebuild))

⚠️ **Unlike the provider rotation, this one does break your players.** These credentials are
embedded in the playlist URLs, so every player must be re-pointed afterwards.

## 1.8 Troubleshooting

| Symptom | Cause | What to do |
|---|---|---|
| Catalogue loads but **every stream fails** | Provider trial expired | Self-heals on the next build. To fix now: [§1.4](#14-force-a-rebuild) with **`force_trial`** ticked |
| Player shows **Expired** / `auth: 0` | Same | Same |
| An **`edge-alert`** issue is open | The monitor found a problem | Read the issue — it names the cause |
| **401 Unauthorized** | Wrong client credentials | Use your `CLIENT_*` values, or reset them ([§1.7](#17-change-the-login-your-players-use)) |
| Playlist loads but **no channels** | Build failed, or deployed empty | Check **Actions** for a red run |
| **"Edge DOWN"** issue | The verifier is failing | Open the issue → linked run → read the failed step |
| **Stale deploy** warning | Scheduled builds stopped | Re-run ([§1.4](#14-force-a-rebuild)); GitHub pauses cron in inactive repos |
| Channels **missing** that used to exist | Provider dropped them, or a filter removed them | Review `config/template.yml`, then rebuild |
| **Changes don't appear** in the player | The player cached the playlist | Reload or re-add the playlist in the player |

**Red build?** Open the run in **Actions** and expand the failing step. The usual causes are an
empty secret or the credential leak gate firing — never bypass the gate, inspect the build instead.

## 1.9 Don'ts

| Don't | Why |
|---|---|
| Edit deployed files on Cloudflare | They're regenerated every build; your edits vanish |
| Bypass the credential leak gate | It exists so a published file can never expose the provider account |
| Commit `.dev.vars` or `.cred/` | Secrets belong in GitHub Secrets. Both are gitignored and the suite enforces it |
| Expect instant updates | A rebuild takes ~4 minutes, and players cache |
| Delete the `edge-alert` label | The monitor uses it to open, update and auto-close alerts |

---

# Part 2 — Developing it

## 2.1 Architecture

| Function | Runs on | Cadence |
|---|---|---|
| Fetch provider → filter → build indexes | GitHub Actions | every 6 hours |
| Serve Xtream / M3U / EPG | Cloudflare Pages Functions | always on |
| Provider credential renewal | GitHub Actions (inside the build) | 24-hour cadence |
| Health monitoring + alerting | GitHub Actions (`Edge Monitor`) | every 6 hours |
| Credential + secret storage | GitHub Secrets / Actions cache / Pages env | — |

```
GitHub Actions  (cron 6h | push | manual dispatch)
  acquire_trial.py      ensure a provider credential: reuse while healthy, else acquire a
                        fresh trial from the provider's API (no email/OTP/browser)
  tuliprox v3.3.0       one-shot: xtream in (provider) -> edge_list.m3u + cat_live.json
  probe_upstream.mjs    record the account's real health (auth/status/exp_date)
  curl xmltv.php        the provider's own EPG
  make_xtream.mjs       reshape m3u + cat_live.json -> static Xtream JSON indexes
  leak gate             fail the build if the provider host appears anywhere in dist/
  wrangler pages deploy -> Cloudflare Pages
                           functions/ serve a real Xtream Codes API over those static files
```

**Nothing depends on a local machine.** Built, served and supervised entirely by GitHub and
Cloudflare; operable from a browser.

## 2.2 Repository layout

```
Makefile                      make test / make test-live
README.md                     this file — the primary documentation: operate, develop, take over
CONTRIBUTING.md               the change process: the tiers, the PR flow, what CI runs and cannot
AGENTS.md                     cold-start page for an AI agent taking this over
config/
  config.yml                  Tuliprox runtime config (one-shot CLI mode)
  source.yml                  the provider input + output targets + filter
  template.yml                named filter templates referenced by source.yml
build/
  acquire_trial.py            provider-credential renewal (reuse vs acquire)
  make_xtream.mjs             m3u + cat_live.json -> static Xtream indexes
  probe_upstream.mjs          provider account health -> dist/_data/upstream.json
  monitor_edge.py             the cloud watchdog's logic
  run_tests.py                the canonical test suite entrypoint
  verify_edge.py              27 live assertions against any base URL
  verify_renewal_policy.py    the renewal decision matrix (offline)
  verify_credential_persistence.py   credential-age persistence (offline)
functions/
  _middleware.js              runs on EVERY request, including static-asset hits — the auth gate
  _lib/auth.js                client credential checking
  _lib/respond.js             shared response helpers
  _lib/xtream.js              player_api.php action handling
  _lib/proxy.js               stream delivery (proxy attempt -> 302 fallback)
  player_api.php.js           canonical Xtream endpoint
  panel_api.php.js            legacy alias some players still request
  get.php.js                  M3U playlist
  xmltv.php.js                EPG
  live, movie, series         stream routes (catch-all per type)
.github/workflows/
  tuliprox-edge.yml           build + deploy (cron 6h, push to main, dispatch)
  edge-monitor.yml            health watchdog (cron 30 */6)
  ci.yml                      the pull-request gate — offline suite, no secrets, no deploy
  main-guard.yml              reports code that reached main outside a pull request
.github/pull_request_template.md   the reviewer's checklist
.dev.vars.example             template for local Pages dev bindings
```

Only `live/` is reachable from the shipped catalogue (the build is live-only), but `movie/` and
`series/` are kept deliberately: real Xtream clients probe them, and they must return an
**auth-gated** response rather than a bare 404.

## 2.3 How the build works

Each run of **Build & Deploy Tuliprox Xtream Edge**:

1. Downloads the official Tuliprox binary and **verifies its SHA-256** checksum
2. Validates that client secrets are non-empty — an empty secret is indistinguishable from an unset
   one at runtime, so this turns a silent misconfiguration into an explicit failure
3. Runs the **test suite** (`build/run_tests.py`) — offline, no credentials
4. **Restores the credential persisted by the previous build** (Actions cache)
5. **Seeds** the effective credential: the persisted one wins over the repository secret, because it
   is *that* credential's real age which decides when renewal is due
6. **Ensures a usable credential** — reuse while healthy, else acquire a fresh trial
7. Runs **Tuliprox** one-shot: fetch → filter → normalise → `work/edge_list.m3u`
8. **Probes the provider account** so the edge can report `Expired` rather than failing mysteriously
9. Fetches the **EPG** (non-fatal if the provider's guide is unavailable)
10. **Reshapes** the output into the static indexes the edge serves
11. **Leak gate** — fails if the provider host *actually used* appears anywhere in `dist/`
12. **Records + persists** the credential in force, for the next build's cadence calculation
13. **Syncs the Pages environment** from the credential that built this deploy
14. **Deploys** to Cloudflare Pages
15. **Smoke-tests the live edge** with the same 27 assertions the monitor uses

## 2.4 The credential renewal engine

Renewal has two triggers, both in `build/acquire_trial.py`:

- **Cadence** — the credential in force is `RENEW_EVERY_HOURS` (24) old or older.
- **Floor** — under `MIN_HOURS_LEFT` (12) of life remains, whatever its age.

An **unknown age is never treated as fresh.** Assuming fresh is precisely what would cause a
renewal to be skipped forever, so an unreadable or missing timestamp falls back to the floor.

### Why the credential can't live in a repository secret

`GITHUB_TOKEN` **cannot rewrite a repository secret.** So a secret can only ever *bootstrap* the
system — if it were also the store, it would age out and then every build would find it dead and
mint a brand-new trial (~4 signups/day), which is the exact abuse pattern this design avoids.

Instead the credential *and its acquisition time* are written to `.cred/provider.json` (gitignored)
and carried between runs in a rolling Actions cache: `actions/cache/restore` with
`restore-keys: provider-cred-`, saved each run under `provider-cred-<run_id>`.

A cache entry is immutable per key, so a unique key plus a prefix restore is how you maintain a
rolling "latest".

**The invariant that makes it work:** the acquisition time is **preserved while the credential is
unchanged**, and reset **only** when the credential actually changes. Re-stamping it every build
would make the account look perpetually brand new, the cadence would never come due, and renewal
would silently never happen. `verify_credential_persistence.py` pins this.

## 2.5 Hard-won design constraints

These are verified behaviours, not theories. Changing the code without knowing them will
reintroduce bugs that are painful to diagnose.

1. **Tuliprox's `xtream` output cannot be shipped.** It's a binary B+Tree (`live.db`, `live.idx`, …)
   readable only by Tuliprox's own server. The build indexes the **M3U + `cat_live.json`** instead.
   Tuliprox still does all fetching, filtering and normalising — only the serving format changes.
2. **Stream URLs are deterministic**, so nothing per-channel needs storing: the upstream URL is
   always `<host>/live/<user>/<pass>/<id>.ts`, rebuilt at request time from env vars. That's why
   **provider credentials never appear in any published file.**
3. **The provider refuses datacenter IPs, and Cloudflare's egress is one.** The identical stream
   request returns `200` from a residential IP and `403` from Cloudflare, on every channel. So the
   edge authenticates the request and returns a **`302`**, letting the player fetch from its own IP —
   exactly how the provider expects clients to behave. `X-Stream-Mode` reports which path was taken.
   This is a deliberate workaround, not a fault.
4. **Never `JSON.parse` the big indexes in a Function.** Free-tier CPU is ~10 ms/request. Pre-split
   per category at build time and **pipe the prebuilt asset through** (`new Response(res.body, …)`),
   which costs ~0 CPU regardless of payload size.
5. **Never hand-set `Content-Encoding`.** Setting `gzip` on an already-gzipped body made Cloudflare
   compress it twice; one gunzip still yields gzip bytes and every player decodes garbage. Ship
   plain `epg.xml` and let Cloudflare compress it (22 MB → ~2.8 MB on the wire).
6. **EPG channel ids are case-sensitive and the two sources disagree.** The provider returns `1.L`
   while Tuliprox emits `1.l`, which cost 729 channels their guide data. Lower-casing `<channel id>`
   and `<programme channel>` at build time took coverage from 77.4% → 100%. Only touch lines
   starting with `<channel`/`<programme`, so element *text* is never mangled.
7. **Pages serves a real file directly without running a Function.** `_data/*` and `playlist.m3u`
   would be world-readable, so `functions/_middleware.js` runs on every inbound request — including
   static-asset hits — and is where the gate lives. Internal `env.ASSETS.fetch()` from your own
   Functions bypasses middleware, which is exactly what lets `get.php`/`xmltv.php` read those files
   after doing their own auth.
8. **`filter:` is required on every target.** Omitting it fails config parse with
   ``missing field `filter` ``.
9. **Pass `-H "$PWD"` to Tuliprox.** Without it, it derives its home from the *binary's* directory,
   so `working_dir` silently resolves next to the binary instead of the repo.
10. **Never probe Cloudflare with bare `python-urllib`.** Its default User-Agent trips a `403`
    (error code 1010) *before* auth, which looks exactly like a real outage. Always send a real UA.
11. **Trial accounts are `max_connections=1`.** One viewer at a time.
12. **Individual channels are unreliable** — real IPTV has dead channels. Probe several and accept
    the first success; never assert on one hardcoded channel id.
13. **The provider identity must never be hardcoded.** The acquisition script targets one named
    third party; naming it in the tree pairs a working automated free-trial signup script with its
    target the moment this repo is public. `PROVIDER_SITE`, `PANEL_URLS` and `TRIAL_EMAIL_BASE` are
    therefore read from the environment with **no defaults** (repository secrets in CI), the script
    exits 2 if one is unset, and the suite fails if any `http(s)://` literal ever returns to
    `build/acquire_trial.py`. Use a **secret** rather than a variable: a variable whose value
    reaches a log is public on a public repo, while secrets are masked — and the script logs the
    site it contacts.
14. **A wall of `401`s is a credentials symptom, not an outage symptom.** `build/verify_edge.py`
    says so explicitly now, and exits `3` (distinct from a real assertion failure) so a caller can
    tell the two apart. Nine times in ten it is a stale local `.dev.vars` — the client login is
    rotated by changing GitHub secrets, and a local file does not follow.

## 2.6 Testing

```bash
make test        # offline: no network, no credentials, no provider contact. Gates every build.
make test-live   # the above, plus 27 assertions against the deployed production edge
```

`build/run_tests.py` is the single entrypoint and **CI runs exactly it**, so a local run and a build
can never check different things. It reports `pass / fail / skip`; only a **fail** breaks the suite,
and a **skip** is printed loudly so a missing optional tool can never pass itself off as success.

**Where it runs, and why those are three separate workflows:**

| Workflow | Trigger | What it does |
|---|---|---|
| `.github/workflows/ci.yml` | every **pull request** | runs the offline suite. No secrets, no network, no deploy — safe on any branch |
| `.github/workflows/tuliprox-edge.yml` | push to `main`, cron 6h, dispatch | runs the **same** suite again, then builds and deploys |
| `.github/workflows/edge-monitor.yml` | cron 30 */6 | runs the **live** group against the deployed edge and raises alerts |

The deploy workflow is deliberately *not* triggered by pull requests: it holds secrets, publishes to
the live edge and can mint a provider trial, so running it on an unmerged branch would deploy
unreviewed code and spend signups as a side effect of opening a PR.

| Check | Why it exists |
|---|---|
| `verify_renewal_policy.py` | the 24h-cadence / 12h-floor decision matrix, boundaries, unknown age, and that no case can reach the signup path |
| `verify_credential_persistence.py` | extracts and executes the workflow's **own** `run:` blocks, proving a credential's age survives between builds |
| `py_compile` | Python syntax across `build/` |
| `node --check` | JS syntax — via a `.mjs` copy, since `node --check` parses a `.js` file as CommonJS and would wrongly reject these ES modules |
| workflows parse | a broken workflow can't be caught by CI, because CI is the thing that breaks |
| documentation integrity | no reference to a deleted file, and no plaintext credential in tracked docs |
| secret hygiene | `.cred/`, `.dev.vars`, `.env` are untracked and unstageable |

The live group runs `build/verify_edge.py`, which also gates every deploy and powers the monitor.
Point it anywhere:

```bash
BASE=https://tuliprox-edge-gateway.pages.dev CLIENT_USER=… CLIENT_PASS=… python3 build/verify_edge.py
```

It asserts: anonymous access to `player_api.php` / `get.php` / `playlist.m3u` / `_data/*` → **401**;
login `auth=1`; `get_live_categories` → string `category_id`; `get_live_streams` → all 12 Xtream
keys; a category filter isolates one category; VOD/series actions → `[]`; the playlist is `#EXTM3U`
with **0 foreign URLs**; the EPG is not double-encoded; and a real channel returns `video/mp2t` with
MPEG-TS sync bytes (`0x47` at offsets 0/188/376).

> If your `python3` lacks PyYAML, set `YAML_PYTHON=/path/to/python` rather than installing anything.

## 2.7 Local development

```bash
cp .dev.vars.example .dev.vars        # then fill in real values; .dev.vars is gitignored
npx wrangler@4 pages dev dist --port 8788 --ip 127.0.0.1
BASE=http://localhost:8788 CLIENT_USER=… CLIENT_PASS=… python3 build/verify_edge.py
```

`dist/` is a build artifact — produce it by running the workflow, or by running the build steps
locally. `functions/` is bundled **from the current working directory**, not from the asset dir, so
always deploy from the repo root.

## 2.8 Configuration reference

**GitHub repository secrets** — [Settings → Secrets → Actions](https://github.com/travispflanz/tuliprox-cf-edge/settings/secrets/actions)

| Secret | Required | Purpose |
|---|---|---|
| `CLIENT_USER`, `CLIENT_PASS` | **yes** | what your players log in with; baked into playlist URLs at build time |
| `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` | **yes** | deploy to Pages |
| `PROVIDER_SIGNUP_SITE`, `PROVIDER_PANEL_URLS`, `TRIAL_EMAIL_BASE` | **yes** | the **provider identity** — which third party, and which panels. Supplied only from secrets, so neither the code nor a public copy of this repo names it. See §2.5.13 |
| `PROVIDER_URL`, `PROVIDER_USER`, `PROVIDER_PASS` | optional | bootstrap + fallback provider credential |
| `IMPLAYER_INTEGRATION_KEY` | no | unused by this repo; belongs to the separate iMPlayer track |

> The provider identity is a **secret**, not a variable, and deliberately has **no default in the
> code**. If renewal ever logs `ERROR: PROVIDER_SITE is required`, one of these three secrets is
> unset — the acquisition step fails safely (`continue-on-error`) and the credential already in
> force keeps serving, so the edge stays up while you fix it.

**GitHub repository variables** (non-secret) — [Settings → Variables → Actions](https://github.com/travispflanz/tuliprox-cf-edge/settings/variables/actions)

| Variable | Default | Purpose |
|---|---|---|
| `RENEW_EVERY_HOURS` | `24` | renewal cadence |
| `MIN_HOURS_LEFT` | `12` | renewal safety floor |

**Cloudflare Pages environment** — synced automatically by every build; don't edit by hand:
`CLIENT_USER`, `CLIENT_PASS`, `UPSTREAM_URL`, `UPSTREAM_USER`, `UPSTREAM_PASS`.

**Monitor tuning** (`build/monitor_edge.py`): `WARN_HOURS` (6) warns only when automated renewal is
*overdue* — deliberately below the 12h floor, so the normal cadence never generates noise;
`STALE_HOURS` (14) catches a stopped schedule.

---

# Part 3 — Taking it over

For whoever inherits this next: a human who has never seen it, or an AI agent starting cold (that
one should read `AGENTS.md` first, then come back here).

## 3.1 What you are actually inheriting

A **live service with no server to log into.** Everything that runs, runs in GitHub Actions or on
Cloudflare's edge. Consequences worth internalising before you touch anything:

- **There is no machine to fix.** If it is not in git and not in Actions/Cloudflare, it does not exist.
- **The only local state that matters is `.dev.vars`** (gitignored), and even that is *not* the
  source of truth — it is a convenience for running the edge locally. Production reads GitHub
  secrets and Pages environment variables.
- **`main` is production.** A push to `main` deploys. There is no promotion step, no staging.
- **Blast radius is small and human.** A handful of personal devices. An outage means someone's TV
  stops, not a business stopping. That is why the alerting is issue-based rather than paging.

## 3.2 Current state, and how to check it yourself

Verified at the time of writing — re-verify rather than trusting this text:

```bash
gh run list --limit 5                                   # recent builds + monitor runs
gh api repos/travispflanz/tuliprox-cf-edge/commits/main --jq .sha   # what main is at
gh issue list --state open                              # any open edge-alert
```

| | |
|---|---|
| Deployed commit | whatever `main` is at — the build deploys that commit on every push and every 6h |
| Production health | the deploy's final step runs the live suite; the monitor repeats it every 6h |
| Open alerts | none, if `gh issue list --state open` is empty |
| Build cadence | cron `0 */6 * * *` on `.github/workflows/tuliprox-edge.yml` |
| Monitor cadence | cron `30 */6 * * *` on `.github/workflows/edge-monitor.yml` |

**A green build is not proof the service works.** The build smoke-tests the live edge before going
green, so green does mean "the edge answered" — but the monitor is what catches the edge failing
*between* builds. Check both.

## 3.3 Where every credential lives

Never print a value from here. Names and locations only — that is all you need to reason about it.

| Credential | Lives in | Purpose | Rotating it breaks |
|---|---|---|---|
| `CLIENT_USER`, `CLIENT_PASS` | GitHub secrets **and** Pages env | what players log in with; baked into playlist URLs at build time | **every player** must be re-pointed (README §1.7) |
| `PROVIDER_URL`, `PROVIDER_USER`, `PROVIDER_PASS` | GitHub secrets | *bootstrap + fallback only*. The credential actually in force lives in `.cred/provider.json` in a rolling Actions cache | nothing, if the cache is healthy — the cache wins over these |
| `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` | GitHub secrets | deploy to Pages, sync the Pages env | deploys only |
| `IMPLAYER_INTEGRATION_KEY` | GitHub secret | **referenced by nothing in this repo.** It belongs to a separate track | nothing here |
| `PROVIDER_SIGNUP_SITE`, `PROVIDER_PANEL_URLS`, `TRIAL_EMAIL_BASE` | GitHub secrets | the **provider identity**: which third party, and which panels. Kept out of the code entirely, so a public copy of this repo never names it | nothing in the code; if unset, renewal stops **non-fatally** and the credential in force keeps serving |

**Repository variables: none are currently set**, so every documented default is in force
(`RENEW_EVERY_HOURS` 24, `MIN_HOURS_LEFT` 12, and `STRICT_MAIN_GUARD` unset = advisory, see §3.5).

**Cloudflare Pages**, project `tuliprox-edge-gateway`, production environment, holds:
`CLIENT_USER`, `CLIENT_PASS`, `UPSTREAM_URL`, `UPSTREAM_USER`, `UPSTREAM_PASS` — the five the
deploy syncs automatically. Do not edit them by hand; the next build overwrites them.

## 3.4 The moving parts, and how each one fails

| Component | Runs on | Cadence | How it fails | How you find out |
|---|---|---|---|---|
| Build + deploy | GitHub Actions | 6h, push to `main`, dispatch | run goes red; the previous deploy keeps serving | red run, and the monitor's staleness check |
| Edge (Xtream API, M3U, EPG) | Cloudflare Pages Functions | always on | streams fail before the catalogue does | monitor alert → `edge-alert` issue |
| Provider credential renewal | GitHub Actions, inside the build | 24h cadence, 12h floor | account lapses; catalogue still serves, **every stream fails** | monitor's `auth`/`status` check |
| Health monitoring | GitHub Actions | 6h | silence | the staleness check is the only thing that catches a stopped schedule |
| Provider itself | third party | — | drops channels, changes its panel, withdraws trials | channel counts change; the build fails if the API moves |

The nastiest row is the third. A lapsed provider account is **not** obvious: the catalogue is a
static snapshot, so it keeps serving perfectly while every stream 404s upstream. That is why the
monitor checks the account separately from the edge.

## 3.5 How a change reaches production

`main` is the deploy trigger, so the process is the release process.

```
branch ──► pull request ──► ci.yml (offline suite) ──► merge to main
                                                          │
                                                          └──► tuliprox-edge.yml: suite → build → deploy → smoke test
```

**The process is tiered** (`CONTRIBUTING.md` explains it fully):

- `config/` — the operator's file. **Direct to `main` is the designed path** (README §1.6). No PR.
- `build/`, `functions/`, `.github/`, `Makefile` — branch → PR → green CI → merge.
- `README.md`, `CONTRIBUTING.md`, `AGENTS.md` — PR, because docs are what everyone trusts later.

**GitHub cannot enforce the review rule here.** Both classic branch protection *and* repository
rulesets answer `403` on a private repo on the Free plan:

```
$ gh api repos/travispflanz/tuliprox-cf-edge/rulesets
{"message":"Upgrade to GitHub Pro or make this repository public to enable this feature.","status":403}
```

So `.github/workflows/main-guard.yml` reports code that reached `main` outside a pull request
instead. It **cannot block** — by the time it runs, the push has already landed. To make the rule
real: make the repo public or move to Pro, then require a pull request and the check
`offline suite — no secrets, no network`. For a tripwire today, set the variable
`STRICT_MAIN_GUARD=true` and that guard fails instead of warning.

## 3.6 Verifying without fooling yourself

```bash
make test        # offline. The gate. No network, no credentials.
make test-live   # + the live suite against the deployed edge. Needs real client credentials.
```

Four ways to get this wrong, all of them observed here:

1. **A stale `.dev.vars` looks exactly like an outage.** `make test-live` reported *"7/22 checks
   passed"* with a wall of `401`s while production was verifiably healthy — the deploy's own smoke
   test and the monitor had both passed minutes earlier. The local file simply predated a client-login
   rotation. **Before believing a live failure, check whether the build's smoke test passed.** If it
   did, suspect your local credentials, not the service.
2. **A SKIP is not a pass.** The suite prints skips loudly and does not fail on them. A missing node
   silently removes the JS syntax check from a green run.
3. **A check that has never failed has not been proven.** Inject the bug, watch it report FAIL, then
   restore — with a **file copy**, never `git checkout --`, which restores from the index and eats
   any unstaged work.
4. **`make test` green does not mean the edge works.** It is offline by definition. Only
   `make test-live` and the monitor touch the deployed service.

## 3.7 Known gaps and unfinished business

Honest inventory. None of these is a surprise to a careful reader, and each one is deliberate or
understood — but all of them are open.

| Gap | Detail | Status |
|---|---|---|
| Review rule is unenforced | branch protection and rulesets are `403` on a private Free repo; `main-guard.yml` only reports | open — fix by making the repo public or moving to Pro (§3.5) |
| Leftover inert Pages env vars | production still carries `CLIENT_2_USER`, `CLIENT_2_PASS`, `UPSTREAM_2_URL`, `UPSTREAM_2_USER`, `UPSTREAM_2_PASS`, `UPSTREAM_HEALTHY_SLOTS` from a rolled-back dual-slot design. **Verified: no tracked file references any of them**, and the deploy syncs only the five real ones, so they are inert — but they mislead anyone reading the dashboard | cosmetic; safe to delete in the Cloudflare UI |
| `.dev.vars` drifts silently | nothing keeps it in sync with the `CLIENT_*` secrets, so local live testing breaks quietly after any rotation (§3.6.1) | open — refresh by hand when you need it |
| `STREAM_MODE` is never set | the runtime reads it (`redirect` skips the doomed proxy attempt), but no deploy sets it, so production always probes first | deliberate default; a knob, not a bug |
| Single provider dependency | one provider, one panel type. If it withdraws free trials or changes its API, the build stops and there is no second source | design choice; the acquisition script is the piece to fork |
| One viewer at a time | provider trials are `max_connections=1` | inherent to free trials; capacity would come from holding several accounts (the dual-slot design does exist, on `backup/pre-rollback`) |
| Streams are HTTP, not HTTPS | the provider is an XUI.one panel with a self-signed certificate, so `https://` fails verification and the 302 must use `http://`. Some Android TV clients block cleartext by default | upstream limitation |

## 3.8 Decision log

Why the odd-looking things are the way they are. Each of these was paid for once.

| Decision | Why | Where |
|---|---|---|
| Static Xtream JSON, not Tuliprox's output | Tuliprox's `xtream` output is a B+Tree only its own server can read | §2.5.1 |
| Redirect (302) instead of proxying streams | the provider refuses datacenter IPs, and Cloudflare's egress is one — verified `200` residential vs `403` from Cloudflare | §2.5.3 |
| Credential in an Actions cache, not a repo secret | `GITHUB_TOKEN` cannot rewrite a secret, so a secret can only bootstrap | §2.4 |
| Acquisition is non-fatal | automation must never turn a working build into a broken one | §2.3 |
| Renewal = cadence + floor, and nothing else | a second timing rule (a backoff) suppressed the retry it was meant to protect | §2.4 |
| Pages uses direct upload, no Cloudflare Git integration | exactly one deploy path; a Git integration would race the workflow | — |
| Three separate workflows | a `pull_request` trigger on the deploy workflow would publish unreviewed code and mint trials | §2.6 |
| `README.md` absorbed the old `docs/` | the deleted files had gone stale and their claims were false; a second copy of the truth is worse than none | commit `4a7f955` |
| Tiered change process | a uniform "always PR" rule would be ignored for `config/`, which is designed for direct commits | `CONTRIBUTING.md` |
| The provider identity lives only in secrets | the code targets one named third party, and naming it in the tree would pair a working automated signup script with its target in a public repo. Pinned by a suite check, not by convention | §2.5.13 |

## 3.9 Rollback

Three levels, fastest first.

1. **Deployed files** — Cloudflare dashboard → Workers & Pages → `tuliprox-edge-gateway` →
   Deployments → **Rollback to this deployment** on a known-good one. Instant, and it does not
   touch git. Every build already keeps its own `<hash>.pages.dev` URL as a permanent preview.
2. **Code** — `git revert <sha>`, merge, and the push redeploys. This is the normal route.
3. **Everything including the credential** — note that the provider credential lives in an
   **Actions cache**, not in git. Reverting code does **not** revert the credential. If a bad change
   was in the renewal logic, delete the `provider-cred-*` cache entries (Actions → Caches) so the
   next build re-bootstraps from `PROVIDER_*`.

The rolled-back dual-slot design is preserved on the `backup/pre-rollback` branch. Do not delete it.

## 3.10 The takeover test

You can say you have taken this over when you can do all of these without help:

- [ ] State what `main` deploys to, and what the deployed commit is right now.
- [ ] Run `make test`, explain why it needs no credentials, and say what it does **not** cover.
- [ ] Explain where the live provider credential is stored, and why it is not in a repository secret.
- [ ] Find the last build, read its failed step if it failed, and say whether the edge is healthy.
- [ ] Say what a lapsed provider account looks like (catalogue fine, every stream dead) and how the
      monitor distinguishes it.
- [ ] Ship a change through a pull request and watch CI gate it.
- [ ] Roll production back, and say what rolling back does *not* restore.
