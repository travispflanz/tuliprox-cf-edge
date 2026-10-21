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


def write_state(hours_old: float | None) -> None:
    """Write a state file whose credential is `hours_old` old; None means 'no file at all'."""
    if hours_old is None:
        CRED.unlink(missing_ok=True)
        return
    when = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours_old)
    CRED.write_text(json.dumps({
        "url": PANEL, "user": USER, "pass": "secret",
        "acquired_at": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }), encoding="utf-8")


def run_if_needed(hours_old: float | None, hours_left: float | None) -> tuple[int, str, bool]:
    """Stub the provider and run --if-needed. Returns (exit_code, stdout, signup_attempted)."""
    write_state(hours_old)
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
    sys.argv = ["acquire_trial.py", "--if-needed"]
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = at.main()
    finally:
        sys.argv = argv
        (at.current_credential_status, at.email_is_used,
         at.find_working_port, at.request_trial) = saved
    return rc, buf.getvalue(), reached["signup"]


print("=== credential renewal policy (24h cadence, 12h floor) ===")
print(f"  cadence={at.RENEW_EVERY_HOURS}h  floor={at.MIN_HOURS_LEFT}h  state-file={at.CRED_FILE}")

CASES = [
    # (description, age_hours, hours_left, expected)
    ("healthy and young -> reuse",           1.0,   35.0, "reuse"),
    ("just under the cadence -> reuse",     23.9,   35.0, "reuse"),
    ("exactly at the cadence -> renew",     24.0,   35.0, "renew"),
    ("past the cadence -> renew",           30.0,    6.0, "renew"),
    ("cadence reached though lots of life", 25.0,   35.0, "renew"),
    ("young but under the floor -> renew",   1.0,   11.0, "renew"),
    ("exactly at the floor -> renew",        1.0,   12.0, "renew"),
    ("just above the floor -> reuse",        1.0,   12.1, "reuse"),
    ("age unknown, healthy -> reuse",        None,  35.0, "reuse"),
    ("age unknown, under the floor -> renew", None,  11.0, "renew"),
    ("age unknown, no expiry reported -> reuse", None, None, "reuse"),
    ("cadence reached, no expiry -> renew", 25.0,   None, "renew"),
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

print("\n=== state-file age reading ===")
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
CRED.write_text(json.dumps({"acquired_at": future.strftime("%Y-%m-%dT%H:%M:%SZ")}), encoding="utf-8")
check("future timestamp -> age unknown, not negative", at.credential_age_hours() is None)

print(f"\n=== {checks - len(failures)}/{checks} checks passed ===")
for f in failures:
    print(f"FAILED: {f}")
sys.exit(1 if failures else 0)