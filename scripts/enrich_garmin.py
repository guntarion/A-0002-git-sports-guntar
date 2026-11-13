"""
Enrich Garmin activities with per-activity detailed data:
- Heart rate zones from get_activity_hr_in_timezones()
- Split/lap data per km from get_activity_splits()
- Additional detail from get_activity()

- Execution Score (Garmin `directWorkoutComplianceScore`) for structured/coach workouts

Persists enriched data to activities/enriched/garmin/{id}.json
Tracks state in data/enriched_state_garmin.json to avoid re-fetching.
"""
import os
import sys
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from sync_garmin import load_garmin_client
from utils import ensure_dir, load_config, raw_activity_dir, read_json, utc_now, write_json

ENRICHED_DIR = "activities/enriched/garmin"
STATE_PATH = "data/enriched_state_garmin.json"
EXEC_STATE_KEY = "execution_checked_ids"
# Garmin scores a workout some time after upload; do not give up on a fresh activity.
EXEC_RECENT_DAYS = 3


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _load_state() -> Dict[str, Any]:
    if not __import__("os").path.exists(STATE_PATH):
        return {}
    try:
        payload = read_json(STATE_PATH)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _save_state(state: Dict[str, Any]) -> None:
    ensure_dir("data")
    write_json(STATE_PATH, state)


def _fetch_detail(client: Any, activity_id: str) -> Optional[Dict]:
    for method_name in ("get_activity", "getActivity"):
        method = getattr(client, method_name, None)
        if not callable(method):
            continue
        try:
            result = method(activity_id)
            if isinstance(result, dict):
                return result
        except Exception:
            continue
    return None


def _fetch_splits(client: Any, activity_id: str) -> Optional[List[Dict]]:
    for method_name in ("get_activity_splits", "getActivitySplits"):
        method = getattr(client, method_name, None)
        if not callable(method):
            continue
        try:
            result = method(activity_id)
            if isinstance(result, list):
                return result
            if isinstance(result, dict):
                laps = result.get("lapDTOs") or result.get("laps") or result.get("splits")
                if isinstance(laps, list):
                    return laps
        except Exception:
            continue
    return None


def _fetch_hr_zones(client: Any, activity_id: str) -> Optional[List[Dict]]:
    for method_name in ("get_activity_hr_in_timezones", "getActivityHrInTimezones"):
        method = getattr(client, method_name, None)
        if not callable(method):
            continue
        try:
            result = method(activity_id)
            if isinstance(result, list):
                return result
            if isinstance(result, dict):
                zones = result.get("timeInZone") or result.get("zones")
                if isinstance(zones, list):
                    return zones
        except Exception:
            continue
    return None


def _parse_splits(raw_splits: List[Dict]) -> List[Dict]:
    splits = []
    for lap in raw_splits:
        if not isinstance(lap, dict):
            continue
        distance = _safe_float(
            lap.get("distance") or lap.get("totalDistance") or lap.get("lapDistance")
        )
        if distance <= 0:
            continue
        duration = _safe_float(
            lap.get("movingDuration")
            or lap.get("duration")
            or lap.get("elapsedDuration")
            or lap.get("lapTime")
        )
        split: Dict[str, Any] = {
            "distance": round(distance, 1),
            "duration": round(duration, 1),
        }
        speed = _safe_float(lap.get("averageSpeed") or lap.get("avgSpeed"))
        if speed > 0:
            split["avg_speed_mps"] = round(speed, 3)
        avg_hr = _safe_int(lap.get("averageHR") or lap.get("avgHR"))
        if avg_hr and avg_hr > 0:
            split["avg_hr"] = avg_hr
        max_hr = _safe_int(lap.get("maxHR") or lap.get("maxHeartRate"))
        if max_hr and max_hr > 0:
            split["max_hr"] = max_hr
        elev_gain = _safe_float(lap.get("elevationGain") or lap.get("totalAscent"))
        if elev_gain != 0:
            split["elevation_gain"] = round(elev_gain, 1)
        splits.append(split)
    return splits


def _parse_hr_zones(raw_zones: List[Dict]) -> List[Dict]:
    zones = []
    for zone in raw_zones:
        if not isinstance(zone, dict):
            continue
        zone_num = _safe_int(
            zone.get("zoneNumber") or zone.get("zone") or zone.get("hrZoneNumber")
        )
        secs = _safe_float(
            zone.get("secsInZone") or zone.get("secondsInZone") or zone.get("timeInZone")
        )
        low_bound = _safe_int(
            zone.get("zoneLowBoundary") or zone.get("lowBoundary") or zone.get("minHr")
        )
        high_bound = _safe_int(
            zone.get("zoneHighBoundary") or zone.get("highBoundary") or zone.get("maxHr")
        )
        if zone_num is None:
            continue
        entry: Dict[str, Any] = {"zone": zone_num, "seconds": int(round(secs))}
        if low_bound is not None:
            entry["low_bpm"] = low_bound
        if high_bound is not None:
            entry["high_bpm"] = high_bound
        zones.append(entry)
    return sorted(zones, key=lambda z: z["zone"])


