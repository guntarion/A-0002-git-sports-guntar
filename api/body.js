// /api/body — weight + home blood-pressure summary for the Body page.
//
// AUTH REQUIRED, without exception. Unlike /api/performance (public read), blood
// pressure and body weight are personal. The data lives in dedicated tables
// (bp_readings, weigh_ins, body_summary) that no public route reads, so a
// forgotten filter entry elsewhere can never leak it.
//
//   GET /api/body -> { computed_at, summary }
//
// `summary` is computed by scripts/body_metrics.py during the daily ETL, so the
// formulas live in one place; this route only serves the newest row.
import { query } from './_lib/db.js';
import { requireAuth, sendJson } from './_lib/http.js';

export default async function handler(req, res) {
  if (!requireAuth(req, res)) return;
  if (req.method !== 'GET') return sendJson(res, 405, { error: 'method not allowed' });

  try {
    const r = await query(
      `SELECT computed_at, payload FROM body_summary ORDER BY computed_at DESC LIMIT 1`
    );
    if (!r.rows.length) {
      return sendJson(res, 200, { computed_at: null, summary: null, note: 'no summary computed yet' });
    }
    const row = r.rows[0];
    return sendJson(res, 200, {
      computed_at: row.computed_at instanceof Date ? row.computed_at.toISOString() : row.computed_at,
      summary: row.payload,
    });
  } catch (err) {
    // 42P01 = undefined_table: the ETL has not created the tables yet.
    if (err && err.code === '42P01') {
      return sendJson(res, 200, { computed_at: null, summary: null, note: 'tables not created yet' });
    }
    return sendJson(res, 500, { error: 'server_error', detail: String((err && err.message) || err) });
  }
}
