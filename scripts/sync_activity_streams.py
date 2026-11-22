"""
Sync per-sample activity streams (HR / pace / cadence / power / stamina ... over
time and distance) plus the full Garmin summary into PostgreSQL, so the
Activities and Coaching pages can draw the same charts Garmin Connect shows.

Why a table and not site/activities.json: one run is ~1500 samples x ~10
metrics (~100 KB). Across the whole history that would bloat the file every page
downloads, so streams are served per activity by /api/activity-detail.

Requests per activity: get_activity_details (the streams), get_activity (the
summary: power, respiration, temperature, training effect, stamina, ...) and, if
the client supports it, the power-zone timings. The sync is resumable: activities
that already have a row are skipped, and activities with no stream are stored with
points=0 so they are not retried forever.

PRIVACY: GPS is never stored here (maxpoly=0, lat/lon keys dropped from the
summary). /api/activity-detail is public like the rest of the dashboard; routes
stay behind /api/locations.

UNITS: Garmin's detail payload is not consistent about units across devices
(stride length in cm vs m, cadence per leg vs per minute, ...). Rather than trust
a table, each stream is rescaled against the matching figure in the activity
summary, and only by a power of ten / a factor of two (see `calibrate`).

Usage (from the repo root):
    DATABASE_URL=... python scripts/sync_activity_streams.py              # newest 25
    DATABASE_URL=... python scripts/sync_activity_streams.py --limit 0    # everything
    DATABASE_URL=... python scripts/sync_activity_streams.py --id 123456  # one activity, refetch
    DATABASE_URL=... python scripts/sync_activity_streams.py --rebuild    # refetch all
"""
import json
import math
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from utils import load_config

DEFAULT_LIMIT = 25
MAX_POINTS = 1500

# (stream name, [Garmin descriptor keys in order of preference], decimals)
KNOWN_KEYS: List[Tuple[str, List[str], int]] = [
    ("hr", ["directHeartRate"], 0),
    ("speed", ["directSpeed"], 3),
    ("gap", ["directGradeAdjustedSpeed"], 3),
    ("cadence", ["directDoubleCadence", "directRunCadence", "directBikeCadence"], 0),
    ("power", ["directPower"], 0),
    ("elev", ["directElevation", "directCorrectedElevation"], 1),
    ("stride", ["directStrideLength"], 2),
    ("vosc", ["directVerticalOscillation"], 1),
    ("gct", ["directGroundContactTime"], 0),
    ("vratio", ["directVerticalRatio"], 1),
    ("resp", ["directRespirationRate"], 0),
    ("temp", ["directAirTemperature"], 1),
]
TIME_KEYS = ["sumElapsedDuration", "sumDuration", "sumMovingDuration"]
DIST_KEY = "sumDistance"
# Never stored as a metric: position (private), bookkeeping and unused variants.
SKIP_KEYS = {
    "directLatitude", "directLongitude", "directTimestamp", "directFractionalCadence",
    "sumElapsedDuration", "sumDuration", "sumMovingDuration", "sumDistance",
    "directDoubleCadence", "directRunCadence", "directBikeCadence",
    "directElevation", "directCorrectedElevation",
}

# name -> (summary keys, "mean"|"max"). The factor that makes the stream agree with
# the summary is snapped to a plausible unit conversion.
CALIBRATION: Dict[str, Tuple[List[str], str]] = {
    "hr": (["averageHR"], "mean"),
    "speed": (["averageMovingSpeed", "averageSpeed"], "mean"),
    "cadence": (["averageRunCadence", "averageBikeCadence"], "mean"),
    "power": (["averagePower", "avgPower"], "mean"),
    "stride": (["strideLength", "avgStrideLength"], "mean"),
    "vosc": (["verticalOscillation", "avgVerticalOscillation"], "mean"),
    "gct": (["groundContactTime", "avgGroundContactTime"], "mean"),
    "vratio": (["verticalRatio", "avgVerticalRatio"], "mean"),
    "elev": (["maxElevation"], "max"),
}
SNAP_FACTORS = [0.01, 0.1, 0.5, 1.0, 2.0, 10.0, 100.0]

