// Input for the on-demand Body analysis (kind=body): the aggregates computed by
// scripts/body_metrics.py, never the per-session timestamps. The rule-based
// insights are included so the model builds on them instead of contradicting them.
import { query } from './db.js';

export async function buildBodyInput() {
  let row = null;
  try {
    const r = await query(`SELECT computed_at, payload FROM body_summary ORDER BY computed_at DESC LIMIT 1`);
    row = r.rows[0] || null;
  } catch (err) {
    if (!(err && err.code === '42P01')) throw err; // tables not created yet
  }
  if (!row) return { has_data: false };

  const S = row.payload || {};
  const w = S.weight || {};
  const bp = S.bp || {};
  const series = w.series || [];

  return {
    has_data: Boolean(bp.n_sessions || series.length),
    // Cheap change detector for the "stale" badge (see analyze.js).
    fingerprint: [S.as_of, bp.n_sessions, w.n_days, w.latest && w.latest.kg].map((x) => (x == null ? '-' : x)).join(':'),
    as_of: S.as_of,
    profile: S.profile,
    weight: {
      height_cm: w.height_cm, latest: w.latest, trend_kg: w.trend_kg, bmi: w.bmi,
      bmi_category: w.bmi_category && w.bmi_category.label,
      bmi_category_who: w.bmi_category_who && w.bmi_category_who.label,
      target: w.target, avg7: w.avg7, avg30: w.avg30, rate_kg_per_week: w.rate,
      changes: w.changes,
      // Unambiguous names: the weigh-ins are spread thinly over a long period.
      n_weighins_total: w.n_weighins,
      n_distinct_days_with_weighin: w.n_days,
      weighin_period: { first: series.length ? series[0].date : null, last: series.length ? series[series.length - 1].date : null },
      weighin_days_in_last_30_days: w.avg30 ? w.avg30.n : 0,
      first_point: series[0] || null, last_point: series[series.length - 1] || null,
    },
    blood_pressure: {
      n_sessions: bp.n_sessions, n_days: bp.n_days, first_date: bp.first_date, last_date: bp.last_date,
      days_since_last: bp.days_since_last, level: bp.level, morning: bp.am, evening: bp.pm,
      share_ge_home_threshold_pct: bp.share_ge_home, share_ge_140_90_pct: bp.share_ge_140_90,
      map_mean: bp.map_mean, pulse_pressure_mean: bp.pp_mean, sd_sys: bp.sd_sys, arv_sys: bp.arv_sys,
      share_sessions_with_2plus_readings_pct: bp.share_multi_reading, days_with_morning_and_evening: bp.days_with_am_pm,
      trend: bp.trend, monthly: bp.monthly, categories_by_month: bp.categories_by_month,
      gaps: bp.gaps, urgent: bp.urgent,
    },
    cross_metric: ((S.cross && S.cross.items) || []).map((i) => ({
      id: i.id, title: i.title, status: i.status, n: i.n, needed: i.needed,
      r: i.r, slope: i.slope, text: i.text, caution: i.caution,
    })),
    vo2max_absolute: S.cross && S.cross.vo2max_abs,
    rule_based_insights: (S.insights || []).map((i) => ({ id: i.id, severity: i.severity, title: i.title })),
  };
}
