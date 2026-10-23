/**
 * Shared credential handling for the edge Xtream API.
 *
 * Two distinct credential pairs exist and must never be confused:
 *   CLIENT_*   — what the PLAYER sends us (ours to publish)
 *   UPSTREAM_* — the IPTV provider's credentials (secrets; never leave the Worker)
 *
 * Secrets are read from env only. Nothing credential-bearing is ever written to disk
 * at build time, so a leaked static asset cannot leak the provider account.
 */

/** Constant-time string compare so auth timing does not leak credential prefixes. */
export function safeEqual(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string') return false;
  const ab = new TextEncoder().encode(a);
  const bb = new TextEncoder().encode(b);
  let diff = ab.length ^ bb.length;
  const n = Math.max(ab.length, bb.length);
  for (let i = 0; i < n; i++) diff |= (ab[i] ?? 0) ^ (bb[i] ?? 0);
  return diff === 0;
}

/** Pull username/password from the query string, or a form-encoded POST body. */
export async function readCreds(request) {
  const url = new URL(request.url);
  let u = url.searchParams.get('username') || url.searchParams.get('user') || '';
  let p =
    url.searchParams.get('password') ||
    url.searchParams.get('pass') ||
    url.searchParams.get('token') ||
    '';

  if ((!u || !p) && request.method === 'POST') {
    try {
      const form = await request.clone().formData();
      u = u || String(form.get('username') || form.get('user') || '');
      p = p || String(form.get('password') || form.get('pass') || '');
    } catch {
      /* not a form body — ignore */
    }
  }
  return { u, p };
}

/** True when the caller presented our client credentials. Fails closed if unset. */
export async function isAuthorized(request, env) {
  const cu = env.CLIENT_USER;
  const cp = env.CLIENT_PASS;
  if (!cu || !cp) return false;
  const { u, p } = await readCreds(request);
  return safeEqual(u, cu) && safeEqual(p, cp);
}

/**
 * PROVIDER CREDENTIAL SLOTS
 *
 * The edge can hold more than one provider account. Slot 1 is `UPSTREAM_URL/USER/PASS`
 * (unchanged, so a single-credential deployment keeps working); slot N>1 adds the
 * `UPSTREAM_<N>_*` triplet and is entirely optional.
 *
 * Why this works at all: every credential is from the SAME provider, so the catalogue — and
 * therefore every stream id — is identical between them. `<host>/live/<user>/<pass>/<id>.ts`
 * resolves under either account, which means any channel can be served by any slot. That is the
 * fact the whole feature rests on.
 *
 * What it buys: each trial allows `max_connections=1`, so one slot serves one viewer. Two slots
 * serve two viewers simultaneously, and either can cover for the other.
 */
export const SLOT_NUMBERS = [1, 2];

function slotCred(env, slot) {
  const suffix = slot === 1 ? '' : `_${slot}`;
  return {
    slot,
    url: (env[`UPSTREAM${suffix}_URL`] || '').replace(/\/+$/, ''),
    user: env[`UPSTREAM${suffix}_USER`] || '',
    pass: env[`UPSTREAM${suffix}_PASS`] || '',
  };
}

/** Every fully-populated slot, in order. A half-filled slot is ignored, not half-used. */
export function upstreamSlots(env) {
  return SLOT_NUMBERS.map((n) => slotCred(env, n)).filter((c) => c.url && c.user && c.pass);
}

/**
 * The slots worth sending traffic to.
 *
 * The build probes each account and publishes the live ones in `UPSTREAM_HEALTHY_SLOTS`
 * (e.g. "1,2"). This matters because the provider refuses our egress, so streams are delivered
 * by redirect and the edge never sees whether one worked — without build-time health the edge
 * would keep handing a third of its viewers to a dead account for hours.
 *
 * Fails OPEN: if the hint is missing or would exclude everything, all configured slots are used.
 * A stale hint must never strand traffic that could have been served.
 */
export function healthySlots(env) {
  const configured = upstreamSlots(env);
  const hint = String(env.UPSTREAM_HEALTHY_SLOTS || '').trim();
  if (!hint) return configured;
  const allowed = new Set(
    hint.split(',').map((s) => Number.parseInt(s.trim(), 10)).filter(Number.isInteger),
  );
  const healthy = configured.filter((c) => allowed.has(c.slot));
  return healthy.length ? healthy : configured;
}

/** The provider stream URL for one slot — rebuilt per request, never stored. */
export function streamUrlFor(cred, kind, id, ext) {
  const u = encodeURIComponent(cred.user);
  const p = encodeURIComponent(cred.pass);
  return `${cred.url}/${kind}/${u}/${p}/${id}.${ext}`;
}

/**
 * Pick a slot for this caller, stably.
 *
 * Keyed on the VIEWER (client IP), not on the request, and that distinction is the whole point:
 * alternating a single player's segment requests across both accounts would occupy both
 * connections for one viewer and still lock out the second device. Hashing the IP gives each
 * device its own account for as long as its address is stable, so two devices really do stream
 * at once. FNV-1a — tiny, no allocation, no crypto needed for a load-spreading decision.
 */
export function pickSlot(request, creds) {
  if (creds.length <= 1) return creds[0];
  const key =
    request.headers.get('CF-Connecting-IP') ||
    request.headers.get('X-Forwarded-For') ||
    request.headers.get('User-Agent') ||
    'anonymous';
  let hash = 2166136261;
  for (let i = 0; i < key.length; i++) {
    hash ^= key.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return creds[(hash >>> 0) % creds.length];
}

/** Back-compatible single-credential helpers (slot 1, or the first populated slot). */
export function upstreamStream(env, kind, id, ext) {
  const [first] = upstreamSlots(env);
  return first ? streamUrlFor(first, kind, id, ext) : '';
}

export function upstreamConfigured(env) {
  return upstreamSlots(env).length > 0;
}