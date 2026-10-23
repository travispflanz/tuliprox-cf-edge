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
| **Health alerts** | [Issues labelled `edge-alert`](https://github.com/travispflanz/tuliprox-cf-edge/issues?q=label%3Aedge-alert) |
| **Secrets** | [Settings → Secrets → Actions](https://github.com/travispflanz/tuliprox-cf-edge/settings/secrets/actions) |
| **Cloudflare** | [dash.cloudflare.com](https://dash.cloudflare.com) → Workers & Pages → `tuliprox-edge-gateway` |
| **Currently serving** | ~9–10k live channels across ~120–165 categories, with EPG |

> Channel and category counts move whenever the provider's lineup changes (observed between 9,026
> and 13,651 depending on the filter and the day), so this document gives ranges rather than a
> figure that is stale the moment it is written. For the live number, see the latest build's summary
> or `get_live_streams` ([§1.3](#13-is-it-working)).

> **Everything in Part 1 is done in a web browser.** No terminal, no CLI, no local install.
> Part 2 is for developers and AI agents working on the code.

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
remove one to bring it back. This filter keeps roughly half of what the provider offers — about
9–10k channels out of its ~300 raw categories — though the exact figure tracks the provider's
lineup and changes week to week.

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
README.md                     this file — the only documentation
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
  tuliprox-edge.yml           build + deploy (cron 6h, push, dispatch)
  edge-monitor.yml            health watchdog (cron 30 */6)
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

## 2.6 Testing

```bash
make test        # offline: no network, no credentials, no provider contact. Gates every build.
make test-live   # the above, plus 27 assertions against the deployed production edge
```

`build/run_tests.py` is the single entrypoint and **CI runs exactly it**, so a local run and a build
can never check different things. It reports `pass / fail / skip`; only a **fail** breaks the suite,
and a **skip** is printed loudly so a missing optional tool can never pass itself off as success.

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
| `PROVIDER_URL`, `PROVIDER_USER`, `PROVIDER_PASS` | optional | bootstrap + fallback provider credential |

**GitHub repository variables** (non-secret) — [Settings → Variables → Actions](https://github.com/travispflanz/tuliprox-cf-edge/settings/variables/actions)

| Variable | Default | Purpose |
|---|---|---|
| `RENEW_EVERY_HOURS` | `24` | renewal cadence |
| `MIN_HOURS_LEFT` | `12` | renewal safety floor |
| `TRIAL_EMAIL_BASE` | `taskgobbler` | local part used to mint `+alias` addresses |

**Cloudflare Pages environment** — synced automatically by every build; don't edit by hand:
`CLIENT_USER`, `CLIENT_PASS`, `UPSTREAM_URL`, `UPSTREAM_USER`, `UPSTREAM_PASS`.

**Monitor tuning** (`build/monitor_edge.py`): `WARN_HOURS` (6) warns only when automated renewal is
*overdue* — deliberately below the 12h floor, so the normal cadence never generates noise;
`STALE_HOURS` (14) catches a stopped schedule.
