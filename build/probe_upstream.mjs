#!/usr/bin/env node
/**
 * probe_upstream.mjs — record the provider account's real health at build time.
 *
 * The edge reports this in its Xtream login response, so a player shows "Expired" instead of
 * silently failing every stream when the trial lapses. Trials here last only ~36h, so this
 * signal is the difference between a diagnosable system and a mysterious one.
 */
import fs from 'node:fs';
import path from 'node:path';

const OUT = process.env.OUT_DIR || 'dist';
const BASE = (process.env.UPSTREAM_URL || '').replace(/\/+$/, '');
const U = process.env.UPSTREAM_USER || '';
const P = process.env.UPSTREAM_PASS || '';

const record = {
  auth: 0,
  status: 'Unknown',
  exp_date: '',
  max_connections: '',
  allowed_output_formats: [],
  checked_at: new Date().toISOString(),
};

if (BASE && U && P) {
  const url = `${BASE}/player_api.php?username=${encodeURIComponent(U)}&password=${encodeURIComponent(P)}`;
  try {
    const res = await fetch(url, {
      headers: { 'User-Agent': 'Mozilla/5.0' },
      signal: AbortSignal.timeout(30000),
    });
    record.http_status = res.status;
    if (res.ok) {
      const body = await res.json();
      const ui = body && body.user_info ? body.user_info : {};
      record.auth = Number(ui.auth) === 1 ? 1 : 0;
      record.status = ui.status || (record.auth ? 'Active' : 'Unknown');
      record.exp_date = ui.exp_date ? String(ui.exp_date) : '';
      record.max_connections = ui.max_connections ? String(ui.max_connections) : '';
      record.allowed_output_formats = ui.allowed_output_formats || [];
      record.is_trial = ui.is_trial !== undefined ? String(ui.is_trial) : '';
    }
  } catch (err) {
    record.error = err && err.message ? err.message : 'probe failed';
  }
}

const dir = path.join(OUT, '_data');
fs.mkdirSync(dir, { recursive: true });
fs.writeFileSync(path.join(dir, 'upstream.json'), JSON.stringify(record, null, 2));

console.log(JSON.stringify(record, null, 2));
if (record.auth !== 1) {
  console.error(`::warning::upstream not active (status=${record.status}) — the edge will report Expired`);
}