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

### B. Rotate the provider account ⚠️ *the one you'll need soonest*

Provider trials last roughly **36 hours**. When one lapses the catalogue keeps serving normally
while **every stream fails** — which is the most confusing failure this system has. The monitor
warns you before it happens and alerts you if it has.

1. Get the new credentials from your provider.
2. Open https://github.com/travispflanz/tuliprox-cf-edge/settings/secrets/actions
3. For each of `PROVIDER_URL`, `PROVIDER_USER`, `PROVIDER_PASS`:
   click **Update** (or **New repository secret** if absent), paste the value, **Save**.
4. Re-run the build: **Actions** → **Build & Deploy Tuliprox Xtream Edge** → **Run workflow**.
5. Confirm: reload the §3 Option 3 URL and check for `"auth":1`.

**Your players are unaffected.** The client credentials and every playlist URL stay the same, and
the build pushes the new provider credentials into the live edge automatically. **GitHub Secrets
are the single source of truth** — you never edit the edge directly.

---

### C. Change what the playlist contains

Edit the file in the browser, commit, and the build runs automatically.

**Drop or keep categories** — `config/source.yml`, the `filter:` line:
```yaml
filter: '!ALL_CHAN! AND !DROP_SHOPPING! AND !DROP_ADULT!'
```

**Change what those templates mean** — `config/template.yml`:
```yaml
templates:
  - name: ALL_CHAN
    value: 'Group ~ ".*"'
  - name: DROP_SHOPPING
    value: 'NOT (Group ~ "(?i).*(shopping|teleshopping|home shopping).*")'
  - name: DROP_ADULT
    value: 'NOT (Group ~ "(?i).*(adult|xxx|18\\+).*")'
```
Patterns are regex matched against the category name; `(?i)` makes them case-insensitive.

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
2. Verifies the required secrets are populated (an empty secret is invisible at runtime, so this
   turns it into an explicit error)
3. Runs Tuliprox — fetch, filter, normalise → `edge_list.m3u`
4. Probes the provider account so the edge can report `Expired` instead of failing silently
5. Fetches the EPG (non-fatal if unavailable)
6. Reshapes Tuliprox's output into the static indexes the edge serves
7. **Leak gate** — fails the build if the provider's hostname appears anywhere in the publish
8. Syncs the edge's environment from repository secrets
9. Deploys to Cloudflare Pages
10. Smoke-tests the live edge with the same 27 assertions the monitor uses

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
| Catalogue loads but **every stream fails** | Provider trial expired | **§4B** |
| Player shows **Expired** / `auth: 0` | Same | **§4B** |
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
Pages, which serves a real Xtream Codes API. A second scheduled workflow watches the live edge and
the provider account, opening an issue when something needs you and closing it when things recover —
all without any local machine involved. To refresh content, re-run the build; to survive a trial
expiry (roughly every 36 hours), update the three `PROVIDER_*` secrets and re-run; to change filters,
edit the config file in the browser and commit. Players connect with the credentials in §5 and never
need changing unless you deliberately rotate them.