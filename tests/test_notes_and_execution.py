import json
import os
import sys
import tempfile
import types
import unittest
from datetime import date
from unittest import mock

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS_DIR = os.path.join(ROOT_DIR, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

yaml_stub = types.ModuleType("yaml")
yaml_stub.safe_load = lambda *_a, **_k: {}
sys.modules.setdefault("yaml", yaml_stub)

import enrich_garmin as eg  # noqa: E402
import generate_activities as ga  # noqa: E402
import sync_garmin as sg  # noqa: E402
import utils  # noqa: E402


def _dump(path, obj):
    with open(path, "w") as fh:
        json.dump(obj, fh)


def _load(path):
    with open(path) as fh:
        return json.load(fh)


class NoteTests(unittest.TestCase):
    def test_clean_note(self):
        self.assertEqual(sg._clean_note("  halo\r\ndunia  "), "halo\ndunia")
        self.assertEqual(sg._clean_note(None), "")
        self.assertEqual(sg._clean_note(123), "")
        self.assertEqual(len(sg._clean_note("x" * 5000)), sg.NOTE_MAX_CHARS)

    def test_switch_defaults_on_and_can_be_turned_off(self):
        self.assertTrue(utils.notes_enabled({}))
        self.assertTrue(utils.notes_enabled({"activities": {"types": []}}))
        self.assertFalse(utils.notes_enabled({"activities": {"include_notes": False}}))

    def test_normalize_includes_note_only_when_enabled(self):
        raw = {"activityId": 1, "startTimeLocal": "2026-01-02 06:00:00", "activityType": {"typeKey": "running"},
               "distance": 5000.0, "duration": 1800.0, "description": "  catatan saya  "}
        with mock.patch.object(sg, "_notes_on", return_value=True):
            self.assertEqual(sg._normalize_activity(raw).get("note"), "catatan saya")
        with mock.patch.object(sg, "_notes_on", return_value=False):
            self.assertNotIn("note", sg._normalize_activity(raw))
        with mock.patch.object(sg, "_notes_on", return_value=True):
            self.assertNotIn("note", sg._normalize_activity({**raw, "description": None}))

    def test_export_honours_switch_even_for_stored_notes(self):
        normalized = {"id": "1", "date": "2026-01-02", "type": "running", "distance": 5000, "moving_time": 1800,
                      "note": "tersimpan dari sebelumnya"}
        self.assertEqual(ga._build_entry(normalized, None, True)["note"], "tersimpan dari sebelumnya")
        self.assertNotIn("note", ga._build_entry(normalized, None, False))

    def test_export_passes_execution_score_through(self):
        normalized = {"id": "1", "date": "2026-01-02", "type": "running", "distance": 5000, "moving_time": 1800}
        self.assertEqual(ga._build_entry(normalized, {"execution_score": 55})["execution_score"], 55)
        self.assertNotIn("execution_score", ga._build_entry(normalized, {"hr_zones": []}))
        self.assertEqual(ga._build_entry(normalized, {"execution_score": 0})["execution_score"], 0)


class ExecutionScoreTests(unittest.TestCase):
    def test_score_from_detail(self):
        f = eg.execution_score_from_detail
        self.assertEqual(f({"summaryDTO": {"directWorkoutComplianceScore": 55}}), 55)
        self.assertEqual(f({"summaryDTO": {"directWorkoutComplianceScore": 87.6}}), 88)
        self.assertEqual(f({"summaryDTO": {"directWorkoutComplianceScore": 0}}), 0)
        self.assertIsNone(f({"summaryDTO": {}}))
        self.assertIsNone(f({}))
        self.assertIsNone(f(None))
        self.assertIsNone(f({"summaryDTO": {"directWorkoutComplianceScore": "n/a"}}))

    def test_candidates_are_structured_workouts_not_yet_checked(self):
        with tempfile.TemporaryDirectory() as d:
            for aid, wid in (("1", 111), ("2", None), ("3", 333), ("4", 444)):
                _dump(os.path.join(d, f"{aid}.json"), {"activityId": int(aid), "workoutId": wid})
            with open(os.path.join(d, "bad.json"), "w") as fh:
                fh.write("{not json")
            got = [a for a, _ in eg._execution_candidates(d, {"4"})]
            self.assertEqual(got, ["1", "3"])
            self.assertEqual(eg._execution_candidates(os.path.join(d, "missing"), set()), [])

    def test_recent_helper(self):
        today = date(2026, 11, 13)
        self.assertTrue(eg._is_recent({"startTimeLocal": "2026-11-12 06:00:00"}, today))
        self.assertTrue(eg._is_recent({"startTimeLocal": "2026-11-10 06:00:00"}, today))
        self.assertFalse(eg._is_recent({"startTimeLocal": "2026-11-09 06:00:00"}, today))
        self.assertFalse(eg._is_recent({}, today))

    def run_enrich(self, details, raws, preexisting=None):
        class Client:
            def get_activity(self_, aid):
                return details.get(aid)

        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(eg, "ENRICHED_DIR", d), mock.patch.object(eg.time, "sleep"):
                if preexisting:
                    for aid, data in preexisting.items():
                        _dump(os.path.join(d, f"{aid}.json"), data)
                checked: set = set()
                stats = eg._enrich_execution(Client(), raws, checked, today=date(2026, 11, 13))
                files = {f[:-5]: _load(os.path.join(d, f)) for f in os.listdir(d)}
            return stats, checked, files

    def test_score_is_merged_into_existing_enriched_file(self):
        stats, checked, files = self.run_enrich(
            {"1": {"summaryDTO": {"directWorkoutComplianceScore": 55}}},
            [("1", {"startTimeLocal": "2026-10-01 06:00:00"})],
            preexisting={"1": {"id": "1", "hr_zones": [{"zone": 1}]}})
        self.assertEqual(stats, {"checked": 1, "scored": 1})
        self.assertEqual(files["1"]["execution_score"], 55)
        self.assertEqual(files["1"]["hr_zones"], [{"zone": 1}])     # existing data preserved
        self.assertEqual(checked, {"1"})

    def test_old_unscored_workout_is_marked_final(self):
        _, checked, files = self.run_enrich({"1": {"summaryDTO": {}}}, [("1", {"startTimeLocal": "2026-10-01 06:00:00"})])
        self.assertEqual(checked, {"1"})
        self.assertEqual(files, {})

    def test_recent_unscored_workout_is_retried_later(self):
        _, checked, _ = self.run_enrich({"1": {"summaryDTO": {}}}, [("1", {"startTimeLocal": "2026-11-12 06:00:00"})])
        self.assertEqual(checked, set())

    def test_fetch_failure_is_not_final(self):
        stats, checked, _ = self.run_enrich({}, [("1", {"startTimeLocal": "2026-10-01 06:00:00"})])
        self.assertEqual(checked, set())
        self.assertEqual(stats["scored"], 0)


if __name__ == "__main__":
    unittest.main()
