#!/usr/bin/env python3
"""verify_renewal_policy.py — network-free regression test for the credential renewal rule.

WHY THIS IS PINNED
------------------
The renewal rule is the heart of this design, and it fails invisibly in BOTH directions:

  - too eager  -> it silently churns free trials at the provider (~4/day), which is exactly the
                  request pattern that risks the account being read as automated abuse.
  - too lazy   -> the provider credential quietly lapses, the catalogue keeps serving tens of
                  thousands of channels, and EVERY stream starts failing. Nobody notices until a
                  viewer complains.

So the decision is exercised here directly: 24h cadence, 12h safety floor, and never a signup while
a healthy credential is still inside that window. No network, no credentials, no trial requests.

Run: python3 build/verify_renewal_policy.py
"""
from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import os
import pathlib
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

TMP = pathlib.Path(tempfile.mkdtemp(prefix="renewal-policy-"))
CRED = TMP / "provider.json"
os.environ["CRED_FILE"] = str(CRED)
# A stray real credential in the environment must not influence the test.
for _k in ("PROVIDER_URL", "PROVIDER_USER", "PROVIDER_PASS", "GITHUB_OUTPUT"):
    os.environ.pop(_k, None)

sys.path.insert(0, str(REPO_ROOT / "build"))
import acquire_trial as at  # noqa: E402

PANEL = "http://panel.example"
USER = "TESTUSER"
failures: list[str] = []
checks = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not ok:
        failures.append(f"{name} — {detail}")
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail and not ok else ""))


def write_state(hours_old: float | None, slot: int = 1, generation: int = 0,
                legacy_flat: bool = False) -> None:
    """Write a state file whose credential is `hours_old` old; None means 'no file at all'.

    `legacy_flat` writes the pre-slot document shape, to prove an existing deployment's measured
    age survives the upgrade instead of resetting to unknown and triggering a needless renewal.
    """
    if hours_old is None:
        CRED.unlink(missing_ok=True)
        return
    when = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours_old)
    entry = {
        "url": PANEL, "user": USER, "pass": "secret",
        "acquired_at": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generation": generation,
    }
    doc = entry if legacy_flat else {"slots": {str(slot): entry}}
    CRED.write_text(json.dumps(doc), encoding="utf-8")


def run_if_needed(hours_old: float | None, hours_left: float | None, slot: int = 1,
                  generation: int = 0, legacy_flat: bool = False) -> tuple[int, str, bool]:
    """Stub the provider and run --if-needed. Returns (exit_code, stdout, signup_attempted)."""
    write_state(hours_old, slot=slot, generation=generation, legacy_flat=legacy_flat)
    at.SLOT = slot
    reached = {"signup": False}

    def boom(*_a, **_k):
        reached["signup"] = True
        raise AssertionError("the signup path must never be reached")

    saved = (at.current_credential_status, at.email_is_used, at.find_working_port, at.request_trial)
    at.current_credential_status = lambda: (PANEL, USER, hours_left)
    # True short-circuits the acquisition branch BEFORE any port race or POST /api/trial, so a
    # decision to renew is proven without ever touching the provider's signup endpoint.
    at.email_is_used = lambda _email: True
    at.find_working_port = boom
    at.request_trial = boom

    argv = sys.argv
    sys.argv = ["acquire_trial.py", "--if-needed", "--slot", str(slot)]
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = at.main()
    finally:
        sys.argv = argv
        (at.current_credential_status, at.email_is_used,
         at.find_working_port, at.request_trial) = saved
    return rc, buf.getvalue(), reached["signup"]


CADENCE = at.RENEW_EVERY_HOURS
FLOOR = at.MIN_HOURS_LEFT
STAGGER = at.RENEW_STAGGER_HOURS
LOTS = CADENCE + FLOOR + 10          # comfortably healthy, whatever the thresholds are

print("=== credential renewal policy ===")
print(f"  cadence={CADENCE}h  floor={FLOOR}h  stagger={STAGGER}h  state-file={at.CRED_FILE}")

# Every case is expressed RELATIVE to the configured thresholds. Hardcoded hours would silently
# start testing the wrong boundary the moment a threshold is tuned — which is exactly what
# happened when the floor moved from 12h to 6h.
CASES = [
    # (description, age_hours, hours_left, expected)
    ("healthy and young -> reuse",                       1.0,             LOTS,      "reuse"),
    ("just under the cadence -> reuse",                  CADENCE - 0.1,   LOTS,      "reuse"),
    ("exactly at the cadence -> renew",                  CADENCE,         LOTS,      "renew"),
    ("past the cadence -> renew",                        CADENCE + 6,     FLOOR + 1, "renew"),
    ("cadence reached though lots of life -> renew",     CADENCE + 1,     LOTS,      "renew"),
    ("young but under the floor -> renew",               1.0,             FLOOR - 1, "renew"),
    ("exactly at the floor -> renew",                    1.0,             FLOOR,     "renew"),
    ("just above the floor -> reuse",                    1.0,             FLOOR + 0.1, "reuse"),
    ("age unknown, healthy -> reuse",                    None,            LOTS,      "reuse"),
    ("age unknown, under the floor -> renew",            None,            FLOOR - 1, "renew"),
    ("age unknown, no expiry reported -> reuse",          None,            None,      "reuse"),
    ("cadence reached, no expiry -> renew",              CADENCE + 1,     None,      "renew"),
]

