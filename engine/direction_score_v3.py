"""
engine/direction_score_v3.py — Olasilik tabanli LONG/SHORT (veto yok).

Moduller additive puan; yapi carpani YOK (ezme riski).
CVD: guven carpani ayri loglanir.
[SCORE_ATTRIBUTION] ile katman katman LONG/SHORT katki.
"""
from __future__ import annotations

import math
import time
from collections import deque
from typing import Any

from core.config import cfg
from core.logger import get_logger
from core.state import state, effective_price
from engine.v3_common import bars_15m
from engine.structure_thresholds import (
    bar_noise_bps,
    channel_geometry,
    close_broke_above,
    close_broke_below,
)

log = get_logger("DirectionScore")

_last_log_key = ""
_last_log_ts = 0.0

# Log sirasi (trend = kirilim / trend_continuation)
LOG_MODULES = (
    "structure",
    "liquidity",
    "event",
    "cvd",
    "zone",
    "volume",
    "trend",
)

# Kirilimda zayiflatilan moduller (rejim: structure/event; CVD ayri carpani)
_DEFAULT_BREAK_DECAY_MODULES = frozenset({"zone", "liquidity", "trend", "volume"})


def _base_score() -> float:
    return float(getattr(cfg, "V3_SCORE_BASE", 50.0) or 50.0)


def _prob_threshold() -> float:
    return float(getattr(cfg, "V3_PROB_ENTRY_THRESHOLD", 0.65) or 0.65)


def _structure_max_contrib() -> float:
    return float(getattr(cfg, "V3_STRUCTURE_MAX_CONTRIB", 25.0) or 25.0)


def _structure_strengths(ms: dict, structure: dict) -> tuple[float, float]:
    """
    Yapı katkısı: varsayılan SWING (BOS/CHoCH, anlık) — collapse'a düşmez.

    V3_STRUCTURE_SOURCE="swing" (varsayılan): ham swing dizisinden BOS/CHoCH.
    Chop'ta nötr (yapışkan 87 yok), collapse'tan bağımsız (çifte sayım yok).
    "collapse": eski davranış (rejim skoru + impulse hafızası).
    """
    src = str(getattr(cfg, "V3_STRUCTURE_SOURCE", "swing") or "swing").lower()
    if src == "swing":
        try:
            from engine.market_structure_v3 import compute_swing_structure

            ss = compute_swing_structure(price=float(ms.get("price", 0) or 0))
            stg = float(ss.get("strength", 0) or 0)
            bias = str(ss.get("bias") or "NEUTRAL").upper()
            if bias == "BEAR":
                return min(1.0, stg), 0.0
            if bias == "BULL":
                return 0.0, min(1.0, stg)
            return min(1.0, stg * 0.15), min(1.0, stg * 0.15)
        except Exception:
            pass  # hata → collapse fallback

    _ = structure  # snap uyumluluk; rejim collapse + unified structure scalar
    collapse = ms.get("collapse") or {}
    raw = min(100.0, max(0.0, float(collapse.get("state_score", 0) or 0))) / 100.0
    dom = str(collapse.get("dominant_bias") or "neutral").lower()
    struct_u = ms.get("structure") or {}
    strength = float(struct_u.get("strength", 0) or 0)
    if strength > 1:
        strength = strength / 100.0
    if raw <= 0 and strength > 0:
        raw = min(1.0, strength)
    dir_u = str(struct_u.get("direction") or "NEUTRAL").upper()

    bear = bull = 0.0
    if dom in ("bear", "bearish", "down"):
        bear = max(raw, strength)
    elif dom in ("bull", "bullish", "up"):
        bull = max(raw, strength)
    elif dir_u == "DOWN":
        bear = max(raw * 0.75, strength * 0.6)
    elif dir_u == "UP":
        bull = max(raw * 0.75, strength * 0.6)
    else:
        bear = bull = raw * 0.15
    return min(1.0, bear), min(1.0, bull)


def _structure_additive(side: str, bear: float, bull: float) -> float:
    """
    Yapi: additive, capped — carpim yok.
    LONG: bull guclu -> +, bear guclu -> kucuk ceza (max -8).
    """
    cap = _structure_max_contrib()
    oppose_cap = cap * float(getattr(cfg, "V3_STRUCTURE_OPPOSE_PENALTY", 0.35) or 0.35)
    if side == "LONG":
        pts = bull * cap - bear * oppose_cap
    else:
        pts = bear * cap - bull * oppose_cap
    floor = -float(getattr(cfg, "V3_STRUCTURE_MIN_PENALTY", 8.0) or 8.0)
    return max(floor, min(cap, pts))


def _cvd_alignment(cvd: dict, side: str) -> float:
    d = str(cvd.get("direction") or "NEUTRAL").upper()
    buy_r = float(cvd.get("buy_ratio", 0.5) or 0.5)
    cum = abs(float(cvd.get("cumulative", 0) or 0))
    norm = max(500.0, float(getattr(cfg, "V3_CVD_NORM", 8000) or 8000))
    delta_part = min(1.0, cum / norm)
    if side == "LONG":
        if d == "BULL":
            ratio_part = max(0.0, (buy_r - 0.5) * 2.0)
        elif d == "BEAR":
            ratio_part = max(0.0, (0.5 - buy_r) * 2.0) * 0.3
        else:
            ratio_part = 0.35
    else:
        if d == "BEAR":
            ratio_part = max(0.0, (0.5 - buy_r) * 2.0)
        elif d == "BULL":
            ratio_part = max(0.0, (buy_r - 0.5) * 2.0) * 0.3
        else:
            ratio_part = 0.35
    if cvd.get("confirmed"):
        ratio_part = min(1.0, ratio_part + 0.25)
    return min(1.0, max(0.0, 0.55 * ratio_part + 0.45 * delta_part))


def _cvd_confidence_mult(cvd: dict, side: str) -> float:
    align = _cvd_alignment(cvd, side)
    return 0.7 + 0.3 * align


