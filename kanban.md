# Kanban Board: Project Tuliprox-Cloudflare-Edge

## Done
- [x] Phase 0A: Audit `default` profile & clone `edge-architect` / `edge-builder` / `edge-auditor`
- [x] Phase 0B: Provider audit -> `MODEL_ROUTING_MATRIX.json` + per-profile `model.default`
- [x] Phase 1: `ideas.md`, `kanban.md`, GitHub + Cloudflare auth verified
- [x] Phase 2: Tuliprox CLI configs (schema validated against the real v3.3.0 binary)
- [x] Phase 3: GitHub Actions pipeline + Cloudflare Pages project live
- [x] Phase 4: Edge verified end-to-end, SKILL.md persisted, cron registered
- [x] Phase 5: **Native Xtream Codes API** served from static assets + stream delivery
- [x] Phase 6: Production hardening — rotated weak `admin`/`tuliprox2026` client creds,
      credential-leak gate, secret validation, EPG shipped and normalised, 27-assertion verifier

## Verified results (production edge)
- CI run `39500239940`: **success**, 27/27 checks passed on the live edge
- Login: `auth=1`, `status=Active`, formats `[m3u8, ts]`
- Catalogue: **301 categories / 13,651 live streams**, all 12 Xtream keys present
- Playlist `/get.php`: 13,651 channels, **0 foreign URLs** (no provider host leaked)
- EPG `/xmltv.php`: 4,772 channels / 62,848 programmes, 100% of playlist ids matched
- Auth: anonymous and wrong-creds -> `401` on `player_api.php`, `get.php`, `playlist.m3u`, `_data/*`
- Stream: real **26.6 MB MPEG-TS** play, sync bytes `0x47` at 0/188/376

## Known limitations (honest)
- **Video is not proxied through the edge.** The provider blocks datacenter ranges, so Cloudflare's
  egress gets `403` on every channel. The edge returns a `302` and the player fetches from its own
  IP. Providers that permit Cloudflare egress proxy normally (verified working locally, `mode=proxy`).
- **`max_connections=1`** on the upstream trial — one viewer at a time.
- **Upstream trials are short-lived (~36h)**; the pipeline refreshes every 6h and the login response
  reports `Expired` when the provider lapses.
- Live-only build (VOD/series skipped) to stay inside the Pages 25 MiB per-file ceiling.
- The watchdog script lives outside the repo (`~/.hermes/scripts/` + profile copy), so it is not
  version-controlled alongside the pipeline.