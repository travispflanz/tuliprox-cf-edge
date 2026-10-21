#!/usr/bin/env python3
"""
monitor_edge.py — cloud-side watchdog for the Tuliprox Xtream edge.

WHY THIS EXISTS
---------------
The edge must be supervised without depending on any single operator machine. This runs from a
scheduled GitHub Actions workflow and emits a machine-readable verdict, so the system is
monitored, alerted and documented entirely inside GitHub + Cloudflare.

It deliberately reuses build/verify_edge.py verbatim instead of reimplementing probes: the
monitor and the deploy's own smoke test therefore can never drift apart.

VERDICTS
  ok     — edge healthy, provider account has more than WARN_HOURS left
  warn   — edge healthy, but the provider account expires soon (automated renewal is overdue)
  alert  — the edge is broken (verifier failed, or the provider account is dead)

The warn/alert split matters: when a trial lapses the catalogue keeps serving 13k channels while
every stream fails, so "is the account still alive" is the signal that actually predicts breakage.

Outputs status/title/body/summary to $GITHUB_OUTPUT when running under Actions; always prints a
human-readable report. Exit code is 1 only for `alert` (so GitHub emails on real breakage).
"""
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
VERIFIER = REPO_ROOT / "build" / "verify_edge.py"

# The build renews the provider credential once a day (RENEW_EVERY_HOURS on ~36h trials) and has a
# 12h safety floor, so the healthy sawtooth never falls below ~6h before a build replaces it. This
# threshold sits BELOW that floor deliberately: in this system "expires in a few hours" no longer
# means "a human must rotate something" — it means the AUTOMATED renewal is overdue, which is a real
# defect. Warning any earlier would fire on the normal cadence and turn into noise.
WARN_HOURS = float(os.environ.get("WARN_HOURS", "6"))
# Builds run every 6h, so this much silence means the pipeline itself has stopped — the failure
# mode nobody notices, because the last-good deploy keeps serving happily.
STALE_HOURS = float(os.environ.get("STALE_HOURS", "14"))
BASE = (os.environ.get("BASE") or "").rstrip("/")
CU = os.environ.get("CLIENT_USER", "")
CP = os.environ.get("CLIENT_PASS", "")
SIMULATE = (os.environ.get("SIMULATE") or "none").strip().lower()


def log(msg=""):
    print(msg, flush=True)


def emit(**kw):
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


