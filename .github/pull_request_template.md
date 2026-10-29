<!--
Small changes to config/ do NOT need a pull request — editing config/template.yml in the
browser and committing to main is the designed operating path (README Part 1). Use this
template for build/, functions/, .github/ and documentation changes. See CONTRIBUTING.md.
-->

## What and why

<!-- One or two sentences. If this fixes an `edge-alert` issue, link it. -->

## Change class

- [ ] **Code** — `build/`, `functions/`, `.github/`, `Makefile` (needs this PR + green CI)
- [ ] **Docs** — `README.md`, `CONTRIBUTING.md`, `AGENTS.md`
- [ ] **Operating input** — `config/` (if you opened a PR for it anyway)

## Verification

- [ ] `make test` passes locally — offline: no network, no credentials
- [ ] If `functions/` or `build/` changed: `make test-live` run, **or** a note below on why not
- [ ] If I added or changed a check, I **injected the failure first and watched it fail** before
      restoring the file (a check that has never failed has not been proven to work)
- [ ] If the process changed, `README.md` / `CONTRIBUTING.md` / `AGENTS.md` were updated in this PR

## Runtime risk

Does this change what the live edge serves, or how it authenticates?

- [ ] **No** — docs and CI only
- [ ] **Yes** — and the deploy after merge is intended; I will watch the run and its smoke test
- [ ] **Yes** — and it touches the credential path, renewal timing, or the leak gate

**Notes for the reviewer:**

<!-- Optional. Anything you are unsure about belongs here rather than in a DM. -->
