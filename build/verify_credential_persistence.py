#!/usr/bin/env python3
"""verify_credential_persistence.py — the state that carries credentials between builds.

WHY THIS IS PINNED
------------------
The renewal cadence only works if each slot's real age survives from one build to the next, and
every way it can break is invisible in a green CI run:

  - State not carried forward     -> the seed ages out, every build mints a new trial, and the
                                     signup churn returns to several a day: the exact pattern that
                                     risks losing provider access altogether.
  - `acquired_at` re-stamped      -> every account looks perpetually brand new, the cadence never
                                     comes due, and renewal silently never happens.
  - Slots sharing one record      -> slot 2 inherits slot 1's age, renews on the wrong schedule,
                                     and both then expire together, killing ALL capacity at once.
  - Health hint computed wrongly  -> the edge keeps handing viewers to a dead account, which it
                                     cannot detect itself (streams leave as redirects).
  - Legacy flat state misread     -> the first build after upgrading discards a measured age and
                                     signs up needlessly.

This drives `build/credential_state.py` directly — the real module the workflow invokes — rather
than re-implementing its logic or scraping shell out of the YAML. Stdlib only, no network.

Run: python3 build/verify_credential_persistence.py
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = REPO / "build" / "credential_state.py"
WORKFLOW = REPO / ".github" / "workflows" / "tuliprox-edge.yml"

S1 = ("http://p1.example", "USER1", "pass1")
S2 = ("http://p2.example", "USER2", "pass2")

failures: list[str] = []
checks = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not ok:
        failures.append(f"{name} — {detail}")
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail and not ok else ""))


class Workspace:
    """A throwaway state file plus a captured env-output file, as Actions would provide."""

    def __init__(self) -> None:
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="credstate-"))
        self.cred = self.dir / ".cred" / "provider.json"
        self.out = self.dir / "env_out"

    def run(self, command: str, **env: str) -> subprocess.CompletedProcess[str]:
        self.out.write_text("", encoding="utf-8")
        # Strip any ambient credential vars so a real environment can never leak into a case.
        clean = {k: v for k, v in os.environ.items()
                 if not k.startswith(("PROVIDER", "UPSTREAM", "SEED", "STATUS", "EXPIRES"))}
        clean.update({"CRED_FILE": str(self.cred), **env})
        clean.pop("GITHUB_ENV", None)
        return subprocess.run([sys.executable, str(SCRIPT), command, "--out", str(self.out)],
                              cwd=REPO, capture_output=True, text=True, env=clean)

    def env(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for line in self.out.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                out[key] = value
        return out

    def state(self) -> dict:
        if not self.cred.exists():
            return {}
        return json.loads(self.cred.read_text(encoding="utf-8")).get("slots", {})

    def write_state(self, doc: dict) -> None:
        self.cred.parent.mkdir(parents=True, exist_ok=True)
        self.cred.write_text(json.dumps(doc), encoding="utf-8")


def seed_env(slot1=S1, slot2=None) -> dict[str, str]:
    env = {"SEED_URL": slot1[0], "SEED_USER": slot1[1], "SEED_PASS": slot1[2]}
    if slot2:
        env |= {"SEED_2_URL": slot2[0], "SEED_2_USER": slot2[1], "SEED_2_PASS": slot2[2]}
    return env


def record_env(slot1=S1, slot2=None, status="reused", status2="reused",
               expires="", expires2="") -> dict[str, str]:
    env = {"PROVIDER_URL": slot1[0], "PROVIDER_USER": slot1[1], "PROVIDER_PASS": slot1[2],
           "STATUS": status, "EXPIRES": expires}
    if slot2:
        env |= {"PROVIDER_2_URL": slot2[0], "PROVIDER_2_USER": slot2[1],
                "PROVIDER_2_PASS": slot2[2], "STATUS_2": status2, "EXPIRES_2": expires2}
    return env


print("=== workflow wiring ===")
text = WORKFLOW.read_text(encoding="utf-8")
check("the workflow restores the persisted credential",
      "actions/cache/restore@v4" in text and "restore-keys: provider-cred-" in text)
check("and saves it under a rolling key",
      "actions/cache/save@v4" in text and "key: provider-cred-${{ github.run_id }}" in text)
check("seed and record are driven by credential_state.py",
      "credential_state.py seed" in text and "credential_state.py record" in text)
check("both slots are acquired, with force_trial applying only to slot 1",
      "--if-needed --slot 1" in text and "--if-needed --slot 2" in text
      and "--force --slot 1" in text)
check("the leak gate covers BOTH provider hosts",
      'for raw in "${PROVIDER_URL:-}" "${PROVIDER_2_URL:-}"' in text)
check("the edge receives the healthy-slot hint", "put UPSTREAM_HEALTHY_SLOTS" in text)
check("builds run every 4 hours", 'cron: "0 */4 * * *"' in text)
check("the renewal floor leaves room for another build",
      "MIN_HOURS_LEFT: ${{ vars.MIN_HOURS_LEFT || '6' }}" in text)
check(".cred/ is gitignored (it carries live provider credentials)",
      any(ln.strip() == ".cred/" for ln in (REPO / ".gitignore").read_text().splitlines()))

print("\n=== bootstrap: no state yet ===")
w = Workspace()
p = w.run("seed", **seed_env(slot1=S1, slot2=S2))
check("seed succeeds on a clean workspace", p.returncode == 0, p.stderr.strip()[:200])
check("it reports bootstrapping from the secret", "repository secret (bootstrap)" in p.stdout,
      p.stdout.strip()[:160])
env = w.env()
check("both slots are exported under PROVIDER_* and UPSTREAM_*",
      env.get("PROVIDER_URL") == S1[0] and env.get("UPSTREAM_2_USER") == S2[1], str(env)[:200])

p = w.run("record", **record_env(S1, S2, status="ok", status2="ok", expires="2026-01-01"))
check("record succeeds", p.returncode == 0, p.stderr.strip()[:200])
state = w.state()
check("each slot gets its own record", set(state) == {"1", "2"}, str(list(state)))
check("generation starts at 1 for a newly recorded credential",
      state["1"]["generation"] == 1 and state["2"]["generation"] == 1, str(state)[:200])
check("the healthy hint lists both verified slots",
      w.env().get("UPSTREAM_HEALTHY_SLOTS") == "1,2", str(w.env()))

print("\n=== the invariant: age is PRESERVED while a credential is unchanged ===")
first = {slot: state[slot]["acquired_at"] for slot in ("1", "2")}
time.sleep(1.1)
w.run("record", **record_env(S1, S2, status="reused", status2="reused"))
again = w.state()
check("a later build does NOT re-stamp acquired_at",
      all(again[s]["acquired_at"] == first[s] for s in ("1", "2")),
      f"{first} -> 1:{again['1']['acquired_at']} 2:{again['2']['acquired_at']}")
check("generation does not advance on a reuse",
      again["1"]["generation"] == 1 and again["2"]["generation"] == 1, str(again)[:160])
check("a known expiry is carried forward when the reuse reports none",
      again["1"]["expires_at"] == "2026-01-01", str(again["1"]))

print("\n=== a replacement credential resets age and advances generation ===")
NEW1 = ("http://p1.example", "USER1NEW", "pass1new")
w.run("record", **record_env(NEW1, S2, status="ok", status2="reused"))
rotated = w.state()
check("slot 1 gets a fresh acquired_at", rotated["1"]["acquired_at"] != first["1"],
      f"{first['1']} -> {rotated['1']['acquired_at']}")
check("slot 1's generation advances", rotated["1"]["generation"] == 2, str(rotated["1"]))
check("slot 2 is untouched by slot 1 rotating",
      rotated["2"]["acquired_at"] == first["2"] and rotated["2"]["generation"] == 1,
      str(rotated["2"]))

print("\n=== the next build restores state, not the aging secret ===")
w2 = Workspace()
w2.write_state({"slots": {
    "1": {"url": "http://stored1", "user": "STORED1", "pass": "sp1",
          "acquired_at": "2026-01-01T00:00:00Z", "generation": 3},
    "2": {"url": "http://stored2", "user": "STORED2", "pass": "sp2",
          "acquired_at": "2026-01-01T00:00:00Z", "generation": 2},
}})
p = w2.run("seed", **seed_env(S1, S2))
env = w2.env()
check("seed prefers the persisted credential for every slot",
      env.get("PROVIDER_URL") == "http://stored1" and env.get("PROVIDER_2_USER") == "STORED2",
      str(env)[:200])
check("it says where each credential came from",
      p.stdout.count("persisted by the previous build") == 2, p.stdout.strip()[:200])

print("\n=== degradation, never a broken build ===")
w3 = Workspace()
w3.write_state({"slots": {"1": {"url": "http://stored1"}}})      # incomplete
p = w3.run("seed", **seed_env(S1))
check("an incomplete persisted slot falls back to the secret",
      p.returncode == 0 and w3.env().get("PROVIDER_URL") == S1[0], str(w3.env())[:160])
check("and warns rather than failing silently", "incomplete" in p.stdout, p.stdout.strip()[:200])

w4 = Workspace()
w4.cred.parent.mkdir(parents=True, exist_ok=True)
w4.cred.write_text("{not json", encoding="utf-8")
p = w4.run("seed", **seed_env(S1))
check("corrupt state degrades to the secret instead of crashing",
      p.returncode == 0 and w4.env().get("PROVIDER_USER") == S1[1], p.stderr.strip()[:160])

print("\n=== slot 2 is genuinely optional ===")
w5 = Workspace()
p = w5.run("seed", **seed_env(S1))
check("a single-slot deployment seeds cleanly", p.returncode == 0, p.stderr.strip()[:160])
check("and says slot 2 has no credential yet", "slot 2: no credential yet" in p.stdout,
      p.stdout.strip()[:200])
w5.run("record", **record_env(S1, status="reused"))
check("record writes only the configured slot", set(w5.state()) == {"1"}, str(list(w5.state())))
check("the healthy hint names just that slot",
      w5.env().get("UPSTREAM_HEALTHY_SLOTS") == "1", str(w5.env()))

print("\n=== health reporting drives the edge, and fails OPEN ===")
w6 = Workspace()
w6.run("record", **record_env(S1, S2, status="ok", status2="failed"))
check("a failed slot is excluded from the healthy hint",
      w6.env().get("UPSTREAM_HEALTHY_SLOTS") == "1", str(w6.env()))
check("but its record is still kept for the next build", set(w6.state()) == {"1", "2"},
      str(list(w6.state())))
w7 = Workspace()
w7.run("record", **record_env(S1, S2, status="failed", status2="failed"))
check("no verified slot -> empty hint, so the edge tries everything (fails OPEN)",
      w7.env().get("UPSTREAM_HEALTHY_SLOTS") == "",
      repr(w7.env().get("UPSTREAM_HEALTHY_SLOTS")))

print("\n=== upgrade path from the pre-slot flat state file ===")
w8 = Workspace()
w8.write_state({"url": "http://legacy", "user": "LEGACY", "pass": "lp",
                "acquired_at": "2026-02-02T00:00:00Z"})
p = w8.run("seed", **seed_env(S1))
check("a legacy flat document is read as slot 1",
      w8.env().get("PROVIDER_USER") == "LEGACY", str(w8.env())[:160])
w8.run("record", PROVIDER_URL="http://legacy", PROVIDER_USER="LEGACY", PROVIDER_PASS="lp",
       STATUS="reused")
check("and its measured age survives the upgrade",
      w8.state()["1"]["acquired_at"] == "2026-02-02T00:00:00Z", str(w8.state()))

print("\n=== a transient gap must not erase a usable record ===")
w9 = Workspace()
w9.run("record", **record_env(S1, S2, status="ok", status2="ok"))
w9.run("record", **record_env(S1, status="reused"))   # slot 2 absent this run
check("slot 2's record survives a build where it was not configured",
      w9.state().get("2", {}).get("user") == S2[1], str(w9.state())[:200])

print(f"\n=== {checks - len(failures)}/{checks} checks passed ===")
for failure in failures:
    print(f"FAILED: {failure}")
sys.exit(1 if failures else 0)