UPSERT = """
INSERT INTO activity_streams (
    activity_id, date, type, points, streams, summary, power_zones, fetched_at
) VALUES (
    %(activity_id)s, %(date)s, %(type)s, %(points)s,
    %(streams)s::jsonb, %(summary)s::jsonb, %(power_zones)s::jsonb, now()
)
ON CONFLICT (activity_id) DO UPDATE SET
    date=EXCLUDED.date, type=EXCLUDED.type, points=EXCLUDED.points,
    streams=EXCLUDED.streams, summary=EXCLUDED.summary,
    power_zones=EXCLUDED.power_zones, fetched_at=now()
"""


def _connect(database_url: str):
    import psycopg2
    from sync_db import _clean_database_url

    return psycopg2.connect(_clean_database_url(database_url), connect_timeout=15)


def _ensure_schema(conn) -> None:
    path = os.path.join("sql", "schema_app.sql")
    if os.path.exists(path):
        with conn, conn.cursor() as cur:
            cur.execute(open(path, "r", encoding="utf-8").read())


# ── pure helpers (unit-tested) ────────────────────────────────────────────────

def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def snap_factor(summary_value: Optional[float], stream_value: Optional[float]) -> float:
    """Unit factor that maps the stream onto the summary figure, or 1.0.

    Only accepts a factor within 25% of a plausible conversion (x0.01 ... x100,
    x2 for per-leg cadence). Anything else is left untouched rather than guessed.
    """
    if not summary_value or not stream_value or summary_value <= 0 or stream_value <= 0:
        return 1.0
    ratio = summary_value / stream_value
    best = min(SNAP_FACTORS, key=lambda f: abs(math.log(ratio / f)))
    return best if abs(math.log(ratio / best)) <= math.log(1.25) else 1.0


def _first_present(summary: Dict[str, Any], keys: List[str]) -> Optional[float]:
    for k in keys:
        v = _num(summary.get(k))
        if v:
            return v
    return None


def calibrate(name: str, values: List[Optional[float]], summary: Dict[str, Any]) -> float:
    spec = CALIBRATION.get(name)
    nums = [v for v in values if v is not None]
    if not spec or not nums:
        return 1.0
    target = _first_present(summary, spec[0])
    stat = max(nums) if spec[1] == "max" else sum(nums) / len(nums)
    return snap_factor(target, stat)


def _extra_name(key: str) -> str:
    base = key[6:] if key.startswith("direct") else key
    return base[:1].lower() + base[1:]


def _column(rows: List[List[Any]], idx: int) -> List[Optional[float]]:
    out: List[Optional[float]] = []
    for m in rows:
        out.append(_num(m[idx]) if idx < len(m) else None)
    return out


def _downsample_indices(n: int, max_points: int) -> List[int]:
    if n <= max_points:
        return list(range(n))
    step = n / float(max_points)
    idx = [int(i * step) for i in range(max_points)]
    if idx[-1] != n - 1:
        idx.append(n - 1)
    return idx


