# CLAUDE.md — Garmin Git (repo: git-sports-guntar)

## Project Overview

Automated fitness dashboard that syncs Garmin activities into GitHub-style heatmaps plus
running analytics, AI coaching, and performance-science metrics. Originally a fork of
`aspain/git-sweaty`; now hosted on **Vercel** with **PostgreSQL**-backed app data.

- **Live site**: https://git-sports-guntar.vercel.app
- **Source**: Garmin Connect (primary)
- **Stack**: Python 3.11+ ETL · vanilla JS frontend (no build step) · Node serverless API · PostgreSQL
- **Deploy**: GitHub Actions (ETL) → Vercel (site + API)

> **GitHub Pages is retired.** The old `guntarion.github.io/git-sports-guntar` URL is disabled
> and `.github/workflows/pages.yml` was deleted. Static hosting cannot reach a database, which
> the journal/todos/performance features require. Do not reinstate it.

## Commands

```bash
# Environment (once)
python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt

# Run full pipeline locally (requires config.local.yaml)
.venv/bin/python scripts/run_pipeline.py            # run from REPO ROOT, paths are root-relative
.venv/bin/python scripts/run_pipeline.py --skip-sync # rebuild from cached data, no Garmin calls

# Local dev server (auto-uses .venv, restores data from dashboard-data branch)
bash scripts/dev_dashboard.sh --skip-build --port 4180

# Tests
cd scripts && ../.venv/bin/python -m pytest ../tests/ -v

# Garmin login (interactive, handles MFA) — writes .garmin_token_store + config.local.yaml
.venv/bin/python scripts/garmin_login_local.py

# Trigger sync
gh workflow run "Sync Heatmaps" --field source=garmin

# Deploy site (use the script: bare `vercel deploy` ships without data -> 404s)
bash scripts/deploy_prod.sh
```

## Architecture

### Data pipeline (`run_pipeline.py` orchestrates all)

```
Garmin Connect
  → sync_garmin.py            (activities/raw/garmin/*.json)
  → enrich_garmin.py          (activities/enriched/garmin/*.json — HR zones, per-km splits)
  → normalize.py              (data/activities_normalized.json)
  → aggregate.py              (data/daily_aggregates.json)
  → generate_heatmaps.py      (site/data.json)
  → generate_activities.py    (site/activities.json)
  → sync_db.py                (PostgreSQL: activities, splits, hr zones)
  → sync_performance_db.py    (PostgreSQL: running_economy, activity_derived,
                               performance_metrics — calls running_economy.py +
                               derived_metrics.py, then pulls Garmin health metrics)
  → generate_ai_insights.py   (site/ai_insights.json + ai_insights table)
```

Every DB/AI stage is **non-fatal** — a failure there must never break the activity pipeline.

### Frontend pages (self-contained HTML, inline CSS/JS, no build)

| Page | File | Data source | Purpose |
|------|------|-------------|---------|
| Dashboard | `site/index.html` + `site/app.js` | `data.json` | Heatmap grid, type/year filters |
| Activities | `site/activities.html` | `activities.json` | Per-activity detail, HR zones, splits |
| Analytics | `site/analytics.html` | `activities.json` + `ai_insights.json` | Charts, AI Coach panel |
| Records | `site/records.html` | `activities.json` | Leaderboards, PRs, split rankings |
| **Performance** | `site/performance.html` | **`/api/performance`** | Garmin health metrics + derived sports-science metrics over time |
| **Wellness** | `site/wellness.html` | **`/api/performance`** | Sleep stages, Body Battery, RHR, steps, stress, PRs |
| **Map** | `site/map.html` | **`/api/locations`** | Session locations + GPS routes (auth-gated) |
| Journal | `site/journal.html` | **`/api/journal`** | Markdown training journal |
| Todos | `site/todos.html` | **`/api/todos`** | Action items |

### Serverless API (`api/`, Node ESM, deployed by Vercel)

| Route | Auth | Purpose |
|-------|------|---------|
| `/api/journal` | **required** | Journal CRUD + bulk import |
| `/api/todos` | **required** | Todo CRUD, toggle, clearCompleted |
| `/api/session` | — | Validates the access token (login gate) |
| `/api/performance` | public* | Time-series performance data (+ `latest_any` singletons) |
| `/api/locations` | **required** | Session coordinates + GPS routes — never public |

\* Public read matches the rest of the dashboard. **`weight_kg` is the one exception** — returned
only to an authenticated caller.

Shared helpers: `api/_lib/db.js` (pg pool + lazy schema), `api/_lib/http.js` (Bearer auth with
constant-time compare, JSON body, responses).

### Database (PostgreSQL on VPS)

Schema reference: `sql/schema_app.sql` (also applied lazily by the API and the sync script).

| Table | Contents |
|-------|----------|
| `journal_entries`, `todos` | App data, replacing browser localStorage |
| `ai_insights` | Every AI report; unique on `(running_fingerprint, generated_at)` |
| `running_economy` | Per-activity RE, score, EI, cardiac cost, vertical ratio |
| `activity_derived` | Per-activity aerobic decoupling + TRIMP |
| `performance_metrics` | Long/narrow time series: `metric`, `date`, `value`, `extra` JSONB |
| `activity_locations` | Session coordinates, place names and `route` polylines (private) |
| `activities`, `activity_splits`, `activity_hr_zones` | From `sync_db.py` (optional) |

`performance_metrics` is intentionally long/narrow so a new metric needs no migration.

### Branching

- `main` — source, site, workflows
- `dashboard-data` — persisted pipeline state (`data/`, `site/*.json`, `activities/enriched/`)

## Key files