def _count_levels_below_price(chart_levels: list, px: float, kind: str) -> int:
    if px <= 0:
        return 0
    n = 0
    for lv in chart_levels or []:
        p = float(lv.get("price") or lv.get("level") or 0)
        if p <= 0 or p >= px:
            continue
        k = str(lv.get("kind") or lv.get("side") or "").lower()
        if kind == "support" and ("support" in k or k in ("s", "destek")):
            n += 1
        if kind == "resistance" and ("resist" in k or k in ("r", "direnc")):
            n += 1
    return n


def _trend_level_points(
    side: str,
    *,
    levels: dict,
    px: float,
    ref_s: float,
    ref_r: float,
    close: float,
    scenario: dict,
    trend_mode: bool,
) -> float:
    """
    Trend = yalnizca fiyat vs S/R (kirilim, derinlik, kirilan destek sayisi).
    trend_mode icin duz +28 yok — ayni dump Structure rejiminde zaten puanlanir.
    """
    side = side.upper()
    max_c = float(getattr(cfg, "V3_TREND_MAX_CONTRIB", 28.0) or 28.0)
    scn = str(scenario.get("name") or "")
    active = levels.get("active") or {}
    chart = levels.get("chart_levels") or []
    pts = 0.0

    if side == "SHORT":
        s = ref_s or float(scenario.get("ref_support") or 0)
        if s > 0 and close_broke_below(close, s, close):
            pts = max(pts, 25.0)
        elif scn == "BREAKOUT_SELL":
            pts = max(pts, 18.0)
        if s > 0 and px > 0 and px < s:
            depth = (s - px) / s
            pts = max(pts, min(22.0, 4.0 + depth * 3500.0))
        broken = float(
            levels.get("last_broken_support")
            or getattr(state, "v3_last_broken_support", 0)
            or 0
        )
        if broken > 0 and px > 0 and px < broken * 0.998:
            pts += 6.0
        if active.get("below_all_supports") or levels.get("below_all_supports"):
            pts += 8.0
        pts += min(12.0, _count_levels_below_price(chart, px, "support") * 4.0)
        if trend_mode and pts < 6.0 and s > 0 and px < s * 0.999:
            pts = max(pts, 6.0)
    else:
        r = ref_r or float(scenario.get("ref_resistance") or 0)
        if r > 0 and close_broke_above(close, r, close):
            pts = max(pts, 25.0)
        elif scn == "BREAKOUT_BUY":
            pts = max(pts, 18.0)
        if r > 0 and px > r:
            depth = (px - r) / r
            pts = max(pts, min(22.0, 4.0 + depth * 3500.0))
        broken = float(
            levels.get("last_broken_resistance")
            or getattr(state, "v3_last_broken_resistance", 0)
            or 0
        )
        if broken > 0 and px > broken * 1.002:
            pts += 6.0
        if active.get("above_all_resistances") or levels.get("above_all_resistances"):
            pts += 8.0
        # LONG trend only gets credit for resistances already broken below price.
        pts += min(12.0, _count_levels_below_price(chart, px, "resistance") * 4.0)
        if trend_mode and pts < 6.0 and r > 0 and px > r * 1.001:
            pts = max(pts, 6.0)

    return min(max_c, pts)


def _apply_structure_trend_overlap_discount(
    pts: dict[str, float], side: str
) -> dict[str, float]:
    """Structure zaten gucluyken Trend katkisini kisalt; toplam puan tavanı."""
    _ = side
    out = dict(pts)
    disc = float(
        getattr(cfg, "V3_STRUCTURE_TREND_OVERLAP_DISCOUNT", 0.45) or 0.45
    )
    cap = _structure_max_contrib()
    struct = float(out.get("structure", 0) or 0)
    trend = float(out.get("trend", 0) or 0)
    if disc > 0 and struct > 0 and trend > 0:
        util = min(1.0, struct / cap)
        if util >= 0.5:
            scale = max(0.25, 1.0 - disc * util)
            trend *= scale
            out["trend"] = trend
    combined_cap = float(
        getattr(cfg, "V3_STRUCTURE_TREND_MAX_COMBINED_POINTS", 32) or 32
    )
    if combined_cap > 0 and struct + trend > combined_cap:
        out["trend"] = max(0.0, combined_cap - struct)
    return out


def _break_decay_module_set() -> frozenset[str]:
    raw = str(getattr(cfg, "V3_BREAK_DECAY_MODULES", "zone,liquidity,trend,volume") or "")
    names = {x.strip().lower() for x in raw.split(",") if x.strip()}
    return frozenset(names) if names else _DEFAULT_BREAK_DECAY_MODULES


def _break_normalize_unit(px: float, ref_s: float, ref_r: float) -> float:
    """
    Kirilim mesafesi paydası (fiyat birimi) — ATR degil.
    median TR (bar_noise) + aktif bant genisligi + taban bps.
    """
    if px <= 0:
        return 1.0
    breath_mult = float(getattr(cfg, "V3_BREAK_DECAY_BREATH_MULT", 1.0) or 1.0)
    breath = px * bar_noise_bps(px) / 10000.0 * breath_mult
    band = max(0.0, ref_r - ref_s) if ref_r > ref_s else 0.0
    geo = channel_geometry(px)
    if geo.width > 0:
        band = max(band, geo.width)
    band_frac = float(getattr(cfg, "V3_BREAK_DECAY_BAND_FRAC", 0.10) or 0.10)
    min_bps = float(getattr(cfg, "V3_BREAK_DECAY_MIN_BPS", 8.0) or 8.0)
    floor_px = px * min_bps / 10000.0
    return max(floor_px, breath, band * band_frac, 1e-6)


