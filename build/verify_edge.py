#!/usr/bin/env python3
"""
verify_edge.py — end-to-end verification of the Tuliprox Xtream edge.

Runs against ANY base URL (local `wrangler pages dev` or the deployed *.pages.dev),
so the exact same assertions gate a local change and a production smoke test.

Usage:
  BASE=http://127.0.0.1:8788 CLIENT_USER=... CLIENT_PASS=... python3 build/verify_edge.py
Exits non-zero if any check fails.
"""
import gzip
import json
import os
import re
import sys
import urllib.error
import urllib.request

BASE = (os.environ.get("BASE") or (sys.argv[1] if len(sys.argv) > 1 else "")).rstrip("/")
CU = os.environ.get("CLIENT_USER", "")
CP = os.environ.get("CLIENT_PASS", "")
if not BASE:
    print("BASE is required (env BASE or argv[1])")
    sys.exit(2)
if not CU or not CP:
    print("CLIENT_USER / CLIENT_PASS are required")
    sys.exit(2)

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{('  — ' + detail) if detail else ''}")
    return ok


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Stop urllib from following a 302, so the edge's own response headers stay observable."""

    def redirect_request(self, *a, **k):
        return None


def req(path, timeout=90, method="GET"):
    url = path if path.startswith("http") else BASE + path
    r = urllib.request.Request(url, method=method, headers={"User-Agent": "verify-edge"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


Q = f"username={CU}&password={CP}"
print(f"=== verifying {BASE} ===\n")

# 1. everything sensitive must refuse anonymous callers -------------------------
for p in ["/player_api.php", "/playlist.m3u", "/_data/live_streams.json", "/_data/meta.json", "/get.php"]:
    code, _, _ = req(p, timeout=30)
    check(f"anon {p} -> 401", code == 401, f"got {code}")

code, _, _ = req(f"/player_api.php?username=wrong&password=wrong", timeout=30)
check("bad credentials -> 401", code == 401, f"got {code}")

# 2. login ----------------------------------------------------------------------
code, body, _ = req(f"/player_api.php?{Q}")
ok = code == 200
try:
    login = json.loads(body)
except Exception:
    login = {}
check("login returns user_info", ok and "user_info" in login, f"http {code}")
check(
    "login carries server_info.url",
    "server_info" in login and login.get("server_info", {}).get("url"),
    str(login.get("server_info", {}).get("url", "")),
)
print(f"      account status: {login.get('user_info', {}).get('status')} "
      f"exp={login.get('user_info', {}).get('exp_date')}")

# 3. xtream catalogue ----------------------------------------------------------
code, body, _ = req(f"/player_api.php?{Q}&action=get_live_categories")
try:
    cats = json.loads(body)
except Exception:
    cats = []
check("get_live_categories", code == 200 and isinstance(cats, list) and len(cats) > 0, f"{len(cats)} categories")
check("category_id is a string (Xtream-compatible)", all(isinstance(c.get("category_id"), str) for c in cats))

code, body, _ = req(f"/player_api.php?{Q}&action=get_live_streams")
try:
    streams = json.loads(body)
except Exception:
    streams = []
check("get_live_streams", code == 200 and len(streams) > 100, f"{len(streams)} streams")
REQ_KEYS = {"num", "name", "stream_type", "stream_id", "stream_icon", "epg_channel_id",
            "added", "category_id", "custom_sid", "tv_archive", "direct_source", "tv_archive_duration"}
check("stream objects carry every Xtream key", bool(streams) and REQ_KEYS <= set(streams[0].keys()),
      f"missing {sorted(REQ_KEYS - set(streams[0].keys())) if streams else 'n/a'}")

if cats:
    cid = cats[0]["category_id"]
    code, body, _ = req(f"/player_api.php?{Q}&action=get_live_streams&category_id={cid}")
    try:
        sub = json.loads(body)
    except Exception:
        sub = []
    check(f"category filter ({cid}) returns only that category",
          code == 200 and len(sub) > 0 and all(s["category_id"] == cid for s in sub), f"{len(sub)} streams")

for a in ["get_vod_categories", "get_vod_streams", "get_series", "get_short_epg"]:
    code, body, _ = req(f"/player_api.php?{Q}&action={a}", timeout=30)
    ok = code == 200
    try:
        ok = ok and json.loads(body) == []
    except Exception:
        ok = False
    check(f"{a} -> []", ok)

code, body, _ = req(f"/player_api.php?{Q}&action=get_vod_info&vod_id=1", timeout=30)
try:
    ok = code == 200 and json.loads(body) == {}
except Exception:
    ok = False
check("get_vod_info -> {}", ok)

# 4. playlist ------------------------------------------------------------------
code, body, _ = req(f"/get.php?{Q}&type=m3u_plus&output=ts")
text = body.decode("utf-8", "replace")
n_chan = text.count("#EXTINF")
check("get.php serves EXTM3U", code == 200 and text.startswith("#EXTM3U"), f"http {code}")
check("playlist has channels", n_chan > 100, f"{n_chan} channels")
urls = re.findall(r"^(https?://\S+)$", text, re.M)
check("every stream URL points at this edge (no upstream host leaks)",
      bool(urls) and all(u.startswith(BASE) for u in urls),
      f"{len(urls)} urls, {sum(1 for u in urls if not u.startswith(BASE))} foreign")

# 5. EPG -----------------------------------------------------------------------
code, body, hdrs = req(f"/xmltv.php?{Q}")
enc = (hdrs.get("Content-Encoding") or "").lower()
raw = gzip.decompress(body) if "gzip" in enc else body
check("xmltv.php returns XMLTV", code == 200 and raw[:60].lstrip().startswith(b"<?xml"), f"http {code}")
check("guide is not double-encoded", b"<?xml" in raw[:200], f"enc={enc or 'none'}")

# 6. the real thing: a stream through the edge --------------------------------
if streams:
    # Channels are individually unreliable (real IPTV: some are dead upstream) and the trial
    # account allows max_connections=1, so probe SEQUENTIALLY and accept the first success.
    step = max(1, len(streams) // 12)
    candidates = [streams[i]["stream_id"] for i in range(0, len(streams), step)][:12]
    got = None
    tried = 0
    for sid in candidates:
        tried += 1
        try:
            r = urllib.request.Request(f"{BASE}/live/{CU}/{CP}/{sid}.ts",
                                       headers={"User-Agent": "VLC/3.0.20", "Range": "bytes=0-400000"})
            with urllib.request.urlopen(r, timeout=35) as resp:
                chunk = resp.read(400_000)
                ct = (resp.headers.get("Content-Type") or "")
        except Exception:
            continue
        if len(chunk) > 1000 and chunk[0] == 0x47 and chunk[188] == 0x47 and "mp2t" in ct:
            got = (sid, ct, len(chunk))
            break

    check("live stream proxies as MPEG-TS", got is not None,
          f"sid={got[0]} ct={got[1]} bytes={got[2]}" if got
          else f"no working channel in {tried} probed (dead channels / max_connections=1)")

    # Report HOW the edge delivered it: a proxied body (credentials fully hidden) or a 302 to
    # the provider because our egress is refused (common for datacenter/Cloudflare IPs).
    if got is not None:
        opener = urllib.request.build_opener(NoRedirect)
        try:
            r = urllib.request.Request(f"{BASE}/live/{CU}/{CP}/{got[0]}.ts",
                                       headers={"User-Agent": "VLC/3.0.20"})
            with opener.open(r, timeout=35) as resp:
                mode = resp.headers.get("X-Stream-Mode", "")
                check("stream delivery mode is reported", mode in ("proxy", "redirect-fallback"), f"mode={mode}")
                if mode == "redirect-fallback":
                    print(f"      provider refuses our egress "
                          f"({resp.headers.get('X-Stream-Fallback-Reason', '?')}); "
                          f"player fetches from its own IP")
        except urllib.error.HTTPError as e:
            mode = e.headers.get("X-Stream-Mode", "") if e.headers else ""
            check("stream delivery mode is reported", mode in ("proxy", "redirect-fallback"),
                  f"http {e.code} mode={mode}")
        except Exception as e:
            check("stream delivery mode is reported", False, f"{e}")

    # ---------------------------------------------------------------- credential slots
    # Capacity is invisible from a single client: the catalogue loads identically whether one
    # account or two are live, so a dead slot silently halves how many people can watch with
    # nothing in the playlist to show it. These assertions are the only live signal.
    print("\n  -- credential slots --")
    code, body, _ = req(f"/_data/slots.json?username={CU}&password={CP}", timeout=30)
    doc: dict = {}
    if code == 200:
        try:
            doc = json.loads(body)
            slots = doc.get("slots") or []
        except Exception:
            slots = []
        check("the per-slot health summary is published", bool(slots), f"body={body[:120]!r}")
        if slots:
            healthy = [s for s in slots if s.get("healthy")]
            check("every configured slot is healthy",
                  len(healthy) == len(slots),
                  "unhealthy: " + ", ".join(str(s.get("slot")) for s in slots
                                            if not s.get("healthy")))
            check("two slots are configured (two simultaneous viewers)",
                  len(slots) >= 2, f"only {len(slots)} slot(s) — capacity is {len(slots)} viewer")
            # Each slot is a separate trial, so a shared expiry means they were acquired together
            # and will DIE together — the staggering exists precisely to prevent that.
            stamps = {str(s.get("expires_at") or "") for s in slots if s.get("expires_at")}
            check("the slots do not expire at the same moment",
                  len(stamps) == len(slots), f"expiries={sorted(stamps)}")
            # The summary must never carry a credential: it is served to any authenticated client.
            blob = json.dumps(doc)
            check("the summary leaks no credential",
                  not any(k in blob for k in ("pass", "user", "url")), blob[:140])
    else:
        check("the per-slot health summary is published", False, f"http {code}")

    if got is not None:
        slot_seen = []
        for _ in range(3):
            try:
                r = urllib.request.Request(f"{BASE}/live/{CU}/{CP}/{got[0]}.ts",
                                           headers={"User-Agent": "VLC/3.0.20"})
                with urllib.request.build_opener(NoRedirect).open(r, timeout=35) as resp:
                    slot_seen.append(resp.headers.get("X-Stream-Slot", ""))
            except urllib.error.HTTPError as e:
                slot_seen.append(e.headers.get("X-Stream-Slot", "") if e.headers else "")
            except Exception:
                slot_seen.append("")
        check("the serving slot is reported", any(slot_seen), f"headers={slot_seen}")
        # Stability is the whole design: a slot chosen per REQUEST would scatter one player across
        # both accounts, occupy both max_connections=1 slots, and lock out the second viewer.
        check("the same viewer is served by a STABLE slot",
              len(set(filter(None, slot_seen))) <= 1, f"slots seen={slot_seen}")

    sid_probe = got[0] if got else candidates[0]
    for path, want, label in [
        (f"/live/bad/bad/{sid_probe}.ts", 401, "wrong creds on stream -> 401"),
        (f"/live/{CU}/{CP}/nonsense.ts", 400, "malformed stream id -> 400"),
    ]:
        code, _, _ = req(path, timeout=30)
        check(label, code == want, f"got {code}")

# ------------------------------------------------------------------------------
failed = [r for r in results if not r[1]]
print(f"\n=== {len(results) - len(failed)}/{len(results)} checks passed ===")
if failed:
    print("FAILURES:")
    for name, _, detail in failed:
        print(f"  - {name} ({detail})")
sys.exit(1 if failed else 0)