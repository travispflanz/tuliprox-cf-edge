#!/usr/bin/env node
/**
 * make_xtream.mjs — turn Tuliprox's own output into an edge-servable Xtream API.
 *
 * WHY THIS EXISTS
 * ---------------
 * `tuliprox` writes its Xtream output as a binary B+Tree (`live.db`/`*.idx`) that only
 * tuliprox's OWN server can read, plus `cat_*.json`. Cloudflare Pages runs no such server,
 * so we cannot ship the DB. But the m3u Tuliprox also emits already carries every field the
 * Xtream API needs (name, logo, group, tvg-id, channel number) and the upstream stream URL
 * is DETERMINISTIC: <host>/live/<user>/<pass>/<id>.ts
 *
 * So we index Tuliprox's m3u once, at build time, into static JSON the edge can pipe
 * through with ~0 CPU. The upstream credentials are NEVER written to disk here: the edge
 * reconstructs any stream URL from the stream id using env vars at request time.
 *
 * All playlist fetching / filtering / normalising is done by the official Tuliprox binary.
 * This script only re-shapes its output — it does not reimplement a playlist parser.
 */
import fs from 'node:fs';
import path from 'node:path';

const WORK = process.env.WORK_DIR || 'work';
const OUT = process.env.OUT_DIR || 'dist';
const EDGE = (process.env.EDGE_ORIGIN || '').replace(/\/+$/, '');
const CU = process.env.CLIENT_USER || '';
const CP = process.env.CLIENT_PASS || '';
const M3U_NAME = process.env.M3U_NAME || 'edge_list.m3u';
const TARGET = process.env.TARGET_DIR || 'edge_list';
const EPG_SRC = process.env.EPG_SRC || '';

if (!EDGE) throw new Error('EDGE_ORIGIN is required (e.g. https://tuliprox-edge-gateway.pages.dev)');
if (!CU || !CP) throw new Error('CLIENT_USER and CLIENT_PASS are required (baked into playlist URLs)');

const srcM3u = path.join(WORK, M3U_NAME);
if (!fs.existsSync(srcM3u)) throw new Error(`missing ${srcM3u} — did Tuliprox run?`);

// ---------------------------------------------------------------- parse the m3u
const text = fs.readFileSync(srcM3u, 'utf8');

/** Pull `key="value"` attributes and the display name off one #EXTINF line. */
function parseExtinf(line) {
  const attrs = {};
  const re = /([A-Za-z0-9_-]+)="([^"]*)"/g;
  let m;
  while ((m = re.exec(line)) !== null) attrs[m[1]] = m[2];
  // The display name follows the LAST closing quote, so commas inside it are safe.
  const cut = line.lastIndexOf('",');
  const name = cut >= 0 ? line.slice(cut + 2).trim() : (attrs['tvg-name'] || '');
  return { attrs, name };
}

const raw = [];
{
  let cur = null;
  for (const line of text.split('\n')) {
    const l = line.trim();
    if (l.startsWith('#EXTINF')) {
      cur = parseExtinf(l);
    } else if (cur && /^https?:\/\//i.test(l)) {
      cur.url = l;
      raw.push(cur);
      cur = null;
    }
  }
}

// ---------------------------------------------------------------- categories
// Tuliprox already resolved the group tree for us in cat_live.json.
const catFile = path.join(WORK, TARGET, 'xtream', 'cat_live.json');
const categories = [];
const groupToCat = new Map();
if (fs.existsSync(catFile)) {
  for (const c of JSON.parse(fs.readFileSync(catFile, 'utf8'))) {
    const id = String(c.category_id);
    categories.push({ category_id: id, category_name: c.category_name, parent_id: 0 });
    if (!groupToCat.has(c.category_name)) groupToCat.set(c.category_name, id);
  }
}

// ---------------------------------------------------------------- streams
const edgeStream = (id, ext) =>
  `${EDGE}/live/${encodeURIComponent(CU)}/${encodeURIComponent(CP)}/${id}.${ext}`;

let synthetic = 100000;
let skipped = 0;
const streams = [];
for (const s of raw) {
  const mm = /(?:live|movie|series)\/[^/]+\/[^/]+\/(\d+)\.([A-Za-z0-9]+)$/.exec(s.url);
  if (!mm) { skipped++; continue; }
  const streamId = Number(mm[1]);
  const ext = mm[2];
  const group = s.attrs['group-title'] || 'Ungrouped';
  let cid = groupToCat.get(group);
  if (cid === undefined) {
    cid = String(synthetic++);
    groupToCat.set(group, cid);
    categories.push({ category_id: cid, category_name: group, parent_id: 0 });
  }
  streams.push({
    num: streams.length + 1,
    name: s.name || s.attrs['tvg-name'] || `Channel ${streamId}`,
    stream_type: 'live',
    stream_id: streamId,
    stream_icon: s.attrs['tvg-logo'] || '',
    epg_channel_id: s.attrs['tvg-id'] || '',
    added: '0',
    category_id: cid,
    custom_sid: '',
    tv_archive: 0,
    direct_source: edgeStream(streamId, ext),
    tv_archive_duration: 0,
    _g: group,
    _n: s.attrs['tvg-chno'] || '',
  });
}

if (streams.length === 0) throw new Error('index produced zero streams — refusing to publish an empty playlist');

