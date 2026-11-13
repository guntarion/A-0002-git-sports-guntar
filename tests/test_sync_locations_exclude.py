import os
import sys
import types
import unittest
from unittest import mock

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS_DIR = os.path.join(ROOT_DIR, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

yaml_stub = types.ModuleType("yaml")
yaml_stub.safe_load = lambda *_a, **_k: {}
sys.modules.setdefault("yaml", yaml_stub)

import sync_locations as sl  # noqa: E402


def activity(aid):
    return {"activityId": aid, "activityType": {"typeKey": "walking"}, "startTimeLocal": "2025-03-10 10:00:00",
            "startLatitude": 1.0, "startLongitude": 1.0, "endLatitude": 1.0, "endLongitude": 1.0,
            "activityName": f"act {aid}", "locationName": "X", "distance": 1000.0}


class FakeClient:
    def get_activities(self, start, limit):
        return [activity(1), activity(2), activity(3)] if start == 0 else []


class ExcludeTests(unittest.TestCase):
    def test_excluded_ids_reads_config_as_strings(self):
        cfg = {"locations": {"exclude_activity_ids": [23862443418, " 7 ", ""]}}
        self.assertEqual(sl.excluded_ids(cfg), frozenset({"23862443418", "7"}))
        self.assertEqual(sl.excluded_ids({}), frozenset())
        self.assertEqual(sl.excluded_ids({"locations": None}), frozenset())

    def test_excluded_activities_are_never_upserted(self):
        conn = mock.MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        stats = sl.fetch_coordinates(conn, FakeClient(), exclude=frozenset({"2"}))
        self.assertEqual(stats, {"seen": 3, "written": 2, "excluded": 1})
        written_ids = [c.args[1]["activity_id"] for c in cur.execute.call_args_list]
        self.assertEqual(written_ids, ["1", "3"])

    def test_without_exclusions_everything_is_written(self):
        conn = mock.MagicMock()
        self.assertEqual(sl.fetch_coordinates(conn, FakeClient())["written"], 3)

    def test_routes_phase_skips_excluded_rows_still_in_the_table(self):
        conn = mock.MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = [("1",), ("2",)]
        client = mock.MagicMock()
        out = sl.fetch_routes(conn, client, exclude=frozenset({"1", "2"}))
        self.assertEqual(out, {"todo": 0, "written": 0, "skipped": 0})
        client.get_activity_details.assert_not_called()


if __name__ == "__main__":
    unittest.main()
