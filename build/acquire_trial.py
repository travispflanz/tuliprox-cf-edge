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
  --if-needed     reuse the credential already in $PROVIDER_* while it is still healthy, and sign
                  up only when a renewal is actually due. This is what the build uses.
  --force         always acquire, even under --if-needed (for testing the acquisition path)

WHEN A RENEWAL IS DUE (--if-needed)
  1. CADENCE — the credential in force is RENEW_EVERY_HOURS (default 24) old or older.
     Trials here last ~36h, so renewing once a day leaves ~12h of overlap: a fresh credential is
     always obtained WELL BEFORE the old one dies, so the playlist never has a window where its
     account is dead. It also holds the signup rate at ~1/day instead of ~4/day, which is the
     whole point — request churn is what risks the provider reading this as automated abuse.
  2. FLOOR — under MIN_HOURS_LEFT (default 12) of life remains, whatever the age. Covers a
     lost/unknown timestamp, a shorter-than-expected trial, or an earlier renewal that failed.

  The age is read from the state file the build persists between runs (CRED_FILE). When that file
  is absent — the very first run, bootstrapping from repository secrets — the age is UNKNOWN and
  only the floor applies. Never assume fresh: assuming fresh is what would silently skip a renewal.

PROVIDER IDENTITY — REQUIRED, AND DELIBERATELY NOT DEFAULTED
------------------------------------------------------------
This script targets ONE named third party. Hardcoding who that is would pair a working automated
free-trial signup script with the provider it targets the moment this repository is public, so the
identity is supplied by the environment (repository secrets in CI) and the script refuses to run
without it. Nothing in this file names a host.

Env (REQUIRED — exits 2 if unset):
  PROVIDER_SITE      the provider's trial-signup base URL
  PANEL_URLS         comma-separated Xtream panel base URLs to race
  TRIAL_EMAIL_BASE   local part to mint the signup aliases from
Env (optional):
  TRIAL_EMAIL        full address to request with (default: a fresh alias, see below)
  TRIAL_EMAIL_DOMAIN default gmail.com
  TRIAL_POLL_TRIES   default 40        TRIAL_POLL_WAIT default 1.5 (seconds)
  RENEW_EVERY_HOURS  default 24 — renewal cadence under --if-needed
  MIN_HOURS_LEFT     default 12 — renewal floor under --if-needed
  CRED_FILE          default .cred/provider.json — the renewal age is read from here
  PROVIDER_URL/USER/PASS  the credential in force, inspected by --if-needed

Writes status/username/password/url/email/expires to $GITHUB_OUTPUT when present
(status is one of ok | reused | unverified | failed).
Exit 0 on success, 1 on failure, 2 when a required setting is missing.
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

# The provider identity. Read from the environment with NO defaults — a default would name the
# third party in this file, and this file is what gets published. Validated in main() rather than
# here, so that importing this module stays free of side effects: the offline harnesses import it
# to exercise the renewal logic and must not need to know who the provider is.
SITE = (os.environ.get("PROVIDER_SITE") or "").strip().rstrip("/")
PANELS = [u.strip().rstrip("/")
          for u in (os.environ.get("PANEL_URLS") or "").split(",") if u.strip()]
EMAIL_BASE = (os.environ.get("TRIAL_EMAIL_BASE") or "").strip()
EMAIL_DOMAIN = os.environ.get("TRIAL_EMAIL_DOMAIN") or "gmail.com"


def require_provider_identity() -> None:
    """Fail loudly if the provider identity is absent — naming the setting, never a value.

    Loud rather than silent because an empty value would be sent to the provider as a request to
    the empty host, which surfaces as a confusing network error far from its real cause.
    """
    missing = [n for n, v in (("PROVIDER_SITE", SITE), ("PANEL_URLS", PANELS),
                              ("TRIAL_EMAIL_BASE", EMAIL_BASE)) if not v]
    if missing:
        print(f"ERROR: missing required setting(s): {', '.join(missing)} — the provider identity "
              f"is supplied by the environment (repository secrets in CI) and deliberately has no "
              f"default in this file.", file=sys.stderr, flush=True)
        sys.exit(2)

# Ports the provider's own client races; kept identical to the site's behaviour.
PROXY_PORTS = range(10010, 10040)
POLL_TRIES = int(os.environ.get("TRIAL_POLL_TRIES") or "40")
POLL_WAIT = float(os.environ.get("TRIAL_POLL_WAIT") or "1.5")

# Renewal cadence: once the credential in force reaches this age, the next build replaces it.
# Trials here live ~36h, so a 24h cadence leaves ~12h of overlap — the fresh credential is obtained
# well before the old one dies, so the playlist never has a window with a dead account. It also
# holds the signup rate at ~1/day instead of ~4/day, which is what would read as abuse.
RENEW_EVERY_HOURS = float(os.environ.get("RENEW_EVERY_HOURS") or "24")

