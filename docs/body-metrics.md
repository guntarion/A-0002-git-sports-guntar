# Body metrics (weight + home blood pressure)

Definitions for `site/body.html`. All formulas live in `scripts/body_metrics.py` (pure functions, covered by
`tests/test_body_metrics.py`); the page only draws the computed summary.

**Everything here is descriptive, not diagnostic.** Cross-metric results are correlations, never causal claims.

## Data path

```
Omron HEM-7143T1 / HN-300T2 → OMRON connect → Apple Health → iOS Shortcut → health-sync (home server)
  → Garmin Connect → scripts/sync_body_db.py → bp_readings / weigh_ins → body_metrics.py → body_summary
  → /api/body (auth required) → site/body.html
```

Garmin is the single source of truth, so readings typed into the Garmin app are picked up too. Latency is up to
one ETL cycle (daily, 02:00 UTC). Tables are private: no public route reads them (see CLAUDE.md gotchas).

Timestamps are Garmin **local wall-clock** times; "morning" is the user's morning. A UTC copy is kept alongside.

## Weight

| Metric | Definition |
|---|---|
| BMI | `kg / (height_m)²`. Height comes from the `body_profile` table (`--set-height`). |
| Category | Default **Asia-Pacific**: <18.5 kurus · 18.5–22.9 normal · 23–24.9 berisiko · 25–29.9 obesitas I · ≥30 obesitas II. WHO (25/30) is shown beside it. `body.bmi_standard` in `config.yaml` may be `asia_pacific`, `who` or `kemenkes`. |
| Target weight | `target_bmi × height_m²`, default BMI 22.9 (top of the Asia-Pacific normal band). |
| Daily weight | Mean of that day's weigh-ins. |
| Trend | Exponentially weighted moving average, α = 0.1 per day, adjusted for gaps: `a = 1 − (1 − α)^days`. |
| Rate (kg/week) | OLS slope over the last 28 days. Shown only with ≥ 4 weigh-in days spanning ≥ 14 days; otherwise the page says what is needed. |
| Changes | Trend change over 30/90/180/365 days. Anchor = last point on/before the window start, skipped if older than `max(14, days/3)` days. Works with sparse weigh-ins; the insight says when the window has < 4 points. |

BMI does not distinguish muscle from fat; read it alongside waist circumference.

## Blood pressure

| Metric | Definition |
|---|---|
| Session | Readings ≤ 10 minutes apart are one session, averaged. (Home-BP guidance asks for 2 readings 1 minute apart; counting them separately would double-count one moment.) |
| Period | `pagi` before 12:00, `malam` from 17:00, `siang` otherwise (`body.morning_before_hour`, `body.evening_from_hour`). |
| MAP | `diastolic + (systolic − diastolic) / 3`. |
| Pulse pressure | `systolic − diastolic`. Flagged at ≥ 60 mmHg. |
| Home level | Mean of the last 30 days when ≥ 6 sessions, otherwise all data (stated on the card). Compared with the **135/85** home threshold (ESH/ESC): `ok` < 130/80 · `borderline` · `above` ≥ 135 or ≥ 85 · `well_above` ≥ 150 or ≥ 95. |
| Category | ACC/AHA 2017 on each session mean: normal < 120/80 · elevated 120–129 and < 80 · stage 1 130–139 or 80–89 · stage 2 ≥ 140 or ≥ 90 · crisis > 180 or > 120. |
| Trend | OLS of systolic/diastolic vs day over the last 90 days, in mmHg per 30 days. Needs ≥ 15 sessions spanning ≥ 28 days. |
| Variability | Population SD and ARV (mean absolute difference of successive sessions) of systolic. |
| Morning vs evening | Reported only with ≥ 10 sessions per side and a difference ≥ 5 mmHg; otherwise "serupa" or "belum bisa dibuat". |
| Urgent | Any session ≥ 180 systolic or ≥ 120 diastolic in the last 14 days raises a banner advising a repeat measurement after 5 minutes' rest and medical contact if it persists or symptoms are present. |
| Data gap | Last reading ≥ 14 days ago, or a past gap longer than 14 days. |
| Protocol | If < 50 % of sessions hold ≥ 2 readings (with ≥ 10 sessions), suggests the ESH protocol: 2 readings 1 minute apart, morning and evening, for 7 days. |

## Cross-metric relationships

Pearson correlation and OLS slope on paired data. Shown as **Terdeteksi** only when there are enough pairs **and**
|r| ≥ 0.3; with enough pairs and a weaker r it says no meaningful relationship; otherwise it says how much data is missing.

| Item | Pairing | Minimum |
|---|---|---|
| Weight vs systolic | weekly mean weight vs weekly mean systolic | 8 weeks, ≥ 5 distinct weights |
| Sleep vs BP | hours slept (night ending that morning) vs morning systolic | 15 days |
| Resting HR vs BP, HRV vs BP | same-morning value vs morning systolic | 15 days |
| After a hard day vs light day | morning systolic after a day with TRIMP ≥ the user's 75th percentile vs below the median | 20 days of load history, 8 mornings per group |
| Weight vs Efficiency Index | per run: nearest earlier weight (≤ 14 days) vs EI | 15 runs, ≥ 6 distinct weights |

Weight-related items carry a standing caution: weight and the compared variable both drift with time (season, fitness),
so the association is easily spurious.

### Why "hard day" is relative

Garmin records activity almost every day, so `TRIMP > 0` is always true and would put every morning in one group. The
thresholds therefore come from the user's own daily-load distribution.

### Absolute VO₂max

`VO₂max(L/min) = VO₂max(ml/kg/min) × weight(kg) / 1000`. "−1 kg at constant absolute VO₂" changes relative VO₂max by
`w/(w−1) − 1` (≈ +1.27 % at 80 kg). The hypothetical value at target weight uses the same arithmetic. This is pure
arithmetic, not a performance prediction.

## Operations

```bash
python scripts/sync_body_db.py --set-height <cm>     # once; stored in body_profile
python scripts/sync_body_db.py                      # pull Garmin + recompute (first run backfills 400 days)
python scripts/sync_body_db.py --summary-only       # recompute from the DB, no Garmin calls
BODY_BACKFILL_DAYS=400 python scripts/sync_body_db.py   # force a deep re-pull
```

The daily pipeline (`run_pipeline.py`) runs it automatically after `sync_performance_db`. It is non-fatal.
