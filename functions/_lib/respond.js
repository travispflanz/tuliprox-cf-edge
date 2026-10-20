/**
 * Response helpers for the edge.
 *
 * CPU matters: Cloudflare's free tier allows only ~10ms CPU per request. We therefore
 * NEVER JSON.parse the big prebuilt indexes — we auth, then pipe the prebuilt asset
 * straight through with `new Response(res.body, ...)`, which costs ~0 CPU regardless
 * of whether the payload is 2 KB or 5 MB.
 */

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': '*',
  'Access-Control-Allow-Methods': 'GET,POST,HEAD,OPTIONS',
};

export function json(data, status = 200, extra = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Cache-Control': 'no-store',
      ...CORS,
      ...extra,
    },
  });
}

export function text(body, status = 200, extra = {}) {
  return new Response(body, {
    status,
    headers: { 'Content-Type': 'text/plain; charset=utf-8', ...CORS, ...extra },
  });
}

/** 401 with a body Xtream clients can read, and the right CORS so players show a real error. */
export function unauthorized() {
  return new Response('401 Unauthorized', {
    status: 401,
    headers: { 'Content-Type': 'text/plain; charset=utf-8', ...CORS },
  });
}

export function serverError(msg) {
  return new Response(`500 ${msg}`, {
    status: 500,
    headers: { 'Content-Type': 'text/plain; charset=utf-8', ...CORS },
  });
}

/**
 * Pipe a prebuilt static asset back to the caller without parsing it.
 * `assetPath` starts with "/_data/...".
 */
export async function serveAsset(request, env, assetPath, extra = {}) {
  const origin = new URL(request.url).origin;
  const res = await env.ASSETS.fetch(new Request(new URL(assetPath, origin), { method: 'GET' }));
  if (!res.ok) {
    return new Response('503 Playlist not built yet — run the GitHub Action', {
      status: 503,
      headers: { 'Content-Type': 'text/plain; charset=utf-8', ...CORS },
    });
  }
  const headers = new Headers(res.headers);
  headers.delete('Content-Length');
  headers.set('Cache-Control', 'no-store');
  for (const [k, v] of Object.entries(CORS)) headers.set(k, v);
  for (const [k, v] of Object.entries(extra)) headers.set(k, v);
  return new Response(res.body, { status: 200, headers });
}

export function options(request) {
  if (request.method !== 'OPTIONS') return null;
  return new Response(null, { status: 204, headers: CORS });
}