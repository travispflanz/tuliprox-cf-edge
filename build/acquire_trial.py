#!/usr/bin/env python3
"""
acquire_trial.py — obtain a fresh provider trial credential straight from the provider's API.

Self-contained by design: stdlib only, no imports from any other project, no shared config, no
local paths. Verified live against the provider on 2026-10-21 (check-email -> port race -> trial
task -> poll -> panel login, yielding a working Xtream credential in ~15 seconds).

WHY THIS EXISTS
---------------
The edge's provider account expires every ~36-48h. Replacing those credentials by hand was the
last manual step in an otherwise fully cloud-only system, so the build now performs it itself.

NO EMAIL OR OTP IS EVER READ. The provider's own flow returns the credentials directly in the
completion-poll response. An email ADDRESS is still required as an *input* (the provider's dedup
is permanent per address), so a unique plus-alias is minted per request — but nothing ever logs
into that inbox, and no confirmation mail matters.

FLOW (HTTP+JSON against the provider's own API — no browser, no captcha, no form POST)
  1. GET  /api/check-email?email=<addr>        -> {"exists": bool}
        A true here means that address can never get a trial; mint another.
  2. POST /api/check-proxy {"port": N}         -> {"working": bool}
        The site's own JS races all 30 candidate ports and takes the first winner; we do the
        same concurrently rather than serially (30x faster in the worst case).
  3. POST /api/trial {"email","phone","proxyPort"} -> {"handle": {"id": <task>}}
  4. GET  /api/task-status?taskId=<id>         -> status COMPLETED -> data.output.{username,password}
  5. Verify the credential against the panel itself (auth == 1) before reporting success.

Flags:
  (none)          always acquire a fresh trial
  --if-needed     reuse the credential already in force while it is still healthy, and sign up only
                  when a renewal is actually due. This is what the build uses.
  --force         always acquire, even under --if-needed (for testing the acquisition path)
  --slot N        which credential slot to manage (default 1)

CREDENTIAL SLOTS
  Slot 1 reads PROVIDER_URL/USER/PASS; slot N>1 reads PROVIDER_<N>_*. Each slot is an independent
  account from the SAME provider, carrying its own age and its own renewal schedule.

  Why more than one: every trial allows max_connections=1, so one account means one viewer at a
  time. Because all the accounts share one catalogue, any of them can serve any channel — so a
  second slot buys a second simultaneous viewer and an account to step over when one dies.

WHEN A RENEWAL IS DUE (--if-needed)
  1. CADENCE — the credential in force is RENEW_EVERY_HOURS (default 24) old or older.
     Trials here last ~36h, so renewing once a day obtains the replacement WELL BEFORE the old one
     dies, and holds the signup rate near 1/day per slot. That restraint is the whole point:
     request churn is what risks the provider reading this as automated abuse.
  2. FLOOR — under MIN_HOURS_LEFT (default 6) of life remains, whatever the age. Covers a
     lost/unknown timestamp, a shorter-than-expected trial, or an earlier renewal that failed.
     Keep it above the build interval, so at least one more build runs while the credential lives.
  3. STAGGER — a slot other than 1 has its FIRST renewal pulled forward to RENEW_STAGGER_HOURS
     (default half the cadence). Without this, slots bootstrapped together would renew together
     and expire together, so one failed cycle would remove ALL capacity at once — the very outage
     the extra slot exists to prevent. After its first renewal a slot is out of phase and rides
     the normal cadence.

  The age is read from the state file the build persists between runs (CRED_FILE). When that file
  is absent — the very first run, bootstrapping from repository secrets — the age is UNKNOWN and
  only the floor applies. Never assume fresh: assuming fresh is what would silently skip a renewal.

Env:
  TRIAL_EMAIL        full address to request with (default: a fresh alias, see below)
  TRIAL_EMAIL_BASE   local part to mint aliases from (default taskgobbler)
  TRIAL_EMAIL_DOMAIN default gmail.com
  PROVIDER_SITE      default https://www.weselliptv.com
  PANEL_URLS         comma-separated, default http://kytv.xyz,http://kemotvpromax.xyz
  TRIAL_POLL_TRIES   default 120       TRIAL_POLL_WAIT default 1.5 (seconds)
  RENEW_EVERY_HOURS  default 24 — renewal cadence under --if-needed
  MIN_HOURS_LEFT     default 6 — renewal floor under --if-needed
  RENEW_STAGGER_HOURS  default cadence/2 — holds slot 2 out of phase with slot 1
  ACQUIRE_RETRY_HOURS  default 12 — backoff after a FAILED acquisition
  CRED_FILE          default .cred/provider.json — the renewal age is read from here
  PROVIDER_URL/USER/PASS  the credential in force, inspected by --if-needed

Writes status/username/password/url/email/expires to $GITHUB_OUTPUT when present
(status is one of ok | reused | unverified | failed).
Exit 0 on success, 1 on failure.
"""
from __future__ import annotations