def _break_gap_price(
    px: float,
    ref: float,
    *,
    direction: str,
    close: float,
    scenario_name: str,
) -> float:
    """Destek alti / direnc ustu mesafe ($). Senaryo kiriliminda close derinligi de sayilir."""
    if ref <= 0:
        return 0.0
    d = direction.upper()
    gap = 0.0
    if d == "SHORT":
        gap = max(0.0, ref - px)
        if gap <= 0 and scenario_name == "BREAKOUT_SELL" and close > 0:
            gap = max(gap, ref - close)
    else:
        gap = max(0.0, px - ref)
        if gap <= 0 and scenario_name == "BREAKOUT_BUY" and close > 0:
            gap = max(gap, close - ref)
    return gap


def _exp_break_decay(break_distance: float) -> float:
    scale = float(getattr(cfg, "V3_BREAK_DECAY_EXP_SCALE", 1.0) or 1.0)
    floor = float(getattr(cfg, "V3_BREAK_DECAY_FLOOR", 0.08) or 0.08)
    if break_distance <= 0:
        return 1.0
    return max(floor, math.exp(-break_distance * scale))


def _break_asymmetry(
    *,
    px: float,
    ref_s: float,
    ref_r: float,
    scenario: dict,
    levels: dict,
    trend_mode: bool,
    close: float,
) -> dict[str, Any]:
    """
    Kirilim yonune gore karsi taraf decay: exp(-gap/normalize_unit).
    Global — yalnizca BREAKOUT_* degil; fiyat seviye disindaysa da calisir.
    """
    scn = str(scenario.get("name") or "").upper()
    norm = _break_normalize_unit(px, ref_s, ref_r)
    active = levels.get("active") or {}

    gap_s = _break_gap_price(px, ref_s, direction="SHORT", close=close, scenario_name=scn)
    gap_r = _break_gap_price(px, ref_r, direction="LONG", close=close, scenario_name=scn)

    if trend_mode and ref_s > 0 and px < ref_s * 0.999:
        gap_s = max(gap_s, ref_s - px)
    if trend_mode and ref_r > 0 and px > ref_r * 1.001:
        gap_r = max(gap_r, px - ref_r)
    if active.get("below_all_supports") or levels.get("below_all_supports"):
        if ref_s > 0:
            gap_s = max(gap_s, ref_s - px) if px < ref_s else gap_s
    if active.get("above_all_resistances") or levels.get("above_all_resistances"):
        if ref_r > 0:
            gap_r = max(gap_r, px - ref_r) if px > ref_r else gap_r

    dist_s = gap_s / norm if gap_s > 0 else 0.0
    dist_r = gap_r / norm if gap_r > 0 else 0.0

    long_decay = _exp_break_decay(dist_s)
    short_decay = _exp_break_decay(dist_r)

    favored = ""
    if dist_s > dist_r and dist_s > 0:
        favored = "SHORT"
    elif dist_r > dist_s and dist_r > 0:
        favored = "LONG"

    return {
        "long_decay": long_decay,
        "short_decay": short_decay,
        "break_dist_short": round(dist_s, 2),
        "break_dist_long": round(dist_r, 2),
        "norm_unit": round(norm, 2),
        "gap_short_usd": round(gap_s, 2),
        "gap_long_usd": round(gap_r, 2),
        "favored": favored,
        "scenario": scn,
        "source": "break",
    }


def _regime_asymmetry(
    *,
    ms: dict,
    bear: float,
    bull: float,
    px: float,
    ref_s: float,
    ref_r: float,
) -> dict[str, Any]:
    """
    Kirilim yokken rejim baskisi: bearish + destek ustunde bounce -> LONG counter modul decay.
    exp(-regime_distance), ATR yok — bear/bull guclu + rejection_watch + destek yakinligi.
    """
    collapse = ms.get("collapse") or {}
    min_st = float(getattr(cfg, "V3_REGIME_DECAY_MIN_STRENGTH", 0.42) or 0.42)
    scale = float(getattr(cfg, "V3_REGIME_DECAY_SCALE", 0.55) or 0.55)
    rej_b = float(getattr(cfg, "V3_REGIME_DECAY_REJECTION_BONUS", 0.28) or 0.28)
    ct_b = float(getattr(cfg, "V3_REGIME_DECAY_COUNTER_TREND_BONUS", 0.12) or 0.12)
    prox_band = float(getattr(cfg, "V3_REGIME_DECAY_PROXIMITY_BAND", 0.80) or 0.80)
    prox_w = float(getattr(cfg, "V3_REGIME_DECAY_PROXIMITY_WEIGHT", 1.15) or 1.15)
    norm = _break_normalize_unit(px, ref_s, ref_r)

    long_decay = short_decay = 1.0
    regime_dist_s = regime_dist_r = 0.0

    if bear >= min_st:
        proximity_dist = 0.0
        if collapse.get("rejection_watch"):
            proximity_dist += rej_b
        if collapse.get("counter_trend_only"):
            proximity_dist += ct_b
        if ref_s > 0 and px >= ref_s and norm > 0:
            ext = (px - ref_s) / norm
            if ext < prox_band:
                proximity_dist += (prox_band - ext) * prox_w
        if proximity_dist > 0:
            regime_dist = bear * scale + proximity_dist
            regime_dist_s = regime_dist
            long_decay = _exp_break_decay(regime_dist)

    if bull >= min_st:
        proximity_dist = 0.0
        if collapse.get("rejection_watch"):
            proximity_dist += rej_b
        if collapse.get("counter_trend_only"):
            proximity_dist += ct_b
        if ref_r > 0 and px <= ref_r and norm > 0:
            ext = (ref_r - px) / norm
            if ext < prox_band:
                proximity_dist += (prox_band - ext) * prox_w
        if proximity_dist > 0:
            regime_dist = bull * scale + proximity_dist
            regime_dist_r = regime_dist
            short_decay = _exp_break_decay(regime_dist)

    favored = ""
    if regime_dist_s > regime_dist_r and long_decay < 0.99:
        favored = "SHORT"
    elif regime_dist_r > regime_dist_s and short_decay < 0.99:
        favored = "LONG"

    return {
        "long_decay": long_decay,
        "short_decay": short_decay,
        "regime_dist_short": round(regime_dist_s, 2),
        "regime_dist_long": round(regime_dist_r, 2),
        "norm_unit": round(norm, 2),
        "favored": favored,
        "source": "regime",
        "rejection_watch": bool(collapse.get("rejection_watch")),
        "counter_trend_only": bool(collapse.get("counter_trend_only")),
    }