# Safety floor: renew regardless of age once this little life is left. This is purely the net for an
# unknown timestamp, a shorter-than-expected trial, or an earlier renewal that failed — it does not
# drive the normal cadence.
MIN_HOURS_LEFT = float(os.environ.get("MIN_HOURS_LEFT") or "12")

# The build records the credential it settled on here so the next run can age it. Absent on the
# first run (bootstrap from repository secrets), which reads as UNKNOWN age — never as fresh.
CRED_FILE = os.environ.get("CRED_FILE") or ".cred/provider.json"

# Registration edges reset connections that arrive without a browser-like UA.
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def log(msg: str = "") -> None:
    print(msg, flush=True)


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
        with open(CRED_FILE, encoding="utf-8") as fh:
            stamp = (json.load(fh) or {}).get("acquired_at")
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
    url = (os.environ.get("PROVIDER_URL") or "").rstrip("/")
    user = os.environ.get("PROVIDER_USER") or ""
    pwd = os.environ.get("PROVIDER_PASS") or ""
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


def main() -> int:
    require_provider_identity()
    argv = sys.argv[1:]
    force = "--force" in argv
    if_needed = "--if-needed" in argv

    # Deliberately conservative: a healthy credential is REUSED until its renewal is actually due
    # — see the CADENCE and FLOOR rules at the top of this file. Signing up on every 6h build would
    # mean ~4 trials/day, and that churn is what risks the provider treating this as automated
    # abuse and withdrawing the free trial entirely.
    if if_needed and not force:
        panel, user, hours = current_credential_status()
        age = credential_age_hours()
        usable = bool(panel) and (hours is None or hours > MIN_HOURS_LEFT)
        due = age is not None and age >= RENEW_EVERY_HOURS
        if usable and not due:
            shown = "expiry not reported" if hours is None else f"{hours}h left"
            aged = "age unknown (bootstrap)" if age is None else f"age {age}h"
            log(f"  reusing the credential already in force: {user} on {panel} ({shown}, {aged})")
            log(f"  no signup made — renewal falls due at {RENEW_EVERY_HOURS}h old "
                f"or {MIN_HOURS_LEFT}h left")
            emit(status="reused", username=user, url=panel,
                 age_hours="" if age is None else age)
            return 0
        reasons = []
        if not usable:
            reasons.append(f"panel={panel}, hours_left={hours}")
        if due:
            reasons.append(f"age {age}h has reached the {RENEW_EVERY_HOURS}h cadence")
        log(f"  renewal due ({'; '.join(reasons)}) — acquiring a fresh trial")

    email = os.environ.get("TRIAL_EMAIL") or mint_alias()
    log("=== acquiring a provider trial via API ===")
    log(f"  site : {SITE}")
    log(f"  email: {email}   (input only — nothing reads this inbox)")

    if email_is_used(email):
        log("  FAIL: this address already has a trial (dedup is permanent per address)")
        emit(status="failed", reason="email_already_used")
        return 1

    port = find_working_port()
    if port is None:
        log("  FAIL: no working proxy port (provider-side gate)")
        emit(status="failed", reason="no_working_proxy_port")
        return 1
    log(f"  working proxy port: {port}")

    ok, detail, task_id = request_trial(email, port)
    if not ok or not task_id:
        log(f"  FAIL: trial request rejected — {detail}")
        emit(status="failed", reason=detail)
        return 1

    creds = poll_task(task_id)
    user, pwd = creds.get("username"), creds.get("password")
    if not (user and pwd):
        log("  FAIL: task did not complete with credentials")
        emit(status="failed", reason="no_credentials_from_task")
        return 1
    log(f"  credentials issued: username={user}")

    panel, exp = verify_credential(user, pwd)
    if not panel:
        log("  FAIL: issued credential did not authenticate within the retry window")
        # Never discard it silently — a trial that hasn't gone live yet is still real, and
        # without this the credential is unrecoverable and undiagnosable.
        log(f"  issued but unverified: username={user} password={pwd}")
        emit(status="unverified", username=user, password=pwd,
             reason="issued_credential_not_active")
        return 1

    exp_str = dt.datetime.fromtimestamp(exp, dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if exp else "unknown"
    log(f"  VERIFIED on {panel} — expires {exp_str}")
    log(f"ACQUIRED: username={user} url={panel}")

    emit(status="ok", username=user, password=pwd, url=panel,
         email=email, expires=exp or "", expires_at=exp_str)
    return 0


if __name__ == "__main__":
    sys.exit(main())