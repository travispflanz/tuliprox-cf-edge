# Edge Operations Runbook

**Everything here happens in a browser.** No local machine, no terminal, no CLI is required to run,
update, or monitor this system — GitHub Actions builds it, Cloudflare Pages serves it, and a
scheduled GitHub workflow watches it.

| | |
|---|---|
| **Live edge** | https://tuliprox-edge-gateway.pages.dev |
| **Build pipeline** | https://github.com/travispflanz/tuliprox-cf-edge/actions |
| **Health alerts** | https://github.com/travispflanz/tuliprox-cf-edge/issues?q=label%3Aedge-alert |
| **Secrets** | https://github.com/travispflanz/tuliprox-cf-edge/settings/secrets/actions |
| **Cloudflare** | https://dash.cloudflare.com → Workers & Pages → `tuliprox-edge-gateway` |

---

## 1. The mental model

**There is no playlist to edit on Cloudflare.** Cloudflare holds no editable state — it serves a
copy of whatever the last build produced.

```
   YOUR PROVIDER                 GITHUB ACTIONS                  CLOUDFLARE PAGES
   the source of truth  ──►  fetch → filter → reshape  ──►  a static snapshot
   (trial account)           every 6 hours                     read-only
```

So "updating the playlist" never means editing a file on the server. It means **re-running the
build** (or changing an input to it). The provider is the source of truth; the edge is a
rebuildable cache.

| You want to change… | What you actually do | Section |
|---|---|---|
| The channels (lineup shifted upstream) | Re-run the build | §4A |
| The provider account (trial expired) | Change a GitHub secret, re-run | §4B |
| Filters / live-only / drop shopping & adult | Edit a file in the GitHub web editor | §4C |
| The username/password your players use | Change a GitHub secret, re-run | §4D |

---

## 2. What runs where

Everything is cloud-hosted. Nothing depends on any particular machine.

| Function | Runs on | Schedule |
|---|---|---|
| Fetch provider → filter → build index | GitHub Actions | every 6 hours |
| Serve Xtream / M3U / EPG | Cloudflare Pages | always on |
| Deploy | GitHub Actions | on every build |
| Provider credentials storage | GitHub Secrets | — |
| **Health monitoring + alerts** | GitHub Actions (`Edge Monitor`) | every 6 hours |

If every personal computer you own were switched off for a month, this system would keep rebuilding
every 6 hours, keep serving, and still tell you if something broke.

---

## 3. Checking health (browser only)

### Option 1 — the alerts page

https://github.com/travispflanz/tuliprox-cf-edge/issues?q=label%3Aedge-alert

A scheduled workflow checks the live edge and the provider account every 6 hours:

- **Healthy** → no issue. If a previous alert existed, it is **closed automatically**.
- **Warning** → an issue titled *"Edge warning — upstream trial expiring soon"*. The edge works,
  but the provider trial runs out shortly (§4B).
- **Broken** → an issue titled *"Edge DOWN"*, and the workflow run goes red. GitHub emails you on
  a failed run.

Each issue is updated in place rather than duplicated, and its comment trail shows every re-check.

### Option 2 — the Actions tab

https://github.com/travispflanz/tuliprox-cf-edge/actions

Green tick = the last build succeeded. Click a run to see its summary, which includes the full
health report (channel counts, account expiry, deploy freshness).

### Option 3 — the live login endpoint

Paste this into any browser tab:
```
https://tuliprox-edge-gateway.pages.dev/player_api.php?username=edgeikknrzkm1z&password=Wf78l0pmKlmViOInQ9Nddca61IAs
```
Healthy response contains `"auth":1` and `"status":"Active"`. If you see `"auth":0` or
`"status":"Expired"`, the provider trial has lapsed → §4B.

An anonymous visitor to the same URL correctly gets `401`.

### What the monitor actually checks

1. **The whole edge** — reuses `build/verify_edge.py`, the same 27 assertions that gate every
   deploy (auth on every endpoint, catalogue size, Xtream key completeness, no upstream host leaked
   into the playlist, EPG, a real MPEG-TS stream, stream-auth and malformed-id handling).
2. **The provider account** — `auth`, `status` and expiry, because a lapsed account still serves
   13,651 channels while every stream fails.
3. **Deploy freshness** — if the last build is over 14 hours old, the schedule has stopped (GitHub
   disables scheduled workflows in inactive repositories), and the monitor says so explicitly.

---

## 4. Making updates (browser only)

### A. Force a rebuild — the common one

Nothing to edit; just re-run the pipeline.

1. Open https://github.com/travispflanz/tuliprox-cf-edge/actions
2. Click **Build & Deploy Tuliprox Xtream Edge** in the left sidebar
3. Click **Run workflow** ▾ → **Run workflow**
4. Wait ~4 minutes; the run turns green and smoke-tests the live edge itself

Use this whenever the provider's lineup changed and you want it reflected immediately, instead of
waiting for the next 6-hour cycle.

---

### B. The provider account — now automatic ✅

