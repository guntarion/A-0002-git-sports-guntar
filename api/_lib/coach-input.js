// Input for the on-demand AI Coach: the same recovery/load/efficiency context the
// Performance analysis uses, plus the monthly running summary the Coach reviews.
// Ported from scripts/generate_ai_insights.py::_build_data_summary so the
// output matches the shape the AI Coach panel already renders.
import { query } from './db.js';
import { buildAnalysisInput } from './analysis-input.js';

const r1 = (v) => (v === null || v === undefined ? null : Math.round(Number(v) * 10) / 10);
const avg = (a) => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : null);
const ymd = (d) => (d instanceof Date ? d.toISOString().slice(0, 10) : String(d).slice(0, 10));

function monthStats(runs, mk) {
  const mr = runs.filter((a) => a.mk === mk);
  if (!mr.length) return null;
  const paces = mr.map((a) => a.avg_pace_secs_per_km).filter((p) => p > 0 && p < 900);
  const hrs = mr.map((a) => a.avg_hr).filter((h) => h > 0);
  const tes = mr.map((a) => a.aerobic_te).filter((t) => t > 0);
  const cad = mr.map((a) => a.avg_cadence).filter((c) => c > 0);
  const effs = mr
    .filter((a) => a.avg_pace_secs_per_km > 0 && a.avg_pace_secs_per_km < 900 && a.avg_hr > 50)
    .map((a) => (1000 / a.avg_pace_secs_per_km / a.avg_hr) * 1000);
  const d = mr.map((a) => a.distance_m);
  return {
    month: mk, runs: mr.length,
    total_km: r1(d.reduce((x, y) => x + y, 0) / 1000),
    avg_pace_secs: paces.length ? Math.round(avg(paces)) : null,
    avg_hr: hrs.length ? Math.round(avg(hrs)) : null,
    avg_aerobic_te: tes.length ? r1(avg(tes)) : null,
    avg_cadence: cad.length ? Math.round(avg(cad)) : null,
    avg_efficiency: effs.length ? Math.round(avg(effs) * 100) / 100 : null,
    longest_km: r1(Math.max(...d) / 1000),
    best_pace_secs: paces.length ? Math.min(...paces) : null,
  };
}

export async function buildCoachInput() {
  const [ctx, acts] = await Promise.all([
    buildAnalysisInput(180),
    query(
      `SELECT date, distance_m, avg_pace_secs_per_km, avg_hr, aerobic_te, avg_cadence,
              vo2_max, avg_stride_len, avg_ground_contact, avg_vertical_osc
         FROM activities
        WHERE lower(coalesce(type,'')) IN ('run','trailrun','virtualrun','trail_run','virtual_run')
          AND distance_m > 500 AND lower(coalesce(raw_type,'')) NOT LIKE '%treadmill%'
        ORDER BY date`
    ),
  ]);
  const runs = acts.rows.map((a) => ({
    ...a,
    date: ymd(a.date),
    mk: ymd(a.date).slice(0, 7),
    distance_m: Number(a.distance_m),
  }));
  if (!runs.length) return { ...ctx, running: null };

  const now = new Date();
  const mkOf = (y, m) => `${y}-${String(m).padStart(2, '0')}`;
  const y = now.getUTCFullYear();
  const m = now.getUTCMonth() + 1; // 1-12
  const back = (n) => {
    const d = new Date(Date.UTC(y, m - 1 - n, 1));
    return mkOf(d.getUTCFullYear(), d.getUTCMonth() + 1);
  };
  const daysLeft = new Date(Date.UTC(y, m, 0)).getUTCDate() - now.getUTCDate();

  return {
    ...ctx,
    running: {
      total_runs: runs.length,
      total_km: r1(runs.reduce((s, a) => s + a.distance_m, 0) / 1000),
      date_range: `${runs[0].date} to ${runs[runs.length - 1].date}`,
      this_month: monthStats(runs, mkOf(y, m)),
      last_month: monthStats(runs, back(1)),
      months_back: [2, 3, 4, 5].map((n) => monthStats(runs, back(n))).filter(Boolean),
      days_left_this_month: daysLeft,
      // Ten, not five: five was too short to see a pattern.
      recent_runs: runs.slice(-10).map((a) => ({
        date: a.date,
        distance_km: r1(a.distance_m / 1000),
        pace_min_km: a.avg_pace_secs_per_km ? Math.round((a.avg_pace_secs_per_km / 60) * 100) / 100 : null,
        avg_hr: a.avg_hr, cadence: a.avg_cadence, aerobic_te: a.aerobic_te,
        vo2_max: a.vo2_max, stride_len: a.avg_stride_len,
        ground_contact: a.avg_ground_contact, vertical_osc: a.avg_vertical_osc,
      })),
    },
  };
}