def execution_score_from_detail(detail: Optional[Dict[str, Any]]) -> Optional[int]:
    """Garmin Coach / structured-workout Execution Score = summaryDTO.directWorkoutComplianceScore."""
    value = ((detail or {}).get("summaryDTO") or {}).get("directWorkoutComplianceScore")
    if value is None:
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _execution_candidates(raw_dir: str, checked: set) -> List[tuple]:
    """Raw activities that came from a structured workout (have a workoutId) and
    have not been checked yet. Only those can carry an execution score."""
    out: List[tuple] = []
    if not os.path.exists(raw_dir):
        return out
    for filename in sorted(os.listdir(raw_dir)):
        if not filename.endswith(".json"):
            continue
        activity_id = filename[:-5]
        if activity_id in checked:
            continue
        try:
            raw = read_json(os.path.join(raw_dir, filename))
        except Exception:
            continue
        if isinstance(raw, dict) and raw.get("workoutId"):
            out.append((activity_id, raw))
    return out


def _is_recent(raw: Dict[str, Any], today: Optional[date] = None) -> bool:
    today = today or date.today()
    try:
        day = datetime.fromisoformat(str(raw.get("startTimeLocal") or "")[:10]).date()
    except ValueError:
        return False
    return (today - day) <= timedelta(days=EXEC_RECENT_DAYS)


def _enrich_execution(client: Any, candidates: List[tuple], checked: set,
                      today: Optional[date] = None) -> Dict[str, int]:
    """One get_activity() per structured workout; merges `execution_score` into the
    enriched file. Resumable: an id is marked checked only once the answer is final."""
    found = 0
    for activity_id, raw in candidates:
        detail = _fetch_detail(client, activity_id)
        if detail is None:
            continue  # transient failure: try again next run
        score = execution_score_from_detail(detail)
        if score is None and _is_recent(raw, today):
            continue  # Garmin may not have scored it yet
        if score is not None:
            path = f"{ENRICHED_DIR}/{activity_id}.json"
            data: Dict[str, Any] = {"id": activity_id}
            if os.path.exists(path):
                try:
                    existing = read_json(path)
                    if isinstance(existing, dict):
                        data = existing
                except Exception:
                    pass
            data["execution_score"] = score
            write_json(path, data)
            found += 1
        checked.add(activity_id)
        time.sleep(0.3)
    return {"checked": len(candidates), "scored": found}


def enrich_garmin(max_per_run: int = 500, max_execution: int = 80) -> Dict[str, Any]:
    """Enrich Garmin activities with per-activity detailed data."""
    config = load_config()
    raw_dir = raw_activity_dir("garmin")
    ensure_dir(ENRICHED_DIR)

    state = _load_state()
    enriched_ids: set = set(state.get("enriched_ids", []))
    checked: set = set(state.get(EXEC_STATE_KEY, []))

    # Find activities needing enrichment
    to_enrich = []
    if os.path.exists(raw_dir):
        for filename in sorted(os.listdir(raw_dir)):
            if not filename.endswith(".json"):
                continue
            activity_id = filename[:-5]
            if activity_id not in enriched_ids:
                to_enrich.append(activity_id)
    to_exec = _execution_candidates(raw_dir, checked)[:max_execution]

    if not to_enrich and not to_exec:
        print(f"All {len(enriched_ids)} activities already enriched.")
        return {
            "enriched": 0,
            "errors": 0,
            "total_enriched": len(enriched_ids),
        }

    client = load_garmin_client(config)
    enriched_count = 0
    error_count = 0

    if to_enrich:
        print(f"Enriching {min(len(to_enrich), max_per_run)} activities (max {max_per_run})...")
        to_enrich = to_enrich[:max_per_run]

    for activity_id in to_enrich:
        try:
            enriched: Dict[str, Any] = {"id": activity_id}

            # Fetch HR zones
            raw_zones = _fetch_hr_zones(client, activity_id)
            if raw_zones:
                zones = _parse_hr_zones(raw_zones)
                if zones:
                    enriched["hr_zones"] = zones

            time.sleep(0.3)

            # Fetch splits
            raw_splits = _fetch_splits(client, activity_id)
            if raw_splits:
                splits = _parse_splits(raw_splits)
                if splits:
                    enriched["splits"] = splits

            time.sleep(0.3)

            enriched_path = f"{ENRICHED_DIR}/{activity_id}.json"
            write_json(enriched_path, enriched)
            enriched_ids.add(activity_id)
            enriched_count += 1
            print(f"  Enriched {activity_id} (hr_zones={bool(enriched.get('hr_zones'))}, splits={bool(enriched.get('splits'))})")

        except Exception as exc:
            print(f"  Error enriching {activity_id}: {exc}", file=sys.stderr)
            error_count += 1

    # Execution scores run AFTER the loop above, which rewrites each enriched file from scratch.
    exec_stats: Dict[str, int] = {}
    if to_exec:
        try:
            exec_stats = _enrich_execution(client, to_exec, checked)
            print(f"  Execution scores: {exec_stats}")
        except Exception as exc:
            print(f"  Execution score step failed (non-fatal): {exc}", file=sys.stderr)

    _save_state({
        "enriched_ids": sorted(enriched_ids),
        EXEC_STATE_KEY: sorted(checked),
        "last_run_utc": utc_now().isoformat(),
    })

    return {
        "enriched": enriched_count,
        "errors": error_count,
        "total_enriched": len(enriched_ids),
        "execution": exec_stats,
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Enrich Garmin activities with per-activity detailed data")
    parser.add_argument("--max-per-run", type=int, default=500)
    args = parser.parse_args()

    summary = enrich_garmin(max_per_run=args.max_per_run)
    print(f"Enrichment complete: {summary}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