// ---------------------------------------------------------------- write
const dataDir = path.join(OUT, '_data');
const catDir = path.join(dataDir, 'live_streams_cat');
fs.mkdirSync(catDir, { recursive: true });

const strip = (s) => {
  const { _g, _n, ...rest } = s;
  return rest;
};

fs.writeFileSync(path.join(dataDir, 'live_categories.json'), JSON.stringify(categories));

// Public shape: no internal fields.
const pub = streams.map(strip);
fs.writeFileSync(path.join(dataDir, 'live_streams.json'), JSON.stringify(pub));

// Split per category so a category-filtered request parses only a small file.
const byCat = new Map();
for (const s of pub) {
  if (!byCat.has(s.category_id)) byCat.set(s.category_id, []);
  byCat.get(s.category_id).push(s);
}
for (const [cid, arr] of byCat) {
  fs.writeFileSync(path.join(catDir, `${cid}.json`), JSON.stringify(arr));
}

// Empty VOD/series responses (this build is live-only).
fs.writeFileSync(path.join(dataDir, 'empty.json'), '[]');
fs.writeFileSync(path.join(dataDir, 'emptyobj.json'), '{}');

// ---------------------------------------------------------------- playlist.m3u
// Rewrite upstream URLs so the player only ever talks to the edge. Because the
// upstream user/pass are stripped here, this file leaks nothing but our own client creds.
const tvg = `${EDGE}/xmltv.php?username=${encodeURIComponent(CU)}&password=${encodeURIComponent(CP)}`;
const m3uLines = [
  `#EXTM3U url-tvg="${tvg}" x-tvg-url="${tvg}"`,
  `# Tuliprox Edge — ${streams.length} channels`,
];
for (const s of streams) {
  const attrs = [
    `tvg-id="${s.epg_channel_id}"`,
    `tvg-name="${s.name.replace(/"/g, "'")}"`,
    `group-title="${s._g.replace(/"/g, "'")}"`,
    s.stream_icon ? `tvg-logo="${s.stream_icon}"` : '',
    s._n ? `tvg-chno="${s._n}"` : '',
  ].filter(Boolean).join(' ');
  m3uLines.push(`#EXTINF:-1 ${attrs},${s.name}`);
  m3uLines.push(s.direct_source);
}
fs.writeFileSync(path.join(OUT, 'playlist.m3u'), m3uLines.join('\n') + '\n');

// ---------------------------------------------------------------- EPG
// Shipped PLAIN and left to Cloudflare's automatic compression.
// Hand-setting `Content-Encoding: gzip` on a pre-gzipped body produced a DOUBLE-gzipped
// response (verified against the real runtime): one gunzip still yields gzip bytes, so every
// player would decode garbage. Never hand-manage Content-Encoding here.
//
// The guide's ids are lower-cased to match Tuliprox's m3u. The provider returns `1.L` while
// Tuliprox emits `1.l`, which silently costs 729 of 3,230 channels their guide data. Only
// <channel>/<programme> lines are touched, so element text is never mangled.
let epgBytes = 0;
let epgMatched = 0;
let epgKnown = 0;
const EPG_LIMIT = 24 * 1024 * 1024; // Cloudflare Pages rejects individual files over 25 MiB

function normalizeEpgIds(xml) {
  let touched = 0;
  const out = xml.split('\n').map((line) => {
    if (!/^\s*<(channel|programme)\b/.test(line)) return line;
    const before = line;
    const fixed = line
      .replace(/(\bid=")([^"]*)(")/, (_, a, id, c) => a + id.toLowerCase() + c)
      .replace(/(\schannel=")([^"]*)(")/, (_, a, id, c) => a + id.toLowerCase() + c);
    if (fixed !== before) touched++;
    return fixed;
  });
  return { xml: out.join('\n'), touched };
}

if (EPG_SRC && fs.existsSync(EPG_SRC)) {
  const size = fs.statSync(EPG_SRC).size;
  if (size > 1024 && size <= EPG_LIMIT) {
    const { xml, touched } = normalizeEpgIds(fs.readFileSync(EPG_SRC, 'utf8'));
    fs.writeFileSync(path.join(dataDir, 'epg.xml'), xml);
    epgBytes = Buffer.byteLength(xml);
    const ids = new Set();
    for (const m of xml.matchAll(/<channel\s+id="([^"]*)"/g)) ids.add(m[1]);
    const ours = new Set(streams.map((s) => s.epg_channel_id).filter(Boolean));
    epgKnown = ours.size;
    for (const id of ours) if (ids.has(id)) epgMatched++;
    console.log(`EPG: normalised ${touched} lines, ${epgMatched}/${epgKnown} channels have guide data`);
  } else {
    console.warn(`EPG skipped: ${size} bytes (must be 1 KiB .. ${EPG_LIMIT})`);
  }
}

// ---------------------------------------------------------------- meta
const meta = {
  built_at: new Date().toISOString(),
  channels: streams.length,
  categories: categories.length,
  skipped_unparsed: skipped,
  epg_bytes: epgBytes,
  epg_matched_channels: epgMatched,
  epg_channels_with_id: epgKnown,
  edge_origin: EDGE,
  target: TARGET,
};
fs.writeFileSync(path.join(dataDir, 'meta.json'), JSON.stringify(meta, null, 2));

console.log(JSON.stringify(meta, null, 2));