def http_json(path, timeout=60):
    """GET a JSON endpoint; returns (status_code, parsed_or_None). Never raises."""
    url = path if path.startswith("http") else BASE + path
    req = urllib.request.Request(url, headers={"User-Agent": "edge-monitor"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except Exception:
        return 0, None


def run_verifier():
    """Run the real verifier as a subprocess. Returns (rc, stdout)."""
    env = dict(os.environ, BASE=BASE, CLIENT_USER=CU, CLIENT_PASS=CP)
    proc = subprocess.run(
        [sys.executable, str(VERIFIER)],
        env=env, capture_output=True, text=True, timeout=1500,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


if not BASE or not CU or not CP:
    log("BASE / CLIENT_USER / CLIENT_PASS are required")
    sys.exit(2)

log(f"=== edge monitor: {BASE} (warn threshold {WARN_HOURS}h) ===\n")

problems = []
warnings = []
notes = []

# ---------------------------------------------------------------- 1. the edge itself
rc, verifier_out = run_verifier()
log("--- build/verify_edge.py ---")
log(verifier_out.strip())
checks_line = ""
for line in reversed(verifier_out.splitlines()):
    if "checks passed" in line:
        checks_line = line.strip("= ").strip()
        break

if rc != 0:
    problems.append(f"build/verify_edge.py exited {rc} — the live edge is failing its own assertions")
else:
    notes.append(f"verifier: {checks_line or 'all checks passed'}")

if SIMULATE == "alert":
    # Explicitly injected fault for testing the alerting path. The verifier above still ran for
    # real, so the attached report is genuine; this only forces the verdict.
    problems.append("simulated failure (workflow_dispatch simulate=alert)")

# ---------------------------------------------------------------- 2. the provider account
acct = {}
code, data = http_json(f"/_data/upstream.json?username={CU}&password={CP}")
if code == 200 and isinstance(data, dict):
    acct = data
    notes.append(f"account probe: /_data/upstream.json (http {code})")
else:
    # Fall back to the login payload the edge itself reports.
    code, data = http_json(f"/player_api.php?username={CU}&password={CP}")
    ui = (data or {}).get("user_info", {}) if isinstance(data, dict) else {}
    if ui:
        acct = {"auth": ui.get("auth"), "status": ui.get("status"), "exp_date": ui.get("exp_date")}
        notes.append(f"account probe: login payload fallback (http {code})")
    else:
        problems.append(f"could not read the provider account state (upstream.json http {code}, login http {code})")

auth = str(acct.get("auth", ""))
status = str(acct.get("status", "unknown"))
exp_raw = str(acct.get("exp_date") or "")
exp_ts = int(exp_raw) if exp_raw.isdigit() else 0
now = int(time.time())
hours_left = round((exp_ts - now) / 3600, 1) if exp_ts else None


def exp_str():
    if not exp_ts:
        return "unknown"
    return dt.datetime.fromtimestamp(exp_ts, dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


if auth != "1":
    problems.append(
        f"provider account is NOT active (auth={auth or '?'}, status={status}) — "
        f"the automated renewal has not replaced it. Re-run the build; if it still fails, "
        f"run the workflow with force_trial. Streams fail while the catalogue still serves."
    )
elif exp_ts and exp_ts < now:
    problems.append(
        f"provider account EXPIRED at {exp_str()} — the automated renewal did not land in time. "
        f"Re-run the build (it will acquire a fresh trial), or dispatch it with force_trial."
    )
elif hours_left is not None and hours_left < WARN_HOURS:
    warnings.append(
        f"provider account expires in {hours_left}h (at {exp_str()}) — the automated renewal is "
        f"OVERDUE: it renews at ~24h old and should never let this drop below {WARN_HOURS}h. "
        f"Check the most recent build run."
    )
else:
    notes.append(f"account: status={status} auth={auth} exp={exp_str()}"
                 + (f" ({hours_left}h left)" if hours_left is not None else ""))

# ---------------------------------------------------------------- 2b. pipeline freshness
code, meta = http_json(f"/_data/meta.json?username={CU}&password={CP}")
if code == 200 and isinstance(meta, dict) and meta.get("built_at"):
    built_at = str(meta["built_at"])
    try:
        built_ts = dt.datetime.fromisoformat(built_at.replace("Z", "+00:00")).timestamp()
        age_h = round((now - built_ts) / 3600, 1)
        if age_h > STALE_HOURS:
            problems.append(
                f"the deploy is STALE: last build was {age_h}h ago ({built_at}), but builds run every "
                f"6h — the scheduled workflow has probably stopped (GitHub disables schedules in "
                f"inactive repositories). Re-run it and check the Actions tab."
            )
        else:
            notes.append(
                f"deploy freshness: built {age_h}h ago "
                f"({meta.get('channels', '?')} channels, {meta.get('categories', '?')} categories)"
            )
    except Exception as exc:
        notes.append(f"deploy freshness: unparseable built_at={built_at!r} ({exc})")
else:
    warnings.append(f"could not read the build metadata (/_data/meta.json http {code})")

if SIMULATE == "healthy":
    # Explicitly forced-healthy verdict, to exercise the auto-close path on demand.
    problems.clear()
    warnings.clear()
    notes.append("verdict forced to ok (workflow_dispatch simulate=healthy)")

# ---------------------------------------------------------------- 3. verdict
verdict = "alert" if problems else ("warn" if warnings else "ok")

if verdict == "alert":
    title = "Edge DOWN — Tuliprox CF Edge needs attention"
elif verdict == "warn":
    title = "Edge warning — upstream trial expiring soon"
else:
    title = "Edge healthy"

lines = [f"**Verdict: `{verdict}`**  ·  monitored {dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
         f"**Target:** {BASE}", ""]
if problems:
    lines += ["### Problems"] + [f"- {p}" for p in problems] + [""]
if warnings:
    lines += ["### Warnings"] + [f"- {w}" for w in warnings] + [""]
if notes:
    lines += ["### Detail"] + [f"- {n}" for n in notes] + [""]
lines += ["---",
          "Health is checked in the cloud by `.github/workflows/edge-monitor.yml`.",
          "Operations runbook: [`docs/OPERATIONS.md`](../blob/main/docs/OPERATIONS.md)"]
body = "\n".join(lines)

summary = "; ".join(problems or warnings or notes[:1]) or "healthy"

log("\n--- verdict ---")
log(f"{verdict.upper()}: {summary}")
print()
print(body)

emit(status=verdict, title=title, body=body, summary=summary)
sys.exit(1 if verdict == "alert" else 0)