# AGENTS.md — read this first

You are an AI agent picking up a **live production system**. It is small, it is fully
automated, and it serves real people's televisions. Read this page before touching anything;
it tells you where the truth lives, what is already known, and which mistakes have already
been paid for.

> **The single most useful thing on this page:** the suite is `make test`. If you have not run
> it, you do not know whether your change works. If you changed `functions/` or `build/` and
> only ran that, you still do not know whether the live edge works — see "Verifying".

---

## 1. What this project is

A serverless IPTV gateway. GitHub Actions fetches a provider's channel list every 6 hours,
filters and reshapes it, and publishes it to Cloudflare Pages, which serves a real **Xtream
Codes API**. The build also **renews its own provider trial account**. A second workflow
watches the deployed edge and opens a GitHub issue when it breaks.

There is no server and no local machine in the loop. If it is not in git, it is not deployed.

| | |
| :--- | :--- |
| Live edge | `https://tuliprox-edge-gateway.pages.dev` |
| Repo | `travispflanz/tuliprox-cf-edge` (private) |
| Deploys | `wrangler pages deploy dist`, from `.github/workflows/tuliprox-edge.yml` |
| Pages project | `tuliprox-edge-gateway`, **direct upload** — no Cloudflare Git integration, so nothing deploys except Actions |

## 2. Where the truth lives

Read in this order. Do not read all of them at once.

| Question | Read |
| :--- | :--- |
| How do I operate it / fix a player? | `README.md` Part 1 |
| How does it work internally, and why is it built this way? | `README.md` Part 2 |
| Who owns what, what are the secrets, what is risky? | `README.md` Part 3 |
| How do I ship a change? | `CONTRIBUTING.md` |
| What is the current state and what is unfinished? | `README.md` §3.2 and §3.7 |

`README.md` is the primary document and is deliberately comprehensive. Do not create parallel
documentation — a second copy of the truth is worse than none, and this repo has already
deleted stale documents once for exactly that reason.

## 3. Ground rules

1. **Never make the pipeline worse than you found it.** The credential renewal is
   non-fatal by design so automation can never break a working build. Preserve that property.
2. **Never commit a credential.** Live provider credentials belong in `.cred/` (gitignored,
   carried between builds in an Actions cache) or in Actions secrets. The suite enforces both.
3. **One credential, one store.** The same name must not exist as both a variable and a
   secret. When it did, the monitor read the superseded one and cried wolf while the edge was
   healthy.
4. **Do not raise the signup rate.** Renewal reuses a healthy credential on purpose; a naive
   "renew every build" would be ~4 trials/day and risks the provider withdrawing free trials
   from an account it reads as abusive. The rule is two numbers — a cadence and a floor — and
   the invariant that makes it safe is `check_interval < margin`.
5. **Do not "improve" the stream path without reading §2.5.3.** The provider refuses
   datacenter IPs, so the edge 302s to the provider instead of proxying. That looks like a
   bug and is a deliberate workaround.
6. **`config/` is the operator's file.** Direct-to-main edits there are the documented
   workflow. Do not route them through a PR or "tidy" someone's regex.

## 4. Verifying

```bash
make test        # offline: no network, no credentials, no provider contact. Gates deploys.
make test-live   # the above + 27 assertions against the DEPLOYED edge
```

Two things to internalise:

- **`build/run_tests.py` is the only entrypoint.** CI runs exactly it. Add new checks *there*,
  never inline in a workflow, or CI and local runs will drift.
- **To prove a check works, break the thing it checks.** Inject the failure, watch it report
  FAIL, then restore — **with a file copy, not `git checkout --`**, which restores from the
  index and destroys any unstaged work in that file.

If your `python3` lacks PyYAML, set `YAML_PYTHON=/path/to/python`. Do not install anything
into the repo, and do not hardcode a machine-specific path into a tracked file.

## 5. Shipping a change

`main` **is** production: a push to it deploys. So:

```bash
git switch -c fix/description
make test
git add -A && git commit -F -        # -F - : backticks in -m get executed by bash
git push -u origin fix/description
gh pr create --fill                  # CI verifies on the PR
gh pr merge --squash --delete-branch # merge => deploy
```

Watch the resulting run in Actions. It smoke-tests the live edge before going green — do not
report success until that run is green.

**Not everything needs a PR:** `config/` changes go straight to `main` by design. Everything
else should go through a PR. GitHub cannot enforce that here (branch protection and rulesets
are 403 on a private Free repo) — `CONTRIBUTING.md` explains the situation and how to change
it. `.github/workflows/main-guard.yml` reports bypasses; it cannot block them.

## 6. Known traps

Each of these has already caused a real incident. The full list, with the reasoning, is in
`README.md` §2.5 and §2.6.

- **A cached Actions cache entry is immutable per key.** The credential is carried forward
  with a rolling key (`provider-cred-<run_id>`) plus a prefix restore. Do not change it to a
  fixed key "for simplicity" — that freezes the credential forever.
- **Never re-stamp a credential's acquisition time while the credential is unchanged.**
  Doing so makes the account look permanently new, the cadence never comes due, and renewal
  silently stops. Both directions of getting this wrong pass a green build.
- **`gh` prints its error body to `stdout`.** `val="$(gh api ... || true)"` captures a JSON
  error document, not a failure. Only trust stdout when `gh` exited 0.
- **Never hand-set `Content-Encoding`.** Cloudflare compresses what you ship; setting `gzip`
  on a pre-gzipped body double-compresses it and every player decodes garbage.
- **Never probe Cloudflare with bare `python-urllib`** — its User-Agent trips a `403` (error
  code 1010) that is indistinguishable from a real outage. Send a real UA.
- **GitHub cron is best-effort and drifts.** Measured gaps here have exceeded 10 hours against
  a 6-hour schedule. Treat the schedule as an intention, never as a guarantee.
- **Pages serves a real file directly without running a Function.** The auth gate lives in
  `functions/_middleware.js`, which runs on every request including static-asset hits.
- **README.md claims are load-bearing.** The suite checks that no tracked doc references a
  non-existent file, so if you delete a file, fix the prose in the same commit.

## 7. Do not touch

| Thing | Why |
| :--- | :--- |
| `backup/pre-rollback` branch | Holds the pre-rollback dual-slot history. Deleting it loses that work. |
| Other Cloudflare Pages projects | `webworkskc*` belong to a different project. Only `tuliprox-edge-gateway` is ours. |
| Deployed files on Cloudflare | They are regenerated by every build; edits vanish. |
| The `edge-alert` label | The monitor uses it to open, update and auto-close alerts. |
| The credential leak gate | Never bypass it; inspect the build instead. |
| Secrets / `.dev.vars` / `.cred/` | Read their **names** when you need to reason about the system. Do not print values into a log or a document. |

## 8. If something is broken

1. Read the open `edge-alert` issue — it names the cause and links the run.
2. Open the failing run and expand the **failed step**. Never re-run blindly first; the
   failure is usually explicit (an empty secret, or the leak gate).
3. If the account lapsed: it self-heals on the next build, or force one with a dispatch
   ticking `force_trial`.
4. If the schedule itself stopped: re-run the workflow. GitHub pauses cron in repos it
   considers inactive, and the monitor's staleness check is how that is noticed.

Report what you actually observed. If a run is still in flight, say so rather than reporting
its outcome.