import concurrent.futures
import datetime as dt
import json
import os
import random
import string
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SITE = (os.environ.get("PROVIDER_SITE") or "https://www.weselliptv.com").rstrip("/")
PANELS = [u.strip().rstrip("/") for u in
          (os.environ.get("PANEL_URLS") or "http://kytv.xyz,http://kemotvpromax.xyz").split(",") if u.strip()]

EMAIL_BASE = os.environ.get("TRIAL_EMAIL_BASE") or "taskgobbler"
EMAIL_DOMAIN = os.environ.get("TRIAL_EMAIL_DOMAIN") or "gmail.com"

# Ports the provider's own client races; kept identical to the site's behaviour.
PROXY_PORTS = range(10010, 10040)
# Wait up to ~3 minutes for the provider's task queue. Raised from 40 tries (60s) after a real
# acquisition gave up while the task was still EXECUTING: the trial may well have been created and
# then thrown away, which wastes a signup — the one thing worth avoiding here. Polling task-status
# is cheap and is NOT a signup, so a generous budget costs the provider nothing.
POLL_TRIES = int(os.environ.get("TRIAL_POLL_TRIES") or "120")
POLL_WAIT = float(os.environ.get("TRIAL_POLL_WAIT") or "1.5")

# Renewal cadence: once the credential in force reaches this age, the next build replaces it.
# Trials here live ~36h, so a 24h cadence obtains the replacement well before the old one dies and
# holds the signup rate near 1/day per slot, which is what keeps this from reading as abuse.
RENEW_EVERY_HOURS = float(os.environ.get("RENEW_EVERY_HOURS") or "24")

# Safety floor: renew regardless of age once this little life is left. Purely a net for an unknown
# timestamp, a shorter-than-expected trial, or an earlier renewal that failed — it does not drive
# the normal cadence. Must stay comfortably above the build interval so at least one more build is
# guaranteed to run while the credential is still alive.
MIN_HOURS_LEFT = float(os.environ.get("MIN_HOURS_LEFT") or "6")

# CREDENTIAL SLOTS
# Slot 1 is PROVIDER_URL/USER/PASS; slot N>1 is PROVIDER_<N>_*. Several accounts from the SAME
# provider share one catalogue, so any slot can serve any channel — which is what lets two devices
# watch at once (each trial allows max_connections=1) and lets a dead account be stepped over.
SLOT = 1

# Keep the slots out of phase. If both were acquired together they would renew together and expire
# together, so a single failed renewal cycle would take out ALL capacity at once — exactly the
# outage the second slot exists to prevent. A slot's FIRST renewal is therefore pulled forward to
# this age; afterwards it rides the normal cadence, permanently offset from the other slot.
RENEW_STAGGER_HOURS = float(os.environ.get("RENEW_STAGGER_HOURS") or str(RENEW_EVERY_HOURS / 2))

# Backoff after a FAILED acquisition. A slot with no credential at all would otherwise be retried
# on every build — at a 4h build interval that is 6 signup attempts a day against a provider whose
# goodwill this depends on. A failure is usually provider-side (a queued task that never completes,
# or a rate limit), and hammering it is exactly how the free-trial route gets withdrawn. Waiting is
# cheap: the other slot keeps serving in the meantime.
ACQUIRE_RETRY_HOURS = float(os.environ.get("ACQUIRE_RETRY_HOURS") or "12")

# The build records the credential it settled on here so the next run can age it. Absent on the
# first run (bootstrap from repository secrets), which reads as UNKNOWN age — never as fresh.
CRED_FILE = os.environ.get("CRED_FILE") or ".cred/provider.json"


def prov(name: str) -> str:
    """Read this slot's PROVIDER_* variable (slot 1 keeps the unsuffixed names)."""
    suffix = "" if SLOT == 1 else f"_{SLOT}"
    return os.environ.get(f"PROVIDER{suffix}_{name}") or ""


