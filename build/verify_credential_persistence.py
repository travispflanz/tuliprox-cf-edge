#!/usr/bin/env python3
"""verify_credential_persistence.py — exercises the workflow's OWN shell for credential state.

WHY THIS IS PINNED
------------------
The 24h renewal cadence only works if the build carries a credential's real age from one run to the
next. Repository secrets cannot be rewritten by GITHUB_TOKEN, so the credential is persisted in a
rolling Actions cache and the age lives in `.cred/provider.json`. Two failures there are invisible:

  - The state is not carried forward -> the seed ages out, every build mints a new trial, and the
    signup churn goes straight back to ~4/day — the exact thing this design exists to avoid.
  - The acquisition time is re-stamped on every build -> the credential always looks brand new, the
    cadence never comes due, and the renewal silently never happens until streams start failing.

Neither shows up in a green CI run, so the real `run:` blocks are extracted from the workflow YAML
and executed here against temp workspaces. Stdlib only, no network, no signup, no credentials.

Run: python3 build/verify_credential_persistence.py
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "tuliprox-edge.yml"
GITIGNORE = REPO_ROOT / ".gitignore"

SEED_STEP = "Seed provider credentials from the persisted file or repository secrets"
RECORD_STEP = "Record the credential in force for the next build"

text = WORKFLOW.read_text(encoding="utf-8")
failures: list[str] = []
checks = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not ok:
        failures.append(f"{name} — {detail}")
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail and not ok else ""))


def extract_run_block(step_name: str) -> str:
    """Pull a step's `run: |` body out of the workflow. Fails loudly rather than returning ''."""
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.strip() == f"- name: {step_name}"), None)
    if start is None:
        raise SystemExit(f"FAIL: step not found in the workflow: {step_name}")
    j = start + 1
    while j < len(lines) and not lines[j].strip().startswith("run:"):
        if lines[j].lstrip().startswith("- name:"):
            raise SystemExit(f"FAIL: step {step_name!r} has no run block")
        j += 1
    if j >= len(lines) or lines[j].strip() != "run: |":
        raise SystemExit(f"FAIL: step {step_name!r} does not start with 'run: |'")
    body, j = [], j + 1
    while j < len(lines):
        ln = lines[j]
        if ln.strip():
            if len(ln) - len(ln.lstrip()) <= 8:
                break
            body.append(ln[10:] if len(ln) > 10 else "")
        else:
            body.append("")
        j += 1
    return "\n".join(body).rstrip() + "\n"