for desc, age, left, expected in CASES:
    rc, out, signup = run_if_needed(age, left)
    if expected == "reuse":
        ok = rc == 0 and "no signup made" in out
        detail = f"rc={rc} signup={signup} out={out.strip()[:90]!r}"
    else:
        ok = "renewal due" in out
        detail = f"rc={rc} signup={signup} out={out.strip()[:90]!r}"
    ok = ok and not signup          # no case may ever reach the signup path
    check(desc, ok, detail)

print("\n=== slot staggering (so the slots never expire together) ===")
# A second slot bootstrapped alongside the first would otherwise renew and expire in lockstep,
# and one failed cycle would then remove ALL viewing capacity at once.
mid = (STAGGER + CADENCE) / 2            # past the stagger, short of the full cadence
rc, out, signup = run_if_needed(mid, LOTS, slot=2, generation=0)
check("slot 2's FIRST renewal is pulled forward to the stagger point",
      "renewal due" in out and not signup, out.strip()[:110])

rc, out, signup = run_if_needed(mid, LOTS, slot=1, generation=0)
check("slot 1 at the same age is NOT due (it rides the full cadence)",
      rc == 0 and "no signup made" in out and not signup, out.strip()[:110])

rc, out, signup = run_if_needed(mid, LOTS, slot=2, generation=1)
check("once slot 2 has renewed it is out of phase and uses the normal cadence",
      rc == 0 and "no signup made" in out and not signup, out.strip()[:110])

rc, out, signup = run_if_needed(STAGGER - 0.1, LOTS, slot=2, generation=0)
check("slot 2 just before the stagger point -> reuse",
      rc == 0 and "no signup made" in out and not signup, out.strip()[:110])

rc, out, signup = run_if_needed(1.0, FLOOR - 1, slot=2, generation=5)
check("the floor still applies to any slot regardless of generation",
      "renewal due" in out and not signup, out.strip()[:110])

print("\n=== per-slot isolation ===")
write_state(1.0, slot=1)
at.SLOT = 2
check("slot 2 does not inherit slot 1's age", at.credential_age_hours() is None)
at.SLOT = 1
check("slot 1 still reads its own age", (at.credential_age_hours() or 0) > 0.9)

print("\n=== upgrade path from the pre-slot state file ===")
# An existing deployment's state file is flat. Reading it as 'unknown' would discard a measured
# age and trigger an immediate, needless signup on the very first build after the upgrade.
write_state(3.0, legacy_flat=True)
at.SLOT = 1
age = at.credential_age_hours()
check("a legacy flat document is read as slot 1, preserving its age",
      age is not None and abs(age - 3.0) < 0.05, f"age={age}")

print("\n=== state-file age reading ===")
at.SLOT = 1
write_state(5.0)
age = at.credential_age_hours()
check("reads a valid timestamp", age is not None and abs(age - 5.0) < 0.01, f"age={age}")

CRED.unlink(missing_ok=True)
check("missing file -> age unknown (never 'fresh')", at.credential_age_hours() is None)

CRED.write_text("{not json", encoding="utf-8")
check("malformed JSON -> age unknown", at.credential_age_hours() is None)

CRED.write_text("{}", encoding="utf-8")
check("no acquired_at -> age unknown", at.credential_age_hours() is None)

future = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=5)
CRED.write_text(json.dumps({"slots": {"1": {"user": USER,
                "acquired_at": future.strftime("%Y-%m-%dT%H:%M:%SZ")}}}), encoding="utf-8")
check("future timestamp -> age unknown, not negative", at.credential_age_hours() is None)

print("\n=== threshold sanity (a misconfiguration here is an outage) ===")
check("the floor leaves room for at least one more build",
      FLOOR >= 4, f"floor={FLOOR}h must exceed the build interval")
check("the stagger sits strictly inside the cadence",
      0 < STAGGER < CADENCE, f"stagger={STAGGER} cadence={CADENCE}")

print(f"\n=== {checks - len(failures)}/{checks} checks passed ===")
for f in failures:
    print(f"FAILED: {f}")
sys.exit(1 if failures else 0)