**There is nothing to do here any more.** Trials last ~36 hours, which used to be the one recurring
manual job. The build now maintains its own credential on a **24-hour cadence** — it renews once a
day, so on a 36-hour trial the replacement arrives with **6–12 hours of overlap** (typically ~9h:
builds run every 6h, so the cadence is evaluated in 6-hour steps) and there is never a window where
the playlist serves a dead account. It also keeps the rate at roughly **1 signup a day**, against the
~4 a day that signing up on every build would produce; that churn is what would risk the provider
reading this as automated abuse and withdrawing the free trial.

If a renewal should ever fail, the next build (6h later) retries it while the current credential
still has hours left. Want more runway than that? Lower `RENEW_EVERY_HOURS` (e.g. `18`) — it buys
overlap at the cost of more signups.

Two rules decide it, both in `build/acquire_trial.py`:

| Rule | Default | Meaning |
|---|---|---|
| `RENEW_EVERY_HOURS` | `24` | replace the credential once it is a day old |
| `MIN_HOURS_LEFT` | `12` | safety floor — renew regardless of age once this little life is left |

The floor is a net, not the engine: it covers a lost timestamp, a shorter-than-expected trial, or an
earlier renewal that failed.

**The credential's age is carried from one build to the next.** The workflow's own token cannot
rewrite a repository secret, so the credential *and its acquisition time* are written to
`.cred/provider.json` and persisted across runs in the workflow's Actions cache. That file is what
makes the cadence measurable — and it is why the seed secret below is only ever a bootstrap.

Acquisition needs **no email, no OTP and no browser** — the provider returns the credentials in its
own poll response. An email *address* is still required as an input, because the provider's dedup
is permanent per address, so a fresh `+alias` is minted per request. Nothing ever reads that inbox.

A lapsed trial therefore heals itself on the next build.

**To force a brand-new trial** (e.g. to exercise the acquisition path):
**Actions** → **Build & Deploy Tuliprox Xtream Edge** → **Run workflow** → tick **force_trial** →
**Run workflow**.

**Pinning a specific account by hand still works.** Set `PROVIDER_URL` / `PROVIDER_USER` /
`PROVIDER_PASS` at https://github.com/travispflanz/tuliprox-cf-edge/settings/secrets/actions. They
act as the *seed* and the *fallback*: a healthy seeded credential is reused, and any acquisition
failure falls back to them — so automatic acquisition can never turn a working build into a broken
one.

**Your players are unaffected either way** — the client credentials and every playlist URL stay
identical.

---

### C. Change what the playlist contains

Edit the file in the browser, commit, and the build runs automatically.

**Which categories are kept** — `config/source.yml`, the `filter:` line. It currently drops
non-English groups:
```yaml
filter: '!ALL_CHAN! AND !DROP_NONENG!'
```

**What those templates mean** — `config/template.yml`:
```yaml
templates:
  - name: ALL_CHAN
    value: 'Group ~ ".*"'
  - name: DROP_NONENG
    value: 'Not (Group ~ "(?i)^(DK|CH|ARG|AR|PL|AL|TR|PT|ES|BR|MX|...):")'
```
The playlist is currently **filtered to English-language groups**: `DROP_NONENG` drops any group
whose name begins with one of those region prefixes. To drop another region, add its prefix to the
list; to bring one back, remove it. (As of the last build this filter yields **10,307 channels**.)

Patterns are regex matched against the category name — `(?i)` is case-insensitive, `^…:` anchors to
the start of the name.

> An earlier iteration used `DROP_SHOPPING` / `DROP_ADULT` templates. They are no longer referenced
> by `filter:`. To reinstate them, re-add the template **and** reference it in `filter:` — a
> template that nothing references has no effect.

**Include movies and series** — `config/source.yml`, set both to `false`:
```yaml
options:
  xtream_skip_vod: false
  xtream_skip_series: false
```
> ⚠️ Expect a **multi-hundred-MB** payload. The build is live-only by design: Cloudflare Pages caps
> individual files at 25 MiB, and staying live-only keeps the edge fast and free.

**How to edit in the browser (no git tooling):**
1. Open the file, e.g. https://github.com/travispflanz/tuliprox-cf-edge/edit/main/config/template.yml
2. Click the ✏️ pencil to edit
3. **Commit changes…** → commit **directly to `main`**
4. That push triggers a rebuild automatically — watch it in the **Actions** tab

---

### D. Change the login your players use

Do this if the `edge…` credentials ever leak, or you just want new ones.

1. https://github.com/travispflanz/tuliprox-cf-edge/settings/secrets/actions
2. Update `CLIENT_USER` and `CLIENT_PASS`
3. Re-run the build (§4A)

⚠️ **Unlike §4B, this one does break your clients.** These credentials are embedded in the playlist
URLs, so **every player must be re-pointed** afterwards.

---

## 5. Connecting a player

