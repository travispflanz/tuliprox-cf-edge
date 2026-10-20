/**
 * /xmltv.php — Xtream-style EPG endpoint.
 *
 * Served as plain XML and deliberately WITHOUT a manual Content-Encoding: setting
 * `Content-Encoding: gzip` on a pre-compressed body made Cloudflare compress it a second
 * time, so clients decoded garbage. Cloudflare compresses application/xml itself.
 */
import { isAuthorized } from './_lib/auth.js';
import { serveAsset, unauthorized, serverError, options } from './_lib/respond.js';

export async function onRequest(context) {
  const { request, env } = context;
  const pre = options(request);
  if (pre) return pre;

  if (!env.CLIENT_USER || !env.CLIENT_PASS) return serverError('Server Misconfigured');
  if (!(await isAuthorized(request, env))) return unauthorized();

  return serveAsset(request, env, '/_data/epg.xml', {
    'Content-Type': 'application/xml; charset=utf-8',
  });
}