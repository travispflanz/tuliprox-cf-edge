/**
 * /get.php — Xtream-style playlist download.
 * The playlist asset is prebuilt with edge URLs only (provider credentials stripped at build
 * time), and it is gated here and in _middleware so it is never publicly fetchable.
 *
 * ONE ASSET PER LOGIN. Credentials are baked into every stream URL, so each login must receive
 * the playlist carrying ITS OWN credentials. Serving one shared file would hand viewer 2 a list
 * full of viewer 1's credentials; every stream would then authenticate as login 1, land on
 * provider slot 1, and the two viewers would fight over a single `max_connections=1` account —
 * silently defeating the whole point of a second provider slot.
 *
 * It stays a pure asset passthrough: rewriting ~5 MB of URLs per request would blow Cloudflare's
 * ~10 ms CPU budget, which is why the build bakes the variants instead.
 */
import { authenticate, clientLogins } from './_lib/auth.js';
import { serveAsset, unauthorized, serverError, options } from './_lib/respond.js';

export async function onRequest(context) {
  const { request, env } = context;
  const pre = options(request);
  if (pre) return pre;

  if (!clientLogins(env).length) return serverError('Server Misconfigured');

  const type = new URL(request.url).searchParams.get('type') || 'm3u_plus';

  const login = await authenticate(request, env);
  if (!login) return unauthorized();

  // Login 1 keeps the original filename, so existing players are unaffected.
  const asset = login.n === 1 ? '/playlist.m3u' : `/playlist-${login.n}.m3u`;

  return serveAsset(request, env, asset, {
    'Content-Type': 'audio/x-mpegurl; charset=utf-8',
    'Content-Disposition': type.startsWith('m3u') ? 'inline; filename="playlist.m3u"' : 'inline',
    // Which login was served, so a misconfigured device is diagnosable from headers alone.
    'X-Client-Login': String(login.n),
  });
}
