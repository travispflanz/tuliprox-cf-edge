/**
 * Live/VOD stream proxy.
 *
 * The player's URL tells us the stream id; we rebuild the provider URL from env and fetch it
 * server-side, following the provider's 302 to its signed CDN URL. The provider credentials
 * and the CDN token therefore never reach the player — they only ever exist inside this Worker.
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

  const headers = { 'User-Agent': 'VLC/3.0.20 LibVLC/3.0.20' };
  const range = request.headers.get('Range');
  if (range) headers['Range'] = range;

  let upstream;
  try {
    upstream = await fetch(target, { headers, redirect: 'follow' });
  } catch (err) {
    return text(`502 Upstream unreachable: ${err && err.message ? err.message : 'fetch failed'}`, 502);
  }

  if (!upstream.ok && upstream.status !== 206) {
    return text(`502 Upstream returned ${upstream.status}`, 502);
  }

  const out = new Headers();
  out.set('Content-Type', CONTENT_TYPES[ext] || upstream.headers.get('Content-Type') || 'application/octet-stream');
  out.set('Cache-Control', 'no-store');
  out.set('Access-Control-Allow-Origin', '*');
  const ct = upstream.headers.get('Content-Range');
  if (ct) out.set('Content-Range', ct);
  const al = upstream.headers.get('Accept-Ranges');
  if (al) out.set('Accept-Ranges', al);

  return new Response(upstream.body, { status: upstream.status === 206 ? 206 : 200, headers: out });
}