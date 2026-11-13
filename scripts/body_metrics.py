"""
Body metrics: BMI, weight trend, home blood-pressure analysis, cross-domain
correlations and rule-based insights.

Pure functions, no I/O: everything takes plain dicts/lists and returns a
JSON-serialisable summary, so the formulas live in exactly one place and are
unit-tested (see tests/test_body_metrics.py). sync_body_db.py feeds it from the
database; the browser only draws what comes back.

Definitions and thresholds are documented in docs/body-metrics.md.

All timestamps are Garmin *local wall-clock* naive datetimes: "morning" means
the user's morning, so no timezone arithmetic is needed here.

Medical note: every insight is descriptive, never diagnostic. Cross-domain
results are correlations, never causal claims, and are shown only when there is
enough paired data.
"""
from __future__ import annotations

import math
import statistics as st
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

DEFAULTS: Dict[str, Any] = {
    "bmi_standard": "asia_pacific",   # asia_pacific | who | kemenkes
    "target_bmi": 22.9,               # top of the Asia-Pacific normal band
    "session_gap_min": 10,            # readings closer than this form one session
    "morning_before_hour": 12,
    "evening_from_hour": 17,
    "home_sys": 135,                  # ESH/ESC 2018 home-BP hypertension threshold
    "home_dia": 85,
    "level_window_days": 30,
    "min_sessions_level": 6,
    "min_sessions_trend": 15,
    "min_span_trend_days": 28,
    "min_per_side_ampm": 10,
    "min_ampm_diff": 5.0,
    "gap_alert_days": 14,
    "urgent_window_days": 14,
    "weight_rate_window_days": 28,
    "weight_rate_min_points": 4,
    "weight_rate_min_span_days": 14,
    "ewma_alpha": 0.1,
    "min_cross_n": 15,
    "min_cross_weeks": 8,
    "min_group_n": 8,
    "min_abs_r": 0.3,
}

# (upper bound exclusive, key, label)
BMI_BANDS = {
    "asia_pacific": [
        (18.5, "underweight", "Kurus"), (23.0, "normal", "Normal"),
        (25.0, "at_risk", "Berisiko (gemuk)"), (30.0, "obese1", "Obesitas I"),
        (math.inf, "obese2", "Obesitas II")],
    "who": [
        (18.5, "underweight", "Kurus"), (25.0, "normal", "Normal"),
        (30.0, "overweight", "Berat berlebih"), (math.inf, "obese", "Obesitas")],
    "kemenkes": [
        (18.5, "underweight", "Kurus"), (25.01, "normal", "Normal"),
        (27.01, "overweight", "Kelebihan berat"), (math.inf, "obese", "Obesitas")],
}
BMI_CUTS = {"asia_pacific": [18.5, 23.0, 25.0, 30.0], "who": [18.5, 25.0, 30.0],
            "kemenkes": [18.5, 25.0, 27.0]}

BP_CATEGORY_LABELS = {
    "normal": "Normal (<120/<80)", "elevated": "Meningkat (120–129/<80)",
    "stage1": "Tahap 1 (130–139 / 80–89)", "stage2": "Tahap 2 (≥140 / ≥90)",
    "crisis": "Sangat tinggi (>180 / >120)",
}


# ── small helpers ─────────────────────────────────────────────────────────────
def _r(v: Optional[float], nd: int = 1) -> Optional[float]:
    return None if v is None else round(float(v), nd)


def _mean(xs: List[float]) -> Optional[float]:
    return st.fmean(xs) if xs else None


def _sd(xs: List[float]) -> Optional[float]:
    return st.pstdev(xs) if len(xs) >= 2 else None


def _fit(xs: List[float], ys: List[float]) -> Optional[Dict[str, float]]:
    """OLS fit with Pearson r. None when degenerate (constant input, n<3)."""
    if len(xs) < 3 or len(set(xs)) < 2 or len(set(ys)) < 2:
        return None
    try:
        slope, intercept = st.linear_regression(xs, ys)
        r = st.correlation(xs, ys)
    except st.StatisticsError:
        return None
    return {"slope": slope, "intercept": intercept, "r": r, "r2": r * r}


def _d(x: datetime | date | str) -> date:
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    return date.fromisoformat(str(x)[:10])


# ── weight ────────────────────────────────────────────────────────────────────
def bmi(weight_kg: float, height_cm: float) -> float:
    m = height_cm / 100.0
    return weight_kg / (m * m)


def weight_for_bmi(target_bmi: float, height_cm: float) -> float:
    m = height_cm / 100.0
    return target_bmi * m * m


def bmi_category(value: float, standard: str = "asia_pacific") -> Dict[str, str]:
    for upper, key, label in BMI_BANDS.get(standard, BMI_BANDS["asia_pacific"]):
        if value < upper:
            return {"key": key, "label": label, "standard": standard}
    return {"key": "unknown", "label": "?", "standard": standard}


