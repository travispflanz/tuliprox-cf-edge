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

So the decision is exercised here directly: renew at or under the margin, never a signup while a
credential is still above it, and — the invariant the whole design rests on — the build interval is
SHORTER than the margin. No network, no credentials, no trial requests.

Run: python3 build/verify_renewal_policy.py
"""
from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import os
import pathlib
import re
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
    entry: dict = {"url": PANEL, "user": USER, "pass": "secret", "generation": generation}
    when = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours_old)
    entry["acquired_at"] = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    doc = entry if legacy_flat else {"slots": {str(slot): entry}}
    CRED.write_text(json.dumps(doc), encoding="utf-8")


def run_if_needed(hours_old: float | None, hours_left: float | None, slot: int = 1,
                  generation: int = 0, legacy_flat: bool = False,
                  force: bool = False) -> tuple[int, str, bool]:
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
    sys.argv = ["acquire_trial.py", "--force" if force else "--if-needed", "--slot", str(slot)]
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = at.main()
    finally:
        sys.argv = argv
        (at.current_credential_status, at.email_is_used,
         at.find_working_port, at.request_trial) = saved
    return rc, buf.getvalue(), reached["signup"]


RUNWAY = at.RENEW_WHEN_HOURS_LEFT      # the ONLY number in the policy
LOTS = RUNWAY * 5                      # comfortably healthy, whatever the margin is

print("=== credential renewal policy ===")
print(f"  renew at or under {RUNWAY}h of remaining life  (state file: {at.CRED_FILE})")

# Every case is expressed RELATIVE to the configured thresholds. Hardcoded hours would silently
# start testing the wrong boundary the moment a threshold is tuned — which is exactly what
# happened when the floor moved from 12h to 6h.
CASES = [
    # (description, age_hours, hours_left, expected)
    ("plenty of life -> reuse",                            1.0,             LOTS,          "reuse"),
    ("just above the margin -> reuse",                     1.0,             RUNWAY + 0.1,  "reuse"),
    ("exactly at the margin -> renew",                     1.0,             RUNWAY,        "renew"),
    ("just under the margin -> renew",                     1.0,             RUNWAY - 0.1,  "renew"),
    # Age alone never renews anything: a credential with life in hand is kept however old it is.
    # That is what makes the policy hold for whatever trial length the provider issues, instead of
    # baking in an assumption about it.
    ("old but with life left -> reuse",                    300.0,           LOTS,          "reuse"),
    ("age unknown, plenty of life -> reuse",               None,            LOTS,          "reuse"),
    ("age unknown, under the margin -> renew",             None,            RUNWAY - 0.1,  "renew"),
    # An unreadable expiry is FRESH: the account authenticates right now, re-signing-up would burn a
    # trial for nothing, and when it dies the panel stops authenticating — which is itself the
    # renewal signal. No age arithmetic, so no trial-length assumption.
    ("expiry not reported -> fresh",                       300.0,           None,          "reuse"),
    ("expiry not reported and no age either -> fresh",     None,            None,          "reuse"),
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
# A second slot bootstrapped alongside the first would otherwise renew and expire in lockstep, and
# one failed cycle would then remove ALL viewing capacity at once. It gets a whole extra margin on
# its first renewal only — derived from the margin, so there is no second number to keep in step.
widened = RUNWAY * 2
inside = widened - 0.1
rc, out, signup = run_if_needed(1.0, inside, slot=2, generation=0)
check("slot 2's FIRST renewal gets a whole extra margin",
      "renewal due" in out and not signup, out.strip()[:110])

rc, out, signup = run_if_needed(1.0, inside, slot=1, generation=0)
check("slot 1 at the same remaining life is NOT due (it rides the plain margin)",
      rc == 0 and "no signup made" in out and not signup, out.strip()[:110])

rc, out, signup = run_if_needed(1.0, inside, slot=2, generation=1)
check("once slot 2 has renewed it is out of phase and uses the plain margin",
      rc == 0 and "no signup made" in out and not signup, out.strip()[:110])

rc, out, signup = run_if_needed(1.0, widened + 0.1, slot=2, generation=0)
check("slot 2 just outside its widened margin -> reuse",
      rc == 0 and "no signup made" in out and not signup, out.strip()[:110])

rc, out, signup = run_if_needed(1.0, RUNWAY - 1, slot=2, generation=5)
check("the margin applies to any slot regardless of generation",
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

print("\n=== the invariant the whole design rests on ===")
# Read the REAL schedule out of the workflow instead of restating it, so this cannot drift from the
# cron that actually runs. The guarantee is interval < margin: checks are closer together than the
# margin is wide, so a credential is always seen while it still has life in hand.
_cron = re.search(r'cron:\s*"0 \*/(\d+) \* \* \*"',
                  (REPO_ROOT / ".github" / "workflows" / "tuliprox-edge.yml").read_text())
INTERVAL = float(_cron.group(1)) if _cron else 0.0
check("the build interval was read from the workflow itself", INTERVAL > 0, f"got {INTERVAL}")
check("THE invariant: the check interval is SHORTER than the margin",
      INTERVAL < RUNWAY,
      f"interval={INTERVAL}h must be under margin={RUNWAY}h or a credential dies between checks")
check("the margin is the only number left in the policy",
      not hasattr(at, "RENEW_MAX_AGE_HOURS") and not hasattr(at, "ACQUIRE_RETRY_HOURS")
      and not hasattr(at, "RENEW_STAGGER_HOURS"),
      "a removed timing knob is still defined in acquire_trial.py")
check("no signup is suppressed by a timer, only by the margin",
      not hasattr(at, "hours_since_last_attempt"),
      "the acquisition backoff is still present")

print(f"\n=== {checks - len(failures)}/{checks} checks passed ===")
for f in failures:
    print(f"FAILED: {f}")
sys.exit(1 if failures else 0)