def _merge_score_asymmetry(
    break_a: dict[str, Any], regime_a: dict[str, Any]
) -> dict[str, Any]:
    """En guclu decay (min carpani) — kirilim ve rejim birlikte."""
    long_d = min(
        float(break_a.get("long_decay", 1) or 1),
        float(regime_a.get("long_decay", 1) or 1),
    )
    short_d = min(
        float(break_a.get("short_decay", 1) or 1),
        float(regime_a.get("short_decay", 1) or 1),
    )
    favored = break_a.get("favored") or regime_a.get("favored") or ""
    if long_d < short_d and long_d < 0.99:
        favored = favored or "SHORT"
    elif short_d < long_d and short_d < 0.99:
        favored = favored or "LONG"
    return {
        "long_decay": long_d,
        "short_decay": short_d,
        "break": break_a,
        "regime": regime_a,
        "break_dist_short": break_a.get("break_dist_short", 0),
        "break_dist_long": break_a.get("break_dist_long", 0),
        "regime_dist_short": regime_a.get("regime_dist_short", 0),
        "regime_dist_long": regime_a.get("regime_dist_long", 0),
        "norm_unit": break_a.get("norm_unit") or regime_a.get("norm_unit"),
        "favored": favored,
    }


def _apply_opposite_break_decay(
    pts: dict[str, float], decay: float
) -> tuple[dict[str, float], dict[str, float]]:
    """Karsi taraf counter-trend modullerini exp decay ile zayiflat. Donus: (yeni, deltas)."""
    if decay >= 0.9999:
        return pts, {}
    mods = _break_decay_module_set()
    out = dict(pts)
    deltas: dict[str, float] = {}
    for k in mods:
        v = float(out.get(k, 0) or 0)
        if v > 0:
            nv = v * decay
            deltas[k] = round(nv - v, 2)
            out[k] = nv
    return out, deltas


def _is_trend_continuation(levels: dict, ref_s: float, ref_r: float) -> bool:
    if str(levels.get("decision_mode") or "") == "TREND_CONTINUATION":
        return True
    px = float(levels.get("price") or effective_price() or state.mark_price or 0)
    active = levels.get("active") or {}
    if active.get("below_all_supports") or levels.get("below_all_supports"):
        return True
    if active.get("above_all_resistances") or levels.get("above_all_resistances"):
        return True
    if ref_s <= 0 and ref_r <= 0:
        return True
    min_unit = float(getattr(cfg, "V3_TREND_CONT_BREAK_MIN_UNIT", 0.12) or 0.12)
    norm = _break_normalize_unit(px, ref_s, ref_r)
    if ref_s > 0 and px > 0 and px < ref_s:
        if (ref_s - px) / norm >= min_unit:
            return True
    if ref_r > 0 and px > ref_r:
        if (px - ref_r) / norm >= min_unit:
            return True
    if ref_s > 0 and ref_r > ref_s and levels.get("range_valid"):
        return False
    if not levels.get("range_valid"):
        return True
    return False


def _score_side_executable(side: str, *, levels: dict, px: float, ref_s: float, ref_r: float) -> bool:
    """
    DirectionScore aksiyonu S/R geometrisini ezemez.
    Destek ustundeki bearish rejim sadece bias'tir; SHORT action degildir.
    """
    zone = str(levels.get("zone") or "").upper()
    active = levels.get("active") or {}
    side = side.upper()
    if side == "SHORT":
        if ref_s > 0 and px < ref_s:
            return True
        if active.get("below_all_supports") or levels.get("below_all_supports"):
            return True
        if zone == "NEAR_RESISTANCE" and (ref_r <= 0 or px <= ref_r):
            return True
        # Flipped direnç varsa (yakın %2 içinde) → executable
        if ref_r > 0 and px < ref_r and (ref_r - px) <= px * 0.02:
            return True
        # Bearish momentum + trade band — swing high flip ile
        ms = levels.get("market_state") or {}
        collapse = ms.get("collapse") or {}
        if (
            str(collapse.get("dominant_bias") or "").lower() in ("bear", "bearish")
            and str(collapse.get("mode") or "") in ("ACTIVE_BIAS", "STRUCTURE_CONTROLLED")
            and bool((ms.get("structure") or {}).get("is_lower_high"))
        ):
            return True
        return False
    if side == "LONG":
        if ref_r > 0 and px > ref_r:
            return True
        if active.get("above_all_resistances") or levels.get("above_all_resistances"):
            return True
        if zone == "NEAR_SUPPORT" and (ref_s <= 0 or px >= ref_s):
            return True
        # Flipped destek varsa (yakın %2 içinde) → executable
        if ref_s > 0 and px > ref_s and (px - ref_s) <= px * 0.02:
            return True
        return False
    return False