def load_state() -> dict:
    """Read the persisted credential state, tolerating the pre-slot flat format.

    Returns {"slots": {"1": {...}, ...}}. A legacy flat document is read as slot 1 so an existing
    deployment keeps its measured age instead of silently resetting to 'unknown' and renewing.
    """
    try:
        with open(CRED_FILE, encoding="utf-8") as fh:
            raw = json.load(fh) or {}
    except Exception:
        return {"slots": {}}
    if isinstance(raw.get("slots"), dict):
        return {"slots": raw["slots"]}
    if raw.get("user"):
        return {"slots": {"1": raw}}
    return {"slots": {}}


def slot_state(slot: int | None = None) -> dict:
    entry = load_state()["slots"].get(str(slot if slot is not None else SLOT))
    return entry if isinstance(entry, dict) else {}

# Registration edges reset connections that arrive without a browser-like UA.
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def log(msg: str = "") -> None:
    print(msg, flush=True)


def mask(*values: str) -> None:
    """Register values with Actions' log masker.

    Acquired credentials are NOT repository secrets, and GitHub only masks what it was given as a
    secret — so without this an auto-renewed account appears in plain text in every later step's
    `env:` dump, which is readable by anyone with Actions access. Observed happening.
    """
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    for value in values:
        if value and len(value) > 3:
            print(f"::add-mask::{value}", flush=True)


def emit(**kw) -> None:
    """Write workflow outputs when running under Actions (multi-line safe)."""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        for key, val in kw.items():
            val = str(val)
            if "\n" in val:
                fh.write(f"{key}<<__EOF__\n{val}\n__EOF__\n")
            else:
                fh.write(f"{key}={val}\n")


def request_json(url: str, payload: dict | None = None, timeout: int = 15) -> tuple[int, dict]:
    """GET (payload=None) or POST-JSON. Never raises: a dead request returns (0, {})."""
    headers = {"User-Agent": UA}
    if payload is None:
        req = urllib.request.Request(url, headers=headers)
    else:
        headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8", "replace") or "{}")
        except Exception:
            return exc.code, {}
    except Exception:
        return 0, {}


def mint_alias() -> str:
    tag = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
    return f"{EMAIL_BASE}+iptvedge_{tag}@{EMAIL_DOMAIN}"


def email_is_used(email: str) -> bool:
    status, data = request_json(f"{SITE}/api/check-email?" + urllib.parse.urlencode({"email": email}))
    return bool(status == 200 and data.get("exists"))


def port_works(port: int) -> int | None:
    status, data = request_json(f"{SITE}/api/check-proxy", {"port": port})
    return port if (status == 200 and data.get("working")) else None


def find_working_port() -> int | None:
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(port_works, p) for p in PROXY_PORTS]
        for fut in concurrent.futures.as_completed(futures):
            try:
                if fut.result() is not None:
                    return fut.result()
            except Exception:
                continue
    return None


def request_trial(email: str, port: int) -> tuple[bool, str, str | None]:
    status, data = request_json(f"{SITE}/api/trial",
                                {"email": email, "phone": "N/A", "proxyPort": port})
    log(f"  POST /api/trial -> HTTP {status}")
    if status not in (200, 201):
        return False, str(data.get("message") or f"HTTP {status}"), None
    task_id = (data.get("handle") or {}).get("id")
    if not task_id:
        return False, "response carried no handle.id", None
    return True, "queued", task_id


def poll_task(task_id: str) -> dict:
    """Poll until COMPLETED (returns creds) or FAILED/exhausted (returns {})."""
    for attempt in range(1, POLL_TRIES + 1):
        time.sleep(POLL_WAIT)
        status, data = request_json(
            f"{SITE}/api/task-status?" + urllib.parse.urlencode({"taskId": task_id}))
        if status != 200:
            continue
        state = data.get("status")
        if state == "COMPLETED":
            out = (data.get("data") or {}).get("output") or {}
            return {"username": out.get("username"), "password": out.get("password")}
        if state == "FAILED":
            log("  task reported FAILED")
            return {}
        if attempt % 5 == 0:
            log(f"  polling ({attempt}/{POLL_TRIES}): {state}")
    return {}