| File | Purpose |
|------|---------|
| `scripts/run_pipeline.py` | Master orchestrator |
| `scripts/sync_garmin.py` | Garmin API sync, OAuth, backfill |
| `scripts/enrich_garmin.py` | Per-activity HR zones & splits |
| `scripts/running_economy.py` | Running Economy, EI, cardiac cost, vertical ratio |
| `scripts/derived_metrics.py` | Decoupling, TRIMP, zone efficiency, recovery balance |
| `scripts/sync_performance_db.py` | Writes the above to PG + pulls Garmin health metrics |
| `scripts/ai_insights_db.py` | Persists AI reports (`--backfill` imports history JSON) |
| `scripts/sync_locations.py` | Session coordinates (bulk) + GPS routes (`--routes`, resumable) |
| `scripts/garmin_login_local.py` | Interactive local Garmin login (MFA-aware) |
| `scripts/vercel_build.sh` | Vercel build step: hydrate `site/*.json` from `dashboard-data` |
| `site/nav.js` | **The** navigation component — add new pages to its `PAGES` list, not to each HTML file |
| `docs/performance-metrics.md` | **Definitions + formulas for every Performance metric** |
| `config.yaml` | Base config incl. `running_economy` personal constants |
| `.env` | `DATABASE_URL` (gitignored, **repo root — never `site/`**) |

## Environment variables

**GitHub Actions secrets:** `GARMIN_TOKENS_B64` (or `GARMIN_EMAIL`+`GARMIN_PASSWORD`),
`DATABASE_URL`, `QWEN_API_KEY`, `VERCEL_TOKEN`.
Workflow env: `HRV_BACKFILL_DAYS` (default 7 — one Garmin request per day).

**Vercel env:** `DATABASE_URL`, `APP_AUTH_TOKEN` (journal/todos password), `PGSSL=require`.

**Local:** `.env` with `DATABASE_URL`; `config.local.yaml` with Garmin credentials.

## Data filtering rules

Applied consistently across frontend and backend:
- Exclude runs < 500 m
- Exclude pace > 15:00/km (900 s) — anomalous data
- Exclude splits < 500 m (records page)
- **Running Economy additionally**: km 2–4 window only, true 800–1200 m splits, 65–85% HRmax,
  split-pace CV ≤ 6%, no treadmill/trail

## Gotchas

- **Never run bare `vercel deploy --prod` from a laptop.** The upload has no `.git`, so `vercel_build.sh` cannot hydrate `site/*.json` from `dashboard-data` and the site ships with an empty `data.json` and no `activities.json` (404 on Activities/Analytics/Records, `slice` error on the dashboard). Use `bash scripts/deploy_prod.sh`.
- **`.env` belongs at the repo root, never in `site/`** — `site/` is web-served, so a `.env`
  there would be publicly fetchable.
- **`gh secret set` reads stdin only WITHOUT `--body -`.** `--body -` stores the literal string
  `"-"`; this silently broke the Garmin sync once (`binascii.Error: Only base64 data is allowed`).
- **Garmin `recoveryTime` is in MINUTES, not hours.** A 12 km run returned 1614 (= 26.9 h).
- **`vo2max_ref` in `config.yaml` must stay frozen.** Garmin derives VO2max from the same
  HR/pace relationship Running Economy measures; feeding it per-activity flattens the series.
- **Treadmill runs must be excluded from running metrics.** Match by substring
  (`"treadmill_running"` does not equal `"treadmill"`), and note their pace is
  accelerometer-derived, not GPS.
- **Garmin sometimes emits merged laps** (a single 2996 m "split"), so split filters must bound
  both a minimum and a maximum.
- **Garmin rejects long ranges on some endpoints** (`get_body_battery`) — chunk to ~28 days.
- **`get_max_metrics` returns empty** for this account; VO2max history is reconstructed from
  per-activity `vo2_max`.
- **Rate limits**: per-day endpoints (HRV, SpO2, readiness, RHR, sleep) cost one request per
  day. Backfills are resumable — they skip dates already stored and back off on failure.
- Python scripts assume **cwd = repo root** (paths like `site/activities.json` are relative).
- `site/app.js` is ~186 KB — the heatmap SPA with complex touch handling.
- **Navigation lives only in `site/nav.js`.** It injects itself and strips the
  three legacy nav shapes (`.header-nav`, `.header-links`, bare `.nav-back`
  links). Do not hand-write nav markup in a page; add the route to `PAGES`.
  It also rescues index.html's `.repo-link`, which `app.js` still updates.
- `api/analyze.js` needs `maxDuration: 180` in `vercel.json` (Pro plan). It calls Qwen with a
  ~5.5 KB prompt and up to 4000 output tokens, which exceeds the 30 s default and returns 504.
  `vercel.json` rejects unknown keys, so this note lives here rather than inline.
- Vercel auto-detects this as a **Python** project because of the root `requirements.txt`;
  `vercel.json` pins `framework: null` and `.vercelignore` excludes the Python files.
- New Vercel projects enable **Deployment Protection** (SSO), which 302-redirects everything
  including the API. It must be disabled for the public dashboard.

## Testing

```bash
cd scripts && ../.venv/bin/python -m pytest ../tests/ -v
```

~263 tests. **9 pre-existing failures** unrelated to this fork's features:
4 in `test_run_pipeline_source_switch.py` (failed before any of this work), 3 in
`test_bootstrap_flow.py` (upstream's fresh-machine onboarding simulation), 2 in
`test_bootstrap_windows_wrapper.py` (they assert upstream README wording, which this fork
deliberately replaced). Treat "9 failed, 254 passed" as the baseline.

## Workflows

| Workflow | Trigger | Does |
|----------|---------|------|
| Sync Heatmaps | Daily 02:00 UTC / manual | Full ETL → `dashboard-data` → deploy to Vercel |

`Deploy Pages` was deleted with the GitHub Pages retirement.
