# Tuliprox → Cloudflare Xtream Edge

A self-hosted IPTV gateway with **no server, no hosting bill, and no machine of your own involved.**

GitHub Actions fetches your provider's channel list every 4 hours, filters and reshapes it, and
publishes it to Cloudflare Pages, which serves a real **Xtream Codes API** that any IPTV player can
log into. The build also **renews its own provider trial account**, so there is no recurring manual
step at all. A second scheduled workflow watches everything and opens a GitHub issue if it breaks.

| | |
|---|---|
| **Live edge** | https://tuliprox-edge-gateway.pages.dev |
| **Builds** | [Actions → Build & Deploy Tuliprox Xtream Edge](https://github.com/travispflanz/tuliprox-cf-edge/actions/workflows/tuliprox-edge.yml) |
| **Health alerts** | [Issues labelled `edge-alert`](https://github.com/travispflanz/tuliprox-cf-edge/issues?q=label%3Aedge-alert) |
| **Your playlist login** | [Settings → Variables → Actions](https://github.com/travispflanz/tuliprox-cf-edge/settings/variables/actions) — `CLIENT_USER` / `CLIENT_PASS`, readable and editable |
| **Secrets** | [Settings → Secrets → Actions](https://github.com/travispflanz/tuliprox-cf-edge/settings/secrets/actions) — Cloudflare token only |
| **Cloudflare** | [dash.cloudflare.com](https://dash.cloudflare.com) → Workers & Pages → `tuliprox-edge-gateway` |
| **Currently serving** | ~9–10k live channels across ~120–165 categories, with EPG |

> Channel and category counts move every time you tighten a filter or the provider's lineup
> changes — observed between 9,026 and 13,651 in a single week — so this document deliberately
> gives ranges instead of a figure that is stale the moment it is written. For the live number, see
> the latest build's summary or `get_live_streams` ([§1.3](#13-is-it-working)).

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
   (trial account)            every 4 hours                    always on
```

So "updating the playlist" always means **re-running the build**, or changing an input to it. The
provider is the source of truth; the edge is a rebuildable cache.

| You want to change… | What you actually do | Where |
|---|---|---|
| Channels (the provider's lineup shifted) | Re-run the build | [§1.4](#14-force-a-rebuild) |
| The provider account | **Nothing — it renews itself** | [§1.5](#15-the-provider-account--automatic) |
| Which categories are included | Edit one config file in the browser | [§1.6](#16-change-what-the-playlist-contains) |
| The login your players use | Change two GitHub variables, re-run | [§1.7](#17-change-the-login-your-players-use) |

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

`CLIENT_USER` / `CLIENT_PASS` are credentials **you** chose. They are stored as GitHub repository
**variables**, not secrets, so you can read them back at any time:

1. Open the [Variables page](https://github.com/travispflanz/tuliprox-cf-edge/settings/variables/actions),
2. or run `gh variable get CLIENT_PASS`.

Secrets are write-only — once saved, GitHub will never show the value again — which would make your
own playlist password unlookupable and leave "reset it and re-point every player" as the only
recourse. The threat model does not justify that: this is a **private** repository, and these
credentials are already in plaintext in every player's URL.

`CLIENT_2_USER` / `CLIENT_2_PASS` are the same thing for a **second** simultaneous viewer
([§2.8](#28-why-two-credential-slots)). Leave both unset for a single viewer.

> A credential must live in **one** place. If a name exists as both a variable and a secret, the
> variable wins — but a reader that looks only at the secret will authenticate with the *stale*
> value. That is a real outage this project has hit: it blinded the health monitor while the edge
> was perfectly healthy. The suite now enforces that every workflow reads them the same way.

> **After any change, refresh the playlist inside your player.** Players cache aggressively, so an
> update can look like it didn't happen until you reload or re-add the playlist.

**Tested players:** TiviMate, iMPlayer, OTT Navigator, VLC.

**How many people can watch at once?** One per configured provider slot. Each trial account allows
a single connection (`max_connections=1`), so the edge holds **two** accounts and hands each device
its own — two devices can stream simultaneously. A third would share, and whoever gets bumped sees
streams fail while the channel list still loads.

The assignment follows your device's IP address, so a given device keeps the same account for as
long as its address is stable. Two devices on the *same* network still get different accounts.

## 1.3 Is it working?

Three ways, fastest first.

**1 — The alerts page.** [Issues labelled `edge-alert`](https://github.com/travispflanz/tuliprox-cf-edge/issues?q=label%3Aedge-alert).
A workflow checks the live edge every 4 hours and:

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

1. **The whole edge** — reuses `build/verify_edge.py`, the same live assertions that gate every deploy:
   auth on every endpoint, catalogue size, Xtream field completeness, no provider host leaked into
   the playlist, EPG integrity, and a real channel returning MPEG-TS bytes.
2. **The provider account** — `auth`, `status`, expiry. This matters because a lapsed account still
   serves the full catalogue while *every stream fails*.
3. **Deploy freshness** — if the newest build is over 10 hours old, the schedule has stopped *or is running badly late* (GitHub cron drifts; see [§1.5](#15-the-provider-account--automatic)).
   GitHub silently disables cron in repositories it considers inactive, and this is the only way
   you'd ever find out.

## 1.4 Force a rebuild

Nothing to edit; just re-run the pipeline.

1. Open [Actions](https://github.com/travispflanz/tuliprox-cf-edge/actions)
2. Click **Build & Deploy Tuliprox Xtream Edge** in the sidebar
3. **Run workflow ▾** → **Run workflow**
4. ~4 minutes. The run smoke-tests the live edge before going green.

Use this when the provider's lineup changed and you don't want to wait for the next 4-hour cycle.

## 1.5 The provider account — automatic

**There is nothing to do here.** This used to be the one recurring chore; it isn't any more.

There are **two** provider accounts ("slots"), each renewing itself independently. Trials last
~36 hours; each slot renews on a **24-hour cadence**, and builds run **every 4 hours**, so a
replacement is always obtained well before the old account dies.

Each slot deliberately **reuses** a healthy credential rather than signing up every build. That
keeps the rate near **1 signup/day per slot**, and that restraint is the point: request churn is
what would make the provider treat this as automated abuse and withdraw free trials altogether.

| Setting | Default | Meaning |
|---|---|---|
| `RENEW_EVERY_HOURS` | `24` | replace a credential once it's a day old |
| `MIN_HOURS_LEFT` | `6` | safety floor — renew regardless of age if life drops below this |
| `RENEW_STAGGER_HOURS` | `12` | how far slot 2 is held out of phase with slot 1 |

The floor is a net, not the engine: it covers a lost timestamp, a shorter-than-expected trial, or a
previous renewal that failed. Because builds run every 4 hours and the floor is 6, **a renewal can
fail once and still be retried while the account is alive** — that relationship is what makes the
pair of numbers safe, so if you raise the build interval, raise the floor too.

> ⚠️ **The real margin is 12 hours, and GitHub's cron does not reliably honour the 4-hour target.**
> Renewal is due at 24h old and the trial dies at ~36h, so a build must land inside that 12h window.
> GitHub schedules are best-effort — **gaps of 10.6h (build) and 11.7h (monitor) have been measured
> on this repository**, and the ~06:00 UTC slot is skipped fairly consistently. That has not broken
> anything yet, but it leaves roughly an hour of margin: one worse gap and the streams fail while
> the catalogue keeps serving.
>
> Two ways to buy margin, both tunable without a code change:
> - Lower `RENEW_EVERY_HOURS` (e.g. `18`) — widens the window to 18h for ~0.3 extra signups/day.
> - Raise `STALE_HOURS`' counterpart by watching for the **Stale deploy** alert, which fires past 10h.
>
> A gap long enough to threaten this is reported by the monitor as a **Stale deploy** problem, so the
> system does tell you. If you want the cadence actually guaranteed rather than merely watched, drive
> the build from a clock that is not GitHub's — a Cloudflare Cron Trigger calling the workflow's
> `workflow_dispatch` API. Cloudflare's scheduler is dependable in a way GitHub's is not.

**If a signup fails, it waits 12 hours before trying again** (`ACQUIRE_RETRY_HOURS`). A slot with
no credential at all counts as "renewal due" on every build, so without this a provider-side
hiccup would mean a signup attempt every 4 hours — six a day, which is the churn that gets free
trials withdrawn. The other slot keeps serving while it waits, so there is nothing to lose by
waiting. Need it sooner? Dispatch the build manually with **force_trial**, which skips the backoff.

**The two slots are kept out of phase on purpose.** If both were acquired together they would renew
together and expire together, so one bad renewal cycle would remove *all* capacity at once — the
very outage the second slot exists to prevent. Slot 2's first renewal is therefore pulled forward to
`RENEW_STAGGER_HOURS`, after which it rides the normal cadence permanently offset.

Tune any of these as [repository variables](https://github.com/travispflanz/tuliprox-cf-edge/settings/variables/actions).

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

Filtering is two files working together, and **both must agree** — this is the single most common
place to trip up:

| File | Role |
|---|---|
| `config/template.yml` | *defines* named rules, e.g. `DROP_NONENG`, `DROP_RELIGION` |
| `config/source.yml` | the `filter:` line *applies* them, e.g. `'!ALL_CHAN! AND !DROP_NONENG! AND !DROP_RELIGION!'` |

> **A template that `filter:` does not reference has no effect at all.** Adding a rule is always two
> edits: define it in `template.yml`, then add `AND !YOUR_RULE!` to `filter:` in `source.yml`.
> Equally, naming a template in `filter:` that doesn't exist fails the build.

Each rule is a regular expression matched against the **category (group) name**:

- `(?i)` — case-insensitive
- `^\s*` … `\s*(:|$)` — anchors to a whole group name, tolerating stray spaces, so `ES` matches the
  group `ES: Sports` but never the word "ES" inside `BEST OF ES`
- `NOT ( … )` — drop what matches; without `NOT` you'd *keep only* what matches

To drop another region, add its prefix to the alternation inside `DROP_NONENG`. To bring one back,
remove it. To drop an unrelated kind of content, add a new template (plus its `filter:` reference)
rather than overloading an existing one — that's exactly what `DROP_RELIGION` is for.

See the two files for the rules currently in force; they are edited often enough that quoting them
here would only go stale.

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

1. Open [Variables](https://github.com/travispflanz/tuliprox-cf-edge/settings/variables/actions)
2. Update `CLIENT_USER` and `CLIENT_PASS` (`gh variable set CLIENT_USER --body '...'`)
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
| **Stale deploy** warning | Scheduled builds stopped, or cron drifting badly late | Re-run ([§1.4](#14-force-a-rebuild)). GitHub pauses cron in inactive repos, and its schedule is best-effort even when active |
| Channels **missing** that used to exist | Provider dropped them, or a filter removed them | Review `config/template.yml`, then rebuild |
| **Changes don't appear** in the player | The player cached the playlist | Reload or re-add the playlist in the player |
| Only **one device** can watch at a time | A provider slot is unhealthy or was never acquired | The monitor says which. It self-heals on a later build; a failed signup waits 12h, so use **`force_trial`** to retry now |
| Both devices fail together | Every slot is dead | The monitor raises this as a hard alert — [§1.4](#14-force-a-rebuild) with **`force_trial`** |

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
| Fetch provider → filter → build indexes | GitHub Actions | every 4 hours |
| Serve Xtream / M3U / EPG | Cloudflare Pages Functions | always on |
| Provider credential renewal | GitHub Actions (inside the build) | 24h per slot, staggered 12h |
| Health monitoring + alerting | GitHub Actions (`Edge Monitor`) | every 4 hours |
| Credential storage | GitHub Variables (your login) / Secrets (Cloudflare token) / Actions cache (provider slots) / Pages env | — |

```
GitHub Actions  (cron 4h | push | manual dispatch)
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
  acquire_trial.py            per-slot credential renewal (reuse vs acquire)
  credential_state.py         seed/record credential state across builds
  slot_health.py              per-slot watchdog verdicts (pure, importable)
  make_xtream.mjs             m3u + cat_live.json -> static Xtream indexes
  probe_upstream.mjs          provider account health -> dist/_data/upstream.json
  monitor_edge.py             the cloud watchdog's logic
  run_tests.py                the canonical test suite entrypoint
  verify_edge.py              27 live assertions against any base URL
  verify_renewal_policy.py    the renewal decision matrix (offline)
  verify_credential_persistence.py   credential-age persistence (offline)
  verify_filter_config.py     source.yml <-> template.yml agreement (offline)
  verify_stream_slots.mjs     dual-credential stream routing, fetch stubbed (offline)
  verify_slot_health.py       watchdog slot verdicts (offline)
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
  tuliprox-edge.yml           build + deploy (cron 4h, push, dispatch)
  edge-monitor.yml            health watchdog (cron 30 */4)
.dev.vars.example             template for local Pages dev bindings
```

Only `live/` is reachable from the shipped catalogue (the build is live-only), but `movie/` and
`series/` are kept deliberately: real Xtream clients probe them, and they must return an
**auth-gated** response rather than a bare 404.

## 2.3 How the build works

Each run of **Build & Deploy Tuliprox Xtream Edge**:

1. Downloads the official Tuliprox binary and **verifies its SHA-256** checksum
2. Validates that the client credentials are non-empty — an empty value is indistinguishable from an unset
   one at runtime, so this turns a silent misconfiguration into an explicit failure
3. Runs the **test suite** (`build/run_tests.py`) — offline, no credentials
4. **Restores the credential persisted by the previous build** (Actions cache)
5. **Seeds** the effective credential: the persisted one wins over the configured value, because it
   is *that* credential's real age which decides when renewal is due
6. **Ensures a usable credential for each slot** — reuse while healthy, else acquire a fresh trial
7. Runs **Tuliprox** one-shot: fetch → filter → normalise → `work/edge_list.m3u`
8. **Probes the provider account** so the edge can report `Expired` rather than failing mysteriously
9. Fetches the **EPG** (non-fatal if the provider's guide is unavailable)
10. **Reshapes** the output into the static indexes the edge serves
11. **Leak gate** — fails if any provider host *actually used* (either slot) appears in `dist/`
12. **Records + persists** each slot's credential (and publishes which slots are healthy), for
    the next build's cadence calculation
13. **Syncs the Pages environment** from the credential that built this deploy
14. **Deploys** to Cloudflare Pages
15. **Smoke-tests the live edge** with the same assertion set the monitor uses

## 2.4 Credential slots

Slot 1 is `PROVIDER_URL/USER/PASS` (and `UPSTREAM_*` at the edge); slot 2 adds a `_2` suffix
(`PROVIDER_2_URL`, `UPSTREAM_2_URL`, …) and is entirely optional — a single-slot deployment keeps
working untouched.

**The fact the whole feature rests on:** every slot is an account at the *same* provider, so the
catalogue — and therefore every stream id — is identical between them. `<host>/live/<user>/<pass>/<id>.ts`
resolves under either account, so any slot can serve any channel. Nothing about the playlist or the
indexes changes; only which credential a given request is served with.

**What it buys:** each trial allows `max_connections=1`, so one slot means one viewer. Two slots
mean two simultaneous viewers plus a spare account to step over.

> **Why NOT a second Tuliprox input.** The obvious move is to add a second `inputs:` entry feeding
> the same target. Don't: both accounts expose the identical catalogue, so the merged playlist would
> list every channel twice, and the edge would still serve all of it through slot 1 — `auth.js`
> builds stream URLs from the credential, not from the playlist. Double the listings, zero extra
> capacity. The second credential has to live at the **edge**, which is where it now lives.

### How a slot is chosen (per viewer, not per request)

`pickSlot()` hashes the client IP (`CF-Connecting-IP`, FNV-1a) and takes it modulo the healthy slot
count. Keying on the **viewer** rather than the request is the entire point: alternating one
player's segment requests across both accounts would occupy both connections for a single viewer
and still lock out the second device — while looking perfectly fine for the first.

### Failover, and its one honest limit

In **proxy** mode the edge retries the other slot when the first answers `401`/`402`/`5xx`. A `403`
stops immediately: that is the provider refusing *our egress*, which is not credential-specific and
would fail identically on every slot, so a second attempt is a wasted round trip.

In **redirect** mode — which is what this provider forces — the player fetches straight from the
provider, so **the edge never learns whether a stream worked and cannot fail over per request.**
That gap is covered at build time instead: each build publishes `UPSTREAM_HEALTHY_SLOTS` from the
acquisition verdicts, and `healthySlots()` keeps dead accounts out of the rotation. It fails **open**
— an empty or nonsensical hint means "use everything", because a stale hint must never strand
traffic that could have been served.

The serving slot is reported in `X-Stream-Slot` on every stream response.

## 2.5 The credential renewal engine

Renewal has three triggers, all in `build/acquire_trial.py`, evaluated per slot:

- **Cadence** — the credential in force is `RENEW_EVERY_HOURS` (24) old or older.
- **Floor** — under `MIN_HOURS_LEFT` (6) of life remains, whatever its age. Keep this above the
  build interval (4h) so a failed renewal still gets retried while the account is alive.
- **Stagger** — a slot other than 1 has its *first* renewal pulled forward to
  `RENEW_STAGGER_HOURS` (12), so the slots stop expiring in lockstep. After that first rotation it
  is out of phase and uses the normal cadence; `generation` in the state file is what distinguishes
  a freshly-bootstrapped slot from one that has already rotated.

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

## 2.6 Hard-won design constraints

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
11. **Trial accounts are `max_connections=1`.** Concurrency therefore comes from holding several
    accounts, not from anything the edge can do with one — see [§2.4](#24-credential-slots).
12. **Individual channels are unreliable** — real IPTV has dead channels. Probe several and accept
    the first success; never assert on one hardcoded channel id.

## 2.7 Testing

```bash
make test        # offline: no network, no credentials, no provider contact. Gates every build.
make test-live   # the above, plus the live assertions against the deployed production edge
```

`build/run_tests.py` is the single entrypoint and **CI runs exactly it**, so a local run and a build
can never check different things. It reports `pass / fail / skip`; only a **fail** breaks the suite,
and a **skip** is printed loudly so a missing optional tool can never pass itself off as success.

| Check | Why it exists |
|---|---|
| `verify_renewal_policy.py` | the cadence / floor / stagger decision matrix, boundaries, unknown age, the post-failure backoff, and that no case can reach the signup path. Cases are expressed *relative* to the configured thresholds, so tuning one can't silently invalidate its own test |
| `verify_filter_config.py` | `source.yml`'s `filter:` and `template.yml` agree — no undefined reference, no orphaned template that silently does nothing, every regex compiles |
| `verify_stream_slots.mjs` | the dual-credential stream path with `fetch` stubbed: per-viewer slot stability, spread across slots, failover, the 403 short-circuit, and that auth still gates everything |
| `verify_credential_persistence.py` | drives `credential_state.py` directly: slot isolation, the preserved-`acquired_at` invariant, generation counting, fail-open health, the legacy upgrade path, the backoff stamp, and Actions log masking |
| `verify_slot_health.py` | the watchdog's severity split — one dead slot warns, *every* slot dead alerts. Unprovable live without killing the real accounts |
| `py_compile` | Python syntax across `build/` |
| `node --check` | JS syntax — via a `.mjs` copy, since `node --check` parses a `.js` file as CommonJS and would wrongly reject these ES modules |
| workflows parse | a broken workflow can't be caught by CI, because CI is the thing that breaks |
| local-dev template | `.dev.vars.example` documents every binding the edge actually reads, including the optional second slot — it had silently fallen a whole feature behind |
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

## 2.8 Local development

```bash
cp .dev.vars.example .dev.vars        # then fill in real values; .dev.vars is gitignored
npx wrangler@4 pages dev dist --port 8788 --ip 127.0.0.1
BASE=http://localhost:8788 CLIENT_USER=… CLIENT_PASS=… python3 build/verify_edge.py
```

`dist/` is a build artifact — produce it by running the workflow, or by running the build steps
locally. `functions/` is bundled **from the current working directory**, not from the asset dir, so
always deploy from the repo root.

## 2.9 Configuration reference

**GitHub repository secrets** — [Settings → Secrets → Actions](https://github.com/travispflanz/tuliprox-cf-edge/settings/secrets/actions)

| Secret | Required | Purpose |
|---|---|---|
| ~~`CLIENT_USER`, `CLIENT_PASS`~~ | **moved** | now repository **variables** (see below) — do not re-create these as secrets |
| `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` | **yes** | deploy to Pages |
| `PROVIDER_URL`, `PROVIDER_USER`, `PROVIDER_PASS` | optional | bootstrap + fallback credential for slot 1 |
| `PROVIDER_2_URL`, `PROVIDER_2_USER`, `PROVIDER_2_PASS` | optional | same, for slot 2. Leave unset and the build mints slot 2 itself |

**GitHub repository variables** (non-secret) — [Settings → Variables → Actions](https://github.com/travispflanz/tuliprox-cf-edge/settings/variables/actions)

| Variable | Default | Purpose |
|---|---|---|
| `CLIENT_USER`, `CLIENT_PASS` | — | **your playlist login.** Required. Set with `gh variable set` |
| `CLIENT_2_USER`, `CLIENT_2_PASS` | unset | second viewer's login, pinned to provider slot 2. Set both or neither |
| `RENEW_EVERY_HOURS` | `24` | renewal cadence |
| `MIN_HOURS_LEFT` | `6` | renewal safety floor (keep above the 4h build interval) |
| `RENEW_STAGGER_HOURS` | `12` | how far slot 2 is held out of phase with slot 1 |
| `ACQUIRE_RETRY_HOURS` | `12` | wait this long after a FAILED signup before retrying |
| `TRIAL_POLL_TRIES` | `120` | status polls (×1.5s) to await the provider's task queue |
| `TRIAL_EMAIL_BASE` | `taskgobbler` | local part used to mint `+alias` addresses |

**Cloudflare Pages environment** — synced automatically by every build; don't edit by hand:
`CLIENT_USER`, `CLIENT_PASS`, `UPSTREAM_URL/USER/PASS` (slot 1), `UPSTREAM_2_URL/USER/PASS`
(slot 2, when configured), and `UPSTREAM_HEALTHY_SLOTS` (e.g. `1,2`) — the build's verdict on
which accounts are live, which the edge cannot determine for itself.

`STREAM_MODE` is optional: `proxy` (default) attempts an edge fetch and falls back to a redirect;
`redirect` skips the doomed fetch entirely, which is the right setting for a provider that blocks
Cloudflare's egress.

**Monitor tuning** (`build/monitor_edge.py`): `WARN_HOURS` (3) warns only when automated renewal is
*overdue* — deliberately below the 6h floor, so the normal cadence never generates noise;
`STALE_HOURS` (10) catches a stopped schedule on a 4-hour build cycle.
