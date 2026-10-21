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
  --if-needed     reuse the credential already in $PROVIDER_* when it is still healthy, and only
                  sign up when it is dead or has less than MIN_HOURS_LEFT left. This is what the
                  build uses: signing up on every 6h build would create ~4 trials/day, and that
                  churn risks the provider treating this as automated abuse.
  --force         always acquire, even under --if-needed (for testing the acquisition path)

Env:
  TRIAL_EMAIL        full address to request with (default: a fresh alias, see below)
  TRIAL_EMAIL_BASE   local part to mint aliases from (default taskgobbler)
  TRIAL_EMAIL_DOMAIN default gmail.com
  PROVIDER_SITE      default https://www.weselliptv.com
  PANEL_URLS         comma-separated, default http://kytv.xyz,http://kemotvpromax.xyz
  TRIAL_POLL_TRIES   default 40        TRIAL_POLL_WAIT default 1.5 (seconds)
  MIN_HOURS_LEFT     default 12 — reuse threshold for --if-needed
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
POLL_TRIES = int(os.environ.get("TRIAL_POLL_TRIES") or "40")
POLL_WAIT = float(os.environ.get("TRIAL_POLL_WAIT") or "1.5")

# Under --if-needed, a credential with at least this much life left is reused rather than
# replaced. Kept well above the 6h build cadence so a healthy account is never churned.
MIN_HOURS_LEFT = float(os.environ.get("MIN_HOURS_LEFT") or "12")

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
    argv = sys.argv[1:]
    force = "--force" in argv
    if_needed = "--if-needed" in argv

    # Deliberately conservative: a healthy credential is REUSED, because signing up on every
    # build would mean ~4 new trials a day — churn that risks the provider treating this as
    # automated abuse and withdrawing the free trial entirely.
    if if_needed and not force:
        panel, user, hours = current_credential_status()
        if panel and (hours is None or hours > MIN_HOURS_LEFT):
            shown = "expiry not reported" if hours is None else f"{hours}h left"
            log(f"  reusing the credential already in force: {user} on {panel} ({shown})")
            log("  no signup made")
            emit(status="reused", username=user, url=panel)
            return 0
        log(f"  credential in force is unusable (panel={panel}, hours_left={hours}) — acquiring a fresh one")

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