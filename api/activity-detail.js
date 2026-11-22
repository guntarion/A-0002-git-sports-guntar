// /api/activity-detail — per-sample streams + full Garmin summary for ONE activity.
//
//   GET /api/activity-detail?id=123456789
//     -> { id, available, points, streams:{n,t,d,m:{hr,speed,...}}, summary:{...}, power_zones:[...] }
//
// Public read, like the rest of the dashboard. It contains no GPS: positions stay
// in /api/locations (auth required). Written by scripts/sync_activity_streams.py.
import { query } from './_lib/db.js';
import { sendJson } from './_lib/http.js';

export default async function handler(req, res) {
  if (req.method !== 'GET') return sendJson(res, 405, { error: 'method not allowed' });

  const id = new URL(req.url, 'http://x').searchParams.get('id') || '';
  if (!/^\d{1,20}$/.test(id)) return sendJson(res, 400, { error: 'bad_id' });

  try {
    const r = await query(
      `SELECT activity_id, to_char(date, 'YYYY-MM-DD') AS date, type, points,
              streams, summary, power_zones
         FROM activity_streams WHERE activity_id = $1`,
      [id]
    );
    if (!r.rows.length) return sendJson(res, 200, { id, available: false });
    const row = r.rows[0];
    return sendJson(res, 200, {
      id: row.activity_id,
      available: row.points > 0 || Object.keys(row.summary || {}).length > 0,
      date: row.date,
      type: row.type,
      points: row.points,
      streams: row.streams,
      summary: row.summary,
      power_zones: row.power_zones,
    });
  } catch (err) {
    // The table appears on the first run of sync_activity_streams.py; until then
    // the UI should just say "not synced yet" instead of showing an error.
    if (err && err.code === '42P01') return sendJson(res, 200, { id, available: false });
    return sendJson(res, 500, { error: 'server_error', detail: String((err && err.message) || err) });
  }
}
