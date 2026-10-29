# Contributing

This repo is operated by one person and a set of AI agents, and it runs a **live service**.
These rules exist so that a change cannot quietly take the edge down or spend provider
signups, and so that whoever picks this up next (human or agent) can tell what is safe.

- **Operating it** (browser only, no terminal) → `README.md` Part 1.
- **Understanding it** (architecture, build sequence, design constraints) → `README.md` Part 2.
- **Taking it over** (state, ownership, secrets, risks) → `README.md` Part 3.
- **An AI agent starting cold** → `AGENTS.md` first.

---

## The short version

Work on a branch, let CI verify it, merge to `main`. **Merging to `main` deploys the live
edge** — that is the release, there is no separate deploy step.

```bash
git switch -c fix/short-description
# ... edit ...
make test                      # offline: no network, no credentials, no provider contact
git add -A && git commit -F -  # -F - because a message containing `backticks` breaks -m
git push -u origin fix/short-description
gh pr create --fill            # CI runs on the PR automatically
gh pr merge --squash --delete-branch   # merge => deploy; then watch the run
```

## Not everything needs a pull request

The process is deliberately **tiered**, because flagging everything gets ignored:

| Class | Paths | Process |
| :--- | :--- | :--- |
| **Operating input** | `config/` | **Direct to `main` is the designed path.** README §1.6 tells the operator to edit `config/template.yml` in the browser and commit; the push rebuilds. No PR needed. |
| **Code** | `build/`, `functions/`, `.github/`, `Makefile` | **Branch → PR → CI green → merge.** A bad edit here can take the live edge down. |
| **Docs** | `README.md`, `CONTRIBUTING.md`, `AGENTS.md`, `.github/pull_request_template.md` | PR, but only because docs are what everyone trusts later. Review is about accuracy, not risk. |

`.github/workflows/main-guard.yml` reports when code reached `main` outside a pull request.
It **cannot block** — see the next section for why.

## GitHub cannot enforce the review rule here

The obvious move is branch protection with a required status check. It is **not available**:

```
$ gh api repos/travispflanz/tuliprox-cf-edge/branches/main/protection
{"message":"Upgrade to GitHub Pro or make this repository public to enable this feature.","status":403}

$ gh api repos/travispflanz/tuliprox-cf-edge/rulesets
{"message":"Upgrade to GitHub Pro or make this repository public to enable this feature.","status":403}
```

Both classic branch protection **and** rulesets are gated on the Free plan for **private**
repos. So the rule is held by convention plus a visible report, not by GitHub.

**To make it enforced**, in order of preference:

1. **Make the repo public** — rulesets become free, and this is a repo of config and glue
   with no secrets in it (everything sensitive lives in Actions secrets and Pages env).
2. **Move to GitHub Pro** — then keep it private and enable protection anyway.
3. Then add a ruleset requiring **a pull request** and the required check
   **`offline suite — no secrets, no network`** (that is the job name in `.github/workflows/ci.yml`).
4. Or, immediately and without changing plans: set the repository variable
   **`STRICT_MAIN_GUARD=true`**, which turns the advisory into a failing job. Read the caveat
   in that workflow's header first — a red run does **not** undo a push, it only records that
   the rule was skipped.

## What CI runs, and what it cannot

`.github/workflows/ci.yml` runs on every pull request. It holds **no secrets** and touches
**no network**, so it is safe on any branch.

It runs one command — `python3 build/run_tests.py` — which is the same thing `make test`
runs locally. That is intentional: **one entrypoint, so CI and a local run can never
disagree.** Never duplicate a check inline in a workflow; add it to the suite.

**What that covers:** Python and JS syntax, both behaviour harnesses (renewal policy,
credential persistence), that every workflow parses, that no tracked doc references a
deleted file, that no plaintext credential appears in tracked docs or config, and that the
secret files are untracked.

**What it does not cover, and why:**

| Not covered | Where it is covered instead |
| :--- | :--- |
| Does the live edge actually serve? | `make test-live`, and the smoke test at the end of every deploy |
| Does the deployed build still authenticate? | `edge-monitor.yml`, every 6 hours, which opens an `edge-alert` issue |
| Is the provider account alive? | The build probes it and the monitor judges the result |
| Can the auto-renewal really sign up? | Only `force_trial` exercises that path — it is a manual dispatch on purpose |

A **SKIP** in the suite is printed loudly and does not fail the run. It means an optional
tool was missing (usually node), **not** that the check passed. Read the log.

## Rules that bite — all of these have already cost real time

1. **Prove a new check catches its bug.** Inject the failure, watch it report FAIL, then
   restore. A check that has never failed has not been shown to work.
2. **Restore with a file copy, never `git checkout -- <file>`.** `git checkout` restores from
   the *index*, so any unstaged work in that file is destroyed along with your injection.
   `cp file file.bak` first, or `git add` the real work before injecting.
3. **`node --check` parses a `.js` file as CommonJS** and therefore rejects `export`/`import`
   in `functions/` outright. Copy to a `.mjs` temp file to force the ESM parser (the suite
   already does this).
4. **Use `python3`, not `jq`, for JSON inside workflow `run:` blocks.** python3 is already a
   hard dependency, and — the real payoff — the block can then be executed off-runner by the
   persistence harness instead of only inside CI.
5. **Keep GitHub expressions out of `run:` bodies** — put them under `env:`. A block with
   `${{ }}` inside it cannot be executed by the harness, which is why
   `verify_credential_persistence.py` fails loudly when it meets one rather than silently
   expanding it to `""`.
6. **Never put backticks in `git commit -m "..."`.** Bash runs the content as command
   substitution, so the message is silently wrong. Use `-F -`.
7. **Never rename an existing step in `tuliprox-edge.yml`.** `verify_credential_persistence.py`
   locates steps by name and executes their real `run:` bodies. A rename breaks the harness.
8. **Adding a workflow is safe; adding a *step* to the deploy job is not.** Anything you add
   to `tuliprox-edge.yml` runs in production, with secrets, on a 6-hour timer.

## Commit messages

Conventional-ish prefixes, matching the existing log: `feat:`, `fix:`, `docs:`, `test:`,
`chore:`, `ci:`. Subject in the imperative, under ~72 characters. Explain **why** in the body
when the reason is not obvious — for this project the reason is usually a bug that was
already paid for once.

## Before you push

- [ ] `make test` is green locally.
- [ ] If you touched `functions/` or `build/`: `make test-live` passes, or you have said why not.
- [ ] If you touched `README.md`: the suite's documentation-integrity check still passes
      (every file it references must exist, and no plaintext credential may be committed).
- [ ] You have **not** committed `.dev.vars`, `.cred/`, or anything pasted out of a run log.
      A credential in git history cannot be unsent — rotating it is the only real fix.

## Don'ts

| Don't | Why |
| :--- | :--- |
| Force-push `main` | It is the deploy trigger and the deployed commit. A force-push can silently revert production. |
| Bypass the credential leak gate | It exists so a published file can never expose the provider account. |
| Commit `dist/` | It is a build artifact, produced fresh by every deploy. |
| Add a check inline to a workflow | Duplicated logic drifts. Add it to `build/run_tests.py`. |
| Delete `backup/pre-rollback` | It holds the pre-rollback history of the dual-slot design. |
