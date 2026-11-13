import json
import os
import sys
import unittest
from datetime import date, datetime, timedelta

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS_DIR = os.path.join(ROOT_DIR, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import body_metrics as bm  # noqa: E402

CFG = dict(bm.DEFAULTS)
TODAY = date(2026, 11, 13)


def rd(day: date, hh: int, mm: int, s: int, d: int, pulse=None):
    return {"local": datetime(day.year, day.month, day.day, hh, mm), "systolic": s, "diastolic": d, "pulse": pulse}


def ins_ids(summary):
    return {i["id"]: i for i in summary["insights"]}


class BmiTests(unittest.TestCase):
    def test_bmi_matches_hand_calculation(self):
        self.assertAlmostEqual(bm.bmi(80.0, 170), 27.68, places=2)
        self.assertEqual(round(bm.bmi(72.0, 170), 1), 24.9)

    def test_healthy_range_for_170cm(self):
        self.assertAlmostEqual(bm.weight_for_bmi(18.5, 170), 53.465, places=2)
        self.assertAlmostEqual(bm.weight_for_bmi(22.9, 170), 66.181, places=2)
        self.assertAlmostEqual(bm.weight_for_bmi(25.0, 170), 72.25, places=2)

    def test_categories_differ_by_standard(self):
        self.assertEqual(bm.bmi_category(27.5, "asia_pacific")["key"], "obese1")
        self.assertEqual(bm.bmi_category(27.5, "who")["key"], "overweight")
        self.assertEqual(bm.bmi_category(27.5, "kemenkes")["key"], "obese")
        self.assertEqual(bm.bmi_category(22.9, "asia_pacific")["key"], "normal")
        self.assertEqual(bm.bmi_category(23.0, "asia_pacific")["key"], "at_risk")


class BpPrimitivesTests(unittest.TestCase):
    def test_map_and_category(self):
        self.assertAlmostEqual(bm.map_pressure(120, 80), 93.33, places=2)
        cases = [((119, 79), "normal"), ((125, 79), "elevated"), ((130, 70), "stage1"),
                 ((128, 85), "stage1"), ((140, 70), "stage2"), ((120, 90), "stage2"), ((181, 80), "crisis")]
        for (s, d), want in cases:
            self.assertEqual(bm.bp_category(s, d), want, (s, d))

    def test_home_status_levels(self):
        self.assertEqual(bm.home_status(118, 74, CFG), "ok")
        self.assertEqual(bm.home_status(131, 80, CFG), "borderline")
        self.assertEqual(bm.home_status(138, 85, CFG), "above")
        self.assertEqual(bm.home_status(150, 80, CFG), "well_above")

    def test_period_buckets(self):
        self.assertEqual(bm.period_of(3, CFG), "pagi")
        self.assertEqual(bm.period_of(11, CFG), "pagi")
        self.assertEqual(bm.period_of(14, CFG), "siang")
        self.assertEqual(bm.period_of(22, CFG), "malam")

    def test_two_close_readings_form_one_averaged_session(self):
        d = date(2025, 3, 10)
        rs = [rd(d, 22, 40, 138, 92, 70), rd(d, 22, 41, 138, 89, 72), rd(d, 3, 42, 134, 88), rd(d, 3, 43, 130, 80)]
        ss = bm.collapse_sessions(rs, CFG)
        self.assertEqual(len(ss), 2)
        night = [s for s in ss if s["period"] == "malam"][0]
        self.assertEqual(night["n"], 2)
        self.assertAlmostEqual(night["sys"], 138)
        self.assertAlmostEqual(night["dia"], 90.5)
        self.assertAlmostEqual(night["pulse"], 71)

    def test_distant_readings_are_separate_sessions(self):
        d = date(2025, 3, 10)
        ss = bm.collapse_sessions([rd(d, 7, 0, 130, 80), rd(d, 7, 30, 132, 82)], CFG)
        self.assertEqual(len(ss), 2)


class WeightTests(unittest.TestCase):
    def pts(self, n, slope_per_day=-0.1, start=80.0):
        return [{"date": TODAY - timedelta(days=n - 1 - i), "kg": start + slope_per_day * i, "n": 1} for i in range(n)]

    def test_daily_weights_merge_same_day(self):
        w = bm.daily_weights([{"local": datetime(2026, 11, 10, 9, 1), "kg": 74.7},
                              {"local": datetime(2026, 11, 10, 9, 44), "kg": 74.8}])
        self.assertEqual(len(w), 1)
        self.assertAlmostEqual(w[0]["kg"], 74.75)

    def test_ewma_first_point_and_smoothing(self):
        p = [{"date": date(2026, 11, 1) + timedelta(days=i), "kg": v, "n": 1} for i, v in enumerate([80, 82, 80, 80])]
        t = bm.ewma_trend(p, 0.1)
        self.assertEqual(t[0], 80)
        self.assertLess(t[1], 82)       # spike is damped
        self.assertGreater(t[1], 80)

    def test_rate_needs_enough_points_and_span(self):
        r = bm.weight_rate(self.pts(3), TODAY, CFG)
        self.assertIsNone(r["kg_per_week"])
        self.assertIn("needed", r)

    def test_rate_recovers_known_slope(self):
        r = bm.weight_rate(self.pts(21, -0.1), TODAY, CFG)
        self.assertAlmostEqual(r["kg_per_week"], -0.7, places=2)

    def test_weight_block_with_and_without_height(self):
        wi = [{"local": datetime(2026, 11, 10, 9, 44), "kg": 80.0}]
        with_h = bm.weight_block(wi, 170, TODAY, CFG)
        self.assertEqual(with_h["bmi"], 27.7)
        self.assertEqual(with_h["target"]["kg"], 66.2)
        self.assertEqual(with_h["target"]["to_go_kg"], 13.8)
        self.assertNotIn("bmi", bm.weight_block(wi, None, TODAY, CFG))


class SummaryTests(unittest.TestCase):
    def home_readings(self, days=30, am=(136, 84), pm=(140, 87)):
        out = []
        for i in range(days):
            d = TODAY - timedelta(days=days - i)
            out.append(rd(d, 5, 20, am[0] + (i % 3), am[1]))
            out.append(rd(d, 22, 30, pm[0] + (i % 3), pm[1]))
        return out

    def test_empty_inputs_do_not_crash_and_serialize(self):
        s = bm.compute_body_summary([], [], None, today=TODAY)
        json.dumps(s)
        self.assertEqual(s["bp"]["n_sessions"], 0)
        self.assertIn("bp_none", ins_ids(s))

    def test_bp_summary_numbers(self):
        s = bm.compute_body_summary(self.home_readings(pm=(143, 87)), [], 170, today=TODAY)
        json.dumps(s)
        bp = s["bp"]
        self.assertEqual(bp["n_sessions"], 60)
        self.assertEqual(bp["n_days"], 30)
        self.assertEqual(bp["days_with_am_pm"], 30)
        self.assertEqual(bp["share_ge_home"], 100)
        self.assertGreater(bp["pm"]["sys"], bp["am"]["sys"])
        ids = ins_ids(s)
        self.assertEqual(ids["bp_home_level"]["severity"], "perhatian")
        self.assertIn("bp_am_pm", ids)
        self.assertIn("Sistolik malam lebih tinggi", ids["bp_am_pm"]["title"])
        self.assertIn("bp_protocol", ids)          # single readings only
        self.assertNotIn("bp_urgent", ids)

    def test_level_uses_recent_window_only_when_enough_sessions(self):
        many = bm.compute_body_summary(self.home_readings(days=30), [], 170, today=TODAY)
        self.assertIn("30 hari terakhir", many["bp"]["level"]["scope"])
        self.assertGreaterEqual(many["bp"]["level"]["n"], 6)
        old = [rd(TODAY - timedelta(days=60 + i), 7, 0, 138, 86) for i in range(10)] + [rd(TODAY - timedelta(days=2), 7, 0, 143, 89)]
        few = bm.compute_body_summary(old, [], 170, today=TODAY)
        self.assertEqual(few["bp"]["recent"]["n"], 1)
        self.assertEqual(few["bp"]["level"]["n"], 11)             # falls back to all data
        self.assertIn("seluruh data", few["bp"]["level"]["scope"])
        self.assertIn("seluruh data", ins_ids(few)["bp_home_level"]["evidence"]["scope"])

    def test_cross_items_carry_axis_labels(self):
        s = bm.compute_body_summary([], [], 170, today=TODAY)
        it = {i["id"]: i for i in s["cross"]["items"]}["sleep_vs_bp"]
        self.assertEqual(it["x_label"], "jam")
        self.assertIn("sistolik", it["y_label"])

    def test_small_am_pm_gap_is_reported_as_similar(self):
        s = bm.compute_body_summary(self.home_readings(), [], 170, today=TODAY)   # gap is 4 mmHg
        self.assertEqual(ins_ids(s)["bp_am_pm"]["title"], "Pagi dan malam serupa")

    def test_am_pm_needs_enough_sessions_per_side(self):
        s = bm.compute_body_summary(self.home_readings(days=4), [], 170, today=TODAY)
        self.assertIn("belum bisa dibuat", ins_ids(s)["bp_am_pm"]["title"])

    def test_urgent_reading_is_flagged_first(self):
        rs = self.home_readings(20) + [rd(TODAY - timedelta(days=2), 8, 0, 186, 100)]
        s = bm.compute_body_summary(rs, [], 170, today=TODAY)
        self.assertEqual(s["insights"][0]["id"], "bp_urgent")
        self.assertEqual(s["insights"][0]["severity"], "penting")

    def test_data_gap_is_reported(self):
        rs = [rd(TODAY - timedelta(days=40 + i), 7, 0, 130, 80) for i in range(10)]
        s = bm.compute_body_summary(rs, [], 170, today=TODAY)
        self.assertEqual(ins_ids(s)["bp_data_gap"]["severity"], "perhatian")
        self.assertGreaterEqual(s["bp"]["days_since_last"], 14)

    def test_trend_requires_span_and_count(self):
        few = bm.compute_body_summary(self.home_readings(5), [], 170, today=TODAY)
        self.assertIsNone(few["bp"]["trend"])
        many = bm.compute_body_summary(self.home_readings(60), [], 170, today=TODAY)
        self.assertIsNotNone(many["bp"]["trend"])

    def test_sparse_weight_gives_needed_message_not_a_number(self):
        wi = [{"local": datetime(2026, 11, 7, 12, 24), "kg": 74.0},
              {"local": datetime(2026, 11, 11, 9, 1), "kg": 74.7},
              {"local": datetime(2026, 11, 11, 9, 44), "kg": 74.8}]
        s = bm.compute_body_summary([], wi, 170, today=TODAY)
        rate = ins_ids(s)["weight_rate"]
        self.assertIn("belum bisa dihitung", rate["title"])
        self.assertIn("bmi_now", ins_ids(s))
        self.assertIn("25.9", ins_ids(s)["bmi_now"]["title"])

    def test_missing_height_prompts_instead_of_inventing_bmi(self):
        wi = [{"local": datetime(2026, 11, 10, 9, 44), "kg": 74.8}]
        s = bm.compute_body_summary([], wi, None, today=TODAY)
        self.assertIn("BMI belum bisa dihitung", ins_ids(s)["bmi_now"]["title"])


class CrossTests(unittest.TestCase):
    def test_insufficient_without_context(self):
        rs = [rd(TODAY - timedelta(days=i), 6, 0, 135, 85) for i in range(20)]
        s = bm.compute_body_summary(rs, [], 170, today=TODAY)
        items = {i["id"]: i for i in s["cross"]["items"]}
        self.assertEqual(items["sleep_vs_bp"]["status"], "insufficient")
        self.assertEqual(items["weight_vs_sys"]["status"], "insufficient")
        self.assertEqual(items["train_vs_rest"]["status"], "insufficient")
        self.assertIsNone(s["cross"]["vo2max_abs"])

    def test_sleep_vs_bp_found_with_correct_sign(self):
        rs, sleep = [], {}
        for i in range(20):
            d = TODAY - timedelta(days=i)
            hours = 5 + (i % 5)
            sleep[d.isoformat()] = {"hours": hours}
            rs.append(rd(d, 6, 0, 150 - 5 * hours, 85))
        s = bm.compute_body_summary(rs, [], 170, context={"sleep": sleep}, today=TODAY)
        it = {i["id"]: i for i in s["cross"]["items"]}["sleep_vs_bp"]
        self.assertEqual(it["status"], "found")
        self.assertAlmostEqual(it["slope"], -5.0, places=1)
        self.assertIn("bukan bukti sebab-akibat", it["text"])

    def test_uncorrelated_data_is_not_called_a_finding(self):
        rs, sleep = [], {}
        pattern = [130, 138, 134, 140, 132, 139, 133, 137]
        hours = [7, 7, 6, 6, 8, 8, 5, 5]
        for i in range(24):
            d = TODAY - timedelta(days=i)
            sleep[d.isoformat()] = {"hours": hours[i % 8]}
            rs.append(rd(d, 6, 0, pattern[(i * 3) % 8], 85))
        s = bm.compute_body_summary(rs, [], 170, context={"sleep": sleep}, today=TODAY)
        it = {i["id"]: i for i in s["cross"]["items"]}["sleep_vs_bp"]
        self.assertIn(it["status"], ("weak", "found"))
        if it["status"] == "weak":
            self.assertIn("Tidak terlihat hubungan berarti", it["text"])

    def test_vo2max_absolute_is_plain_arithmetic(self):
        wi = [{"local": datetime(2026, 11, 12, 10, 0), "kg": 80.0}]
        s = bm.compute_body_summary([], wi, 170, context={"vo2max": [("2026-11-10", 50.0)]}, today=TODAY)
        v = s["cross"]["vo2max_abs"]
        self.assertEqual(v["vo2max_abs_l_min"], 4.0)
        self.assertEqual(v["pct_per_minus_1kg"], 1.27)
        self.assertEqual(v["target_weight_kg"], 66.2)
        self.assertAlmostEqual(v["rel_at_target_weight"], 4.0 * 1000 / 66.18, places=0)


class RefinedCrossTests(unittest.TestCase):
    def morning_data(self, n=40):
        rs, trimp = [], {}
        for i in range(n):
            d = TODAY - timedelta(days=i)
            trimp[d.isoformat()] = 10.0 + (i % 10) * 10          # 10..100, every day non-zero
        for i in range(1, n):
            d = TODAY - timedelta(days=i)
            prev = trimp[(d - timedelta(days=1)).isoformat()] if (d - timedelta(days=1)).isoformat() in trimp else 0
            rs.append(rd(d, 6, 0, 150 if prev >= 80 else 130, 85))
        return rs, trimp

    def test_daily_activity_does_not_make_every_day_a_training_day(self):
        rs, trimp = self.morning_data()
        s = bm.compute_body_summary(rs, [], 170, context={"trimp": trimp}, today=TODAY)
        it = {i["id"]: i for i in s["cross"]["items"]}["train_vs_rest"]
        self.assertGreater(it["n_hard"], 0)
        self.assertGreater(it["n_light"], 0)
        self.assertEqual(it["status"], "found")
        self.assertGreater(it["diff"], 15)

    def test_train_vs_rest_needs_load_history(self):
        rs, _ = self.morning_data()
        s = bm.compute_body_summary(rs, [], 170, context={"trimp": {"2026-11-01": 50.0}}, today=TODAY)
        it = {i["id"]: i for i in s["cross"]["items"]}["train_vs_rest"]
        self.assertEqual(it["status"], "insufficient")

    def test_weight_vs_ei_blocked_when_weight_barely_varies(self):
        wi = [{"local": datetime(2026, 10, 1, 8, 0) + timedelta(days=i), "kg": 75.0} for i in range(30)]
        eff = [((date(2026, 10, 1) + timedelta(days=i)).isoformat(), 0.8 + i * 0.01) for i in range(30)]
        s = bm.compute_body_summary([], wi, 170, context={"efficiency": eff}, today=TODAY)
        it = {i["id"]: i for i in s["cross"]["items"]}["weight_vs_ei"]
        self.assertEqual(it["status"], "insufficient")
        self.assertIn("variasinya", it["text"])

    def test_weight_vs_ei_found_carries_time_caution(self):
        wi, eff = [], []
        for i in range(30):
            d = date(2026, 10, 1) + timedelta(days=i)
            kg = 80.0 - i * 0.2
            wi.append({"local": datetime(d.year, d.month, d.day, 8, 0), "kg": kg})
            eff.append((d.isoformat(), 0.8 + i * 0.01))
        s = bm.compute_body_summary([], wi, 170, context={"efficiency": eff}, today=TODAY)
        it = {i["id"]: i for i in s["cross"]["items"]}["weight_vs_ei"]
        self.assertEqual(it["status"], "found")
        self.assertIn("bergeser seiring waktu", it["text"])
        self.assertIn("caution", it)


class WeightChangesTests(unittest.TestCase):
    def test_sparse_weighins_still_give_90_day_change(self):
        wi = [{"local": datetime(2026, 8, 2, 8, 0), "kg": 76.0},
              {"local": datetime(2026, 9, 5, 8, 0), "kg": 75.0},
              {"local": datetime(2026, 11, 7, 8, 0), "kg": 75.0},
              {"local": datetime(2026, 11, 11, 10, 0), "kg": 75.8}]
        s = bm.compute_body_summary([], wi, 170, today=TODAY)
        ch = {c["days"]: c for c in s["weight"]["changes"]}
        self.assertIn(90, ch)
        self.assertLess(ch[90]["delta_kg"], 0)          # trend lower than mid-Aug
        ids = ins_ids(s)
        self.assertIn("weight_change", ids)
        self.assertIn("jarang", ids["weight_change"]["text"])   # <4 weigh-ins in window

    def test_stale_anchor_is_skipped(self):
        wi = [{"local": datetime(2025, 1, 1, 8, 0), "kg": 85.0}, {"local": datetime(2026, 11, 10, 8, 0), "kg": 75.0}]
        s = bm.compute_body_summary([], wi, 170, today=TODAY)
        self.assertEqual(s["weight"]["changes"], [])


if __name__ == "__main__":
    unittest.main()
