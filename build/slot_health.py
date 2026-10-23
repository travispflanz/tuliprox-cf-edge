#!/usr/bin/env python3
"""slot_health.py — judge the edge's redacted per-slot summary.

Its own module purely so it is IMPORTABLE. `monitor_edge.py` is a top-to-bottom script: importing
it would fire every live probe, so a unit test could not reach this logic at all. The branch that
matters most here — every slot dead, meaning nobody can watch anything — cannot be exercised
against production without deliberately killing the user's provider accounts, so if it were not
testable offline it would ship unverified.

The severity split is the whole point:
  - ONE dead slot  -> reduced capacity, repaired by the next build on its own  => warning
  - ALL slots dead -> total outage, nothing will play, a human is needed       => alert
Collapsing them would either cry wolf over a self-healing condition or stay silent during a real
outage. Both are worse than no monitoring, because they teach the operator to ignore the alert.
"""
from __future__ import annotations


def judge_slots(code: int, doc: object) -> tuple[list[str], list[str], list[str]]:
    """Returns (problems, warnings, notes) for the given HTTP status and parsed summary."""
    problems: list[str] = []
    warnings: list[str] = []
    notes: list[str] = []

    if code != 200:
        # Not fatal: a deploy from before slot support simply has no summary to read, and the
        # single-slot checks elsewhere still cover the account the catalogue was built with.
        notes.append(f"no per-slot summary published (http {code}) — judging slot 1 only")
        return problems, warnings, notes

    if not isinstance(doc, dict) or not isinstance(doc.get("slots"), list):
        warnings.append("the slot summary is malformed — per-slot health cannot be judged")
        return problems, warnings, notes

    entries = [e for e in doc["slots"] if isinstance(e, dict)]
    if not entries:
        warnings.append("the slot summary lists no slots — per-slot health cannot be judged")
        return problems, warnings, notes

    unhealthy = [s for s in entries if not s.get("healthy")]
    if len(unhealthy) == len(entries):
        problems.append(
            f"EVERY provider slot is unhealthy ({len(entries)} configured) — no viewer can stream. "
            f"Re-run the build; if it persists, dispatch it with force_trial."
        )
    elif unhealthy:
        warnings.append(
            f"provider slot(s) {', '.join(str(s.get('slot')) for s in unhealthy)} unhealthy — "
            f"capacity is reduced to {len(entries) - len(unhealthy)} of {len(entries)} "
            f"simultaneous viewer(s). The next build should replace them."
        )

    for entry in entries:
        exp = str(entry.get("expires_at") or "") or "unknown"
        notes.append(f"slot {entry.get('slot')}: "
                     f"{'healthy' if entry.get('healthy') else 'UNHEALTHY'}, expires {exp}, "
                     f"generation {entry.get('generation')}")
    return problems, warnings, notes
