"""
Sync blood pressure + weigh-ins from Garmin Connect into PostgreSQL and compute
the Body-page summary (scripts/body_metrics.py).

Data path: Omron -> OMRON connect -> Apple Health -> iOS Shortcut -> health-sync
-> Garmin Connect -> HERE. Garmin stays the single source of truth, so readings
typed into the Garmin app are picked up too.

Non-fatal by design: a failure here must never break the activity pipeline.
Requires DATABASE_URL; Garmin calls additionally need a token store.

Usage (cwd = repo root):
    python scripts/sync_body_db.py                    # pull Garmin + recompute
    python scripts/sync_body_db.py --set-height <cm>   # one-off: store height (cm)
    python scripts/sync_body_db.py --summary-only     # recompute from the DB, no Garmin calls
    BODY_BACKFILL_DAYS=400 python scripts/sync_body_db.py   # force a deep backfill
"""
import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from utils import load_config

CHUNK_DAYS = 90          # Garmin rejects very long ranges on some endpoints
FIRST_BACKFILL_DAYS = 400
ROUTINE_DAYS = 45        # overlap on purpose: upserts are idempotent
KEEP_SUMMARIES = 30

BP_UPSERT = """
INSERT INTO bp_readings (garmin_id, measured_at, measured_local, local_date, systolic,
                         diastolic, pulse, source, notes, synced_at)
VALUES (%(garmin_id)s, %(measured_at)s, %(measured_local)s, %(local_date)s, %(systolic)s,
        %(diastolic)s, %(pulse)s, %(source)s, %(notes)s, now())
ON CONFLICT (garmin_id) DO UPDATE SET
    measured_at=EXCLUDED.measured_at, measured_local=EXCLUDED.measured_local,
    local_date=EXCLUDED.local_date, systolic=EXCLUDED.systolic, diastolic=EXCLUDED.diastolic,
    pulse=EXCLUDED.pulse, source=EXCLUDED.source, notes=EXCLUDED.notes, synced_at=now()
"""

WI_UPSERT = """
INSERT INTO weigh_ins (garmin_pk, measured_at, measured_local, local_date, weight_kg, source, synced_at)
VALUES (%(garmin_pk)s, %(measured_at)s, %(measured_local)s, %(local_date)s, %(weight_kg)s, %(source)s, now())
ON CONFLICT (garmin_pk) DO UPDATE SET
    measured_at=EXCLUDED.measured_at, measured_local=EXCLUDED.measured_local,
    local_date=EXCLUDED.local_date, weight_kg=EXCLUDED.weight_kg, source=EXCLUDED.source, synced_at=now()
"""


# ── parsing Garmin payloads (pure; covered by tests) ──────────────────────────
def _parse_garmin_dt(s: str) -> datetime:
    """'2025-03-10T22:15:30.0' -> naive datetime."""
    return datetime.fromisoformat(str(s).replace("Z", "")).replace(microsecond=0)


