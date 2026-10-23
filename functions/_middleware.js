/**
 * Gate the raw build artifacts.
 *
 * Pages serves a real file under the assets dir directly, without running a Function — so
 * `/_data/*.json` and `/playlist.m3u` would otherwise be world-readable. _middleware runs on
 * every inbound request (including ones that would hit a static asset), so this is the place
 * to gate them. Internal `env.ASSETS.fetch()` calls from our own Functions bypass middleware,
 * which is what lets get.php/xmltv.php read the same files after doing their own auth check.
 */
import { clientLogins, isAuthorized } from './_lib/auth.js';
import { unauthorized } from './_lib/respond.js';

export async function onRequest(context) {
  const { request, env, next } = context;
  const { pathname } = new URL(request.url);

  const gated =
    pathname.startsWith('/_data/') ||
    pathname === '/_data' ||
    // Every per-login playlist variant, not just login 1's. A prefix match rather than a literal
    // list, so adding a third login can never leave its playlist world-readable by omission.
    /^\/playlist(-\d+)?\.m3u$/.test(pathname);

  if (gated) {
    // Fail closed when NO login is configured — not just when login 1 is missing.
    if (!clientLogins(env).length) return unauthorized();
    if (!(await isAuthorized(request, env))) return unauthorized();
  }

  return next();
}