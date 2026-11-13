-- App data schema (journal + todos) for git-sports-guntar.
-- Mirrors the object shapes in site/storage.js exactly so migration is 1:1.
-- Applied automatically by the API (CREATE ... IF NOT EXISTS); this file is the
-- canonical reference and can also be run manually:
--   psql "$DATABASE_URL" -f sql/schema_app.sql

CREATE TABLE IF NOT EXISTS journal_entries (
    id            TEXT PRIMARY KEY,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    title         TEXT NOT NULL DEFAULT '',
    body          TEXT NOT NULL DEFAULT '',
    activity_id   TEXT,
    activity_date TEXT,
    activity_name TEXT,
    tags          JSONB NOT NULL DEFAULT '[]'::jsonb,
    source        TEXT NOT NULL DEFAULT 'manual'
);

CREATE TABLE IF NOT EXISTS todos (
    id            TEXT PRIMARY KEY,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    text          TEXT NOT NULL DEFAULT '',
    done          BOOLEAN NOT NULL DEFAULT false,
    done_at       TIMESTAMPTZ,
    priority      TEXT NOT NULL DEFAULT 'medium',
    due_date      TEXT,
    source        TEXT NOT NULL DEFAULT 'manual',
    source_detail TEXT
);

-- AI coaching insights. One row per distinct data state (running_fingerprint),
-- so re-running the pipeline on unchanged data updates rather than duplicates.
-- period_start/period_end are NULL for the daily "this month vs last month"
-- report and set for future on-demand analysis over an arbitrary date range.
CREATE TABLE IF NOT EXISTS ai_insights (
    id                  BIGSERIAL PRIMARY KEY,
    generated_at        TIMESTAMPTZ NOT NULL,
    model               TEXT,
    kind                TEXT NOT NULL DEFAULT 'daily',
    running_fingerprint TEXT,
    period_start        DATE,
    period_end          DATE,
    data_summary        JSONB,
    insights            JSONB NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- The same data state (fingerprint) can legitimately be analysed at
    -- different times; dedupe only on an exact re-save of one generation.
    CONSTRAINT ai_insights_fp_gen_key UNIQUE (running_fingerprint, generated_at)
);