def _module_points(
    side: str,
    *,
    levels: dict,
    structure: dict,
    scenario: dict,
    cvd: dict,
    ms: dict,
    px: float,
    ref_s: float,
    ref_r: float,
    trend_mode: bool,
    bear: float,
    bull: float,
) -> dict[str, float]:
    side = side.upper()
    pts: dict[str, float] = {k: 0.0 for k in LOG_MODULES}
    zone = str(levels.get("zone") or "").upper()
    collapse = ms.get("collapse") or {}
    events = ms.get("events") or {}

    pts["structure"] = _structure_additive(side, bear, bull)

    bias = levels.get("liquidity_bias") or getattr(state, "v3_liquidity_bias", None) or {}
    b = str(bias.get("bias") or "NEUTRAL").upper()
    if side == "LONG" and b == "UP":
        pts["liquidity"] = 15.0
    elif side == "SHORT" and b == "DOWN":
        pts["liquidity"] = 15.0
    elif b != "NEUTRAL":
        pts["liquidity"] = 5.0

    if side == "LONG" and collapse.get("event_confirms_long"):
        pts["event"] = 20.0
    if side == "SHORT" and collapse.get("event_confirms_short"):
        pts["event"] = 20.0
    if float(events.get("decayed_score", 0) or 0) >= 40:
        dom = str(collapse.get("dominant_bias") or "").lower()
        if side == "LONG" and dom in ("bull", "bullish"):
            pts["event"] += 8.0
        if side == "SHORT" and dom in ("bear", "bearish"):
            pts["event"] += 8.0

    align = _cvd_alignment(cvd, side)
    pts["cvd"] = align * 12.0
    if side == "LONG" and str(cvd.get("direction") or "") == "BEAR":
        pts["cvd"] -= min(8.0, (0.5 - float(cvd.get("buy_ratio", 0.5) or 0.5)) * 16.0)
    if side == "SHORT" and str(cvd.get("direction") or "") == "BULL":
        pts["cvd"] -= min(8.0, (float(cvd.get("buy_ratio", 0.5) or 0.5) - 0.5) * 16.0)

    active = levels.get("active") or {}
    below_s = bool(active.get("below_all_supports") or levels.get("below_all_supports"))
    above_r = bool(active.get("above_all_resistances") or levels.get("above_all_resistances"))
    if side == "LONG":
        if zone == "NEAR_SUPPORT" and not (ref_s > 0 and px > 0 and px < ref_s):
            pts["zone"] = 18.0
        elif zone == "MID_RANGE" and not below_s:
            pts["zone"] = 5.0
    else:
        if zone == "NEAR_RESISTANCE" and not (ref_r > 0 and px > ref_r * 1.001):
            pts["zone"] = 18.0
        elif zone == "MID_RANGE" and not above_r:
            pts["zone"] = 5.0

    vac = int(getattr(state, "v3_vacuum_score", 0) or 0)
    if vac >= 50:
        if side == "SHORT":
            pts["volume"] = min(12.0, vac / 8.0)
        else:
            pts["volume"] = min(8.0, vac / 12.0)

    bars = bars_15m(3)
    close = float(bars[-1].get("close", 0) or 0) if bars else px
    if close <= 0:
        close = px
    pts["trend"] = _trend_level_points(
        side,
        levels=levels,
        px=px,
        ref_s=ref_s,
        ref_r=ref_r,
        close=close,
        scenario=scenario,
        trend_mode=trend_mode,
    )
    return _apply_structure_trend_overlap_discount(pts, side)


def _sum_modules(pts: dict[str, float]) -> float:
    return float(sum(pts.values()))


def _fmt_contrib(v: float) -> str:
    if v > 0:
        return f"+{v:.0f}"
    if v < 0:
        return f"{v:.0f}"
    return "0"


def _pct_of_total(part: float, total: float) -> float:
    if total <= 0:
        return 0.0
    return 100.0 * max(0.0, part) / total


def _positive_module_total(mods: dict[str, Any]) -> float:
    return sum(max(0.0, float(mods.get(k, 0) or 0)) for k in LOG_MODULES)


class _AttributionRolling:
    """Son N karar: modul bazli ortalama pozitif katki %."""

    def __init__(self) -> None:
        self._samples: deque[dict[str, float]] = deque()

    def _window(self) -> int:
        return int(getattr(cfg, "V3_ATTRIBUTION_ROLLING_WINDOW", 1000) or 1000)

    def record(self, attrib: dict[str, Any]) -> dict[str, Any] | None:
        snap = _dominant_side_pct_snapshot(attrib)
        if not snap:
            return None
        win = max(1, self._window())
        if len(self._samples) >= win:
            self._samples.popleft()
        self._samples.append(snap)
        summary = self.summary()
        state.v3_score_attribution_rolling = summary
        return summary

    def summary(self) -> dict[str, Any]:
        n = len(self._samples)
        if n == 0:
            return {"count": 0}
        keys = list(LOG_MODULES)
        avg = {k: 0.0 for k in keys}
        st_sum = se_sum = 0.0
        for s in self._samples:
            for k in keys:
                avg[k] += float(s.get(k, 0) or 0)
            st_sum += float(s.get("structure_trend", 0) or 0)
            se_sum += float(s.get("structure_event", 0) or 0)
        for k in keys:
            avg[k] /= n
        st_avg = st_sum / n
        se_avg = se_sum / n
        return {
            "count": n,
            "window": self._window(),
            "avg_pct": {k: round(avg[k], 1) for k in keys},
            "structure_trend_combined_pct": round(st_avg, 1),
            "structure_event_combined_pct": round(se_avg, 1),
            "overlap_warn": st_avg >= 55.0,
            "structure_event_warn": se_avg >= 62.0,
        }


_rolling_attribution = _AttributionRolling()


def _dominant_side_pct_snapshot(attrib: dict[str, Any]) -> dict[str, float] | None:
    long_sub = float(attrib.get("long_subtotal", 0) or 0)
    short_sub = float(attrib.get("short_subtotal", 0) or 0)
    if short_sub >= long_sub:
        mods = attrib.get("short_modules") or {}
    else:
        mods = attrib.get("long_modules") or {}
    sub = _positive_module_total(mods)
    if sub <= 0:
        return None
    out: dict[str, float] = {}
    for k in LOG_MODULES:
        out[k] = _pct_of_total(float(mods.get(k, 0) or 0), sub)
    out["structure_trend"] = out.get("structure", 0) + out.get("trend", 0)
    out["structure_event"] = out.get("structure", 0) + out.get("event", 0)
    return out