def subst(script: str, values: dict[str, str]) -> str:
    """Stand in for GitHub's expression expansion; unknown expressions fail loudly."""
    def repl(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        if key not in values:
            raise SystemExit(f"FAIL: unhandled workflow expression: {match.group(0)!r}")
        return values[key]
    return re.sub(r"\$\{\{\s*([^}]+?)\s*\}\}", repl, script)


def run_step(script: str, workdir: pathlib.Path, extra_env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    for k in ("PROVIDER_URL", "PROVIDER_USER", "PROVIDER_PASS",
              "UPSTREAM_URL", "UPSTREAM_USER", "UPSTREAM_PASS"):
        env.pop(k, None)
    env.update(extra_env)
    env["GITHUB_WORKSPACE"] = str(workdir)
    env["GITHUB_ENV"] = str(workdir / "github_env")
    (workdir / "github_env").touch()
    return subprocess.run(["bash", "-c", script], cwd=workdir, env=env,
                          capture_output=True, text=True)


def exported(workdir: pathlib.Path) -> dict[str, str]:
    vals: dict[str, str] = {}
    for line in (workdir / "github_env").read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            vals[k] = v
    return vals


def state(workdir: pathlib.Path) -> dict:
    p = workdir / ".cred" / "provider.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


SEED_BLOCK = extract_run_block(SEED_STEP)
RECORD_BLOCK = extract_run_block(RECORD_STEP)
# The record step receives these through `env:` (GitHub expressions), not shell interpolation, so
# they are supplied via the environment. subst() stays strict: any new ${{ ... }} inside a run block
# fails loudly rather than being silently dropped.
RECORD_EXPRS: dict[str, str] = {}
RECORD_ENV = {"NEW_EXP": "", "SOURCE": "reused"}

SEED_URL, SEED_USER, SEED_PASS = "http://seed.panel", "SEEDUSER", "seedpass"

print("=== static wiring ===")
check("a restore step for the persisted credential exists",
      "Restore the credential persisted by the previous build" in text
      and "restore-keys: provider-cred-" in text)
check("a save step publishes the credential for the next build",
      "actions/cache/save@v4" in text
      and "key: provider-cred-${{ github.run_id }}" in text)
check("renewal cadence is wired into the acquisition step",
      "RENEW_EVERY_HOURS: ${{ vars.RENEW_EVERY_HOURS || '24' }}" in text
      and "MIN_HOURS_LEFT: ${{ vars.MIN_HOURS_LEFT || '12' }}" in text)
check("the default acquisition path is --if-needed, with --force only under force_trial",
      '--if-needed' in text and 'inputs.force_trial' in text)
check(".cred is gitignored (it carries provider credentials)",
      any(ln.strip() == ".cred/" for ln in GITIGNORE.read_text(encoding="utf-8").splitlines()))

print("\n=== bootstrap: no state file yet ===")
w1 = pathlib.Path(tempfile.mkdtemp(prefix="credpersist-"))
seed_env = {"PU": SEED_URL, "PUN": SEED_USER, "PP": SEED_PASS}
p = run_step(SEED_BLOCK, w1, seed_env)
check("seed step succeeds on a fresh workspace", p.returncode == 0, p.stderr.strip()[:200])
check("it reports bootstrapping from repository secrets",
      "repository secrets (bootstrap)" in p.stdout, p.stdout.strip()[:200])
exp = exported(w1)
check("the seeded credential is exported to GITHUB_ENV",
      exp.get("PROVIDER_URL") == SEED_URL and exp.get("UPSTREAM_USER") == SEED_USER,
      str(exp))

p = run_step(subst(RECORD_BLOCK, RECORD_EXPRS), w1,
             {"PROVIDER_URL": SEED_URL, "PROVIDER_USER": SEED_USER, "PROVIDER_PASS": SEED_PASS}
             | RECORD_ENV)
check("record step succeeds", p.returncode == 0, p.stderr.strip()[:200])
s1 = state(w1)
check("it persists url/user/pass and an acquisition time",
      s1.get("url") == SEED_URL and s1.get("user") == SEED_USER
      and s1.get("pass") == SEED_PASS and bool(s1.get("acquired_at")),
      str(s1))

print("\n=== the invariant: age is PRESERVED while the credential is unchanged ===")
p = run_step(subst(RECORD_BLOCK, RECORD_EXPRS), w1,
             {"PROVIDER_URL": SEED_URL, "PROVIDER_USER": SEED_USER, "PROVIDER_PASS": SEED_PASS}
             | RECORD_ENV)
s2 = state(w1)
check("a later build does NOT re-stamp acquired_at", s2.get("acquired_at") == s1.get("acquired_at"),
      f"{s1.get('acquired_at')} -> {s2.get('acquired_at')}")
check("it still reuses the same credential", s2.get("user") == SEED_USER, str(s2))

print("\n=== a NEW credential resets the age ===")
time.sleep(1.1)
p = run_step(subst(RECORD_BLOCK, RECORD_EXPRS), w1,
             {"PROVIDER_URL": "http://new.panel", "PROVIDER_USER": "NEWUSER", "PROVIDER_PASS": "newpass"}
             | RECORD_ENV)
s3 = state(w1)
check("a replacement credential gets a fresh acquired_at",
      s3.get("user") == "NEWUSER" and s3.get("acquired_at") != s1.get("acquired_at"),
      f"{s1.get('acquired_at')} -> {s3.get('acquired_at')}")

print("\n=== the next run restores the persisted credential, not the aging secret ===")
w2 = pathlib.Path(tempfile.mkdtemp(prefix="credpersist-"))
(w2 / ".cred").mkdir()
(w2 / ".cred" / "provider.json").write_text(json.dumps(
    {"url": "http://stored.panel", "user": "STOREDUSER", "pass": "storedpass",
     "acquired_at": "2026-01-01T00:00:00Z"}), encoding="utf-8")
p = run_step(SEED_BLOCK, w2, seed_env)
exp2 = exported(w2)
check("seed step prefers the persisted credential over the secret",
      exp2.get("PROVIDER_URL") == "http://stored.panel" and exp2.get("PROVIDER_USER") == "STOREDUSER",
      str(exp2))
check("it says where the credential came from",
      "persisted by the previous build" in p.stdout, p.stdout.strip()[:200])

print("\n=== a damaged state file degrades to the secret, never to a broken build ===")
w3 = pathlib.Path(tempfile.mkdtemp(prefix="credpersist-"))
(w3 / ".cred").mkdir()
(w3 / ".cred" / "provider.json").write_text(json.dumps({"url": "http://stored.panel"}), encoding="utf-8")
p = run_step(SEED_BLOCK, w3, seed_env)
exp3 = exported(w3)
check("an incomplete persisted credential falls back to the secret",
      p.returncode == 0 and exp3.get("PROVIDER_URL") == SEED_URL, str(exp3))
check("and it warns rather than failing silently",
      "incomplete" in p.stdout, p.stdout.strip()[:200])

print(f"\n=== {checks - len(failures)}/{checks} checks passed ===")
for f in failures:
    print(f"FAILED: {f}")
sys.exit(1 if failures else 0)