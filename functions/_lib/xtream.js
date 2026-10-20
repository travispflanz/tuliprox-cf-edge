/**
 * Xtream Codes API surface, served from prebuilt static indexes.
 *
 * Every action maps to a prebuilt JSON file that is piped through untouched (no parsing),
 * so responses cost ~0 CPU no matter how many channels the playlist holds. Only the login
 * response is assembled in code, because it carries a live timestamp.
 */
import { isAuthorized } from './auth.js';
import { json, serveAsset, unauthorized, serverError, options } from './respond.js';

/** Read a small JSON asset; used only for tiny files (meta/upstream), never the big indexes. */
async function readSmallJson(request, env, assetPath, fallback) {
  try {
    const origin = new URL(request.url).origin;
    const res = await env.ASSETS.fetch(new URL(assetPath, origin));
    if (!res.ok) return fallback;
    return await res.json();
  } catch {
    return fallback;
  }
}

export async function loginPayload(request, env) {
  const host = new URL(request.url).host;
  const up = await readSmallJson(request, env, '/_data/upstream.json', {
    auth: 1,
    status: 'Active',
    exp_date: '',
  });
  const meta = await readSmallJson(request, env, '/_data/meta.json', { built_at: '' });

  const now = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  const timeNow = `${now.getUTCFullYear()}-${pad(now.getUTCMonth() + 1)}-${pad(now.getUTCDate())} ${pad(
    now.getUTCHours()
  )}:${pad(now.getUTCMinutes())}:${pad(now.getUTCSeconds())}`;

  const authOk = String(up.auth) === '1';
  return {
    user_info: {
      username: env.CLIENT_USER,
      password: env.CLIENT_PASS,
      message: authOk ? 'Welcome' : `Upstream unavailable (${up.status || 'unknown'})`,
      auth: authOk ? 1 : 0,
      status: authOk ? 'Active' : 'Expired',
      exp_date: up.exp_date || '',
      is_trial: '0',
      active_cons: '0',
      created_at: meta.built_at ? String(Math.floor(Date.parse(meta.built_at) / 1000)) : '0',
      max_connections: '1',
      allowed_output_formats: ['m3u8', 'ts'],
    },
    server_info: {
      url: host,
      port: '443',
      https_port: '443',
      server_protocol: 'https',
      rtmp_port: '',
      timezone: 'America/Chicago',
      timestamp_now: Math.floor(now.getTime() / 1000),
      time_now: timeNow,
    },
  };
}

/**
 * Map an Xtream `action` to the prebuilt asset that answers it.
 * Returns null for "unknown action" (answered with an empty list).
 */
function assetForAction(action, params) {
  switch (action) {
    case 'get_live_categories':
      return '/_data/live_categories.json';
    case 'get_live_streams': {
      const cid = params.get('category_id');
      if (cid && /^[0-9]+$/.test(cid)) return `/_data/live_streams_cat/${cid}.json`;
      return '/_data/live_streams.json';
    }
    case 'get_vod_categories':
    case 'get_vod_streams':
    case 'get_series_categories':
    case 'get_series':
    case 'get_short_epg':
    case 'get_simple_data_table':
    case 'get_live_info':
      return '/_data/empty.json';
    case 'get_vod_info':
    case 'get_series_info':
      return '/_data/emptyobj.json';
    default:
      return null;
  }
}

export async function handlePlayerApi(context, { panel = false } = {}) {
  const { request, env } = context;
  const pre = options(request);
  if (pre) return pre;

  if (!env.CLIENT_USER || !env.CLIENT_PASS) return serverError('Server Misconfigured (CLIENT_USER/CLIENT_PASS not set)');
  if (!(await isAuthorized(request, env))) return unauthorized();

  const params = new URL(request.url).searchParams;
  const action = params.get('action') || '';

  if (!action) {
    const payload = await loginPayload(request, env);
    if (!panel) return json(payload);
    const payload2 = {
      ...payload,
      categories: { live: [], movie: [], series: [] },
      available_channels: {},
    };
    return json(payload2);
  }

  const asset = assetForAction(action, params);
  if (asset) return serveAsset(request, env, asset);

  // Unknown/exotic action: an empty array is the safest answer and keeps players happy.
  return json([]);
}