def parse_bp_payload(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for day in (payload or {}).get("measurementSummaries") or []:
        for m in day.get("measurements") or []:
            if m.get("systolic") is None or m.get("diastolic") is None:
                continue
            local_s, gmt_s = m.get("measurementTimestampLocal"), m.get("measurementTimestampGMT")
            if not local_s or not gmt_s:
                continue
            local, gmt = _parse_garmin_dt(local_s), _parse_garmin_dt(gmt_s)
            rows.append({
                "garmin_id": str(m.get("version") or f"{gmt_s}-{m['systolic']}-{m['diastolic']}"),
                "measured_at": gmt.replace(tzinfo=timezone.utc),
                "measured_local": local, "local_date": local.date(),
                "systolic": int(m["systolic"]), "diastolic": int(m["diastolic"]),
                "pulse": int(m["pulse"]) if m.get("pulse") else None,
                "source": m.get("sourceType"), "notes": m.get("notes") or None,
            })
    return rows


def parse_weight_payload(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for day in (payload or {}).get("dailyWeightSummaries") or []:
        for m in day.get("allWeightMetrics") or []:
            grams, gmt_ms, loc_ms = m.get("weight"), m.get("timestampGMT"), m.get("date")
            if not grams or gmt_ms is None or loc_ms is None:
                continue
            # "date" is local wall-clock time encoded as epoch-ms; read it as UTC to get the naive local time.
            local = datetime.fromtimestamp(loc_ms / 1000.0, tz=timezone.utc).replace(tzinfo=None, microsecond=0)
            rows.append({
                "garmin_pk": str(m.get("samplePk") or gmt_ms),
                "measured_at": datetime.fromtimestamp(gmt_ms / 1000.0, tz=timezone.utc).replace(microsecond=0),
                "measured_local": local, "local_date": local.date(),
                "weight_kg": round(float(grams) / 1000.0, 2), "source": m.get("sourceType"),
            })
    return rows


# ── DB ────────────────────────────────────────────────────────────────────────
def _scalar(conn, sql: str, params=()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    return row[0] if row else None


def get_height(conn) -> Optional[float]:
    env = os.environ.get("BODY_HEIGHT_CM", "").strip()
    if env:
        try:
            return float(env)
        except ValueError:
            pass
    v = _scalar(conn, "SELECT value FROM body_profile WHERE key='height_cm'")
    return float(v) if v else None


def set_height(conn, cm: float) -> None:
    if not 100 <= cm <= 250:
        raise ValueError("height must be between 100 and 250 cm")
    with conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO body_profile (key, value, updated_at) VALUES ('height_cm', %s, now()) "
            "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()", (str(cm),))


def _chunks(start: date, end: date):
    cur = start
    while cur <= end:
        stop = min(cur + timedelta(days=CHUNK_DAYS - 1), end)
        yield cur, stop
        cur = stop + timedelta(days=1)


def pull_garmin(conn, client, days: int) -> Dict[str, int]:
    today = date.today()
    start = today - timedelta(days=days)
    counts = {"bp": 0, "weigh_ins": 0}
    for a, b in _chunks(start, today):
        try:
            for row in parse_bp_payload(client.get_blood_pressure(a.isoformat(), b.isoformat())):
                with conn, conn.cursor() as cur:
                    cur.execute(BP_UPSERT, row)
                counts["bp"] += 1
            time.sleep(1)
        except Exception as exc:
            print(f"  blood pressure {a}..{b} failed: {exc}", file=sys.stderr)
        try:
            for row in parse_weight_payload(client.get_weigh_ins(a.isoformat(), b.isoformat())):
                with conn, conn.cursor() as cur:
                    cur.execute(WI_UPSERT, row)
                counts["weigh_ins"] += 1
            time.sleep(1)
        except Exception as exc:
            print(f"  weigh-ins {a}..{b} failed: {exc}", file=sys.stderr)
    return counts


def load_inputs(conn) -> Dict[str, Any]:
    """Read everything body_metrics needs back out of the DB."""
    with conn.cursor() as cur:
        cur.execute("SELECT measured_local, systolic, diastolic, pulse FROM bp_readings ORDER BY measured_local")
        readings = [{"local": r[0], "systolic": r[1], "diastolic": r[2], "pulse": r[3]} for r in cur.fetchall()]
        cur.execute("SELECT measured_local, weight_kg FROM weigh_ins ORDER BY measured_local")
        weigh_ins = [{"local": r[0], "kg": float(r[1])} for r in cur.fetchall()]

        def metric(name):
            cur.execute("SELECT date, value, extra FROM performance_metrics WHERE metric=%s ORDER BY date", (name,))
            return cur.fetchall()

        ctx: Dict[str, Any] = {
            "sleep": {r[0].isoformat(): {"hours": float(r[1]), "score": (r[2] or {}).get("score")}
                      for r in metric("sleep_hours") if r[1] is not None},
            "rhr": {r[0].isoformat(): float(r[1]) for r in metric("resting_hr") if r[1] is not None},
            "hrv": {r[0].isoformat(): float(r[1]) for r in metric("hrv") if r[1] is not None},
            "vo2max": [(r[0].isoformat(), float(r[1])) for r in metric("vo2max") if r[1] is not None],
        }
        cur.execute("SELECT date, SUM(trimp) FROM activity_derived WHERE trimp IS NOT NULL GROUP BY date")
        ctx["trimp"] = {r[0].isoformat(): float(r[1]) for r in cur.fetchall()}
        cur.execute("SELECT date, efficiency_index FROM running_economy WHERE efficiency_index IS NOT NULL ORDER BY date")
        ctx["efficiency"] = [(r[0].isoformat(), float(r[1])) for r in cur.fetchall()]
    return {"readings": readings, "weigh_ins": weigh_ins, "context": ctx}


def write_summary(conn, summary: Dict[str, Any]) -> None:
    with conn, conn.cursor() as cur:
        cur.execute("INSERT INTO body_summary (payload) VALUES (%s::jsonb)",
                    (json.dumps(summary, ensure_ascii=False, default=str),))
        cur.execute("DELETE FROM body_summary WHERE id NOT IN "
                    "(SELECT id FROM body_summary ORDER BY computed_at DESC LIMIT %s)", (KEEP_SUMMARIES,))


def run(conn, client, config: Dict[str, Any], force_days: int = 0) -> Dict[str, Any]:
    import body_metrics as bm

    result: Dict[str, Any] = {}
    if client is not None:
        have = _scalar(conn, "SELECT count(*) FROM bp_readings") or 0
        days = force_days or (ROUTINE_DAYS if have else FIRST_BACKFILL_DAYS)
        result["pulled"] = pull_garmin(conn, client, days)
    inputs = load_inputs(conn)
    height = get_height(conn)
    summary = bm.compute_body_summary(inputs["readings"], inputs["weigh_ins"], height,
                                      context=inputs["context"], cfg=config.get("body") or {})
    write_summary(conn, summary)
    result.update(readings=len(inputs["readings"]), weigh_ins=len(inputs["weigh_ins"]),
                  height_cm=height, insights=len(summary["insights"]))
    return result


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Sync Garmin BP + weight and compute the Body summary")
    ap.add_argument("--set-height", type=float, metavar="CM", help="store height in the DB and exit")
    ap.add_argument("--summary-only", action="store_true", help="recompute from the DB, no Garmin calls")
    args = ap.parse_args(argv)

    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        print("DATABASE_URL not set; skipping body sync.", file=sys.stderr)
        return 0

    try:
        from sync_performance_db import _connect, _ensure_schema
        config = load_config()
        conn = _connect(database_url)
    except Exception as exc:
        print(f"Warning: body sync could not start: {exc}", file=sys.stderr)
        return 0

    try:
        _ensure_schema(conn)
        if args.set_height is not None:
            set_height(conn, args.set_height)
            print(f"Height stored: {args.set_height:g} cm")
            return 0

        client = None
        if not args.summary_only and os.environ.get("SKIP_GARMIN_METRICS", "").strip() != "1":
            try:
                from sync_garmin import load_garmin_client
                client = load_garmin_client(config)
            except Exception as exc:
                print(f"  Garmin client unavailable ({exc}); using stored data only.", file=sys.stderr)

        force = int(os.environ.get("BODY_BACKFILL_DAYS", "0") or 0)
        print("Body sync: " + json.dumps(run(conn, client, config, force), default=str))
    except Exception as exc:
        print(f"Warning: body sync failed (non-fatal): {exc}", file=sys.stderr)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