def credential_age_hours() -> float | None:
    """Hours since the credential in force was acquired, from the state file the build persists.

    Returns None when the age cannot be established (no state file yet, unreadable, malformed, or
    a future timestamp) — the caller then falls back to the expiry floor alone. It must never
    assume the credential is fresh: that is exactly what would silently skip a renewal.
    """
    try:
        stamp = slot_state().get("acquired_at")
        if not stamp:
            return None
        when = dt.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt.timezone.utc)
        hours = (dt.datetime.now(dt.timezone.utc) - when).total_seconds() / 3600
        return round(hours, 2) if hours >= 0 else None
    except Exception:
        return None


def hours_since_last_attempt() -> float | None:
    """Hours since this slot's last acquisition attempt, or None if never attempted.

    None means "no record of an attempt", which must NOT be read as "attempted long ago" — the
    backoff only suppresses a signup when it can prove a recent failure.
    """
    try:
        stamp = slot_state().get("last_attempt_at")
        if not stamp:
            return None
        when = dt.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt.timezone.utc)
        hours = (dt.datetime.now(dt.timezone.utc) - when).total_seconds() / 3600
        return round(hours, 2) if hours >= 0 else None
    except Exception:
        return None


def current_credential_status() -> tuple[str | None, str, float | None]:
    """Inspect the credential already in the environment (PROVIDER_*).

    Returns (working_panel_url_or_None, username, hours_left_or_None). `hours_left` is None when
    the panel reports no expiry — treated by the caller as usable, because re-signing-up on an
    account that authenticates right now would burn a trial for nothing.
    """
    url = prov("URL").rstrip("/")
    user = prov("USER")
    pwd = prov("PASS")
    if not (user and pwd):
        return None, user, 0.0
    for panel in ([url] if url else PANELS):
        status, data = request_json(
            f"{panel}/player_api.php?" + urllib.parse.urlencode({"username": user, "password": pwd}),
            timeout=25)
        info = (data or {}).get("user_info") or {}
        if status == 200 and str(info.get("auth")) == "1":
            raw = info.get("exp_date")
            if isinstance(raw, (int, str)) and str(raw).isdigit():
                return panel, user, round((int(raw) - time.time()) / 3600, 1)
            return panel, user, None
    return None, user, 0.0


def verify_credential(user: str, pwd: str, attempts: int = 6, wait: int = 10) -> tuple[str | None, int]:
    """Confirm the credential is live on the panel. Returns (working_panel_url, exp_epoch).

    Retries deliberately: the panel needs a short moment after the task reports COMPLETED before
    the account actually authenticates. Observed live — an immediate check fails while the very
    same credential succeeds seconds later — so checking once would report a good credential as
    broken and throw away a valid trial.
    """
    last = "no attempt made"
    for attempt in range(1, attempts + 1):
        for panel in PANELS:
            status, data = request_json(
                f"{panel}/player_api.php?" + urllib.parse.urlencode({"username": user, "password": pwd}),
                timeout=25)
            info = (data or {}).get("user_info") or {}
            if status == 200 and str(info.get("auth")) == "1":
                raw = info.get("exp_date")
                exp = int(raw) if isinstance(raw, (int, str)) and str(raw).isdigit() else 0
                return panel, exp
            last = f"{panel} -> HTTP {status} auth={info.get('auth')}"
        if attempt < attempts:
            log(f"  not active yet ({last}) — retry {attempt}/{attempts - 1} in {wait}s")
            time.sleep(wait)
    log(f"  gave up: {last}")
    return None, 0


def cadence_for_slot() -> float:
    """The age at which THIS slot's renewal falls due.

    Slot 1 always rides the plain cadence. Another slot's FIRST renewal is pulled forward to
    RENEW_STAGGER_HOURS so the slots stop expiring in lockstep; once it has renewed at least once
    (generation > 0) it is already out of phase and uses the normal cadence.
    """
    if SLOT == 1:
        return RENEW_EVERY_HOURS
    generation = slot_state().get("generation") or 0
    try:
        generation = int(generation)
    except (TypeError, ValueError):
        generation = 0
    return RENEW_EVERY_HOURS if generation > 0 else min(RENEW_STAGGER_HOURS, RENEW_EVERY_HOURS)


