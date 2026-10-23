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
                legacy_flat: bool = False, last_status: str = "",
                last_attempt_hours: float | None = None) -> None:
    """Write a state file whose credential is `hours_old` old; None means 'no file at all'.

    `legacy_flat` writes the pre-slot document shape, to prove an existing deployment's measured
    age survives the upgrade instead of resetting to unknown and triggering a needless renewal.
    `last_status`/`last_attempt_hours` drive the acquisition backoff.
    """
    if hours_old is None and not last_status:
        CRED.unlink(missing_ok=True)
        return
    entry: dict = {"url": PANEL, "user": USER, "pass": "secret", "generation": generation}
    if hours_old is not None:
        when = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours_old)
        entry["acquired_at"] = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    if last_status:
        entry["last_status"] = last_status
    if last_attempt_hours is not None:
        tried = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=last_attempt_hours)
        entry["last_attempt_at"] = tried.strftime("%Y-%m-%dT%H:%M:%SZ")
    doc = entry if legacy_flat else {"slots": {str(slot): entry}}
    CRED.write_text(json.dumps(doc), encoding="utf-8")


def run_if_needed(hours_old: float | None, hours_left: float | None, slot: int = 1,
                  generation: int = 0, legacy_flat: bool = False, last_status: str = "",
                  last_attempt_hours: float | None = None,
                  force: bool = False) -> tuple[int, str, bool]:
    """Stub the provider and run --if-needed. Returns (exit_code, stdout, signup_attempted)."""
    write_state(hours_old, slot=slot, generation=generation, legacy_flat=legacy_flat,
                last_status=last_status, last_attempt_hours=last_attempt_hours)
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


RUNWAY = at.RENEW_WHEN_HOURS_LEFT      # the ONLY renewal trigger
MAX_AGE = at.RENEW_MAX_AGE_HOURS       # fallback cap, used only when no expiry is reported
STAGGER = at.RENEW_STAGGER_HOURS
LOTS = RUNWAY + 30                     # comfortably healthy, whatever the threshold is

print("=== credential renewal policy ===")
print(f"  runway={RUNWAY}h  max_age={MAX_AGE}h  stagger={STAGGER}h  state-file={at.CRED_FILE}")