def daily_weights(weigh_ins: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One point per local day (mean of that day's weigh-ins), oldest first."""
    by_day: Dict[date, List[float]] = defaultdict(list)
    for w in weigh_ins:
        by_day[_d(w["local"])].append(float(w["kg"]))
    return [{"date": d, "kg": st.fmean(v), "n": len(v)} for d, v in sorted(by_day.items())]


def ewma_trend(points: List[Dict[str, Any]], alpha: float = 0.1) -> List[float]:
    """Time-aware exponentially weighted trend: a longer gap since the previous
    weigh-in lets the new reading count for more (1-(1-a)^days)."""
    out: List[float] = []
    prev_t: Optional[float] = None
    prev_d: Optional[date] = None
    for p in points:
        if prev_t is None:
            prev_t = p["kg"]
        else:
            gap = max((p["date"] - prev_d).days, 1)
            a = 1 - (1 - alpha) ** gap
            prev_t = a * p["kg"] + (1 - a) * prev_t
        prev_d = p["date"]
        out.append(prev_t)
    return out


def weight_rate(points: List[Dict[str, Any]], today: date, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """kg/week from an OLS fit over the recent window, or a reason it is unavailable."""
    since = today - timedelta(days=cfg["weight_rate_window_days"])
    pts = [p for p in points if p["date"] >= since]
    need_n, need_span = cfg["weight_rate_min_points"], cfg["weight_rate_min_span_days"]
    span = (pts[-1]["date"] - pts[0]["date"]).days if len(pts) >= 2 else 0
    if len(pts) < need_n or span < need_span:
        return {"kg_per_week": None, "n": len(pts), "span_days": span,
                "needed": f"≥{need_n} hari penimbangan dalam rentang ≥{need_span} hari"}
    t0 = pts[0]["date"]
    fit = _fit([(p["date"] - t0).days for p in pts], [p["kg"] for p in pts])
    if not fit:
        return {"kg_per_week": None, "n": len(pts), "span_days": span, "needed": "data bervariasi"}
    return {"kg_per_week": _r(fit["slope"] * 7, 2), "n": len(pts), "span_days": span, "r2": _r(fit["r2"], 2)}


def weight_changes(points: List[Dict[str, Any]], trend: List[float], today: date) -> List[Dict[str, Any]]:
    """Trend change over 30/90/180/365 days. Works with sparse weigh-ins: the
    anchor is the last point on/before the window start, skipped if too stale."""
    out = []
    for days in (30, 90, 180, 365):
        anchor = today - timedelta(days=days)
        prior = [(p, t) for p, t in zip(points, trend) if p["date"] <= anchor]
        if not prior:
            continue
        p, t = prior[-1]
        if (anchor - p["date"]).days > max(14, days // 3):
            continue
        n_in = sum(1 for q in points if q["date"] > anchor)
        out.append({"days": days, "from_date": p["date"].isoformat(), "from_kg": _r(t, 1),
                    "to_kg": _r(trend[-1], 1), "delta_kg": _r(trend[-1] - t, 1), "n_in_window": n_in})
    return out


def weight_block(weigh_ins, height_cm, today, cfg) -> Dict[str, Any]:
    pts = daily_weights(weigh_ins)
    block: Dict[str, Any] = {"n_weighins": len(weigh_ins), "n_days": len(pts), "series": []}
    if not pts:
        return block
    trend = ewma_trend(pts, cfg["ewma_alpha"])
    block["series"] = [{"date": p["date"].isoformat(), "kg": _r(p["kg"], 2), "trend": _r(t, 2)}
                       for p, t in zip(pts, trend)]
    latest = pts[-1]
    block["latest"] = {"date": latest["date"].isoformat(), "kg": _r(latest["kg"], 1)}
    block["trend_kg"] = _r(trend[-1], 1)
    for days in (7, 30):
        win = [p["kg"] for p in pts if p["date"] > today - timedelta(days=days)]
        block[f"avg{days}"] = {"kg": _r(_mean(win), 1), "n": len(win)}
    block["rate"] = weight_rate(pts, today, cfg)
    block["changes"] = weight_changes(pts, trend, today)
    if height_cm:
        std = cfg["bmi_standard"]
        b = bmi(latest["kg"], height_cm)
        cuts = BMI_CUTS.get(std, BMI_CUTS["asia_pacific"])
        block["height_cm"] = height_cm
        block["bmi"] = _r(b, 1)
        block["bmi_category"] = bmi_category(b, std)
        block["bmi_category_who"] = bmi_category(b, "who")
        block["bmi_lines_kg"] = [{"bmi": c, "kg": _r(weight_for_bmi(c, height_cm), 1)} for c in cuts]
        target_kg = weight_for_bmi(cfg["target_bmi"], height_cm)
        block["target"] = {"bmi": cfg["target_bmi"], "kg": _r(target_kg, 1),
                           "to_go_kg": _r(latest["kg"] - target_kg, 1),
                           "healthy_range_kg": [_r(weight_for_bmi(18.5, height_cm), 1),
                                                _r(weight_for_bmi(cfg["target_bmi"], height_cm), 1)]}
        block["who_normal_max_kg"] = _r(weight_for_bmi(25.0, height_cm), 1)
    return block


# ── blood pressure ────────────────────────────────────────────────────────────
def period_of(hour: int, cfg: Dict[str, Any]) -> str:
    if hour < cfg["morning_before_hour"]:
        return "pagi"
    if hour >= cfg["evening_from_hour"]:
        return "malam"
    return "siang"


def map_pressure(sys_: float, dia: float) -> float:
    return dia + (sys_ - dia) / 3.0


def bp_category(sys_: float, dia: float) -> str:
    """ACC/AHA 2017 category of a mean pressure."""
    if sys_ > 180 or dia > 120:
        return "crisis"
    if sys_ >= 140 or dia >= 90:
        return "stage2"
    if sys_ >= 130 or dia >= 80:
        return "stage1"
    if sys_ >= 120:
        return "elevated"
    return "normal"


def home_status(sys_: float, dia: float, cfg: Dict[str, Any]) -> str:
    """Level of a mean home pressure against the 135/85 home threshold."""
    hs, hd = cfg["home_sys"], cfg["home_dia"]
    if sys_ >= hs + 15 or dia >= hd + 10:
        return "well_above"
    if sys_ >= hs or dia >= hd:
        return "above"
    if sys_ >= hs - 5 or dia >= hd - 5:
        return "borderline"
    return "ok"


def collapse_sessions(readings: List[Dict[str, Any]], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Readings taken within `session_gap_min` of each other are one session,
    averaged. Home-BP guidance asks for two readings a minute apart; treating
    them as separate points would double-count a single moment."""
    rs = sorted(readings, key=lambda r: r["local"])
    groups: List[List[Dict[str, Any]]] = []
    gap = timedelta(minutes=cfg["session_gap_min"])
    for r in rs:
        if groups and r["local"] - groups[-1][-1]["local"] <= gap:
            groups[-1].append(r)
        else:
            groups.append([r])
    out = []
    for g in groups:
        s = st.fmean(x["systolic"] for x in g)
        d = st.fmean(x["diastolic"] for x in g)
        pulses = [x["pulse"] for x in g if x.get("pulse")]
        t = g[0]["local"]
        out.append({
            "t": t, "date": t.date(), "period": period_of(t.hour, cfg), "n": len(g),
            "sys": s, "dia": d, "pulse": st.fmean(pulses) if pulses else None,
            "map": map_pressure(s, d), "pp": s - d,
        })
    return out


def _avg(sessions: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"sys": _r(_mean([s["sys"] for s in sessions])), "dia": _r(_mean([s["dia"] for s in sessions])),
            "n": len(sessions)}


def _month(d: date) -> str:
    return d.strftime("%Y-%m")


def bp_block(readings, today, cfg) -> Dict[str, Any]:
    sessions = collapse_sessions(readings, cfg)
    block: Dict[str, Any] = {"n_readings": len(readings), "n_sessions": len(sessions), "sessions": []}
    if not sessions:
        return block
    days = sorted({s["date"] for s in sessions})
    block["n_days"] = len(days)
    block["first_date"], block["last_date"] = days[0].isoformat(), days[-1].isoformat()
    block["days_since_last"] = (today - days[-1]).days
    block["all"] = _avg(sessions)
    win_from = today - timedelta(days=cfg["level_window_days"])
    recent = [s for s in sessions if s["date"] > win_from]
    block["recent"] = {**_avg(recent), "days": cfg["level_window_days"]}
    # The headline level: recent window when it has enough sessions, else everything.
    if block["recent"]["n"] >= cfg["min_sessions_level"]:
        block["level"] = {**_avg(recent), "scope": f"{cfg['level_window_days']} hari terakhir"}
    else:
        block["level"] = {**block["all"], "scope": "seluruh data (30 hari terakhir terlalu sedikit)"}
    block["am"] = _avg([s for s in sessions if s["period"] == "pagi"])
    block["pm"] = _avg([s for s in sessions if s["period"] == "malam"])
    n = len(sessions)
    hs, hd = cfg["home_sys"], cfg["home_dia"]
    block["share_ge_home"] = _r(100 * sum(1 for s in sessions if s["sys"] >= hs or s["dia"] >= hd) / n, 0)
    block["share_ge_140_90"] = _r(100 * sum(1 for s in sessions if s["sys"] >= 140 or s["dia"] >= 90) / n, 0)
    block["map_mean"] = _r(_mean([s["map"] for s in sessions]))
    block["pp_mean"] = _r(_mean([s["pp"] for s in sessions]))
    sys_series = [s["sys"] for s in sessions]
    block["sd_sys"] = _r(_sd(sys_series))
    block["arv_sys"] = _r(_mean([abs(b - a) for a, b in zip(sys_series, sys_series[1:])])) if n >= 2 else None
    block["share_multi_reading"] = _r(100 * sum(1 for s in sessions if s["n"] >= 2) / n, 0)
    both = {d for d in days if {"pagi", "malam"} <= {s["period"] for s in sessions if s["date"] == d}}
    block["days_with_am_pm"] = len(both)

    # Trend over the last 90 days
    t_from = today - timedelta(days=90)
    tr = [s for s in sessions if s["date"] >= t_from]
    block["trend"] = None
    if len(tr) >= cfg["min_sessions_trend"]:
        span = (tr[-1]["date"] - tr[0]["date"]).days
        if span >= cfg["min_span_trend_days"]:
            t0 = tr[0]["date"]
            xs = [(s["date"] - t0).days for s in tr]
            fs, fd = _fit(xs, [s["sys"] for s in tr]), _fit(xs, [s["dia"] for s in tr])
            if fs and fd:
                block["trend"] = {"sys_per_30d": _r(fs["slope"] * 30), "dia_per_30d": _r(fd["slope"] * 30),
                                  "r2_sys": _r(fs["r2"], 2), "n": len(tr), "span_days": span}

    # Per-month: category distribution and am/pm means
    months = sorted({_month(s["date"]) for s in sessions})
    block["categories_by_month"] = []
    block["monthly"] = []
    for m in months:
        ms = [s for s in sessions if _month(s["date"]) == m]
        cats = defaultdict(int)
        for s in ms:
            cats[bp_category(s["sys"], s["dia"])] += 1
        block["categories_by_month"].append({"month": m, "n": len(ms), **{k: cats.get(k, 0) for k in BP_CATEGORY_LABELS}})
        block["monthly"].append({"month": m, "all": _avg(ms),
                                 "am": _avg([s for s in ms if s["period"] == "pagi"]),
                                 "pm": _avg([s for s in ms if s["period"] == "malam"])})

    # 7-day rolling mean at each day that has a session
    block["rolling7"] = []
    for d in days:
        win = [s for s in sessions if d - timedelta(days=6) <= s["date"] <= d]
        block["rolling7"].append({"date": d.isoformat(), **{k: v for k, v in _avg(win).items() if k != "n"}, "n": len(win)})

    # Gaps longer than the alert threshold (within the data span)
    gaps = [{"from": a.isoformat(), "to": b.isoformat(), "days": (b - a).days}
            for a, b in zip(days, days[1:]) if (b - a).days > cfg["gap_alert_days"]]
    block["gaps"] = sorted(gaps, key=lambda g: -g["days"])[:3]

    # Urgent: any very high session lately
    u_from = today - timedelta(days=cfg["urgent_window_days"])
    block["urgent"] = [{"date": s["date"].isoformat(), "sys": _r(s["sys"], 0), "dia": _r(s["dia"], 0)}
                       for s in sessions if s["date"] >= u_from and (s["sys"] >= 180 or s["dia"] >= 120)]

    block["sessions"] = [{"t": s["t"].isoformat(timespec="minutes"), "date": s["date"].isoformat(),
                          "period": s["period"], "n": s["n"], "sys": _r(s["sys"]), "dia": _r(s["dia"]),
                          "pulse": _r(s["pulse"], 0), "map": _r(s["map"]), "pp": _r(s["pp"], 0),
                          "cat": bp_category(s["sys"], s["dia"])} for s in sessions]
    return block


# ── cross-domain correlations ─────────────────────────────────────────────────
def _verdict(fit: Optional[Dict[str, float]], cfg) -> str:
    if not fit:
        return "weak"
    return "found" if abs(fit["r"]) >= cfg["min_abs_r"] else "weak"


TIME_CONFOUND = ("Berat dan variabel pembanding sama-sama bergeser seiring waktu (musim, kebugaran), "
                 "sehingga hubungan ini mudah semu.")


def _cross_item(id_: str, title: str, pairs: List[Tuple[float, float]], min_n: int, unit_x: str,
                unit_y: str, cfg, note: str = "", per: float = 1.0, per_label: str = "",
                min_distinct_x: int = 0, caution: str = "") -> Dict[str, Any]:
    n = len(pairs)
    item: Dict[str, Any] = {"id": id_, "title": title, "n": n, "needed": min_n,
                            "x_label": unit_x, "y_label": unit_y}
    if caution:
        item["caution"] = caution
    if n < min_n:
        item.update(status="insufficient", text=f"Butuh ≥{min_n} pasangan data; sekarang {n}.")
        return item
    distinct = len({round(p[0], 1) for p in pairs})
    if distinct < min_distinct_x:
        item.update(status="insufficient", distinct_x=distinct,
                    text=f"Nilai {unit_x} terlalu sedikit variasinya ({distinct} nilai berbeda; butuh ≥{min_distinct_x}).")
        return item
    fit = _fit([p[0] for p in pairs], [p[1] for p in pairs])
    item["status"] = _verdict(fit, cfg)
    if not fit:
        item["text"] = "Data terlalu seragam untuk dihitung."
        return item
    item["r"] = _r(fit["r"], 2)
    item["slope"] = _r(fit["slope"] * per, 2)
    item["points"] = [[_r(a, 2), _r(b, 2)] for a, b in pairs][-120:]
    if item["status"] == "found":
        direction = "naik" if fit["slope"] > 0 else "turun"
        item["text"] = (f"Setiap {per_label or ('+1 ' + unit_x)}, {unit_y} cenderung {direction} "
                        f"≈{abs(item['slope']):g} (r={item['r']}, n={n}, {distinct} nilai {unit_x} berbeda). "
                        f"Korelasi, bukan bukti sebab-akibat. {caution} {note}").strip()
    else:
        item["text"] = f"Tidak terlihat hubungan berarti (r={item['r']}, n={n}). {note}".strip()
    return item


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def cross_block(sessions, weight_pts, ctx: Dict[str, Any], height_cm, today, cfg) -> Dict[str, Any]:
    ctx = ctx or {}
    items: List[Dict[str, Any]] = []
    am = [s for s in sessions if s["period"] == "pagi"]

    # 1. weekly weight vs weekly systolic
    wk_w: Dict[date, List[float]] = defaultdict(list)
    for p in weight_pts:
        wk_w[_week_start(p["date"])].append(p["kg"])
    wk_s: Dict[date, List[float]] = defaultdict(list)
    for s in sessions:
        wk_s[_week_start(s["date"])].append(s["sys"])
    pairs = [(st.fmean(wk_w[w]), st.fmean(wk_s[w])) for w in sorted(set(wk_w) & set(wk_s))]
    items.append(_cross_item("weight_vs_sys", "Berat vs tekanan sistolik (rata-rata mingguan)", pairs,
                             cfg["min_cross_weeks"], "kg", "sistolik (mmHg)", cfg, per_label="+1 kg",
                             min_distinct_x=5, caution=TIME_CONFOUND))

    # 2-5. morning BP vs same-morning / previous-day context
    def morning_pairs(getter):
        out = []
        for s in am:
            v = getter(s["date"].isoformat())
            if v is not None:
                out.append((float(v), s["sys"]))
        return out

    sleep = ctx.get("sleep") or {}
    items.append(_cross_item("sleep_vs_bp", "Jam tidur semalam vs sistolik pagi",
                             morning_pairs(lambda d: (sleep.get(d) or {}).get("hours")),
                             cfg["min_cross_n"], "jam", "sistolik pagi (mmHg)", cfg, per_label="+1 jam tidur"))
    rhr = ctx.get("rhr") or {}
    items.append(_cross_item("rhr_vs_bp", "Detak istirahat vs sistolik pagi",
                             morning_pairs(lambda d: rhr.get(d)), cfg["min_cross_n"], "bpm",
                             "sistolik pagi (mmHg)", cfg, per_label="+1 bpm RHR"))
    hrv = ctx.get("hrv") or {}
    items.append(_cross_item("hrv_vs_bp", "HRV semalam vs sistolik pagi",
                             morning_pairs(lambda d: hrv.get(d)), cfg["min_cross_n"], "ms",
                             "sistolik pagi (mmHg)", cfg, per_label="+1 ms HRV"))

    # 3. morning after a hard day vs after a light day. Daily activity is near-constant
    # (walks count), so "any training" is always true; thresholds are relative to the
    # user's own daily-load distribution: hard = top quartile, light = below the median.
    trimp = ctx.get("trimp") or {}
    totals = sorted(v for v in trimp.values() if v and v > 0)
    item = {"id": "train_vs_rest", "title": "Sistolik pagi: sehari setelah hari berat vs hari ringan",
            "needed": cfg["min_group_n"]}
    if len(totals) >= 20:
        q75 = totals[int(0.75 * (len(totals) - 1))]
        med = totals[len(totals) // 2]
        hard, light = [], []
        for s_ in am:
            v = trimp.get((s_["date"] - timedelta(days=1)).isoformat()) or 0
            if v >= q75:
                hard.append(s_["sys"])
            elif v < med:
                light.append(s_["sys"])
        item.update(n=len(hard) + len(light), n_hard=len(hard), n_light=len(light),
                    hard_threshold=_r(q75, 0), light_threshold=_r(med, 0))
        if len(hard) >= cfg["min_group_n"] and len(light) >= cfg["min_group_n"]:
            diff = st.fmean(hard) - st.fmean(light)
            item.update(status="found" if abs(diff) >= cfg["min_ampm_diff"] else "weak", diff=_r(diff),
                        text=f"Pagi setelah hari berat (TRIMP ≥{q75:.0f}): {st.fmean(hard):.0f} mmHg vs setelah hari ringan "
                             f"(TRIMP <{med:.0f}): {st.fmean(light):.0f} mmHg (selisih {diff:+.1f}; n={len(hard)}/{len(light)}). "
                             "Korelasi, bukan sebab-akibat.")
        else:
            item.update(status="insufficient",
                        text=f"Butuh ≥{cfg['min_group_n']} pagi di tiap kelompok; sekarang {len(hard)} (hari berat) / {len(light)} (hari ringan).")
    else:
        item.update(n=0, status="insufficient", text="Butuh ≥20 hari dengan data beban latihan (TRIMP).")
    items.append(item)

    # 6. weight vs running efficiency index
    eff = ctx.get("efficiency") or []
    pairs = []
    for e_date, e_val in eff:
        d = _d(e_date)
        prior = [p for p in weight_pts if p["date"] <= d and (d - p["date"]).days <= 14]
        if prior and e_val is not None:
            pairs.append((prior[-1]["kg"], float(e_val)))
    items.append(_cross_item("weight_vs_ei", "Berat vs Efficiency Index lari", pairs, cfg["min_cross_n"],
                             "kg", "Efficiency Index", cfg, per_label="+1 kg",
                             min_distinct_x=6, caution=TIME_CONFOUND))

    # 7. absolute VO2max (pure arithmetic from relative VO2max and weight)
    vo2 = ctx.get("vo2max") or []
    vo2_abs = None
    if vo2 and weight_pts:
        v_rel = float(vo2[-1][1])
        w = weight_pts[-1]["kg"]
        abs_l = v_rel * w / 1000.0
        vo2_abs = {"vo2max_rel": _r(v_rel), "weight_kg": _r(w, 1), "vo2max_abs_l_min": _r(abs_l, 2),
                   "pct_per_minus_1kg": _r(100.0 * (w / (w - 1.0) - 1.0), 2),
                   "note": "Aritmetika murni dengan VO2 mutlak dianggap tetap; bukan prediksi performa."}
        if height_cm:
            tgt = weight_for_bmi(cfg["target_bmi"], height_cm)
            if tgt < w:
                vo2_abs["rel_at_target_weight"] = _r(abs_l * 1000.0 / tgt)
                vo2_abs["target_weight_kg"] = _r(tgt, 1)
    return {"items": items, "vo2max_abs": vo2_abs}


# ── insights ──────────────────────────────────────────────────────────────────
SEVERITY_RANK = {"penting": 0, "perhatian": 1, "info": 2}


def _ins(id_, severity, title, text, evidence=None):
    return {"id": id_, "severity": severity, "title": title, "text": text, "evidence": evidence or {}}


def build_insights(summary: Dict[str, Any], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    bp, w = summary.get("bp") or {}, summary.get("weight") or {}
    hs, hd = cfg["home_sys"], cfg["home_dia"]

    # ---- blood pressure
    if bp.get("urgent"):
        u = bp["urgent"][-1]
        out.append(_ins("bp_urgent", "penting", "Ada pengukuran sangat tinggi",
                        f"Pada {u['date']} tercatat {u['sys']:.0f}/{u['dia']:.0f} mmHg. Ulangi pengukuran setelah "
                        "duduk tenang 5 menit. Bila tetap setinggi itu, atau disertai nyeri dada, sesak, lemah satu sisi, "
                        "atau sakit kepala hebat, segera hubungi layanan medis.", {"urgent": bp["urgent"]}))

    if bp.get("n_sessions"):
        use = bp["level"]
        scope = use["scope"]
        status = home_status(use["sys"], use["dia"], cfg)
        sev = {"ok": "info", "borderline": "perhatian", "above": "perhatian", "well_above": "penting"}[status]
        phrase = {"ok": "di bawah ambang tensi rumah", "borderline": "mendekati ambang tensi rumah",
                  "above": "di atas ambang tensi rumah", "well_above": "jauh di atas ambang tensi rumah"}[status]
        tail = (" Rata-rata tensi rumah yang konsisten di atas ambang layak dibahas dengan dokter "
                "(bawa catatan pengukuran ini). Ini bukan diagnosis." if status in ("above", "well_above") else "")
        out.append(_ins("bp_home_level", sev, f"Tensi rumah {use['sys']:.0f}/{use['dia']:.0f} mmHg — {phrase}",
                        f"Rata-rata {scope}, n={use['n']} sesi. Ambang hipertensi pada tensi rumah: {hs}/{hd} mmHg "
                        f"(ESH/ESC).{tail}", {"avg": {k: use[k] for k in ("sys", "dia", "n")}, "scope": scope}))
        out.append(_ins("bp_share_above", "info",
                        f"{bp['share_ge_home']:.0f}% sesi ≥ {hs}/{hd}",
                        f"Dari {bp['n_sessions']} sesi, {bp['share_ge_home']:.0f}% berada di/atas {hs}/{hd} dan "
                        f"{bp['share_ge_140_90']:.0f}% di/atas 140/90.", {"n": bp["n_sessions"]}))

        am, pm = bp["am"], bp["pm"]
        m = cfg["min_per_side_ampm"]
        if am["n"] >= m and pm["n"] >= m:
            diff = pm["sys"] - am["sys"]
            if abs(diff) >= cfg["min_ampm_diff"]:
                hi, lo = ("malam", "pagi") if diff > 0 else ("pagi", "malam")
                out.append(_ins("bp_am_pm", "info", f"Sistolik {hi} lebih tinggi {abs(diff):.0f} mmHg",
                                f"Pagi {am['sys']:.0f}/{am['dia']:.0f} (n={am['n']}) vs malam "
                                f"{pm['sys']:.0f}/{pm['dia']:.0f} (n={pm['n']}).", {"am": am, "pm": pm}))
            else:
                out.append(_ins("bp_am_pm", "info", "Pagi dan malam serupa",
                                f"Pagi {am['sys']:.0f}/{am['dia']:.0f} vs malam {pm['sys']:.0f}/{pm['dia']:.0f} "
                                f"(selisih <{cfg['min_ampm_diff']:.0f} mmHg).", {"am": am, "pm": pm}))
        else:
            out.append(_ins("bp_am_pm", "info", "Perbandingan pagi vs malam belum bisa dibuat",
                            f"Butuh ≥{m} sesi pagi dan ≥{m} sesi malam; sekarang {am['n']} / {pm['n']}.",
                            {"am_n": am["n"], "pm_n": pm["n"]}))

        tr = bp.get("trend")
        if tr:
            d = tr["sys_per_30d"]
            word = "naik" if d > 1 else "turun" if d < -1 else "stabil"
            out.append(_ins("bp_trend", "info" if word != "naik" else "perhatian",
                            f"Tren sistolik {word} ({d:+.1f} mmHg/30 hari)",
                            f"Regresi linear 90 hari terakhir, n={tr['n']} sesi, rentang {tr['span_days']} hari, "
                            f"R²={tr['r2_sys']}. Diastolik {tr['dia_per_30d']:+.1f} mmHg/30 hari.", tr))
        else:
            out.append(_ins("bp_trend", "info", "Tren belum bisa dihitung",
                            f"Butuh ≥{cfg['min_sessions_trend']} sesi dalam 90 hari yang membentang ≥{cfg['min_span_trend_days']} hari.",
                            {}))

        if bp.get("pp_mean") is not None and bp["pp_mean"] >= 60:
            out.append(_ins("bp_pulse_pressure", "perhatian", f"Tekanan nadi rata-rata {bp['pp_mean']:.0f} mmHg",
                            "Selisih sistolik–diastolik ≥60 mmHg sering diperhatikan dokter sebagai penanda kekakuan "
                            "pembuluh darah. Sampaikan saat konsultasi.", {"pp_mean": bp["pp_mean"]}))
        if bp.get("sd_sys") is not None:
            out.append(_ins("bp_variability", "info", f"Variabilitas sistolik SD {bp['sd_sys']:.1f} mmHg",
                            f"Selisih rata-rata antar-sesi berurutan (ARV) {bp['arv_sys']:.1f} mmHg.",
                            {"sd": bp["sd_sys"], "arv": bp["arv_sys"]}))

        dsl = bp["days_since_last"]
        if dsl >= cfg["gap_alert_days"]:
            out.append(_ins("bp_data_gap", "perhatian", f"Terakhir mengukur {dsl} hari lalu",
                            "Pantauan tensi rumah paling berguna bila rutin (pagi dan malam).", {"days": dsl}))
        elif bp.get("gaps"):
            g = bp["gaps"][0]
            out.append(_ins("bp_data_gap", "info", f"Pernah ada jeda {g['days']} hari tanpa data",
                            f"Dari {g['from']} sampai {g['to']}.", {"gaps": bp["gaps"]}))

        if bp["n_sessions"] >= 10 and bp["share_multi_reading"] < 50:
            out.append(_ins("bp_protocol", "info", "Ukur 2 kali per sesi",
                            f"Hanya {bp['share_multi_reading']:.0f}% sesi berisi ≥2 pengukuran. Protokol tensi rumah (ESH): "
                            "2 kali berurutan dengan jeda 1 menit, pagi dan malam, selama 7 hari; rata-ratakan.",
                            {"share_multi_reading": bp["share_multi_reading"]}))
    else:
        out.append(_ins("bp_none", "info", "Belum ada data tensi",
                        "Ukur tensi dan pastikan Shortcut berjalan agar data masuk ke Garmin.", {}))

    # ---- weight
    if w.get("latest") and w.get("bmi") is not None:
        cat, who = w["bmi_category"], w["bmi_category_who"]
        sev = "info" if cat["key"] == "normal" else "perhatian"
        tgt = w["target"]
        out.append(_ins("bmi_now", sev, f"BMI {w['bmi']:.1f} — {cat['label']}",
                        f"{w['latest']['kg']:.1f} kg pada {w['height_cm']:.0f} cm. Standar Asia-Pasifik; menurut WHO: "
                        f"{who['label']}. Rentang berat BMI 18.5–{tgt['bmi']}: {tgt['healthy_range_kg'][0]}–"
                        f"{tgt['healthy_range_kg'][1]} kg"
                        + (f" (selisih {tgt['to_go_kg']:+.1f} kg ke batas atas)." if tgt['to_go_kg'] > 0 else ".")
                        + " BMI tidak membedakan otot dan lemak; interpretasikan bersama lingkar pinggang.",
                        {"bmi": w["bmi"], "target": tgt}))
    elif w.get("latest"):
        out.append(_ins("bmi_now", "info", "BMI belum bisa dihitung",
                        "Tinggi badan belum diatur. Jalankan: python scripts/sync_body_db.py --set-height <tinggi_cm>", {}))

    rate = w.get("rate")
    if w.get("latest"):
        if rate and rate.get("kg_per_week") is not None:
            kw = rate["kg_per_week"]
            word = "naik" if kw > 0.05 else "turun" if kw < -0.05 else "stabil"
            out.append(_ins("weight_rate", "info", f"Berat {word}: {kw:+.2f} kg/minggu",
                            f"Regresi {cfg['weight_rate_window_days']} hari, n={rate['n']} hari penimbangan, R²={rate.get('r2')}. "
                            "Laju sehat umumnya ≤0,5–1% berat badan per minggu.", rate))
        else:
            out.append(_ins("weight_rate", "info", "Laju perubahan berat belum bisa dihitung",
                            f"Butuh {rate['needed'] if rate else '≥4 penimbangan'}; sekarang n={rate['n'] if rate else 0}. "
                            "Timbang pada jam yang sama (pagi, setelah buang air, sebelum makan) agar tren jelas.",
                            rate or {}))
        ch = w.get("changes") or []
        pick = next((c for c in ch if c["days"] == 90), None) or (ch[-1] if ch else None)
        if pick:
            word = "naik" if pick["delta_kg"] > 0.2 else "turun" if pick["delta_kg"] < -0.2 else "stabil"
            sparse = " Penimbangan di jendela ini jarang, jadi angkanya kasar." if pick["n_in_window"] < 4 else ""
            out.append(_ins("weight_change", "info", f"Tren berat {word} {abs(pick['delta_kg']):.1f} kg dalam {pick['days']} hari",
                            f"{pick['from_kg']:.1f} kg ({pick['from_date']}) → {pick['to_kg']:.1f} kg, "
                            f"{pick['n_in_window']} penimbangan dalam jendela.{sparse}", {"changes": ch}))
        n30 = (w.get("avg30") or {}).get("n", 0)
        if n30 < 8:
            out.append(_ins("weigh_consistency", "info", f"{n30} hari penimbangan dalam 30 hari",
                            "Penimbangan 3–7 kali seminggu membuat tren lebih andal; naik-turun 0,5–1 kg antar hari itu wajar.",
                            {"n30": n30}))

    out.sort(key=lambda i: SEVERITY_RANK.get(i["severity"], 9))
    return out


# ── entry point ───────────────────────────────────────────────────────────────
def compute_body_summary(readings: List[Dict[str, Any]], weigh_ins: List[Dict[str, Any]],
                         height_cm: Optional[float] = None, context: Optional[Dict[str, Any]] = None,
                         cfg: Optional[Dict[str, Any]] = None, today: Optional[date] = None) -> Dict[str, Any]:
    """readings: [{local: datetime, systolic, diastolic, pulse}], weigh_ins: [{local: datetime, kg}]."""
    c = {**DEFAULTS, **(cfg or {})}
    today = today or date.today()
    sessions = collapse_sessions(readings, c)
    wpts = daily_weights(weigh_ins)
    summary: Dict[str, Any] = {
        "as_of": today.isoformat(),
        "profile": {"height_cm": height_cm, "bmi_standard": c["bmi_standard"], "target_bmi": c["target_bmi"],
                    "home_threshold": [c["home_sys"], c["home_dia"]],
                    "morning_before_hour": c["morning_before_hour"], "evening_from_hour": c["evening_from_hour"]},
        "bp_category_labels": BP_CATEGORY_LABELS,
        "weight": weight_block(weigh_ins, height_cm, today, c),
        "bp": bp_block(readings, today, c),
    }
    summary["cross"] = cross_block(sessions, wpts, context or {}, height_cm, today, c)
    summary["insights"] = build_insights(summary, c)
    return summary
