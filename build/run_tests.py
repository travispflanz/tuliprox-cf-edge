#!/usr/bin/env python3
"""run_tests.py — the canonical test suite for this repo.

Run it with `make test` (offline) or `make test-live` (offline + the live production edge).

WHY THIS EXISTS
---------------
This repo had no single test command, so every verification was an ad-hoc probe written from
scratch and thrown away — which means nothing was ever actually pinned, and a check that mattered
could silently stop being run. This is the one entrypoint: CI calls exactly this, so CI and a local
run can never diverge.

GROUPS
  offline  no network, no credentials, no provider contact. Safe to run anywhere, any number of
           times. This is what gates every build.
  live     probes the deployed edge with real client credentials. Opt-in (`--live`), because it
           needs secrets and a deployed artifact.

A check reports pass / fail / skip. Only a FAIL fails the suite; a SKIP is reported loudly so a
missing optional dependency can never masquerade as success.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
PROD = "https://tuliprox-edge-gateway.pages.dev"
# Optional escape hatch for a workstation whose default python3 lacks PyYAML: point YAML_PYTHON at
# an interpreter that has it. Deliberately NOT a hardcoded path — nothing tracked in this repo may
# depend on one particular machine's filesystem.
INTERPRETER_WITH_YAML = os.environ.get("YAML_PYTHON", "")

PASS, FAIL, SKIP = "pass", "fail", "skip"
results: list[tuple[str, str, str]] = []


def record(name: str, state: str, detail: str = "") -> None:
    mark = {PASS: "PASS", FAIL: "FAIL", SKIP: "SKIP"}[state]
    results.append((name, state, detail))
    suffix = f"   [{detail}]" if detail and state != PASS else ""
    print(f"  {mark}  {name}{suffix}", flush=True)


def run(cmd: list[str], env: dict | None = None, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    merged = {**os.environ, **(env or {})}
    return subprocess.run(cmd, cwd=REPO, capture_output=True, text=True,
                          env=merged, timeout=timeout)


def dev_vars() -> dict[str, str]:
    """Client credentials for the live group, from the gitignored .dev.vars. Never printed."""
    path = REPO / ".dev.vars"
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            key, val = line.split("=", 1)
            out[key.strip()] = val.strip().strip('"').strip("'")
    return out


# --------------------------------------------------------------------------- offline group
def offline() -> None:
    print("\n=== offline: behaviour harnesses ===")
    # These two encode the rules that fail INVISIBLY in both directions — churning free trials at
    # the provider, or letting the credential lapse while the catalogue keeps serving.
    for script in ("build/verify_renewal_policy.py", "build/verify_credential_persistence.py"):
        proc = run([sys.executable, script])
        summary = next((ln for ln in proc.stdout.splitlines() if "checks passed" in ln), "")
        # Surface the harness's OWN failure lines. Reporting only the count forces whoever sees a
        # red build to go digging through raw Actions logs to learn which assertion broke.
        detail = " | ".join(ln.strip() for ln in proc.stdout.splitlines()
                            if ln.startswith("FAILED:") or "  FAIL  " in ln)
        record(script, PASS if proc.returncode == 0 else FAIL,
               f"rc={proc.returncode} {summary or proc.stdout[-200:]} "
               f"{detail} {proc.stderr[-200:]}".strip())

    print("\n=== offline: python syntax ===")
    py = sorted(p.relative_to(REPO).as_posix() for p in (REPO / "build").glob("*.py"))
    proc = run([sys.executable, "-m", "py_compile", *py])
    record(f"py_compile ({len(py)} modules)", PASS if proc.returncode == 0 else FAIL,
           proc.stderr.strip()[:300])

    print("\n=== offline: edge stream routing (fetch stubbed) ===")
    node = shutil.which("node")
    if not node:
        record("verify_stream_slots.mjs", SKIP, "node is not installed here (CI has it)")
    else:
        proc = run([node, "build/verify_stream_slots.mjs"])
        summary = next((ln for ln in proc.stdout.splitlines() if "checks passed" in ln), "")
        record("verify_stream_slots.mjs", PASS if proc.returncode == 0 else FAIL,
               f"rc={proc.returncode} {summary} :: {proc.stderr.strip()[:200]}".strip())

    print("\n=== offline: javascript syntax ===")
    if not node:
        record("node --check", SKIP, "node is not installed here (CI has it)")
    else:
        # Everything here is an ES module (Pages Functions and build/*.mjs alike), but
        # `node --check` parses a .js file as CommonJS and so rejects `export`/`import` outright.
        # Checking through a .mjs copy forces the ESM parser, which is the grammar these files
        # actually run under.
        js = sorted([*(REPO / "build").glob("*.mjs"), *(REPO / "functions").rglob("*.js")])
        bad = []
        with tempfile.TemporaryDirectory() as tmp:
            for idx, path in enumerate(js):
                probe = pathlib.Path(tmp) / f"probe{idx}.mjs"
                probe.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
                proc = run([node, "--check", str(probe)])
                if proc.returncode != 0:
                    bad.append(f"{path.relative_to(REPO)}: {proc.stderr.strip().splitlines()[-1][:80]}"
                               if proc.stderr.strip() else str(path.relative_to(REPO)))
        record(f"node --check, ESM ({len(js)} files)", FAIL if bad else PASS, "; ".join(bad))

    print("\n=== offline: workflows parse ===")
    # A broken workflow cannot be caught by CI (CI is the thing that breaks), so it is checked
    # here. Any interpreter with PyYAML will do — this machine's system python3 lacks it while
    # the agent venv has it, and CI runners ship it.
    candidates = [sys.executable, shutil.which("python3") or "", INTERPRETER_WITH_YAML]
    parser = next((p for p in candidates
                   if p and run([p, "-c", "import yaml"]).returncode == 0), None)
    for wf in sorted((REPO / ".github" / "workflows").glob("*.yml")):
        if not parser:
            record(f"{wf.name} parses", SKIP, "no interpreter with PyYAML found")
            continue
        proc = run([parser, "-c",
                    "import yaml,sys; d=yaml.safe_load(open(sys.argv[1]));"
                    "assert d.get('jobs'), 'no jobs key';"
                    "assert all(s.get('run') or s.get('uses')"
                    "           for j in d['jobs'].values() for s in j.get('steps', [])),"
                    "       'a step has neither run nor uses';"
                    "print('ok')", str(wf)])
        record(f"{wf.name} parses", PASS if proc.returncode == 0 else FAIL,
               (proc.stderr.strip() or proc.stdout.strip())[:300])

    print("\n=== offline: filter config consistency ===")
    # The filter is split across two hand-edited files that must agree. Needs PyYAML, so it runs
    # under whichever interpreter has it (see the workflow-parse note above).
    if parser:
        proc = run([parser, "build/verify_filter_config.py"])
        summary = next((ln for ln in reversed(proc.stdout.splitlines()) if ln.strip()), "")
        record("source.yml filter and template.yml agree",
               PASS if proc.returncode == 0 else FAIL,
               f"{summary} :: {proc.stderr.strip()[:200]}".strip())
    else:
        record("source.yml filter and template.yml agree", SKIP, "no interpreter with PyYAML found")

    print("\n=== offline: documentation integrity ===")
    # Deleting a file and leaving a reference to it behind is invisible until a human follows the
    # link. Both of these fired for real when docs/ was folded into the README.
    tracked = run(["git", "ls-files"]).stdout.split()
    tracked_set = set(tracked)
    # Only paths under directories that are actually committed are checkable: build artifacts
    # (work/, dist/) and gitignored state (.cred/) are referenced legitimately but never tracked.
    # A real file reference always carries an extension. Requiring one keeps prose like
    # "tracked docs/config" from being mistaken for a path.
    ref_pat = re.compile(
        r"(?:(?:build|config|functions)/[A-Za-z0-9_./-]+"
        r"|\.github/workflows/[A-Za-z0-9_.-]+"
        r"|[A-Z][A-Za-z_]*)"
        r"\.(?:py|mjs|js|yml|yaml|json|md)\b"
    )
    dangling: list[str] = []
    for rel in tracked:
        if pathlib.Path(rel).suffix not in {".md", ".py", ".mjs", ".js", ".yml", ".yaml", ""}:
            continue
        try:
            text = (REPO / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for match in set(ref_pat.findall(text)):
            # Composite/elided references in prose are not real paths.
            if any(ch in match for ch in "[]|…*") or match.endswith("/"):
                continue
            if match not in tracked_set and not (REPO / match).exists():
                dangling.append(f"{rel} → {match}")
    record("no reference to a non-existent file", FAIL if dangling else PASS,
           "; ".join(sorted(set(dangling))[:6]))

    # A credential pasted into documentation is committed to git history, where it cannot be
    # unsent. Placeholders are fine; a real-looking value is not.
    placeholder = re.compile(r"^(?:<[^>]*>|\.{3}|…|\*{3,}|\$\{?\w|YOUR_|your[-_]|edgeUser|providerUser|example)", re.I)
    leaks: list[str] = []
    cred_pat = re.compile(r"(?:password|passwd|pass)\s*[:=]\s*\"?([^\s\"&<>|]+)", re.I)
    for rel in tracked:
        if pathlib.Path(rel).suffix not in {".md", ".yml", ".yaml", ""}:
            continue
        try:
            lines = (REPO / rel).read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        for num, line in enumerate(lines, 1):
            for value in cred_pat.findall(line):
                if placeholder.match(value) or len(value) < 10:
                    continue
                leaks.append(f"{rel}:{num}")
    record("no plaintext credential in tracked docs/config", FAIL if leaks else PASS,
           "; ".join(sorted(set(leaks))[:6]))

    print("\n=== offline: secret hygiene ===")
    # .cred/ carries live provider credentials between builds. If it were ever committable, a
    # working account would leak into git history, where it cannot be unsent.
    record(".cred/ is gitignored",
           PASS if run(["git", "check-ignore", ".cred/provider.json"]).returncode == 0 else FAIL)
    record("no .cred file is tracked",
           PASS if run(["git", "ls-files", "--error-unmatch", ".cred/provider.json"]).returncode != 0 else FAIL)
    for secret_file in (".dev.vars", ".env"):
        tracked = run(["git", "ls-files", "--error-unmatch", secret_file]).returncode == 0
        record(f"{secret_file} is not tracked", FAIL if tracked else PASS)


# --------------------------------------------------------------------------- live group
def live() -> None:
    print("\n=== live: the deployed edge ===")
    base = (os.environ.get("BASE") or PROD).rstrip("/")
    env = dev_vars()
    user = os.environ.get("CLIENT_USER") or env.get("CLIENT_USER", "")
    pwd = os.environ.get("CLIENT_PASS") or env.get("CLIENT_PASS", "")
    if not (user and pwd):
        record(f"verify_edge.py against {base}", SKIP,
               "no client credentials (set CLIENT_USER/CLIENT_PASS or .dev.vars)")
        return
    proc = run([sys.executable, "build/verify_edge.py"],
               env={"BASE": base, "CLIENT_USER": user, "CLIENT_PASS": pwd})
    summary = next((ln for ln in proc.stdout.splitlines() if "checks passed" in ln), "")
    record(f"verify_edge.py against {base}", PASS if proc.returncode == 0 else FAIL,
           f"rc={proc.returncode} {summary} {proc.stdout[-400:] if proc.returncode else ''}".strip())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", action="store_true",
                    help="also probe the deployed edge (needs client credentials)")
    args = ap.parse_args()

    print(f"=== tuliprox-cf-edge test suite ({'offline + live' if args.live else 'offline'}) ===")
    offline()
    if args.live:
        live()

    passed = sum(1 for _, s, _ in results if s == PASS)
    failed = [r for r in results if r[1] == FAIL]
    skipped = [r for r in results if r[1] == SKIP]

    print(f"\n=== {passed} passed, {len(failed)} failed, {len(skipped)} skipped ===")
    for name, _, detail in skipped:
        print(f"SKIPPED: {name} — {detail}")
    for name, _, detail in failed:
        print(f"FAILED:  {name} — {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
