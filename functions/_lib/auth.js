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

/** The provider stream URL for a given id — rebuilt from env, never stored. */
export function upstreamStream(env, kind, id, ext) {
  const base = (env.UPSTREAM_URL || '').replace(/\/+$/, '');
  const u = encodeURIComponent(env.UPSTREAM_USER || '');
  const p = encodeURIComponent(env.UPSTREAM_PASS || '');
  return `${base}/${kind}/${u}/${p}/${id}.${ext}`;
}

export function upstreamConfigured(env) {
  return Boolean(env.UPSTREAM_URL && env.UPSTREAM_USER && env.UPSTREAM_PASS);
}