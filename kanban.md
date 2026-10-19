# Kanban Board: Project Tuliprox-Cloudflare-Edge

## Done
- [x] Phase 0A: Audit `default` profile & clone `edge-architect` / `edge-builder` / `edge-auditor`
- [x] Phase 0B: Provider audit -> `MODEL_ROUTING_MATRIX.json` + per-profile `model.default`
- [x] Phase 1: `ideas.md`, `kanban.md`, GitHub + Cloudflare auth verified
- [x] Phase 2: Tuliprox CLI configs (schema validated against the real v3.3.0 binary)
- [x] Phase 3: GitHub Actions pipeline + Cloudflare Pages project live
- [x] Phase 4: Edge verified end-to-end, SKILL.md persisted, cron registered

## Verified results
- GitHub Actions run 39331949087: green in 28s (download+checksum, ingest, deploy)
- Ingest: 1482 channels, 73 groups, 0 errors
- Edge: `/get.php` 401 unauth / 200 auth (391,409 bytes, `audio/x-mpegurl`)
- Edge: `/playlist.m3u` 401 unauth via `_middleware.js` / 200 auth

## Known limitation
- No XMLTV: Tuliprox v3.3.0 has no EPG input/output variant. External EPG documented in the skill.