def format_attribution_rolling_stats(summary: dict[str, Any]) -> str:
    n = int(summary.get("count", 0) or 0)
    if n <= 0:
        return "[ATTRIBUTION_ROLLING] henuz ornek yok"
    win = int(summary.get("window", 0) or 0)
    avg = summary.get("avg_pct") or {}
    st = float(summary.get("structure_trend_combined_pct", 0) or 0)
    lines = [
        f"[ATTRIBUTION_ROLLING] son {n} karar (pencere max {win})",
        "Ortalama pozitif katki (dominant taraf %):",
    ]
    for k in LOG_MODULES:
        v = float(avg.get(k, 0) or 0)
        lines.append(f"  {k.capitalize():12} {v:5.1f}%")
    lines.append(f"  {'Structure+Trend':12} {st:5.1f}%")
    se = float(summary.get("structure_event_combined_pct", 0) or 0)
    lines.append(f"  {'Structure+Event':12} {se:5.1f}%")
    if summary.get("structure_event_warn"):
        lines.append(
            "  UYARI: Structure+Event >= %62 — kirilim/rejim ayni olayda iki modul "
            "(structure_break + event skoru)"
        )
    elif summary.get("overlap_warn"):
        lines.append(
            "  UYARI: Structure+Trend >= %55 — ayni dump iki modulde yuksek pay"
        )
    elif st <= 48.0 and se <= 55.0:
        lines.append(
            "  Structure+Trend / Structure+Event orta band: moduller daha bagimsiz."
        )
    return "\n".join(lines)


def format_score_attribution(attrib: dict[str, Any]) -> str:
    """[SCORE_ATTRIBUTION] — katman bazli LONG/SHORT katki + pay %."""
    long_m = attrib.get("long_modules") or {}
    short_m = attrib.get("short_modules") or {}
    long_sub = float(attrib.get("long_subtotal", 0) or 0)
    short_sub = float(attrib.get("short_subtotal", 0) or 0)
    long_pos = _positive_module_total(long_m)
    short_pos = _positive_module_total(short_m)
    long_total = float(attrib.get("long_total", 0) or 0)
    short_total = float(attrib.get("short_total", 0) or 0)
    base = float(attrib.get("base", 0) or 0)

    lines = [
        "[ATTRIBUTION]",
        f"{'':14} {'LONG':>8} {'SHORT':>8}  (LONG% / SHORT% of positive side contrib)",
        f"{'base':14} {_fmt_contrib(base):>8} {_fmt_contrib(base):>8}",
    ]
    for key in LOG_MODULES:
        lv = float(long_m.get(key, 0) or 0)
        sv = float(short_m.get(key, 0) or 0)
        lp = _pct_of_total(lv, long_pos)
        sp = _pct_of_total(sv, short_pos)
        label = key.capitalize().ljust(14)
        lines.append(
            f"{label} {_fmt_contrib(lv):>8} {_fmt_contrib(sv):>8}  "
            f"({lp:.0f}% / {sp:.0f}%)"
        )

    lc = float(attrib.get("long_cvd_conf_delta", 0) or 0)
    sc = float(attrib.get("short_cvd_conf_delta", 0) or 0)
    if lc != 0 or sc != 0:
        lines.append(
            f"{'cvd_conf':14} {_fmt_contrib(lc):>8} {_fmt_contrib(sc):>8}  "
            f"(guven carpani, veto degil)"
        )
    lr = float(attrib.get("long_rr_delta", 0) or 0)
    sr = float(attrib.get("short_rr_delta", 0) or 0)
    if lr != 0 or sr != 0:
        lines.append(f"{'rr_adj':14} {_fmt_contrib(lr):>8} {_fmt_contrib(sr):>8}")

    lines.append(f"{'— subtotal':14} {_fmt_contrib(long_sub):>8} {_fmt_contrib(short_sub):>8}")
    lines.append("")
    lines.append(f"LONG={long_total:.0f}")
    lines.append(f"SHORT={short_total:.0f}")
    pl = float(attrib.get("prob_long_pct", 0) or 0)
    ps = float(attrib.get("prob_short_pct", 0) or 0)
    lines.append(f"prob_LONG={pl:.1f}%  prob_SHORT={ps:.1f}%  -> {attrib.get('action', 'WAIT')}")
    ba = attrib.get("score_asymmetry") or attrib.get("break_asymmetry") or {}
    if ba and (
        float(ba.get("long_decay", 1) or 1) < 0.99
        or float(ba.get("short_decay", 1) or 1) < 0.99
    ):
        br = ba.get("break") or {}
        rg = ba.get("regime") or {}
        bds = float(br.get("break_dist_short", 0) or 0)
        bdl = float(br.get("break_dist_long", 0) or 0)
        break_note = (
            "destek_alti_kirilim"
            if bds > 0.05
            else ("destek_ustu" if bds <= 0 else "—")
        )
        lines.append(
            f"score_decay LONG={float(ba.get('long_decay', 1) or 1):.2f} "
            f"SHORT={float(ba.get('short_decay', 1) or 1):.2f} "
            f"break_u={bds:.2f}/{bdl:.2f} ({break_note}) "
            f"regime_u={rg.get('regime_dist_short', 0)}/{rg.get('regime_dist_long', 0)} "
            f"norm={ba.get('norm_unit', 0)}$ favored={ba.get('favored') or '—'}"
        )
        if rg.get("rejection_watch"):
            lines.append("  regime: rejection_watch + destek/direnc yakinligi")
        ld = attrib.get("long_break_decay_delta") or {}
        sd = attrib.get("short_break_decay_delta") or {}
        if ld or sd:
            parts = []
            for k, dv in sorted({**ld, **sd}.items()):
                if dv:
                    parts.append(f"{k}{dv:+.0f}")
            if parts:
                lines.append(f"  decay_delta: {', '.join(parts)}")
    if attrib.get("structure_dominance_warn"):
        lines.append(
            f"UYARI: Structure tek taraf subtotal'in %{attrib['structure_dominance_warn']:.0f}'ini "
            f"olusturuyor (carpan yok, additive cap={_structure_max_contrib():.0f})"
        )
    dom_snap = _dominant_side_pct_snapshot(attrib)
    if dom_snap and dom_snap.get("structure_trend", 0) >= 55:
        lines.append(
            f"NOT: Structure+Trend bu kararda %{dom_snap['structure_trend']:.0f} "
            f"(Structure {dom_snap.get('structure', 0):.0f}% + "
            f"Trend {dom_snap.get('trend', 0):.0f}%)"
        )
    return "\n".join(lines)


