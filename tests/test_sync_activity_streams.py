import os
import sys
import types
import unittest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS_DIR = os.path.join(ROOT_DIR, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

yaml_stub = types.ModuleType("yaml")
yaml_stub.safe_load = lambda *_a, **_k: {}
sys.modules.setdefault("yaml", yaml_stub)

import sync_activity_streams as sas  # noqa: E402


def payload(keys, rows):
    return {
        "metricDescriptors": [{"metricsIndex": i, "key": k} for i, k in enumerate(keys)],
        "activityDetailMetrics": [{"metrics": r} for r in rows],
    }


class SnapFactorTests(unittest.TestCase):
    def test_identity_and_unit_conversions(self):
        self.assertEqual(sas.snap_factor(145, 146), 1.0)
        self.assertEqual(sas.snap_factor(0.85, 85), 0.01)   # cm -> m
        self.assertEqual(sas.snap_factor(146, 73), 2.0)     # per-leg cadence
        self.assertEqual(sas.snap_factor(83, 8.3), 10.0)    # mm vs cm

    def test_implausible_ratio_is_left_alone(self):
        self.assertEqual(sas.snap_factor(100, 37), 1.0)
        self.assertEqual(sas.snap_factor(None, 10), 1.0)
        self.assertEqual(sas.snap_factor(10, 0), 1.0)


class BuildStreamsTests(unittest.TestCase):
    def test_columns_are_resolved_by_descriptor_key_not_position(self):
        d = payload(
            ["directHeartRate", "sumElapsedDuration", "sumDistance", "directSpeed", "directLatitude"],
            [[100, 0, 0, 2.0, 1.1], [110, 1, 2, 2.0, 1.2], [120, 2, 4, 2.0, 1.3]],
        )
        out = sas.build_streams(d, {"distance": 4.0})
        self.assertEqual(out["n"], 3)
        self.assertEqual(out["t"], [0.0, 1.0, 2.0])
        self.assertEqual(out["d"], [0.0, 2.0, 4.0])
        self.assertEqual(out["m"]["hr"], [100, 110, 120])
        self.assertEqual(out["m"]["speed"], [2.0, 2.0, 2.0])
        self.assertNotIn("directLatitude", out["m"])
        self.assertFalse(any("lat" in k.lower() for k in out["m"]))

    def test_stride_is_rescaled_to_match_summary(self):
        rows = [[0, 85], [1, 86], [2, 84]]
        out = sas.build_streams(payload(["sumDuration", "directStrideLength"], rows), {"strideLength": 0.85})
        self.assertEqual(out["m"]["stride"], [0.85, 0.86, 0.84])
        out2 = sas.build_streams(payload(["sumDuration", "directStrideLength"], rows), {"strideLength": 85})
        self.assertEqual(out2["m"]["stride"], [85.0, 86.0, 84.0])

    def test_unknown_metric_is_kept_under_a_readable_name(self):
        rows = [[0, 100], [1, 99], [2, 98], [3, 97]]
        out = sas.build_streams(payload(["sumDuration", "directPotentialStamina"], rows))
        self.assertIn("potentialStamina", out["m"])

    def test_time_is_made_monotonic_and_downsampled(self):
        rows = [[i, 100 + i % 5] for i in range(5000)]
        out = sas.build_streams(payload(["sumDuration", "directHeartRate"], rows), max_points=1000)
        self.assertLessEqual(out["n"], 1001)
        self.assertEqual(len(out["t"]), out["n"])
        self.assertEqual(out["t"], sorted(out["t"]))
        self.assertEqual(out["t"][-1], 4999.0)

    def test_empty_or_unusable_payloads(self):
        self.assertEqual(sas.build_streams({}), {})
        self.assertEqual(sas.build_streams(payload(["directHeartRate"], [])), {})
        self.assertEqual(sas.build_streams(payload(["sumDuration"], [[0]])), {})


class SummaryTests(unittest.TestCase):
    def test_gps_and_non_scalars_are_dropped(self):
        s = sas.clean_summary({"summaryDTO": {
            "distance": 6410.0, "startLatitude": 1.2, "endLongitude": 3.4,
            "weird": {"a": 1}, "trainingEffectLabel": "BASE", "avgPower": 236,
        }})
        self.assertEqual(s, {"distance": 6410.0, "trainingEffectLabel": "BASE", "avgPower": 236})

    def test_power_zones_use_watts(self):
        z = sas.parse_power_zones([{"zoneNumber": 1, "secsInZone": 401, "zoneLowBoundary": 189}])
        self.assertEqual(z, [{"zone": 1, "seconds": 401, "low_w": 189}])


if __name__ == "__main__":
    unittest.main()