def build_streams(details: Dict[str, Any], summary: Optional[Dict[str, Any]] = None,
                  max_points: int = MAX_POINTS) -> Dict[str, Any]:
    """Compact column-oriented streams from a get_activity_details() payload.

        {"n": 1432, "t": [sec...], "d": [metres...], "m": {"hr": [...], "speed": [...]}}

    Empty dict when the activity has no usable samples (strength, manual entries).
    """
    summary = summary or {}
    descs = (details or {}).get("metricDescriptors") or []
    rows = [r.get("metrics") or [] for r in ((details or {}).get("activityDetailMetrics") or [])
            if isinstance(r, dict)]
    if not descs or not rows:
        return {}
    by_key = {d.get("key"): d.get("metricsIndex") for d in descs
              if isinstance(d, dict) and d.get("key") and isinstance(d.get("metricsIndex"), int)}

    time_key = next((k for k in TIME_KEYS if k in by_key), None)
    t = _column(rows, by_key[time_key]) if time_key else [float(i) for i in range(len(rows))]
    dist = _column(rows, by_key[DIST_KEY]) if DIST_KEY in by_key else None
    if dist is not None:
        factor = snap_factor(_num(summary.get("distance")), max([v for v in dist if v is not None] or [0]))
        dist = [None if v is None else v * factor for v in dist]

    metrics: Dict[str, List[Optional[float]]] = {}
    decimals: Dict[str, int] = {}
    for name, keys, dec in KNOWN_KEYS:
        key = next((k for k in keys if k in by_key), None)
        if key is None:
            continue
        col = _column(rows, by_key[key])
        f = calibrate(name, col, summary)
        metrics[name] = [None if v is None else v * f for v in col]
        decimals[name] = dec
    for key, idx in by_key.items():
        if key in SKIP_KEYS or any(key in ks for _n, ks, _d in KNOWN_KEYS):
            continue
        col = _column(rows, idx)
        if sum(1 for v in col if v is not None) < 3:
            continue
        name = _extra_name(key)
        if name in metrics:
            continue
        metrics[name] = col
        decimals[name] = 1

    # Keep only rows that advance time, then downsample.
    keep: List[int] = []
    last_t = None
    for i, tv in enumerate(t):
        if tv is None:
            continue
        if last_t is not None and tv < last_t:
            continue
        keep.append(i)
        last_t = tv
    if len(keep) < 2:
        return {}
    pick = [keep[j] for j in _downsample_indices(len(keep), max_points)]

    def pack(col: List[Optional[float]], dec: int) -> List[Optional[float]]:
        out: List[Optional[float]] = []
        for i in pick:
            v = col[i]
            out.append(None if v is None else (int(round(v)) if dec == 0 else round(v, dec)))
        return out

    t0 = t[pick[0]] or 0.0
    out: Dict[str, Any] = {
        "n": len(pick),
        "t": [round((t[i] or 0.0) - t0, 1) for i in pick],
        "m": {},
    }
    if dist is not None:
        out["d"] = pack(dist, 1)
    for name, col in metrics.items():
        packed = pack(col, decimals[name])
        if any(v is not None for v in packed):
            out["m"][name] = packed
    return out if out["m"] or "d" in out else {}