-- Per-activity Running Economy, computed over a fixed km 2-4 window so runs of
-- different lengths stay comparable (see scripts/running_economy.py).
CREATE TABLE IF NOT EXISTS running_economy (
    activity_id             TEXT PRIMARY KEY,
    date                    DATE NOT NULL,
    re_ml_kg_km             NUMERIC(6,1) NOT NULL,
    re_rolling              NUMERIC(6,1),
    score                   INTEGER,
    rating                  TEXT,
    efficiency_index        NUMERIC(8,4),
    cardiac_cost            NUMERIC(7,1),
    vertical_ratio          NUMERIC(5,2),
    avg_cadence             INTEGER,
    avg_ground_contact      INTEGER,
    window_hr               NUMERIC(5,1),
    window_pace_secs_per_km NUMERIC(6,1),
    window_distance_m       NUMERIC(8,1),
    total_distance_m        NUMERIC(9,1),
    computed_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Garmin performance metrics over time (VO2max, endurance score, hill score,
-- HRV, race predictions, weight...). Long/narrow so new metric types need no
-- migration; `extra` keeps the raw payload for anything not modelled yet.
CREATE TABLE IF NOT EXISTS performance_metrics (
    id          BIGSERIAL PRIMARY KEY,
    metric      TEXT NOT NULL,
    date        DATE NOT NULL,
    value       NUMERIC(12,3),
    extra       JSONB,
    synced_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT performance_metrics_metric_date_key UNIQUE (metric, date)
);

-- Second-order per-activity metrics (see scripts/derived_metrics.py).
-- Kept separate from running_economy because they qualify different runs:
-- decoupling needs a long steady effort, RE needs a clean km 2-4 window.
CREATE TABLE IF NOT EXISTS activity_derived (
    activity_id     TEXT PRIMARY KEY,
    date            DATE NOT NULL,
    decoupling_pct  NUMERIC(6,2),
    trimp           NUMERIC(8,1),
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Session locations and routes. PRIVATE: served only to an authenticated
-- caller, because start coordinates of a run effectively disclose a home
-- address. `route` holds [[lat,lon], ...] rounded to 5 decimals (~1 m).
CREATE TABLE IF NOT EXISTS activity_locations (
    activity_id     TEXT PRIMARY KEY,
    date            DATE NOT NULL,
    type            TEXT,
    name            TEXT,
    location_name   TEXT,
    start_lat       DOUBLE PRECISION,
    start_lon       DOUBLE PRECISION,
    end_lat         DOUBLE PRECISION,
    end_lon         DOUBLE PRECISION,
    min_lat         DOUBLE PRECISION,
    max_lat         DOUBLE PRECISION,
    min_lon         DOUBLE PRECISION,
    max_lon         DOUBLE PRECISION,
    distance_m      DOUBLE PRECISION,
    route           JSONB,
    route_points    INTEGER,
    synced_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Fastest time covering a target distance within a single activity, found by a
-- rolling window over the activity's distance-vs-time stream (see
-- scripts/sync_best_efforts.py). One row per activity per distance.
CREATE TABLE IF NOT EXISTS best_efforts (
    activity_id      TEXT NOT NULL,
    distance_key     TEXT NOT NULL,
    date             DATE,
    activity_name    TEXT,
    activity_type    TEXT,
    distance_m       DOUBLE PRECISION NOT NULL,
    duration_s       DOUBLE PRECISION NOT NULL,
    pace_secs_per_km DOUBLE PRECISION,
    computed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (activity_id, distance_key)
);

-- On-demand AI analyses, cached per (kind, period) so repeat clicks are free.
-- `inputs_fingerprint` lets the UI tell a stale analysis from a current one
-- without re-billing the model.
CREATE TABLE IF NOT EXISTS ai_analyses (
    kind               TEXT NOT NULL,
    period_key         TEXT NOT NULL,
    generated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    model              TEXT,
    inputs_fingerprint TEXT,
    payload            JSONB NOT NULL,
    PRIMARY KEY (kind, period_key)
);

CREATE INDEX IF NOT EXISTS idx_be_key_time ON best_efforts(distance_key, duration_s);
CREATE INDEX IF NOT EXISTS idx_be_date ON best_efforts(date DESC);
CREATE INDEX IF NOT EXISTS idx_loc_date ON activity_locations(date DESC);
CREATE INDEX IF NOT EXISTS idx_loc_type ON activity_locations(type);
CREATE INDEX IF NOT EXISTS idx_loc_name ON activity_locations(location_name);
CREATE INDEX IF NOT EXISTS idx_derived_date ON activity_derived(date DESC);
CREATE INDEX IF NOT EXISTS idx_re_date ON running_economy(date DESC);
CREATE INDEX IF NOT EXISTS idx_perf_metric_date ON performance_metrics(metric, date DESC);
CREATE INDEX IF NOT EXISTS idx_journal_created ON journal_entries(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_todos_created   ON todos(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_todos_done      ON todos(done);
CREATE INDEX IF NOT EXISTS idx_ai_generated    ON ai_insights(generated_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_kind         ON ai_insights(kind);

-- ── Body page: blood pressure, weigh-ins, profile and the computed summary ──
-- PRIVATE: served only by /api/body (auth required). Deliberately NOT stored in
-- performance_metrics, because /api/performance is public and filters by metric
-- name — a forgotten filter entry there would leak these. Separate tables are
-- unreachable from any public route by construction.
-- measured_local is Garmin's local wall-clock time (morning/evening buckets
-- depend on it); measured_at is the same instant in UTC.
CREATE TABLE IF NOT EXISTS bp_readings (
    garmin_id       TEXT PRIMARY KEY,            -- Garmin "version"
    measured_at     TIMESTAMPTZ NOT NULL,
    measured_local  TIMESTAMP NOT NULL,
    local_date      DATE NOT NULL,
    systolic        INTEGER NOT NULL,
    diastolic       INTEGER NOT NULL,
    pulse           INTEGER,
    source          TEXT,
    notes           TEXT,
    synced_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS weigh_ins (
    garmin_pk       TEXT PRIMARY KEY,            -- Garmin "samplePk"
    measured_at     TIMESTAMPTZ NOT NULL,
    measured_local  TIMESTAMP NOT NULL,
    local_date      DATE NOT NULL,
    weight_kg       NUMERIC(6,2) NOT NULL,
    source          TEXT,
    synced_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Personal constants that must not live in the (public) repo, e.g. height_cm.
CREATE TABLE IF NOT EXISTS body_profile (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per computation (see scripts/body_metrics.py); the API serves the newest.
CREATE TABLE IF NOT EXISTS body_summary (
    id          BIGSERIAL PRIMARY KEY,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    payload     JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bp_local  ON bp_readings(measured_local DESC);
CREATE INDEX IF NOT EXISTS idx_wi_local  ON weigh_ins(measured_local DESC);
CREATE INDEX IF NOT EXISTS idx_body_sum  ON body_summary(computed_at DESC);