def main() -> int:
    global SLOT
    argv = sys.argv[1:]
    force = "--force" in argv
    if_needed = "--if-needed" in argv
    if "--slot" in argv:
        try:
            SLOT = int(argv[argv.index("--slot") + 1])
        except (IndexError, ValueError):
            log("  FAIL: --slot needs an integer")
            return 1

    # Deliberately conservative: a healthy credential is REUSED until its renewal is actually due
    # — see the CADENCE and FLOOR rules at the top of this file. Signing up on every build would
    # mean several trials/day per slot, and that churn is what risks the provider treating this as
    # automated abuse and withdrawing the free trial entirely.
    if if_needed and not force:
        panel, user, hours = current_credential_status()
        age = credential_age_hours()
        cadence = cadence_for_slot()
        usable = bool(panel) and (hours is None or hours > MIN_HOURS_LEFT)
        due = age is not None and age >= cadence
        if usable and not due:
            shown = "expiry not reported" if hours is None else f"{hours}h left"
            aged = "age unknown (bootstrap)" if age is None else f"age {age}h"
            log(f"  slot {SLOT}: reusing the credential already in force: {user} on {panel} "
                f"({shown}, {aged})")
            log(f"  no signup made — renewal falls due at {cadence}h old or {MIN_HOURS_LEFT}h left")
            emit(status="reused", username=user, url=panel, slot=SLOT,
                 age_hours="" if age is None else age)
            return 0
        reasons = []
        if not usable:
            reasons.append(f"panel={panel}, hours_left={hours}")
        if due:
            reasons.append(f"age {age}h has reached the {cadence}h cadence")
        log(f"  slot {SLOT}: renewal due ({'; '.join(reasons)})")

        # Respect the backoff before touching the provider's signup endpoint.
        waited = hours_since_last_attempt()
        last = slot_state().get("last_status") or ""
        if last and last not in ("ok", "reused") and waited is not None \
                and waited < ACQUIRE_RETRY_HOURS:
            log(f"  slot {SLOT}: the last attempt failed ({last}) {waited}h ago — backing off "
                f"until {ACQUIRE_RETRY_HOURS}h have passed. NO signup made.")
            emit(status="backoff", slot=SLOT, reason=f"last_attempt_{last}_{waited}h_ago")
            return 0
        log(f"  slot {SLOT}: acquiring a fresh trial")

    email = os.environ.get("TRIAL_EMAIL") or mint_alias()
    log(f"=== acquiring a provider trial via API (slot {SLOT}) ===")
    log(f"  site : {SITE}")
    log(f"  email: {email}   (input only — nothing reads this inbox)")

    if email_is_used(email):
        log("  FAIL: this address already has a trial (dedup is permanent per address)")
        emit(status="failed", slot=SLOT, reason="email_already_used")
        return 1

    port = find_working_port()
    if port is None:
        log("  FAIL: no working proxy port (provider-side gate)")
        emit(status="failed", slot=SLOT, reason="no_working_proxy_port")
        return 1
    log(f"  working proxy port: {port}")

    ok, detail, task_id = request_trial(email, port)
    if not ok or not task_id:
        log(f"  FAIL: trial request rejected — {detail}")
        emit(status="failed", slot=SLOT, reason=detail)
        return 1

    creds = poll_task(task_id)
    user, pwd = creds.get("username"), creds.get("password")
    if not (user and pwd):
        log("  FAIL: task did not complete with credentials")
        emit(status="failed", slot=SLOT, reason="no_credentials_from_task")
        return 1
    # Mask before anything else can echo them into a log.
    mask(user, pwd)
    log(f"  credentials issued: username={user}")

    panel, exp = verify_credential(user, pwd)
    if not panel:
        log("  FAIL: issued credential did not authenticate within the retry window")
        # Never discard it silently — a trial that hasn't gone live yet is still real, and
        # without this the credential is unrecoverable and undiagnosable.
        log(f"  issued but unverified: username={user} password={pwd}")
        emit(status="unverified", slot=SLOT, username=user, password=pwd,
             reason="issued_credential_not_active")
        return 1

    exp_str = dt.datetime.fromtimestamp(exp, dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if exp else "unknown"
    log(f"  VERIFIED on {panel} — expires {exp_str}")
    log(f"ACQUIRED: slot={SLOT} username={user} url={panel}")

    emit(status="ok", slot=SLOT, username=user, password=pwd, url=panel,
         email=email, expires=exp or "", expires_at=exp_str)
    return 0


if __name__ == "__main__":
    sys.exit(main())