**Xtream login** (preferred — gives categories, logos and EPG):
```
Server URL:  https://tuliprox-edge-gateway.pages.dev
Username:    edgeikknrzkm1z
Password:    Wf78l0pmKlmViOInQ9Nddca61IAs
```

**M3U playlist:**
```
https://tuliprox-edge-gateway.pages.dev/get.php?username=edgeikknrzkm1z&password=Wf78l0pmKlmViOInQ9Nddca61IAs&type=m3u_plus&output=ts
```

**EPG:** usually picked up automatically from the playlist header. Explicit URL:
```
https://tuliprox-edge-gateway.pages.dev/xmltv.php?username=edgeikknrzkm1z&password=Wf78l0pmKlmViOInQ9Nddca61IAs
```

> **After any update, refresh the playlist inside the player.** Players cache it, so a change can
> look like it didn't happen until you reload or re-add the playlist.

---

## 6. How the build works

Each run of **Build & Deploy Tuliprox Xtream Edge**:

1. Downloads the official Tuliprox binary and verifies its SHA-256 checksum
2. Verifies the client secrets are populated (an empty secret is invisible at runtime, so this
   turns it into an explicit error)
3. **Ensures a usable provider credential** — reuses the current one while it is healthy, otherwise
   acquires a fresh trial from the provider's API (§4B)
4. Runs Tuliprox — fetch, filter, normalise → `edge_list.m3u`
5. Probes the provider account so the edge can report `Expired` instead of failing silently
6. Fetches the EPG (non-fatal if unavailable)
7. Reshapes Tuliprox's output into the static indexes the edge serves
8. **Leak gate** — fails the build if the provider host actually used appears anywhere in the publish
9. Syncs the edge's environment from the credential that built this deploy
10. Deploys to Cloudflare Pages
11. Smoke-tests the live edge with the same 27 assertions the monitor uses

**Two design decisions worth knowing:**

- **Tuliprox's own Xtream output can't be shipped.** It writes a binary B+Tree only its own server
  can read, and Cloudflare Pages runs no such server. The build indexes the M3U Tuliprox emits
  alongside it. Tuliprox still does all the fetching, filtering and normalising.
- **Stream URLs are rebuilt at request time** from the channel id via environment variables, so
  **provider credentials are never written into any published file** — a leaked static asset
  cannot leak the provider account.

**On playback:** the provider refuses datacenter IP ranges, and Cloudflare's egress is one of them,
so the edge cannot proxy the video (it gets `403` on every channel). Instead it authenticates the
player and returns a `302`, letting the player fetch the stream from its own residential IP —
exactly how the provider intends clients to work. This is a deliberate workaround, not a fault.

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Catalogue loads but **every stream fails** | Provider trial expired | Self-healing: the next build acquires a new trial (§4B). To do it now — **§4A** and tick **force_trial** |
| Player shows **Expired** / `auth: 0` | Provider trial expired | Same — auto-renewed on the next build |
| An **edge-alert** issue is open | Monitor found a problem | Read the issue — it states the cause |
| **401 Unauthorized** | Wrong client credentials | Use the §5 credentials (or see §4D) |
| Playlist loads but **no channels** | Build failed or deployed empty | Check the **Actions** tab for a red run |
| **"Edge DOWN"** issue | Verifier failing | Open the issue → linked run → read the failed step |
| **Stale deploy** warning | Scheduled builds stopped | **§4A** to re-run; GitHub pauses schedules in inactive repos |
| Channels **missing** that used to exist | Provider dropped them, or a filter removed them | Review `config/template.yml`, then **§4A** |
| **Changes don't appear** in the player | Player cached the playlist | Refresh/reload the playlist in the player |

**Red build?** Open the run in the **Actions** tab and expand the failing step. The most common
causes are an empty/corrupt secret (§4B) or the leak gate firing (never bypass it — inspect the
build instead).

---

## 8. Rules

| Don't | Why |
|---|---|
| **Edit the deployed files on Cloudflare** | They are regenerated on every build; your edits vanish |
| **Bypass the credential leak gate** | It exists so a published file can never expose the provider account |
| **Commit `.env` / `.dev.vars`** | Secrets belong in GitHub Secrets, never in the repository |
| **Expect instant updates** | A rebuild takes ~4 minutes, and players cache the playlist |
| **Delete the `edge-alert` label** | The monitor uses it to open, update and auto-close alerts |

---

## 9. One-paragraph summary

GitHub Actions rebuilds this edge from your provider every 6 hours and deploys it to Cloudflare
Pages, which serves a real Xtream Codes API. The build also maintains its **own** provider
credential: it reuses a healthy one and, when that lapses, acquires a fresh trial straight from the
provider's API — no email, no OTP, no browser — so there is no recurring credential rotation left to
perform. A second scheduled workflow watches the live edge and the provider account, opening an issue
when something needs you and closing it when things recover; it runs in the cloud too. To refresh
content, re-run the build; to change filters, edit the config file in the browser and commit. Players
connect with the credentials in §5 and never need changing unless you deliberately rotate them.