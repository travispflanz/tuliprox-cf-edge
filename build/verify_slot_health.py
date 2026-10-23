#!/usr/bin/env python3
"""verify_slot_health.py — the watchdog's per-slot verdicts.

WHY THIS IS PINNED
------------------
These branches decide whether a human gets woken up, and the important ones can never be observed
in production: proving "every slot is dead" live would mean deliberately killing the user's
provider accounts. So they are proven here instead.

Two failure modes, both silent:
  - Alerting on ONE dead slot   -> pages a human for a condition the next build repairs by itself.
                                   The operator learns to ignore edge-alert issues, and the next
                                   real outage is ignored too.
  - Warning on ALL slots dead   -> nobody can watch anything and the monitor stays quiet, because
                                   the catalogue still loads perfectly and the verifier still
                                   passes its anonymous-access checks.

Stdlib only, no network.

Run: python3 build/verify_slot_health.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from slot_health import judge_slots  # noqa: E402

failures: list[str] = []
checks = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not ok:
        failures.append(f"{name} — {detail}")
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail and not ok else ""))


def slot(n: int, healthy: bool, expires: str = "2026-01-01 00:00 UTC", generation: int = 1) -> dict:
    return {"slot": n, "healthy": healthy, "expires_at": expires, "generation": generation}


def judge(*slots: dict, code: int = 200):
    return judge_slots(code, {"generated_at": "now", "slots": list(slots)})


print("=== the healthy case must stay silent ===")
problems, warnings, notes = judge(slot(1, True), slot(2, True))
check("two healthy slots raise nothing", not problems and not warnings,
      f"problems={problems} warnings={warnings}")
check("but both are still reported for the record", len(notes) == 2, str(notes))
check("each note names the slot and its expiry",
      all("slot " in n and "expires" in n for n in notes), str(notes))

print("\n=== one dead slot is a WARNING, not an alert (the next build repairs it) ===")
problems, warnings, notes = judge(slot(1, True), slot(2, False))
check("no alert is raised", not problems, str(problems))
check("a warning is raised", len(warnings) == 1, str(warnings))
check("it names the dead slot", "slot(s) 2" in warnings[0], warnings[0])
check("and states the remaining capacity honestly",
      "reduced to 1 of 2" in warnings[0], warnings[0])
check("the dead slot is marked UNHEALTHY in the notes",
      any("UNHEALTHY" in n and "slot 2" in n for n in notes), str(notes))

print("\n=== every slot dead is an ALERT (nothing will play) ===")
problems, warnings, notes = judge(slot(1, False), slot(2, False))
check("an alert is raised", len(problems) == 1, str(problems))
check("it is not merely a warning", not warnings, str(warnings))
check("it says no viewer can stream", "no viewer can stream" in problems[0], problems[0])
check("and tells the operator what to do", "force_trial" in problems[0], problems[0])

print("\n=== a single-slot deployment is judged on its own terms ===")
problems, warnings, notes = judge(slot(1, True))
check("one healthy slot raises nothing", not problems and not warnings,
      f"{problems} {warnings}")
problems, warnings, notes = judge(slot(1, False))
check("its only slot dead IS a total outage", len(problems) == 1, str(problems))
check("reported as 1 configured", "(1 configured)" in problems[0], problems[0])

print("\n=== three slots: a minority dying must not alert ===")
problems, warnings, notes = judge(slot(1, True), slot(2, False), slot(3, True))
check("one of three dead -> warning only", not problems and len(warnings) == 1,
      f"{problems} {warnings}")
check("capacity is stated as 2 of 3", "reduced to 2 of 3" in warnings[0], warnings[0])
problems, warnings, notes = judge(slot(1, False), slot(2, False), slot(3, True))
check("two of three dead is still only a warning (someone can watch)",
      not problems and len(warnings) == 1, f"{problems} {warnings}")
check("and it names both dead slots", "slot(s) 1, 2" in warnings[0], warnings[0])

print("\n=== missing or malformed input must never alert ===")
# A deploy predating slot support has no summary. Alerting on that would fire on every run of an
# otherwise perfectly healthy edge.
problems, warnings, notes = judge_slots(404, None)
check("no summary published -> a note, nothing more",
      not problems and not warnings and len(notes) == 1, f"{problems} {warnings} {notes}")
check("the note explains the degraded judgement",
      "judging slot 1 only" in notes[0], notes[0])

problems, warnings, notes = judge_slots(0, None)   # network failure
check("an unreachable endpoint does not alert", not problems, str(problems))

for label, doc in (("null body", None), ("a list", []), ("no slots key", {"x": 1}),
                   ("slots not a list", {"slots": "nope"})):
    problems, warnings, notes = judge_slots(200, doc)
    check(f"malformed ({label}) -> warning, never an alert",
          not problems and len(warnings) == 1, f"{problems} {warnings}")

problems, warnings, notes = judge_slots(200, {"slots": []})
check("an empty slot list -> warning, never an alert",
      not problems and len(warnings) == 1, f"{problems} {warnings}")

problems, warnings, notes = judge_slots(200, {"slots": ["garbage", 7]})
check("non-dict entries are ignored rather than crashing the watchdog",
      not problems and len(warnings) == 1, f"{problems} {warnings}")

print("\n=== missing fields degrade gracefully ===")
problems, warnings, notes = judge_slots(200, {"slots": [{"slot": 1, "healthy": True}]})
check("an absent expiry reads as unknown", "expires unknown" in notes[0], notes[0])
problems, warnings, notes = judge_slots(200, {"slots": [{"healthy": True}]})
check("an absent slot number still produces a note", len(notes) == 1, str(notes))

print(f"\n=== {checks - len(failures)}/{checks} checks passed ===")
for failure in failures:
    print(f"FAILED: {failure}")
sys.exit(1 if failures else 0)