def clean_summary(detail: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Scalar fields of get_activity()['summaryDTO'], minus anything positional."""
    src = (detail or {}).get("summaryDTO") or {}
    out: Dict[str, Any] = {}
    for k, v in src.items():
        lk = k.lower()
        if "latitude" in lk or "longitude" in lk or lk in ("startlocation", "endlocation"):
            continue
        if isinstance(v, (int, float, str, bool)) and not (isinstance(v, float) and not math.isfinite(v)):
            out[k] = v
    return out


def parse_power_zones(raw: Any) -> List[Dict[str, Any]]:
    from enrich_garmin import _parse_hr_zones

    zones = raw
    if isinstance(raw, dict):
        zones = raw.get("timeInZone") or raw.get("zones")
    if not isinstance(zones, list):
        return []
    parsed = _parse_hr_zones(zones)
    # _parse_hr_zones names the boundaries *_bpm; for power they are watts.
    return [
        {("low_w" if k == "low_bpm" else "high_w" if k == "high_bpm" else k): v for k, v in z.items()}
        for z in parsed
    ]


# ── Garmin calls ──────────────────────────────────────────────────────────────

def _fetch_details(client: Any, activity_id: str) -> Dict[str, Any]:
    try:
        return client.get_activity_details(activity_id, maxchart=MAX_POINTS, maxpoly=0) or {}
    except TypeError:
        return client.get_activity_details(activity_id) or {}


def _fetch_power_zones(client: Any, activity_id: str) -> List[Dict[str, Any]]:
    method = getattr(client, "get_activity_power_in_timezones", None)
    if not callable(method):
        return []
    try:
        return parse_power_zones(method(activity_id))
    except Exception:
        return []  # not every activity has power; never fatal


def _candidates(client: Any, done: set) -> List[Dict[str, str]]:
    """Newest first. Uses the bulk activity list (one request per ~200 activities)."""
    out: List[Dict[str, str]] = []
    start = 0
    while True:
        batch = client.get_activities(start, 200) or []
        if not batch:
            break
        for a in batch:
            aid = str(a.get("activityId"))
            if aid in done:
                continue
            out.append({
                "id": aid,
                "date": (a.get("startTimeLocal") or "")[:10],
                "type": ((a.get("activityType") or {}).get("typeKey")) or "",
            })
        if len(batch) < 200:
            break
        start += 200
        time.sleep(1.0)
    return out


def store_activity(conn, client: Any, cand: Dict[str, str]) -> int:
    detail = client.get_activity(cand["id"]) or {}
    time.sleep(0.4)
    summary = clean_summary(detail)
    streams = build_streams(_fetch_details(client, cand["id"]), summary)
    time.sleep(0.4)
    power_zones = _fetch_power_zones(client, cand["id"]) if "power" in streams.get("m", {}) else []
    with conn, conn.cursor() as cur:
        cur.execute(UPSERT, {
            "activity_id": cand["id"],
            "date": cand["date"] or None,
            "type": cand["type"],
            "points": streams.get("n", 0),
            "streams": json.dumps(streams),
            "summary": json.dumps(summary),
            "power_zones": json.dumps(power_zones),
        })
    return streams.get("n", 0)


def _arg_int(flag: str, env: str, default: Optional[int]) -> Optional[int]:
    raw: Optional[str] = None
    if flag in sys.argv:
        try:
            raw = sys.argv[sys.argv.index(flag) + 1]
        except IndexError:
            raw = None
    if raw is None:
        raw = os.environ.get(env, "") or None
    if raw is None:
        return default
    try:
        val = int(raw)
    except ValueError:
        return default
    return val or None  # 0 means "no limit"


def main() -> int:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        print("DATABASE_URL not set; skipping activity streams.", file=sys.stderr)
        return 0

    rebuild = "--rebuild" in sys.argv
    only = sys.argv[sys.argv.index("--id") + 1] if "--id" in sys.argv else None
    limit = _arg_int("--limit", "STREAM_LIMIT", DEFAULT_LIMIT)

    try:
        config = load_config()
        from sync_garmin import load_garmin_client

        client = load_garmin_client(config)
        conn = _connect(database_url)
    except Exception as exc:
        print(f"Warning: activity-stream sync could not start: {exc}", file=sys.stderr)
        return 0

    try:
        _ensure_schema(conn)
        done: set = set()
        if not rebuild and not only:
            with conn, conn.cursor() as cur:
                cur.execute("SELECT activity_id FROM activity_streams")
                done = {r[0] for r in cur.fetchall()}

        if only:
            todo = [{"id": only, "date": "", "type": ""}]
            meta = next((c for c in _candidates(client, set()) if c["id"] == only), None)
            if meta:
                todo = [meta]
        else:
            todo = _candidates(client, done)
        if limit:
            todo = todo[:limit]
        if not todo:
            print("Activity streams: nothing new to process.")
            return 0

        print(f"Activity streams: {len(todo)} activities to fetch ({len(done)} already stored)")
        written = empty = misses = 0
        for n, cand in enumerate(todo, 1):
            try:
                points = store_activity(conn, client, cand)
                written += 1
                empty += 0 if points else 1
                misses = 0
                time.sleep(1.0)
            except Exception as exc:
                misses += 1
                print(f"  {cand['id']} failed ({misses}): {exc}", file=sys.stderr)
                if misses >= 5:
                    print("  too many consecutive failures; stopping early.", file=sys.stderr)
                    break
                time.sleep(5 * misses)
            if n % 20 == 0:
                print(f"  progress: {n}/{len(todo)}")
        print(f"Activity streams: stored {written} ({empty} without samples)")
    except Exception as exc:
        print(f"Warning: activity-stream sync failed: {exc}", file=sys.stderr)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
