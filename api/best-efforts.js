// /api/best-efforts — fastest time per target distance.
//
//   GET /api/best-efforts            -> best per distance + a top-N leaderboard each
//   GET /api/best-efforts?top=10     -> deeper leaderboards
//
// Public read, matching records.html and the rest of the dashboard.
import { query } from './_lib/db.js';
import { sendJson } from './_lib/http.js';

// Display order and labels. Keys match scripts/sync_best_efforts.py.
const ORDER = [
  ['400m', '400 m', 400],
  ['half_mile', '½ mile', 804.672],
  ['1k', '1 km', 1000],
  ['1mile', '1 mile', 1609.344],
  ['5k', '5 km', 5000],
  ['10k', '10 km', 10000],
  ['15k', '15 km', 15000],
  ['20k', '20 km', 20000],
  ['half', 'Half Marathon', 21097.5],
  ['30k', '30 km', 30000],
  ['marathon', 'Marathon', 42195],
];

export default async function handler(req, res) {
  if (req.method !== 'GET') return sendJson(res, 405, { error: 'method not allowed' });

  try {
    const url = new URL(req.url, 'http://x');

    // ?history=1 -> every activity's best per distance, oldest first, for progress charts.
    // Small table (one row per run per distance), so no paging.
    if (url.searchParams.get('history')) {
      const h = await query(
        `SELECT distance_key, activity_id, to_char(date, 'YYYY-MM-DD') AS date, activity_name, activity_type,
                distance_m, duration_s, pace_secs_per_km
           FROM best_efforts ORDER BY distance_key, date, activity_id`
      );
      const iso0 = (d) => (d instanceof Date ? d.toISOString().slice(0, 10) : d);
      const series = {};
      for (const r of h.rows) {
        (series[r.distance_key] ||= []).push({
          activity_id: r.activity_id,
          date: iso0(r.date),
          name: r.activity_name,
          type: r.activity_type,
          duration_s: Number(r.duration_s),
          pace_secs_per_km: r.pace_secs_per_km === null ? null : Number(r.pace_secs_per_km),
        });
      }
      return sendJson(res, 200, {
        distances: ORDER.map(([key, label, metres]) => ({ key, label, distance_m: metres, efforts: series[key] || [] })),
      });
    }
    let top = parseInt(url.searchParams.get('top') || '5', 10);
    if (!Number.isFinite(top) || top < 1) top = 5;
    if (top > 25) top = 25;

    // Rank within each distance, then keep the first `top` per distance.
    const { rows } = await query(
      `SELECT * FROM (
         SELECT distance_key, activity_id, date, activity_name, activity_type,
                distance_m, duration_s, pace_secs_per_km,
                ROW_NUMBER() OVER (PARTITION BY distance_key ORDER BY duration_s ASC) AS rank
           FROM best_efforts
       ) t WHERE rank <= $1 ORDER BY distance_key, rank`,
      [top]
    );

    const iso = (d) => (d instanceof Date ? d.toISOString().slice(0, 10) : d);
    const byKey = {};
    for (const r of rows) {
      (byKey[r.distance_key] ||= []).push({
        rank: Number(r.rank),
        activity_id: r.activity_id,
        date: iso(r.date),
        name: r.activity_name,
        type: r.activity_type,
        distance_m: Number(r.distance_m),
        duration_s: Number(r.duration_s),
        pace_secs_per_km: r.pace_secs_per_km === null ? null : Number(r.pace_secs_per_km),
      });
    }

    // Always return every target distance, including ones never covered yet,
    // so the UI can say "no record yet" rather than silently omitting them.
    const distances = ORDER.map(([key, label, metres]) => ({
      key,
      label,
      distance_m: metres,
      best: (byKey[key] && byKey[key][0]) || null,
      leaderboard: byKey[key] || [],
    }));

    const counts = await query(
      `SELECT COUNT(DISTINCT activity_id)::int AS activities, COUNT(*)::int AS rows FROM best_efforts`
    );

    return sendJson(res, 200, {
      distances,
      activities_analysed: counts.rows[0]?.activities || 0,
      total_rows: counts.rows[0]?.rows || 0,
      top,
    });
  } catch (err) {
    return sendJson(res, 500, { error: 'server_error', detail: String((err && err.message) || err) });
  }
}
