"""
Generate site/activities.json with per-activity detailed stats for the activities page.
Combines normalized activity data with enriched per-activity data (splits, HR zones).
"""
import os
from typing import Any, Dict, List, Optional

from utils import ensure_dir, load_config, normalize_source, notes_enabled, read_json, utc_now, write_json

NORMALIZED_PATH = os.path.join("data", "activities_normalized.json")
ENRICHED_DIR = os.path.join("activities", "enriched", "garmin")
OUT_PATH = os.path.join("site", "activities.json")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _pace_secs_per_km(speed_mps: float) -> Optional[int]:
    """Convert speed in m/s to pace in seconds per km."""
    if speed_mps <= 0:
        return None
    return int(round(1000.0 / speed_mps))


def _load_enriched(activity_id: str) -> Optional[Dict]:
    path = os.path.join(ENRICHED_DIR, f"{activity_id}.json")
    if not os.path.exists(path):
        return None
    try:
        return read_json(path)
    except Exception:
        return None


def _build_entry(normalized: Dict, enriched: Optional[Dict], include_notes: bool = True) -> Dict:
    distance = _safe_float(normalized.get("distance"))
    moving_time = _safe_float(normalized.get("moving_time"))

    entry: Dict[str, Any] = {
        "id": normalized.get("id"),
        "date": normalized.get("date"),
        "start_date_local": normalized.get("start_date_local"),
        "type": normalized.get("type"),
        "raw_type": normalized.get("raw_type") or normalized.get("type"),
        "distance": round(distance, 1),
        "moving_time": round(moving_time, 1),
        "elevation_gain": round(_safe_float(normalized.get("elevation_gain")), 1),
    }

    if normalized.get("name"):
        entry["name"] = normalized["name"]

    # Pass-through extended stats
    for field in (
        "avg_hr", "max_hr", "calories",
        "aerobic_te", "anaerobic_te",
        "avg_cadence", "tss", "vo2_max",
        "avg_vertical_osc", "avg_ground_contact", "avg_stride_len",
    ):
        val = normalized.get(field)
        if val is not None:
            entry[field] = val

    # Session note typed in Garmin Connect. Public site: honour the config switch even for
    # notes already stored in earlier normalized data.
    if normalized.get("note") and include_notes:
        entry["note"] = normalized["note"]

    # Compute average pace
    avg_speed = _safe_float(normalized.get("avg_speed_mps"))
    if avg_speed > 0:
        pace = _pace_secs_per_km(avg_speed)
        if pace:
            entry["avg_pace_secs_per_km"] = pace
    elif distance > 0 and moving_time > 0:
        pace = _pace_secs_per_km(distance / moving_time)
        if pace:
            entry["avg_pace_secs_per_km"] = pace

    # Add enriched data
    if enriched:
        if enriched.get("execution_score") is not None:
            entry["execution_score"] = enriched["execution_score"]
        if "hr_zones" in enriched:
            entry["hr_zones"] = enriched["hr_zones"]
        if "splits" in enriched:
            splits = enriched["splits"]
            # Add pace to each split
            for split in splits:
                speed = _safe_float(split.get("avg_speed_mps"))
                if speed > 0:
                    pace = _pace_secs_per_km(speed)
                    if pace:
                        split["pace_secs_per_km"] = pace
                elif (
                    _safe_float(split.get("distance")) > 0
                    and _safe_float(split.get("duration")) > 0
                ):
                    computed_speed = _safe_float(split["distance"]) / _safe_float(split["duration"])
                    pace = _pace_secs_per_km(computed_speed)
                    if pace:
                        split["pace_secs_per_km"] = pace
            entry["splits"] = splits

    return entry


def generate_activities() -> None:
    config = load_config()
    source = normalize_source(config.get("source", "strava"))

    if not os.path.exists(NORMALIZED_PATH):
        print("No normalized activities; writing empty activities.json.")
        ensure_dir("site")
        write_json(OUT_PATH, {
            "generated_at": utc_now().isoformat(),
            "source": source,
            "units": config.get("units", {"distance": "km", "elevation": "m"}),
            "activities": [],
        })
        return

    normalized_list: List[Dict] = read_json(NORMALIZED_PATH) or []

    activities = []
    for normalized in normalized_list:
        if not isinstance(normalized, dict):
            continue
        activity_id = str(normalized.get("id") or "").strip()
        if not activity_id:
            continue
        enriched = _load_enriched(activity_id) if source == "garmin" else None
        entry = _build_entry(normalized, enriched, notes_enabled(config))
        activities.append(entry)

    # Sort most recent first
    activities.sort(key=lambda x: (x.get("date", ""), x.get("id", "")), reverse=True)

    units = config.get("units", {}) or {}
    payload = {
        "generated_at": utc_now().isoformat(),
        "source": source,
        "units": {
            "distance": units.get("distance", "km"),
            "elevation": units.get("elevation", "m"),
        },
        "activities": activities,
    }

    ensure_dir("site")
    write_json(OUT_PATH, payload)
    print(f"Generated {OUT_PATH} with {len(activities)} activities")


def main() -> int:
    generate_activities()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
