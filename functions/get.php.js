/**
 * /get.php — Xtream-style playlist download.
 * The playlist asset is prebuilt with edge URLs only (provider credentials stripped at build
 * time), and it is gated here and in _middleware so it is never publicly fetchable.
 */
import { isAuthorized } from './_lib/auth.js';
import { serveAsset, unauthorized, serverError, options } from './_lib/respond.js';

export async function onRequest(context) {
  const { request, env } = context;
  const pre = options(request);
  if (pre) return pre;

  if (!env.CLIENT_USER || !env.CLIENT_PASS) return serverError('Server Misconfigured');

  const type = new URL(request.url).searchParams.get('type') || 'm3u_plus';

  if (!(await isAuthorized(request, env))) return unauthorized();

  return serveAsset(request, env, '/playlist.m3u', {
    'Content-Type': 'audio/x-mpegurl; charset=utf-8',
    'Content-Disposition': type.startsWith('m3u') ? 'inline; filename="playlist.m3u"' : 'inline',
  });
}