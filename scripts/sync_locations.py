"""
Sync session locations and GPS routes from Garmin into PostgreSQL.

Two phases, both resumable:

1. Coordinates + place names — one bulk `get_activities()` page covers ~200
   activities, so the whole history costs a couple of requests.
2. Routes (polylines) — one `get_activity_details()` request per activity, so
   this is the expensive part. It only fetches activities that do not already
   have a stored route, backs off on failure, and can be stopped and resumed.

EXCLUSIONS: `locations.exclude_activity_ids` in config.yaml lists activities whose
GPS data is corrupt (e.g. a final fix that jumped hundreds of km). Both phases skip
them, so a manual DELETE from activity_locations stays deleted. Without this the
bulk phase re-lists every activity from Garmin on each run and would re-insert it.

PRIVACY: this data is served only to authenticated callers (see
api/locations.js). Start coordinates of a run typically disclose a home address.

Usage (from the repo root):
    DATABASE_URL=... python scripts/sync_locations.py            # coords only
    DATABASE_URL=... python scripts/sync_locations.py --routes   # + routes
    DATABASE_URL=... python scripts/sync_locations.py --routes --limit 50
"""
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

from utils import load_config

# No sport allowlist on purpose. An allowlist silently drops whatever it did
# not anticipate — it already lost a `multi_sport` session. The presence of GPS
# coordinates is the real test of "can this be mapped", and it also excludes
# indoor work (treadmill, indoor_cycling, strength) for free, since Garmin
# reports no position for those.