# Every case is expressed RELATIVE to the configured thresholds. Hardcoded hours would silently
# start testing the wrong boundary the moment a threshold is tuned — which is exactly what
# happened when the floor moved from 12h to 6h.
CASES = [
    # (description, age_hours, hours_left, expected)
    ("healthy with plenty of life -> reuse",               1.0,             LOTS,          "reuse"),
    ("just above the runway -> reuse",                    1.0,             RUNWAY + 0.1,  "reuse"),
    ("exactly at the runway -> renew",                    1.0,             RUNWAY,        "renew"),
    ("just under the runway -> renew",                    1.0,             RUNWAY - 0.1,  "renew"),
    # The whole point of collapsing two rules into one: AGE ALONE no longer renews anything. A
    # credential that still has life is kept however old it is, which is what makes the policy adapt
    # to whatever trial length the provider actually issues instead of assuming one.
    ("old but with life left -> reuse (age alone never renews)", 99.0,      LOTS,          "reuse"),
    ("very old, yet just under the runway -> renew",      300.0,           RUNWAY - 0.1,  "renew"),
    ("age unknown, plenty of life -> reuse",              None,            LOTS,          "reuse"),
    ("age unknown, under the runway -> renew",            None,            RUNWAY - 0.1,  "renew"),
    # Expiry unreadable: the age cap is the only signal, and it asks a genuinely different question.
    ("no expiry reported, age under the cap -> reuse",    MAX_AGE - 0.1,   None,          "reuse"),
    ("no expiry reported, age AT the cap -> renew",       MAX_AGE,         None,          "renew"),
    ("no expiry reported, age past the cap -> renew",     MAX_AGE + 6,     None,          "renew"),
    # Neither readable: adopt what is configured. It authenticates right now, and renewing instead
    # would burn a trial on every build if the state file could not persist.
    ("neither age nor expiry readable -> adopt",          None,            None,          "reuse"),
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
inside = RUNWAY + STAGGER - 0.1          # inside slot 2's WIDENED runway, outside slot 1's
rc, out, signup = run_if_needed(1.0, inside, slot=2, generation=0)
check("slot 2's FIRST renewal widens the runway by the stagger",
      "renewal due" in out and not signup, out.strip()[:110])

rc, out, signup = run_if_needed(1.0, inside, slot=1, generation=0)
check("slot 1 at the same remaining life is NOT due (it rides the plain runway)",
      rc == 0 and "no signup made" in out and not signup, out.strip()[:110])

rc, out, signup = run_if_needed(1.0, inside, slot=2, generation=1)
check("once slot 2 has renewed it is out of phase and uses the plain runway",
      rc == 0 and "no signup made" in out and not signup, out.strip()[:110])

rc, out, signup = run_if_needed(1.0, RUNWAY + STAGGER + 0.1, slot=2, generation=0)
check("slot 2 just outside the widened runway -> reuse",
      rc == 0 and "no signup made" in out and not signup, out.strip()[:110])

rc, out, signup = run_if_needed(1.0, RUNWAY - 1, slot=2, generation=5)
check("the runway applies to any slot regardless of generation",
      "renewal due" in out and not signup, out.strip()[:110])

print("\n=== backoff after a failed acquisition (the provider-goodwill guard) ===")
# A slot with no usable credential is 'renewal due' on EVERY build. At a 4h interval that is six
# signup attempts a day — the churn that gets the free-trial route withdrawn. Proven necessary:
# slot 2's first real acquisition failed provider-side with the task stuck EXECUTING.
rc, out, signup = run_if_needed(None, 0.0, slot=2, generation=0,
                                last_status="failed", last_attempt_hours=1.0)
check("a recent failure suppresses the signup entirely",
      rc == 0 and not signup and "backing off" in out, out.strip()[:160])

rc, out, signup = run_if_needed(None, 0.0, slot=2, generation=0,
                                last_status="failed",
                                last_attempt_hours=at.ACQUIRE_RETRY_HOURS + 1)
check("once the backoff window has passed it tries again",
      "acquiring a fresh trial" in out, out.strip()[:160])

rc, out, signup = run_if_needed(None, 0.0, slot=2, generation=0,
                                last_status="reused", last_attempt_hours=1.0)
check("a SUCCESSFUL last attempt never backs off",
      "backing off" not in out, out.strip()[:160])

rc, out, signup = run_if_needed(None, 0.0, slot=2, generation=0)
check("no attempt on record is not treated as a recent failure",
      "backing off" not in out, out.strip()[:160])

rc, out, signup = run_if_needed(1.0, RUNWAY - 1, slot=1,
                                last_status="failed", last_attempt_hours=1.0, force=True)
check("--force overrides the backoff (an operator asked explicitly)",
      "backing off" not in out, out.strip()[:160])

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
# The runway doubles as the WINDOW a build must land in, so it has to clear the interval this
# pipeline really sees — not the nominal 4h. GitHub's cron has been measured 10h+ late.
check("the runway clears the REAL (not nominal) build interval",
      RUNWAY >= 12, f"runway={RUNWAY}h must exceed the 10h+ this pipeline actually sees")
check("the fallback cap is a distinct knob, not a second copy of the runway",
      MAX_AGE > 0 and MAX_AGE != RUNWAY, f"max_age={MAX_AGE} runway={RUNWAY}")
check("slot 2's widened runway still starts from a live credential",
      RUNWAY + STAGGER < 36, f"margin={RUNWAY + STAGGER}h must stay under the ~36h trial life")

print(f"\n=== {checks - len(failures)}/{checks} checks passed ===")
for f in failures:
    print(f"FAILED: {f}")
sys.exit(1 if failures else 0)