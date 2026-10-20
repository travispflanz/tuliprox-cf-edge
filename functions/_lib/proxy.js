/**
 * Live/VOD stream delivery.
 *
 * WHY THERE IS A FALLBACK
 * -----------------------
 * The happy path proxies the stream through the edge, which hides the provider credentials
 * and their signed CDN token completely.
 *
 * But many providers refuse datacenter IP ranges, and Cloudflare's egress is one of them.
 * Verified against the live provider: the identical request returns 200 from a residential
 * IP and 403 from Cloudflare, on every channel. When that happens a proxy can never work,
 * so instead of failing we hand the player a 302 to the provider and let it fetch the stream
 * from its own (residential) IP — which is exactly how the provider intends clients to work.
 *
 * The credentials stay out of every static asset either way: the redirect is only ever
 * emitted to a caller that already passed CLIENT_USER/CLIENT_PASS auth.
 *
 * STREAM_MODE:
 *   proxy     (default) — try the edge fetch, fall back to a redirect on refusal
 *   redirect            — always redirect (skip the doomed fetch, saves a round trip)
 */
import { safeEqual, upstreamStream, upstreamConfigured } from './auth.js';
import { text, unauthorized, serverError, options } from './respond.js';

const CONTENT_TYPES = {
  ts: 'video/mp2t',
  m3u8: 'application/vnd.apple.mpegurl',
  mp4: 'video/mp4',
  mkv: 'video/x-matroska',
  avi: 'video/x-msvideo',
};

function redirectTo(target, reason) {
  return new Response(null, {
    status: 302,
    headers: {
      Location: target,
      'Cache-Control': 'no-store',
      'Access-Control-Allow-Origin': '*',
      'X-Stream-Mode': 'redirect-fallback',
      'X-Stream-Fallback-Reason': reason,
    },
  });
}

export async function handleStream(context, kind) {
  const { request, env, params } = context;
  const pre = options(request);
  if (pre) return pre;

  if (!env.CLIENT_USER || !env.CLIENT_PASS) return serverError('Server Misconfigured');
  if (!upstreamConfigured(env)) return serverError('UPSTREAM_URL/UPSTREAM_USER/UPSTREAM_PASS not set');

  const segs = Array.isArray(params.path) ? params.path : [params.path].filter(Boolean);
  if (segs.length < 3) return text('400 Bad stream path', 400);

  const [u, p, file] = segs;
  if (!safeEqual(u, env.CLIENT_USER) || !safeEqual(p, env.CLIENT_PASS)) return unauthorized();

  const m = /^([0-9]+)\.([A-Za-z0-9]{1,5})$/.exec(file);
  if (!m) return text('400 Bad stream id', 400);
  const [, id, ext] = m;

  const target = upstreamStream(env, kind, id, ext);

  if ((env.STREAM_MODE || 'proxy') === 'redirect') {
    return redirectTo(target, 'mode=redirect');
  }

  const headers = { 'User-Agent': 'VLC/3.0.20 LibVLC/3.0.20' };
  const range = request.headers.get('Range');
  if (range) headers['Range'] = range;

  let upstream;
  try {
    upstream = await fetch(target, { headers, redirect: 'follow' });
  } catch (err) {
    // Network-level refusal from our egress — let the player try from its own.
    return redirectTo(target, `fetch-error:${(err && err.message ? err.message : 'unknown').slice(0, 60)}`);
  }

  if (!upstream.ok && upstream.status !== 206) {
    return redirectTo(target, `upstream-${upstream.status}`);
  }

  const out = new Headers();
  out.set('Content-Type', CONTENT_TYPES[ext] || upstream.headers.get('Content-Type') || 'application/octet-stream');
  out.set('Cache-Control', 'no-store');
  out.set('Access-Control-Allow-Origin', '*');
  out.set('X-Stream-Mode', 'proxy');
  const ct = upstream.headers.get('Content-Range');
  if (ct) out.set('Content-Range', ct);
  const al = upstream.headers.get('Accept-Ranges');
  if (al) out.set('Accept-Ranges', al);

  return new Response(upstream.body, { status: upstream.status === 206 ? 206 : 200, headers: out });
}