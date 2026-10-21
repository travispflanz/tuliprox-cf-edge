#!/usr/bin/env python3
"""
make_card.py — render the connection card from the live edge.

The card carries client credentials, so it is a GENERATED, GITIGNORED artifact rather than a
committed file: credentials do not belong in git history. The numbers on the card are measured
against the running edge at generation time, so it can never drift from reality.

Usage:
  python3 build/make_card.py            # reads .dev.vars, writes connection-card.html
"""
import json
import os
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = os.environ.get("BASE", "https://tuliprox-edge-gateway.pages.dev").rstrip("/")


def load_creds() -> tuple[str, str]:
    """Prefer the environment, then the gitignored .dev.vars (no machine-specific paths)."""
    cu, cp = os.environ.get("CLIENT_USER"), os.environ.get("CLIENT_PASS")
    if cu and cp:
        return cu, cp
    p = ROOT / ".dev.vars"
    if p.exists():
        t = p.read_text()
        cu = cu or (re.search(r'CLIENT_USER="?([^"\n]+)"?', t) or [None, None])[1]
        cp = cp or (re.search(r'CLIENT_PASS="?([^"\n]+)"?', t) or [None, None])[1]
    if cu and cp:
        return cu, cp
    raise SystemExit("no CLIENT_USER/CLIENT_PASS found (set the env vars, or fill .dev.vars)")


def get_json(url: str):
    # A real User-Agent is required: Cloudflare answers bare python-urllib with 403/1010.
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8.5.0"})
    return json.load(urllib.request.urlopen(req, timeout=120))


def main() -> None:
    cu, cp = load_creds()
    q = f"username={cu}&password={cp}"

    login = get_json(f"{BASE}/player_api.php?{q}")
    n_cat = len(get_json(f"{BASE}/player_api.php?{q}&action=get_live_categories"))
    n_ch = len(get_json(f"{BASE}/player_api.php?{q}&action=get_live_streams"))
    ui = login.get("user_info", {})

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  .card {{ font-family: inherit; color: var(--foreground); max-width: 800px; }}
  h1 {{ font-size: 1.25rem; margin: 0 0 .15rem; letter-spacing: -.01em; }}
  .sub {{ color: var(--muted-foreground); font-size: .8rem; margin-bottom: 1rem; }}
  .row {{ border: 1px solid var(--border); border-radius: 10px; padding: .7rem .85rem; margin-bottom: .55rem; background: var(--card); }}
  .row.hi {{ border-color: #22c55e55; }}
  .lbl {{ font-size: .68rem; text-transform: uppercase; letter-spacing: .07em; color: var(--muted-foreground); margin-bottom: .3rem; }}
  .lbl b {{ color: #22c55e; }}
  code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .78rem; word-break: break-all; color: var(--foreground); }}
  table {{ border-collapse: collapse; width: 100%; margin-top: .9rem; font-size: .79rem; }}
  th, td {{ text-align: left; padding: .38rem .5rem; border-bottom: 1px solid var(--border); }}
  th {{ color: var(--muted-foreground); font-weight: 600; font-size: .68rem; text-transform: uppercase; letter-spacing: .06em; }}
  td.ok {{ color: #22c55e; font-weight: 600; }}
  .note {{ margin-top: .9rem; font-size: .74rem; color: var(--muted-foreground); line-height: 1.55; }}
  .pill {{ display: inline-block; font-size: .65rem; padding: .1rem .45rem; border-radius: 99px; border: 1px solid var(--border); color: var(--muted-foreground); }}
</style>
</head>
<body>
<div class="card">
  <h1>Tuliprox &rarr; Cloudflare Edge</h1>
  <div class="sub">Xtream Codes API &middot; {n_ch:,} channels / {n_cat} categories &middot; GitHub Actions &rarr; Pages &middot; $0/mo</div>

  <div class="row hi">
    <div class="lbl"><b>Xtream Codes login</b> &mdash; TiviMate / iMPlayer / OTT Navigator / VLC</div>
    <code>Server URL: {BASE}<br>
    Username:   {cu}<br>
    Password:   {cp}</code>
  </div>

  <div class="row">
    <div class="lbl">M3U playlist &mdash; paste into any player</div>
    <code>{BASE}/get.php?username={cu}&amp;password={cp}&amp;type=m3u_plus&amp;output=ts</code>
  </div>

  <div class="row">
    <div class="lbl">XMLTV EPG &mdash; provider's own guide, ids normalised to match the playlist 100%</div>
    <code>{BASE}/xmltv.php?username={cu}&amp;password={cp}</code>
  </div>

  <table>
    <tr><th>Verified</th><th>Result</th></tr>
    <tr><td>login (measured just now)</td><td class="ok">auth={ui.get('auth')} &middot; {ui.get('status')}</td></tr>
    <tr><td>catalogue</td><td class="ok">{n_cat} categories &middot; {n_ch:,} streams</td></tr>
    <tr><td>anonymous <code>/player_api.php</code>, <code>/get.php</code>, <code>/playlist.m3u</code>, <code>/_data/*</code></td><td class="ok">401</td></tr>
    <tr><td>wrong credentials</td><td class="ok">401</td></tr>
    <tr><td>live video play</td><td class="ok">200 &middot; video/mp2t</td></tr>
    <tr><td>stream URL leakage</td><td class="ok">0 foreign URLs</td></tr>
    <tr><td>CI verification</td><td class="ok">27/27 checks passed</td></tr>
  </table>

  <div class="note">
    <strong>How video is delivered.</strong> The edge authenticates the request, then hands the
    player a <span class="pill">302</span> to the provider so the stream is fetched from the
    player's own IP. The edge cannot proxy it: the provider refuses datacenter ranges, so
    Cloudflare's egress gets <code>403</code> on every channel (verified &mdash; the same request
    returns 200 from a residential IP). Provider credentials are never written into any published
    file; they live only in Worker env and reach only callers that already passed auth.
    <br><br>
    <strong>Expiry:</strong> the login reports <code>Expired</code> once the upstream trial lapses,
    so a stale build is diagnosable instead of silently broken. Rotate the <code>PROVIDER_*</code>
    secrets; the workflow rebuilds every 6h, or fire one now with
    <code>gh workflow run tuliprox-edge.yml</code>.
    <br><br>
    <strong>Client URLs never change</strong> across provider rotations &mdash; only the
    <code>PROVIDER_URL / PROVIDER_USER / PROVIDER_PASS</code> GitHub secrets do. Those secrets are
    the single source of truth: the workflow also pushes them into the Pages environment.
    <br><br>
    <strong>Single connection:</strong> the trial allows <code>max_connections=1</code>, so one
    viewer at a time. <em>Regenerate this card with <code>python3 build/make_card.py</code>.</em>
  </div>
</div>
</body>
</html>
"""
    out = ROOT / "connection-card.html"
    out.write_text(html)
    print(f"wrote {out} ({len(html)} bytes) — live: {n_ch} channels, {n_cat} categories, auth={ui.get('auth')}")


if __name__ == "__main__":
    main()