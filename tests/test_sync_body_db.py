import os
import sys
import types
import unittest
from datetime import date, datetime, timezone

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS_DIR = os.path.join(ROOT_DIR, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

yaml_stub = types.ModuleType("yaml")
yaml_stub.safe_load = lambda *_a, **_k: {}
sys.modules.setdefault("yaml", yaml_stub)

import sync_body_db as sb  # noqa: E402

# Synthetic payloads in the shape of the live Garmin API (get_blood_pressure / get_weigh_ins).
# Values are made up on purpose: this repo is public.
BP_PAYLOAD = {
    "measurementSummaries": [{
        "startDate": "2025-03-10", "measurements": [
            {"version": 1700000000001, "systolic": 130, "diastolic": 82, "pulse": None,
             "notes": "test reading", "sourceType": "MANUAL",
             "measurementTimestampLocal": "2025-03-10T22:15:30.0",
             "measurementTimestampGMT": "2025-03-10T15:15:30.0"},
            {"version": 2, "systolic": 125, "diastolic": 80, "pulse": 66, "sourceType": "MANUAL",
             "measurementTimestampLocal": "2025-03-10T03:10:20.0",
             "measurementTimestampGMT": "2025-03-09T20:10:20.0"},
            {"version": 3, "systolic": None, "diastolic": 90,
             "measurementTimestampLocal": "2025-03-10T03:20:00.0",
             "measurementTimestampGMT": "2025-03-09T20:20:00.0"},
        ]}]
}

WEIGHT_PAYLOAD = {
    "dailyWeightSummaries": [{
        "summaryDate": "2025-03-10", "allWeightMetrics": [
            {"samplePk": 1700000000002, "date": 1741593600000, "calendarDate": "2025-03-10",
             "weight": 75000.0, "bmi": 25.95, "sourceType": "MANUAL", "timestampGMT": 1741568400000},
            {"samplePk": 5, "date": None, "weight": 80000.0, "timestampGMT": 1},
            {"samplePk": 6, "date": 1741593600000, "weight": None, "timestampGMT": 1741568400000},
        ]}]
}


class ParseBpTests(unittest.TestCase):
    def test_parses_valid_rows_and_skips_incomplete(self):
        rows = sb.parse_bp_payload(BP_PAYLOAD)
        self.assertEqual(len(rows), 2)
        a = rows[0]
        self.assertEqual(a["garmin_id"], "1700000000001")
        self.assertEqual(a["measured_local"], datetime(2025, 3, 10, 22, 15, 30))
        self.assertEqual(a["measured_at"], datetime(2025, 3, 10, 15, 15, 30, tzinfo=timezone.utc))
        self.assertEqual(a["local_date"], date(2025, 3, 10))
        self.assertIsNone(a["pulse"])
        self.assertEqual(rows[1]["pulse"], 66)

    def test_empty_and_missing_payloads(self):
        self.assertEqual(sb.parse_bp_payload({}), [])
        self.assertEqual(sb.parse_bp_payload(None), [])


class ParseWeightTests(unittest.TestCase):
    def test_local_time_is_wall_clock_and_grams_become_kg(self):
        rows = sb.parse_weight_payload(WEIGHT_PAYLOAD)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["weight_kg"], 75.0)
        self.assertEqual(r["measured_local"], datetime(2025, 3, 10, 8, 0))   # wall-clock time as shown in Garmin
        self.assertEqual(r["measured_at"], datetime(2025, 3, 10, 1, 0, tzinfo=timezone.utc))
        self.assertEqual(r["local_date"], date(2025, 3, 10))

    def test_local_minus_gmt_is_the_utc_offset(self):
        r = sb.parse_weight_payload(WEIGHT_PAYLOAD)[0]
        offset = r["measured_local"] - r["measured_at"].replace(tzinfo=None)
        self.assertEqual(offset.total_seconds(), 7 * 3600)


class ChunkTests(unittest.TestCase):
    def test_chunks_cover_range_without_overlap(self):
        spans = list(sb._chunks(date(2026, 1, 1), date(2026, 11, 13)))
        self.assertEqual(spans[0][0], date(2026, 1, 1))
        self.assertEqual(spans[-1][1], date(2026, 11, 13))
        for (a, b), (c, d) in zip(spans, spans[1:]):
            self.assertEqual((c - b).days, 1)
        self.assertTrue(all((b - a).days < sb.CHUNK_DAYS for a, b in spans))


if __name__ == "__main__":
    unittest.main()