UPSERT = """
INSERT INTO activity_locations (
    activity_id, date, type, name, location_name,
    start_lat, start_lon, end_lat, end_lon,
    min_lat, max_lat, min_lon, max_lon,
    distance_m, route, route_points, synced_at
) VALUES (
    %(activity_id)s, %(date)s, %(type)s, %(name)s, %(location_name)s,
    %(start_lat)s, %(start_lon)s, %(end_lat)s, %(end_lon)s,
    %(min_lat)s, %(max_lat)s, %(min_lon)s, %(max_lon)s,
    %(distance_m)s, %(route)s::jsonb, %(route_points)s, now()
)
ON CONFLICT (activity_id) DO UPDATE SET
    date=EXCLUDED.date, type=EXCLUDED.type, name=EXCLUDED.name,
    location_name=EXCLUDED.location_name,
    start_lat=EXCLUDED.start_lat, start_lon=EXCLUDED.start_lon,
    end_lat=EXCLUDED.end_lat, end_lon=EXCLUDED.end_lon,
    -- Never overwrite an existing route with NULL: the coords-only phase runs
    -- again on every sync and would otherwise wipe routes already fetched.
    min_lat=COALESCE(EXCLUDED.min_lat, activity_locations.min_lat),
    max_lat=COALESCE(EXCLUDED.max_lat, activity_locations.max_lat),
    min_lon=COALESCE(EXCLUDED.min_lon, activity_locations.min_lon),
    max_lon=COALESCE(EXCLUDED.max_lon, activity_locations.max_lon),
    distance_m=EXCLUDED.distance_m,
    route=COALESCE(EXCLUDED.route, activity_locations.route),
    route_points=COALESCE(EXCLUDED.route_points, activity_locations.route_points),
    synced_at=now()
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


def _simplify(points: List[Dict], max_points: int = 500) -> List[List[float]]:
    """[[lat, lon], ...] rounded to ~1 m, evenly downsampled if very long.

    Keeps the last point so the route always closes where the activity ended.
    """
    coords = [
        [round(p["lat"], 5), round(p["lon"], 5)]
        for p in points
        if p.get("lat") is not None and p.get("lon") is not None
    ]
    if len(coords) <= max_points:
        return coords
    step = len(coords) / float(max_points)
    out = [coords[int(i * step)] for i in range(max_points)]
    if out[-1] != coords[-1]:
        out.append(coords[-1])
    return out


def excluded_ids(config: Dict[str, Any]) -> frozenset:
    """Activity ids listed under `locations.exclude_activity_ids`, as strings."""
    raw = ((config or {}).get("locations") or {}).get("exclude_activity_ids") or []
    return frozenset(str(x).strip() for x in raw if str(x).strip())


def fetch_coordinates(conn, client, page_size: int = 200, exclude=frozenset()) -> Dict[str, int]:
    """Phase 1 — bulk activity list, cheap."""
    seen = 0
    written = 0
    skipped_excluded = 0
    start = 0
    while True:
        batch = client.get_activities(start, page_size) or []
        if not batch:
            break
        for a in batch:
            seen += 1
            if str(a.get("activityId")) in exclude:
                skipped_excluded += 1
                continue
            type_key = ((a.get("activityType") or {}).get("typeKey")) or ""
            # Mappable == has coordinates. See the note at the top of the file.
            if a.get("startLatitude") is None or a.get("startLongitude") is None:
                continue
            row = {
                "activity_id": str(a.get("activityId")),
                "date": (a.get("startTimeLocal") or "")[:10] or None,
                "type": type_key,
                "name": a.get("activityName"),
                "location_name": a.get("locationName"),
                "start_lat": a.get("startLatitude"),
                "start_lon": a.get("startLongitude"),
                "end_lat": a.get("endLatitude"),
                "end_lon": a.get("endLongitude"),
                "min_lat": None, "max_lat": None, "min_lon": None, "max_lon": None,
                "distance_m": a.get("distance"),
                "route": None, "route_points": None,
            }
            if not row["date"]:
                continue
            with conn, conn.cursor() as cur:
                cur.execute(UPSERT, row)
            written += 1
        if len(batch) < page_size:
            break
        start += page_size
        time.sleep(1.0)
    return {"seen": seen, "written": written, "excluded": skipped_excluded}


def fetch_routes(conn, client, limit: Optional[int] = None, exclude=frozenset()) -> Dict[str, int]:
    """Phase 2 — one request per activity. Resumable: skips rows that already
    have a route, so re-running fills in whatever was missed."""
    with conn, conn.cursor() as cur:
        cur.execute(
            "SELECT activity_id FROM activity_locations WHERE route IS NULL ORDER BY date DESC"
        )
        todo = [r[0] for r in cur.fetchall() if str(r[0]) not in exclude]
    if limit:
        todo = todo[:limit]
    if not todo:
        return {"todo": 0, "written": 0, "skipped": 0}

    print(f"  routes: {len(todo)} activities to fetch")
    written = 0
    skipped = 0
    misses = 0
    for n, aid in enumerate(todo, 1):
        try:
            det = client.get_activity_details(aid) or {}
            gp = det.get("geoPolylineDTO") or {}
            pts = gp.get("polyline") or []
            if not pts:
                skipped += 1
            else:
                coords = _simplify(pts)
                with conn, conn.cursor() as cur:
                    cur.execute(
                        """UPDATE activity_locations
                              SET route=%s::jsonb, route_points=%s,
                                  min_lat=%s, max_lat=%s, min_lon=%s, max_lon=%s,
                                  synced_at=now()
                            WHERE activity_id=%s""",
                        (json.dumps(coords), len(coords),
                         gp.get("minLat"), gp.get("maxLat"),
                         gp.get("minLon"), gp.get("maxLon"), aid),
                    )
                written += 1
            misses = 0
            time.sleep(1.2)
        except Exception as exc:
            misses += 1
            print(f"  route {aid} failed ({misses}): {exc}", file=sys.stderr)
            if misses >= 5:
                print("  too many consecutive failures; stopping early.", file=sys.stderr)
                break
            time.sleep(5 * misses)
        if n % 25 == 0:
            print(f"  routes progress: {n}/{len(todo)}")
    return {"todo": len(todo), "written": written, "skipped": skipped}


def main() -> int:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        print("DATABASE_URL not set; skipping location sync.", file=sys.stderr)
        return 0

    # CLI flags win; otherwise fall back to env so the daily pipeline can call
    # main() directly. Routes cost one request per activity, so the pipeline
    # caps how many it fetches per run — a backlog then drains over several
    # days instead of hammering Garmin in one go.
    want_routes = "--routes" in sys.argv or os.environ.get("LOCATION_ROUTES", "") == "1"
    limit = None
    if "--limit" in sys.argv:
        try:
            limit = int(sys.argv[sys.argv.index("--limit") + 1])
        except (IndexError, ValueError):
            limit = None
    if limit is None:
        try:
            limit = int(os.environ.get("LOCATION_ROUTE_LIMIT", "") or 0) or None
        except ValueError:
            limit = None

    try:
        config = load_config()
        from sync_garmin import load_garmin_client

        client = load_garmin_client(config)
        conn = _connect(database_url)
    except Exception as exc:
        print(f"Warning: location sync could not start: {exc}", file=sys.stderr)
        return 0

    try:
        _ensure_schema(conn)
        exclude = excluded_ids(config)
        stats = fetch_coordinates(conn, client, exclude=exclude)
        print(f"Locations: scanned={stats['seen']} stored={stats['written']} excluded={stats['excluded']}")
        if want_routes:
            r = fetch_routes(conn, client, limit=limit, exclude=exclude)
            print(f"Routes: written={r['written']} no-gps={r['skipped']} of {r['todo']}")
        else:
            print("Routes skipped (pass --routes to fetch them).")
    except Exception as exc:
        print(f"Warning: location sync failed: {exc}", file=sys.stderr)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
