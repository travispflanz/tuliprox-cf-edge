/**
 * verify_stream_slots.mjs — the dual-credential stream path, with `fetch` stubbed.
 *
 * WHY THIS IS PINNED
 * ------------------
 * Two provider accounts exist so that two devices can watch at once (each trial allows
 * `max_connections=1`) and so a dead account can be stepped over. Both properties fail silently:
 *
 *   - If slot choice were keyed on the REQUEST rather than the VIEWER, one player's segments would
 *     alternate across both accounts, occupy both connections, and still lock out the second
 *     device — while looking perfectly fine for the first viewer.
 *   - If a dead slot were not excluded, a share of viewers would be handed a dead account for
 *     hours, and because the provider refuses our egress (streams go out as redirects) the edge
 *     never observes the failure.
 *
 * The provider blocks Cloudflare's egress, so a real end-to-end test of the proxy path is
 * impossible from CI. Stubbing `globalThis.fetch` is the only way to exercise it deterministically.
 *
 * Run: node build/verify_stream_slots.mjs
 */
import {
  upstreamSlots,
  healthySlots,
  pickSlot,
  streamUrlFor,
  upstreamConfigured,
} from '../functions/_lib/auth.js';
import { handleStream } from '../functions/_lib/proxy.js';

const CLIENT = { CLIENT_USER: 'cu', CLIENT_PASS: 'cp' };
const SLOT1 = { UPSTREAM_URL: 'http://p1.example', UPSTREAM_USER: 'u1', UPSTREAM_PASS: 'k1' };
const SLOT2 = { UPSTREAM_2_URL: 'http://p2.example', UPSTREAM_2_USER: 'u2', UPSTREAM_2_PASS: 'k2' };
const BOTH = { ...CLIENT, ...SLOT1, ...SLOT2 };

let checks = 0;
const failures = [];