def compute_probabilistic_decision(
    *,
    levels: dict,
    structure: dict,
    scenario: dict,
    cvd: dict,
    entry: dict | None = None,
) -> dict[str, Any]:
    ms = levels.get("market_state") or getattr(state, "v3_market_state", None) or {}
    px = float(
        levels.get("price") or effective_price() or state.mark_price or 0
    )
    ref_s = float(
        scenario.get("ref_support")
        or levels.get("active_support")
        or (levels.get("support") or {}).get("price")
        or 0
    )
    ref_r = float(
        scenario.get("ref_resistance")
        or levels.get("active_resistance")
        or (levels.get("resistance") or {}).get("price")
        or 0
    )
    trend_mode = _is_trend_continuation(levels, ref_s, ref_r)
    if trend_mode:
        levels = dict(levels)
        levels["decision_mode"] = "TREND_CONTINUATION"

    bear, bull = _structure_strengths(ms, structure)
    base = _base_score()

    long_mod = _module_points(
        "LONG",
        levels=levels,
        structure=structure,
        scenario=scenario,
        cvd=cvd,
        ms=ms,
        px=px,
        ref_s=ref_s,
        ref_r=ref_r,
        trend_mode=trend_mode,
        bear=bear,
        bull=bull,
    )
    short_mod = _module_points(
        "SHORT",
        levels=levels,
        structure=structure,
        scenario=scenario,
        cvd=cvd,
        ms=ms,
        px=px,
        ref_s=ref_s,
        ref_r=ref_r,
        trend_mode=trend_mode,
        bear=bear,
        bull=bull,
    )

    bars = bars_15m(2)
    close_px = float(bars[-1].get("close", 0) or 0) if bars else px
    if close_px <= 0:
        close_px = px

    score_asym: dict[str, Any] = {}
    long_decay_delta: dict[str, float] = {}
    short_decay_delta: dict[str, float] = {}
    decay_on = getattr(cfg, "V3_BREAK_DECAY_ENABLED", True) or getattr(
        cfg, "V3_REGIME_DECAY_ENABLED", True
    )
    if decay_on:
        break_a = (
            _break_asymmetry(
                px=px,
                ref_s=ref_s,
                ref_r=ref_r,
                scenario=scenario,
                levels=levels,
                trend_mode=trend_mode,
                close=close_px,
            )
            if getattr(cfg, "V3_BREAK_DECAY_ENABLED", True)
            else {"long_decay": 1.0, "short_decay": 1.0}
        )
        regime_a = (
            _regime_asymmetry(
                ms=ms,
                bear=bear,
                bull=bull,
                px=px,
                ref_s=ref_s,
                ref_r=ref_r,
            )
            if getattr(cfg, "V3_REGIME_DECAY_ENABLED", True)
            else {"long_decay": 1.0, "short_decay": 1.0}
        )
        score_asym = _merge_score_asymmetry(break_a, regime_a)
        long_mod, long_decay_delta = _apply_opposite_break_decay(
            long_mod, float(score_asym.get("long_decay", 1.0) or 1.0)
        )
        short_mod, short_decay_delta = _apply_opposite_break_decay(
            short_mod, float(score_asym.get("short_decay", 1.0) or 1.0)
        )

    long_sub = _sum_modules(long_mod)
    short_sub = _sum_modules(short_mod)
    long_score = base + long_sub
    short_score = base + short_sub

    pre_l, pre_s = long_score, short_score
    cvd_ml = _cvd_confidence_mult(cvd, "LONG")
    cvd_ms = _cvd_confidence_mult(cvd, "SHORT")
    long_score *= cvd_ml
    short_score *= cvd_ms
    long_cvd_conf_delta = long_score - pre_l
    short_cvd_conf_delta = short_score - pre_s

    # RR giris kalitesi — V3Decision RR_TOO_LOW; olasilik skorunu bozmaz (log/decision uyumu)
    long_rr_delta = short_rr_delta = 0.0
    _ = entry

    total = long_score + short_score
    if total <= 0:
        prob_long = prob_short = 0.5
    else:
        prob_long = long_score / total
        prob_short = short_score / total

    th = _prob_threshold()
    short_exec = _score_side_executable("SHORT", levels=levels, px=px, ref_s=ref_s, ref_r=ref_r)
    long_exec = _score_side_executable("LONG", levels=levels, px=px, ref_s=ref_s, ref_r=ref_r)
    score_bias = ""
    if prob_short >= th and short_exec:
        action = "SHORT"
        pick = "SHORT secildi"
    elif prob_long >= th and long_exec:
        action = "LONG"
        pick = "LONG secildi"
    elif prob_short >= th:
        action = "WAIT"
        score_bias = "SHORT_BIAS"
        pick = "SHORT_BIAS — executable S/R tezi yok"
    elif prob_long >= th:
        action = "WAIT"
        score_bias = "LONG_BIAS"
        pick = "LONG_BIAS — executable S/R tezi yok"
    else:
        action = "WAIT"
        pick = "EDGE YOK"

    dom_warn = 0.0
    warn_side = "SHORT" if short_score >= long_score else "LONG"
    warn_mod = short_mod if warn_side == "SHORT" else long_mod
    warn_sub = short_sub if warn_side == "SHORT" else long_sub
    struct_part = abs(float(warn_mod.get("structure", 0) or 0))
    if warn_sub > 0 and struct_part / warn_sub >= 0.70:
        dom_warn = 100.0 * struct_part / warn_sub

    rolling_summary = _rolling_attribution.record(
        {
            "long_modules": long_mod,
            "short_modules": short_mod,
            "long_subtotal": long_sub,
            "short_subtotal": short_sub,
        }
    )

    attrib = {
        "base": base,
        "long_modules": {k: round(v, 1) for k, v in long_mod.items()},
        "short_modules": {k: round(v, 1) for k, v in short_mod.items()},
        "long_subtotal": round(long_sub, 1),
        "short_subtotal": round(short_sub, 1),
        "long_cvd_conf_delta": round(long_cvd_conf_delta, 1),
        "short_cvd_conf_delta": round(short_cvd_conf_delta, 1),
        "long_rr_delta": round(long_rr_delta, 1),
        "short_rr_delta": round(short_rr_delta, 1),
        "long_total": round(long_score, 1),
        "short_total": round(short_score, 1),
        "prob_long_pct": round(prob_long * 100, 1),
        "prob_short_pct": round(prob_short * 100, 1),
        "action": action,
        "score_bias": score_bias,
        "short_executable": short_exec,
        "long_executable": long_exec,
        "structure_bear": round(bear, 2),
        "structure_bull": round(bull, 2),
        "structure_dominance_warn": round(dom_warn, 1) if dom_warn else 0,
        "rolling_summary": rolling_summary,
        "score_asymmetry": score_asym,
        "break_asymmetry": score_asym,
        "long_break_decay_delta": long_decay_delta,
        "short_break_decay_delta": short_decay_delta,
    }

    mode = "TREND_CONTINUATION" if trend_mode else "RANGE_BAND"
    return {
        "long_score": round(long_score, 1),
        "short_score": round(short_score, 1),
        "long_breakdown": dict(attrib["long_modules"]),
        "short_breakdown": dict(attrib["short_modules"]),
        "score_attribution": attrib,
        "prob_long": round(prob_long, 3),
        "prob_short": round(prob_short, 3),
        "prob_long_pct": attrib["prob_long_pct"],
        "prob_short_pct": attrib["prob_short_pct"],
        "action": action,
        "score_bias": score_bias,
        "pick_line": pick,
        "decision_mode": mode,
        "structure_bear": round(bear, 2),
        "structure_bull": round(bull, 2),
        "cvd_conf_long": round(cvd_ml, 3),
        "cvd_conf_short": round(cvd_ms, 3),
        "threshold": th,
        "trend_continuation": trend_mode,
    }


