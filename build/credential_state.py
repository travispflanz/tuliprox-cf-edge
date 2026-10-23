#!/usr/bin/env python3
"""credential_state.py — carry provider credentials between builds, one record per slot.

    seed     restore the credentials in force into $GITHUB_ENV (or stdout)
    record   write back what this build settled on, for the next build to age

WHY A STATE FILE AT ALL
-----------------------
`GITHUB_TOKEN` cannot rewrite a repository secret, so a secret can only ever *bootstrap* this
system. If it were also the store, it would age out and then every build would find it dead and
mint a brand-new trial — several signups a day, which is the exact abuse pattern the renewal
cadence exists to avoid. So the credentials, their acquisition times and their generation counters
live in `.cred/provider.json`, carried between runs in a rolling Actions cache.

THE INVARIANT THAT MAKES THE CADENCE WORK
-----------------------------------------
`acquired_at` is PRESERVED while a slot's credential is unchanged, and reset ONLY when the
credential actually changes. Re-stamping it every build would make every account look perpetually
brand new, so the cadence would never come due and renewal would silently never happen. The
`generation` counter increments on each real change; the renewal logic uses it to tell a
freshly-bootstrapped slot from one that has already rotated.

SLOTS
-----
Slot 1 is `PROVIDER_URL/USER/PASS` and `UPSTREAM_URL/USER/PASS`; slot N>1 adds the `_<N>` suffix
(`PROVIDER_2_URL`, `UPSTREAM_2_URL`, …). Both name sets are exported because Tuliprox's config
expands `${env:PROVIDER_*}` while the edge and probe scripts read `UPSTREAM_*`.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sys

SLOTS = [int(s) for s in (os.environ.get("CRED_SLOTS") or "1,2").split(",") if s.strip()]
CRED_FILE = pathlib.Path(os.environ.get("CRED_FILE") or ".cred/provider.json")

FIELDS = ("URL", "USER", "PASS")


def suffix(slot: int) -> str:
    return "" if slot == 1 else f"_{slot}"


def now_stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load() -> dict:
    """Read persisted state, tolerating the pre-slot flat document (read as slot 1)."""
    try:
        raw = json.loads(CRED_FILE.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    if isinstance(raw.get("slots"), dict):
        return {str(k): v for k, v in raw["slots"].items() if isinstance(v, dict)}
    if raw.get("user"):
        return {"1": raw}
    return {}


def env_lines(slot: int, cred: dict) -> list[str]:
    sfx = suffix(slot)
    out = []
    for field in FIELDS:
        value = cred.get(field.lower(), "")
        out.append(f"PROVIDER{sfx}_{field}={value}")
        out.append(f"UPSTREAM{sfx}_{field}={value}")
    return out


def cmd_seed(args: argparse.Namespace) -> int:
    """Pick each slot's starting credential: persisted state wins over the repository secret.

    The persisted one wins because it is THAT credential's real age which decides when renewal is
    due. An incomplete persisted entry degrades to the secret with a warning rather than taking a
    half-populated credential — a half-filled slot would fail every stream it was handed.
    """
    state = load()
    lines: list[str] = []
    configured: list[int] = []

    for slot in SLOTS:
        sfx = suffix(slot)
        secret = {f.lower(): os.environ.get(f"SEED{sfx}_{f}", "") for f in FIELDS}
        stored = state.get(str(slot)) or {}
        stored_cred = {f.lower(): str(stored.get(f.lower()) or "") for f in FIELDS}

        if all(stored_cred.values()):
            cred, source = stored_cred, "the credential persisted by the previous build"
        else:
            if any(stored_cred.values()):
                print(f"::warning::slot {slot}: the persisted credential is incomplete "
                      f"— falling back to the repository secret")
            cred, source = secret, "repository secret (bootstrap)"

        if all(cred.values()):
            configured.append(slot)
            lines += env_lines(slot, cred)
            print(f"slot {slot} credential source: {source}")
        else:
            # Not an error: slot 2 is optional, and an unconfigured slot is simply acquired below.
            print(f"slot {slot}: no credential yet — acquisition will mint one")

    print(f"slots with a starting credential: {','.join(map(str, configured)) or 'none'}")
    write_env(lines, args.out)
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    """Persist what this build settled on, and publish which slots are healthy.

    Health comes from the acquisition step's own verdict per slot (`ok` = freshly verified,
    `reused` = verified as still live), so it costs no extra provider requests. The edge needs it
    because the provider refuses our egress: streams go out as redirects, so the edge never
    observes a dead account and would otherwise keep handing viewers to it for hours.
    """
    state = load()
    healthy: list[int] = []
    out_state: dict[str, dict] = {}

    for slot in SLOTS:
        sfx = suffix(slot)
        cred = {f.lower(): os.environ.get(f"PROVIDER{sfx}_{f}", "") for f in FIELDS}
        status = os.environ.get(f"STATUS{sfx}", "")
        expires = os.environ.get(f"EXPIRES{sfx}", "")
        prev = state.get(str(slot)) or {}

        if not all(cred.values()):
            # Keep any previous record rather than dropping it: a transient gap must not erase a
            # credential the next build could still have used.
            if prev:
                out_state[str(slot)] = prev
            continue

        unchanged = prev.get("user") == cred["user"] and prev.get("url") == cred["url"]
        if unchanged:
            acquired = prev.get("acquired_at") or now_stamp()
            generation = prev.get("generation") or 0
            expires = expires or (prev.get("expires_at") or "")
        else:
            acquired = now_stamp()
            try:
                generation = int(prev.get("generation") or 0) + 1
            except (TypeError, ValueError):
                generation = 1

        out_state[str(slot)] = {
            **cred,
            "acquired_at": acquired,
            "expires_at": expires,
            "generation": generation,
            "source": status,
        }
        if status in ("ok", "reused"):
            healthy.append(slot)
        print(f"slot {slot}: recorded {cred['user']} (acquired {acquired}, "
              f"expires {expires or 'unknown'}, generation {generation}, status {status or '?'})")

    CRED_FILE.parent.mkdir(parents=True, exist_ok=True)
    CRED_FILE.write_text(json.dumps({"slots": out_state}), encoding="utf-8")

    # A REDACTED summary for the monitor: slot numbers, health and expiry, never a credential.
    # Without this the watchdog can only see slot 1 (the account the catalogue was built with), so
    # slot 2 could die and silently halve capacity with nothing to notice it.
    summary = os.environ.get("SLOTS_SUMMARY")
    if summary:
        pathlib.Path(summary).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(summary).write_text(json.dumps({
            "generated_at": now_stamp(),
            "slots": [
                {
                    "slot": int(slot),
                    "healthy": (entry.get("source") in ("ok", "reused")),
                    "expires_at": entry.get("expires_at") or "",
                    "generation": entry.get("generation") or 0,
                }
                for slot, entry in sorted(out_state.items())
            ],
        }), encoding="utf-8")
        print(f"wrote redacted slot summary to {summary}")

    # Fail OPEN: with no verified slot, publish nothing and let the edge try everything it has.
    # Stranding all traffic because a probe was inconclusive would be worse than a stale hint.
    hint = ",".join(map(str, healthy))
    print(f"healthy slots: {hint or '(none verified — edge will try all configured slots)'}")
    write_env([f"UPSTREAM_HEALTHY_SLOTS={hint}"], args.out)
    return 0


def write_env(lines: list[str], out: str | None) -> None:
    if not lines:
        return
    target = out or os.environ.get("GITHUB_ENV")
    text = "\n".join(lines) + "\n"
    if target:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, handler in (("seed", cmd_seed), ("record", cmd_record)):
        p = sub.add_parser(name)
        p.add_argument("--out", help="write env assignments here (default $GITHUB_ENV, else stdout)")
        p.set_defaults(func=handler)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