function check(name, ok, detail = '') {
  checks += 1;
  if (!ok) failures.push(`${name} — ${detail}`);
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${name}${!ok && detail ? `   [${detail}]` : ''}`);
}

function req(ip, { ua = 'test', range = null } = {}) {
  const headers = new Headers();
  if (ip) headers.set('CF-Connecting-IP', ip);
  headers.set('User-Agent', ua);
  if (range) headers.set('Range', range);
  return new Request('https://edge.example/live/cu/cp/55.ts', { headers });
}

/**
 * Drive handleStream with a scripted fetch. Returns { response, attempts }.
 *
 * `params.path` is derived from the request URL exactly as Pages' `[[path]]` route would fill it.
 * Hardcoding it here would hand every case valid parameters and silently defeat the auth and
 * bad-input assertions below.
 */
async function run(env, request, responder) {
  const segments = new URL(request.url).pathname.split('/').filter(Boolean).slice(1);
  const attempts = [];
  const original = globalThis.fetch;
  globalThis.fetch = async (url) => {
    attempts.push(String(url));
    return responder(String(url), attempts.length);
  };
  try {
    const response = await handleStream({ request, env, params: { path: segments } }, 'live');
    return { response, attempts };
  } finally {
    globalThis.fetch = original;
  }
}

const ok = (body = 'x') =>
  new Response(body, { status: 200, headers: { 'Content-Type': 'video/mp2t' } });
const status = (code) => new Response(null, { status: code });

console.log('=== slot discovery ===');
check('one credential -> one slot', upstreamSlots({ ...CLIENT, ...SLOT1 }).length === 1);
check('two credentials -> two slots', upstreamSlots(BOTH).length === 2);
check('a half-filled slot is ignored, not half-used',
  upstreamSlots({ ...CLIENT, ...SLOT1, UPSTREAM_2_URL: 'http://p2.example' }).length === 1);
check('no credential -> not configured', upstreamConfigured(CLIENT) === false);
check('slot 1 keeps the plain UPSTREAM_* names (back-compatible)',
  upstreamSlots(BOTH)[0].url === 'http://p1.example');

console.log('\n=== build-time health hint ===');
check('hint "1,2" keeps both', healthySlots({ ...BOTH, UPSTREAM_HEALTHY_SLOTS: '1,2' }).length === 2);
check('hint "2" isolates slot 2',
  healthySlots({ ...BOTH, UPSTREAM_HEALTHY_SLOTS: '2' }).map((c) => c.slot).join() === '2');
check('absent hint falls OPEN to every slot', healthySlots(BOTH).length === 2);
check('a hint excluding everything falls OPEN rather than stranding traffic',
  healthySlots({ ...BOTH, UPSTREAM_HEALTHY_SLOTS: '7' }).length === 2);
check('whitespace/garbage in the hint is tolerated',
  healthySlots({ ...BOTH, UPSTREAM_HEALTHY_SLOTS: ' 2 , x' }).map((c) => c.slot).join() === '2');

console.log('\n=== per-viewer slot assignment (the capacity property) ===');
const creds = upstreamSlots(BOTH);
const sameIp = new Set(
  Array.from({ length: 8 }, () => pickSlot(req('203.0.113.9'), creds).slot),
);
check('one viewer always gets ONE slot (never alternates)', sameIp.size === 1, [...sameIp].join());

const spread = new Set();
for (let i = 0; i < 60; i++) spread.add(pickSlot(req(`203.0.113.${i}`), creds).slot);
check('different viewers are spread across both slots', spread.size === 2, [...spread].join());
check('a single configured slot is always chosen',
  pickSlot(req('203.0.113.9'), [creds[0]]).slot === 1);
check('no client IP still yields a stable choice',
  pickSlot(req(null, { ua: 'vlc' }), creds).slot === pickSlot(req(null, { ua: 'vlc' }), creds).slot);

console.log('\n=== URL construction ===');
check('slot 2 builds from its own credential',
  streamUrlFor(creds[1], 'live', '55', 'ts') === 'http://p2.example/live/u2/k2/55.ts');
check('credentials are URL-encoded',
  streamUrlFor({ url: 'http://p', user: 'a b', pass: 'c/d' }, 'live', '1', 'ts')
    === 'http://p/live/a%20b/c%2Fd/1.ts');

console.log('\n=== failover (proxy mode) ===');
let r = await run(BOTH, req('203.0.113.9'), (url) => (url.includes('p1.') ? status(401) : ok()));
check('a 401 on the chosen slot fails over to the other', r.response.status === 200,
  `status=${r.response.status} attempts=${r.attempts.length}`);
check('the serving slot is reported', r.response.headers.get('X-Stream-Slot') === '2',
  r.response.headers.get('X-Stream-Slot'));
check('it tried both accounts', r.attempts.length === 2, String(r.attempts.length));

r = await run(BOTH, req('203.0.113.9'), () => ok());
check('a healthy first attempt does NOT touch the second account', r.attempts.length === 1,
  String(r.attempts.length));

r = await run(BOTH, req('203.0.113.9'), () => status(403));
check('a 403 (egress block) stops after ONE attempt — every slot would answer the same',
  r.attempts.length === 1, String(r.attempts.length));
check('and it redirects so the player can fetch from its own IP', r.response.status === 302,
  String(r.response.status));

r = await run(BOTH, req('203.0.113.9'), () => status(401));
check('both accounts refusing ends in a redirect, not a 500', r.response.status === 302,
  String(r.response.status));

r = await run(BOTH, req('203.0.113.9'), () => { throw new Error('econnreset'); });
check('a thrown fetch falls back to a redirect (not a 500)', r.response.status === 302,
  String(r.response.status));
check('the fallback reason is reported',
  (r.response.headers.get('X-Stream-Fallback-Reason') || '').startsWith('fetch-error'),
  r.response.headers.get('X-Stream-Fallback-Reason'));

console.log('\n=== redirect mode ===');
r = await run({ ...BOTH, STREAM_MODE: 'redirect' }, req('203.0.113.9'), () => {
  throw new Error('fetch must not be called in redirect mode');
});
check('redirect mode never fetches', r.attempts.length === 0, String(r.attempts.length));
check('it still 302s', r.response.status === 302, String(r.response.status));
check('and names the slot it sent the viewer to',
  ['1', '2'].includes(r.response.headers.get('X-Stream-Slot')),
  r.response.headers.get('X-Stream-Slot'));

console.log('\n=== auth still gates everything ===');
r = await run(BOTH, new Request('https://edge.example/live/bad/creds/55.ts'), () => ok());
check('wrong client credentials -> 401 before any fetch',
  r.response.status === 401 && r.attempts.length === 0,
  `status=${r.response.status} attempts=${r.attempts.length}`);

r = await run(CLIENT, req('203.0.113.9'), () => ok());
check('no provider credential -> 500, no fetch',
  r.response.status === 500 && r.attempts.length === 0, String(r.response.status));

r = await run({ ...BOTH }, new Request('https://edge.example/live/cu/cp/nope.ts', {
  headers: new Headers({ 'CF-Connecting-IP': '203.0.113.9' }),
}), () => ok());
check('a malformed stream id -> 400, no fetch',
  r.response.status === 400 && r.attempts.length === 0, String(r.response.status));

console.log('\n=== single-credential deployments are unaffected ===');
r = await run({ ...CLIENT, ...SLOT1 }, req('203.0.113.9'), () => ok());
check('one slot still serves normally', r.response.status === 200, String(r.response.status));
check('and reports slot 1', r.response.headers.get('X-Stream-Slot') === '1');
r = await run({ ...CLIENT, ...SLOT1 }, req('203.0.113.9'), () => status(401));
check('with one slot there is nothing to fail over to -> single attempt',
  r.attempts.length === 1, String(r.attempts.length));

console.log(`\n=== ${checks - failures.length}/${checks} checks passed ===`);
for (const failure of failures) console.log(`FAILED: ${failure}`);
process.exit(failures.length ? 1 : 0);
