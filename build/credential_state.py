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
import hashlib
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


def mask(*values: str) -> None:
    """Register values with Actions' log masker.

    A credential restored from the state file is NOT a repository secret, and GitHub masks only
    what it was given as a secret — so without this the account appears in plain text in every
    later step's `env:` dump, readable by anyone with Actions access. Observed happening.
    """
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    for value in values:
        if value and len(value) > 3:
            print(f"::add-mask::{value}", flush=True)


def env_lines(slot: int, cred: dict) -> list[str]:
    sfx = suffix(slot)
    out = []
    mask(cred.get("user", ""), cred.get("pass", ""))
    for field in FIELDS:
        value = cred.get(field.lower(), "")
        out.append(f"PROVIDER{sfx}_{field}={value}")
        out.append(f"UPSTREAM{sfx}_{field}={value}")
    return out


def fingerprint(cred: dict) -> str:
    """A short, non-reversible marker for a configured seed value.

    Lets the next build notice that a HUMAN edited the configured credential, without the state
    file having to carry the seed itself.
    """
    blob = "|".join(str(cred.get(f.lower()) or "") for f in FIELDS)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16] if blob.strip("|") else ""


def cmd_seed(args: argparse.Namespace) -> int:
    """Pick each slot's starting credential.

    Normally the PERSISTED credential wins, because it is THAT credential's real age which decides
    when renewal is due — and because a repository secret cannot be rewritten by the workflow, so
    it would otherwise age out and trigger a needless signup on every build.

    THE EXCEPTION IS A HUMAN EDIT. If the configured value differs from the one this system last
    saw configured, someone changed it deliberately — to take manual control when automatic
    acquisition is broken — and it must win. Without this, typing a fresh credential into the
    repository settings does nothing until the cached one dies, which is the opposite of a usable
    manual override. `PREFER_SEED=1` forces the same behaviour explicitly.

    An incomplete persisted entry degrades to the configured value with a warning rather than
    taking a half-populated credential — a half-filled slot fails every stream it is handed.
    """
    state = load()
    lines: list[str] = []
    configured: list[int] = []
    forced = os.environ.get("PREFER_SEED", "").strip().lower() in ("1", "true", "yes")
    prints: dict[str, str] = {}

    for slot in SLOTS:
        sfx = suffix(slot)
        secret = {f.lower(): os.environ.get(f"SEED{sfx}_{f}", "") for f in FIELDS}
        stored = state.get(str(slot)) or {}
        stored_cred = {f.lower(): str(stored.get(f.lower()) or "") for f in FIELDS}

        seed_print = fingerprint(secret)
        prints[str(slot)] = seed_print
        # An absent record means "never tracked", NOT "changed" — otherwise the first build after
        # this feature ships would treat every credential as hand-edited and override itself.
        known_print = str(stored.get("seed_fingerprint") or "")
        edited = bool(seed_print) and bool(known_print) and seed_print != known_print

        if all(secret.values()) and (forced or edited):
            cred = secret
            source = ("configured value (PREFER_SEED was set)" if forced
                      else "configured value — IT CHANGED since the last build, so a human edited "
                           "it and it takes precedence")
            print(f"::notice::slot {slot}: using the hand-configured provider credential")
        elif all(stored_cred.values()):
            cred, source = stored_cred, "the credential persisted by the previous build"
        else:
            if any(stored_cred.values()):
                print(f"::warning::slot {slot}: the persisted credential is incomplete "
                      f"— falling back to the configured value")
            cred, source = secret, "configured value (bootstrap)"

        if all(cred.values()):
            configured.append(slot)
            lines += env_lines(slot, cred)
            print(f"slot {slot} credential source: {source}")
        else:
            # Not an error: slot 2 is optional, and an unconfigured slot is simply acquired below.
            print(f"slot {slot}: no credential yet — acquisition will mint one")

    print(f"slots with a starting credential: {','.join(map(str, configured)) or 'none'}")
    # Hand the observed seed fingerprints to `record`, so the NEXT build can tell whether the
    # configured value changed. Passed through the environment file rather than recomputed there,
    # because record() sees the resolved credential and no longer knows what was configured.
    write_env([f"SEED_FINGERPRINT_{slot}={fp}" for slot, fp in prints.items() if fp], args.out)
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
            # credential the next build could still have used. Still stamp the attempt, because
            # that stamp is what the acquisition backoff reads — without it, a slot that cannot be
            # acquired would be retried on EVERY build, which is precisely the signup churn the
            # whole design exists to avoid.
            entry = dict(prev)
            fp = os.environ.get(f"SEED_FINGERPRINT_{slot}", "")
            if fp:
                entry["seed_fingerprint"] = fp
            if status:
                entry["last_status"] = status
                entry["last_attempt_at"] = now_stamp()
            if entry:
                out_state[str(slot)] = entry
                print(f"slot {slot}: no credential in force (status {status or '?'}) "
                      f"— attempt recorded for backoff")
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
            "last_status": status or prev.get("last_status", ""),
            "last_attempt_at": now_stamp() if status else prev.get("last_attempt_at", ""),
            # What was CONFIGURED this run (not what is in force). The next build compares against
            # it to notice a hand edit; losing it would make the manual override undetectable.
            "seed_fingerprint": os.environ.get(f"SEED_FINGERPRINT_{slot}",
                                               prev.get("seed_fingerprint", "")),
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