def compute_direction_scores_from_snap(snap: dict) -> dict[str, Any]:
    levels = snap.get("levels") or {}
    result = compute_probabilistic_decision(
        levels=levels,
        structure=snap.get("structure") or {},
        scenario=snap.get("scenario") or {},
        cvd=snap.get("cvd") or {},
        entry=None,
    )
    result["winner"] = result["action"] if result["action"] != "WAIT" else "NONE"
    result["verdict_tail"] = result["action"] if result["action"] != "WAIT" else "WAIT"
    result["edge"] = abs(result["long_score"] - result["short_score"])
    return result


def format_direction_score_block(scores: dict[str, Any]) -> str:
    ls = float(scores.get("long_score", 0) or 0)
    ss = float(scores.get("short_score", 0) or 0)
    pl = float(scores.get("prob_long_pct", 0) or 0)
    ps = float(scores.get("prob_short_pct", 0) or 0)
    mode = str(scores.get("decision_mode") or "")
    lines = [
        f"LONG_SCORE={ls:.0f}",
        f"SHORT_SCORE={ss:.0f}",
        f"prob_LONG={pl:.1f}% prob_SHORT={ps:.1f}% mode={mode}",
        "",
    ]
    pick = str(scores.get("pick_line") or "")
    if pick == "EDGE YOK":
        lines.append("EDGE YOK")
        lines.append("WAIT")
    elif "BIAS" in pick:
        lines.append(pick)
        lines.append("WAIT")
    else:
        lines.append(pick)
    return "\n".join(lines)


def maybe_log_direction_score(snap: dict, *, force: bool = False) -> dict[str, Any] | None:
    if not getattr(cfg, "V3_DIRECTION_SCORE_LOG_ENABLED", True):
        return None
    existing = snap.get("direction_scores")
    if existing and existing.get("long_score") is not None:
        scores = existing
    else:
        scores = compute_direction_scores_from_snap(snap)
    snap["direction_scores"] = scores
    snap["long_score"] = scores["long_score"]
    snap["short_score"] = scores["short_score"]
    snap["score_attribution"] = scores.get("score_attribution")
    snap["prob_long"] = scores.get("prob_long")
    snap["prob_short"] = scores.get("prob_short")

    global _last_log_key, _last_log_ts
    key = f"{scores['long_score']}|{scores['short_score']}|{scores.get('action')}"
    now = time.time()
    interval = float(getattr(cfg, "V3_DIRECTION_SCORE_LOG_SEC", 30) or 30)
    force_log = force or str(snap.get("action", "")).upper() in ("LONG", "SHORT")
    if not force_log and key == _last_log_key and (now - _last_log_ts) < interval:
        return scores

    _last_log_key = key
    _last_log_ts = now
    log.info("[DIR_SCORE]\n" + format_direction_score_block(scores))
    if getattr(cfg, "V3_SCORE_ATTRIBUTION_LOG", True):
        attrib = scores.get("score_attribution") or {}
        if attrib:
            log.info(format_score_attribution(attrib))
            roll = attrib.get("rolling_summary") or {}
            n = int(roll.get("count", 0) or 0)
            every = int(getattr(cfg, "V3_ATTRIBUTION_ROLLING_LOG_EVERY", 50) or 50)
            if roll and n > 0 and (force or (every > 0 and n % every == 0)):
                log.info(format_attribution_rolling_stats(roll))
    return scores
