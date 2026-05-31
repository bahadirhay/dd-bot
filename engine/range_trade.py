"""
engine/range_trade.py — Kanal fade: seviye mesafesi (bps) + 1m red + CVD eğimi.

Band %30/70 YOK — destek/direnç yakınlığı ve chop filtresi.
TP1=mid, TP2=karşı band (buffer). ENTRY_MODE=range | hybrid.
"""
from __future__ import annotations

import time
from typing import Optional

from core.config import cfg
from core.state import state
from core.logger import get_logger
from engine.bars_1m import get_bars_1m
from engine.breakout import (
    get_active_levels,
    get_status_snapshot,
    feeds_ok,
    hybrid_continuation_candidate,
    hybrid_pressure_candidate,
    is_inside_band,
    level_on_cooldown,
    mark_level_entered,
    _proximity_ratio,
    _level_tests,
    _level_key,
)

log = get_logger("Range")

_last_status_log: float = 0.0


def _status_line(code: str, detail: str = "") -> str:
    return f"{code} — {detail}" if detail else code


def _bps_to_ratio(bps: float) -> float:
    return float(bps) / 10000.0


def _dist_bps(price: float, level: float) -> float:
    if level <= 0 or price <= 0:
        return 99999.0
    return _proximity_ratio(price, level) * 10000.0


def _band_pct(price: float, support: float, resistance: float) -> float:
    if resistance <= support:
        return 0.5
    return (price - support) / (resistance - support)


def _band_width_bps(price: float, support: float, resistance: float) -> float:
    if price <= 0 or resistance <= support:
        return 0.0
    return (resistance - support) / price * 10000.0


def _range_valid(price: float, support: float, resistance: float) -> tuple[bool, str]:
    if support <= 0 or resistance <= 0 or resistance <= support:
        return False, "seviye yok"
    from engine.structure_thresholds import range_min_width_bps

    w = _band_width_bps(price, support, resistance)
    min_w = range_min_width_bps(price)
    if w < min_w:
        return False, f"kanal dar ({w:.0f}bps < {min_w:.0f})"
    return True, ""


def _range_major_clearance_ok(
    direction: str,
    price: float,
    support: float,
    resistance: float,
    lv: dict,
) -> tuple[bool, str]:
    """
    Kanal trade'i majörler içinde serbesttir.
    Yalnızca kanalın dış kenarı hemen majör duvara yapışmışsa bloke et.
    """
    if price <= 0 or resistance <= support:
        return True, ""

    from engine.structure_thresholds import bar_noise_bps

    width_bps = _band_width_bps(price, support, resistance)
    min_clearance = max(bar_noise_bps(price) * 1.2, width_bps * 0.15, 12.0)
    d = (direction or "").upper()

    if d == "LONG":
        candidates = [
            float(lv.get("deep_major_resistance") or 0),
            float(lv.get("structural_major_resistance") or 0),
        ]
        cap = min(
            [v for v in candidates if v > resistance * 1.0002],
            default=0.0,
        )
        if cap > 0:
            gap = (cap - resistance) / resistance * 10000.0
            if gap <= min_clearance:
                return False, f"LONG için üst majör {cap:.2f} çok yakın ({gap:.0f}bps)"
        return True, ""

    candidates = [
        float(lv.get("deep_major_support") or 0),
        float(lv.get("structural_major_support") or 0),
    ]
    floor = max(
        [v for v in candidates if 0 < v < support * 0.9998],
        default=0.0,
    )
    if floor > 0:
        gap = (support - floor) / support * 10000.0
        if gap <= min_clearance:
            return False, f"SHORT için alt majör {floor:.2f} çok yakın ({gap:.0f}bps)"
    return True, ""


def _effective_proximity_bps(band_width_bps: float) -> float:
    """
    Mesafe eşiği = kanal genişliğine orantılı (referans genişlikte RANGE_PROXIMITY_BPS).
    Sabit 35→50 gevşetme değil; dar/geniş kanalda aynı göreli konum.
    """
    ref = float(getattr(cfg, "RANGE_REF_WIDTH_BPS", 300))
    base = float(getattr(cfg, "RANGE_PROXIMITY_BPS", 35))
    if band_width_bps <= 0 or ref <= 0:
        return base
    return base * (band_width_bps / ref)


def _scaled_bps(base_val: float, band_width_bps: float) -> float:
    ref = float(getattr(cfg, "RANGE_REF_WIDTH_BPS", 300))
    prox = float(getattr(cfg, "RANGE_PROXIMITY_BPS", 35))
    eff_prox = _effective_proximity_bps(band_width_bps)
    if prox <= 0:
        return base_val
    return base_val * (eff_prox / prox)


def _band_quartiles(support: float, resistance: float) -> tuple[float, float, float]:
    mid = (resistance + support) / 2.0
    zf = float(getattr(cfg, "RANGE_ZONE_FRAC", 0.25))
    zf = max(0.1, min(0.4, zf))
    width = resistance - support
    return support + zf * width, mid, resistance - zf * width


def _local_shelves(support: float, resistance: float) -> tuple[float, float]:
    """Son 1m mumlardan kanal içi mikro destek/direnç (hareketli raf)."""
    if not getattr(cfg, "RANGE_USE_LOCAL_SHELF", True):
        return 0.0, 0.0
    look = int(getattr(cfg, "RANGE_LOCAL_LOOKBACK", 12))
    bars = get_bars_1m(look)
    if len(bars) < 3:
        return 0.0, 0.0
    tol = (resistance - support) * 0.002
    valid = [
        b
        for b in bars
        if b["high"] <= resistance + tol and b["low"] >= support - tol
    ]
    if len(valid) < 3:
        valid = bars[-min(5, len(bars)) :]
    local_r = min(max(b["high"] for b in valid), resistance)
    local_s = max(min(b["low"] for b in valid), support)
    min_span = (resistance - support) * 0.08
    if local_r - local_s < min_span:
        return 0.0, 0.0
    return local_s, local_r


def _inner_swings(support: float, resistance: float, price: float) -> tuple[float, float]:
    """15m swing'lerden kanal içi en yakın tepe/dip."""
    if not getattr(cfg, "RANGE_USE_INNER_SWING", True):
        return 0.0, 0.0
    from engine.structure_levels import _swing_prices

    highs = [p for p in _swing_prices(state.swing_highs_15m) if support < p < resistance]
    lows = [p for p in _swing_prices(state.swing_lows_15m) if support < p < resistance]
    inner_r = 0.0
    inner_s = 0.0
    above = [p for p in highs if p >= price * 0.9995]
    below = [p for p in lows if p <= price * 1.0005]
    if above:
        inner_r = min(above)
    elif highs:
        inner_r = max(highs)
    if below:
        inner_s = max(below)
    elif lows:
        inner_s = min(lows)
    return inner_s, inner_r


def _recent_zone_rejection(
    support: float, resistance: float, direction: str
) -> tuple[bool, float, str]:
    """Üst/alt çeyrekte 1m red — fiyat geri çekilse bile hafıza."""
    if not getattr(cfg, "RANGE_ZONE_REJECTION", True):
        return False, 0.0, ""
    q25, _mid, q75 = _band_quartiles(support, resistance)
    wick_min = float(getattr(cfg, "RANGE_WICK_MIN", 0.45))
    look = int(getattr(cfg, "RANGE_LOCAL_LOOKBACK", 12))
    bars = get_bars_1m(look)
    for i, b in enumerate(reversed(bars)):
        age = i + 1
        if direction == "SHORT":
            if b["high"] < q75:
                continue
            if _wick_upper(b) >= wick_min and b["close"] <= b["open"]:
                return True, float(b["high"]), f"üst red {age}×1m"
        else:
            if b["low"] > q25:
                continue
            if _wick_lower(b) >= wick_min and b["close"] >= b["open"]:
                return True, float(b["low"]), f"alt red {age}×1m"
    return False, 0.0, ""


def _band_oscillation(support: float, resistance: float) -> int:
    """Mid çizgisini kaç kez kesti — kanal içi hareketlilik."""
    look = int(getattr(cfg, "RANGE_LOCAL_LOOKBACK", 12))
    bars = get_bars_1m(look)
    if len(bars) < 4:
        return 0
    mid = (resistance + support) / 2.0
    crosses = 0
    prev: str | None = None
    for b in bars:
        side = "a" if b["close"] >= mid else "b"
        if prev and side != prev:
            crosses += 1
        prev = side
    return crosses


def _nearest_level_dist(
    price: float,
    outer: float,
    local: float,
    inner: float,
) -> tuple[float, float, str]:
    """Sade model: range işlemi ana destek/direnç çizgilerinden yapılır."""
    return _dist_bps(price, outer), outer, "S/R"


def _resolve_side(
    price: float, support: float, resistance: float
) -> tuple[str, str, float, float, float, str]:
    """
    Yön: LONG | SHORT | wait | chop
    Mesafe: dış S/R + 1m raf + kanal içi swing + (varsa) bölge red.
    """
    width_bps = _band_width_bps(price, support, resistance)
    prox = _effective_proximity_bps(width_bps)
    chop_diff = _scaled_bps(float(getattr(cfg, "RANGE_CHOP_DIFF_BPS", 15)), width_bps)
    side_margin = _scaled_bps(float(getattr(cfg, "RANGE_SIDE_MARGIN_BPS", 8)), width_bps)
    center_min = _scaled_bps(float(getattr(cfg, "RANGE_CENTER_MIN_BPS", 28)), width_bps)

    local_s, local_r = _local_shelves(support, resistance)
    inner_s, inner_r = _inner_swings(support, resistance, price)

    ds, level_s, tag_s = _nearest_level_dist(price, support, local_s, inner_s)
    dr, level_r, tag_r = _nearest_level_dist(price, resistance, local_r, inner_r)

    active_level = 0.0
    active_tag = ""

    upper_red, shelf_h, red_h_msg = _recent_zone_rejection(support, resistance, "SHORT")
    lower_red, shelf_l, red_l_msg = _recent_zone_rejection(support, resistance, "LONG")
    bp = _band_pct(price, support, resistance)
    q25, mid, q75 = _band_quartiles(support, resistance)

    if upper_red and shelf_h > 0:
        d_red = _dist_bps(price, shelf_h)
        if d_red < dr:
            dr, level_r, tag_r = d_red, shelf_h, red_h_msg
        if bp >= 0.55 and d_red <= prox * 1.15 and d_red + side_margin < ds:
            active_level, active_tag = shelf_h, red_h_msg
            return (
                "SHORT",
                f"üst bölge red → raf {shelf_h:.2f} ({d_red:.0f}bps, prox={prox:.0f})",
                ds,
                dr,
                active_level,
                active_tag,
            )

    if lower_red and shelf_l > 0:
        d_red = _dist_bps(price, shelf_l)
        if d_red < ds:
            ds, level_s, tag_s = d_red, shelf_l, red_l_msg
        if bp <= 0.45 and d_red <= prox * 1.15 and d_red + side_margin < dr:
            active_level, active_tag = shelf_l, red_l_msg
            return (
                "LONG",
                f"alt bölge red → raf {shelf_l:.2f} ({d_red:.0f}bps, prox={prox:.0f})",
                ds,
                dr,
                active_level,
                active_tag,
            )

    if ds <= prox and dr <= prox and abs(ds - dr) < chop_diff:
        return (
            "chop",
            f"iki seviye yakın ({tag_s}/{tag_r} {ds:.0f}/{dr:.0f}bps prox={prox:.0f})",
            ds,
            dr,
            0.0,
            "",
        )

    if min(ds, dr) >= center_min and abs(ds - dr) < chop_diff:
        osc = _band_oscillation(support, resistance)
        extra = f" salınım={osc}" if osc >= 2 else ""
        return (
            "wait",
            f"ortada ({tag_s} {ds:.0f} / {tag_r} {dr:.0f} bps, prox={prox:.0f}){extra}",
            ds,
            dr,
            0.0,
            "",
        )

    if ds <= prox and ds + side_margin < dr:
        active_level, active_tag = level_s, tag_s
        return (
            "LONG",
            f"{tag_s} yakın {ds:.0f}bps (R {dr:.0f} prox={prox:.0f})",
            ds,
            dr,
            active_level,
            active_tag,
        )

    if dr <= prox and dr + side_margin < ds:
        active_level, active_tag = level_r, tag_r
        return (
            "SHORT",
            f"{tag_r} yakın {dr:.0f}bps (S {ds:.0f} prox={prox:.0f})",
            ds,
            dr,
            active_level,
            active_tag,
        )

    if ds <= prox * 1.2 and ds < dr:
        active_level, active_tag = level_s, tag_s
        return (
            "LONG",
            f"{tag_s} tercih {ds:.0f}<{dr:.0f} bps",
            ds,
            dr,
            active_level,
            active_tag,
        )

    if dr <= prox * 1.2 and dr < ds:
        active_level, active_tag = level_r, tag_r
        return (
            "SHORT",
            f"{tag_r} tercih {dr:.0f}<{ds:.0f} bps",
            ds,
            dr,
            active_level,
            active_tag,
        )

    osc = _band_oscillation(support, resistance)
    if osc >= 2 and bp >= 0.7 and upper_red:
        return (
            "wait",
            f"üst salınım (raf {level_r:.2f}, şimdi {dr:.0f}bps) — skor beklenir",
            ds,
            dr,
            level_r,
            tag_r,
        )
    if osc >= 2 and bp <= 0.3 and lower_red:
        return (
            "wait",
            f"alt salınım (raf {level_s:.2f}, şimdi {ds:.0f}bps) — skor beklenir",
            ds,
            dr,
            level_s,
            tag_s,
        )

    return (
        "wait",
        f"seviyeye uzak ({tag_s} {ds:.0f} / {tag_r} {dr:.0f} bps, prox={prox:.0f})",
        ds,
        dr,
        0.0,
        "",
    )


def _range_tp1(
    direction: str,
    entry: float,
    support: float,
    resistance: float,
    width_bps: float,
    edge_conf: float,
) -> float:
    """Dar/zayıf bandda mid yerine yapısal ilk hedef (quartile / iç swing)."""
    q25, mid, q75 = _band_quartiles(support, resistance)
    inner_s, inner_r = _inner_swings(support, resistance, entry)
    from engine.structure_thresholds import range_min_width_bps

    px = entry or float(state.mark_price or state.price or 0)
    min_w = range_min_width_bps(px)
    narrow = width_bps < min_w * 1.2 or edge_conf < 0.58

    if direction == "LONG":
        if narrow and inner_r > entry:
            return inner_r
        if narrow:
            return q75
        return mid
    if narrow and inner_s > 0 and inner_s < entry:
        return inner_s
    if narrow:
        return q25
    return mid


def _calc_range_sl_tp(
    direction: str,
    entry: float,
    support: float,
    resistance: float,
    *,
    width_bps: float = 0.0,
    edge_conf: float = 1.0,
) -> tuple[float, float, float]:
    break_bps = float(getattr(cfg, "FS_STRUCT_BREAK_BPS", 8.0))
    buf_bps = float(getattr(cfg, "RANGE_TP_BUFFER_BPS", 12.0))
    if width_bps <= 0 and entry > 0 and resistance > support:
        width_bps = _band_width_bps(entry, support, resistance)

    if direction == "LONG":
        sl = support * (1.0 - _bps_to_ratio(break_bps))
        tp1 = _range_tp1(direction, entry, support, resistance, width_bps, edge_conf)
        tp2 = resistance * (1.0 - _bps_to_ratio(buf_bps))
    else:
        sl = resistance * (1.0 + _bps_to_ratio(break_bps))
        tp1 = _range_tp1(direction, entry, support, resistance, width_bps, edge_conf)
        tp2 = support * (1.0 + _bps_to_ratio(buf_bps))
    return sl, tp1, tp2


def _cvd_slope(sec: float | None = None) -> float:
    """Son N sn CVD 5m eğimi (metrics_history)."""
    look = float(sec or getattr(cfg, "RANGE_CVD_SLOPE_SEC", 90))
    now = time.time()
    pts = [h for h in state.metrics_history if now - h["ts"] <= look]
    if len(pts) < 2:
        return 0.0
    return float(pts[-1]["cvd"]) - float(pts[0]["cvd"])


def _wick_lower(c: dict) -> float:
    rng = max(c["high"] - c["low"], 1e-9)
    body_bot = min(c["open"], c["close"])
    return max(0.0, (body_bot - c["low"]) / rng)


def _wick_upper(c: dict) -> float:
    rng = max(c["high"] - c["low"], 1e-9)
    body_top = max(c["open"], c["close"])
    return max(0.0, (c["high"] - body_top) / rng)


def _score_1m_long(support: float, effective: float = 0.0) -> tuple[int, list[str]]:
    level = effective if effective > 0 else support
    bars = get_bars_1m(int(getattr(cfg, "RANGE_1M_LOOKBACK", 5)))
    if len(bars) < 2:
        return 0, ["1m az"]

    reasons: list[str] = []
    score = 0
    last, prev = bars[-1], bars[-2]
    from engine.structure_thresholds import level_touch_bps

    tol = level * _bps_to_ratio(level_touch_bps(level, state.price or state.mark_price))

    if last["close"] > last["open"]:
        score += 10
        reasons.append("1m yeşil")
    if _wick_lower(last) >= float(getattr(cfg, "RANGE_WICK_MIN", 0.45)):
        score += 12
        reasons.append(f"alt fitil {_wick_lower(last):.0%}")
    if prev["low"] < level and last["close"] > level:
        score += 15
        reasons.append("raf altı red")
    if abs(last["low"] - level) <= tol or abs(prev["low"] - level) <= tol:
        score += 8
        reasons.append("raf dokunuş")

    return min(30, score), reasons


def _score_1m_short(resistance: float, effective: float = 0.0) -> tuple[int, list[str]]:
    level = effective if effective > 0 else resistance
    bars = get_bars_1m(int(getattr(cfg, "RANGE_1M_LOOKBACK", 5)))
    if len(bars) < 2:
        return 0, ["1m az"]

    reasons: list[str] = []
    score = 0
    last, prev = bars[-1], bars[-2]
    from engine.structure_thresholds import level_touch_bps

    tol = level * _bps_to_ratio(level_touch_bps(level, state.price or state.mark_price))

    if last["close"] < last["open"]:
        score += 10
        reasons.append("1m kırmızı")
    if _wick_upper(last) >= float(getattr(cfg, "RANGE_WICK_MIN", 0.45)):
        score += 12
        reasons.append(f"üst fitil {_wick_upper(last):.0%}")
    if prev["high"] > level and last["close"] < level:
        score += 15
        reasons.append("raf üstü red")
    if abs(last["high"] - level) <= tol or abs(prev["high"] - level) <= tol:
        score += 8
        reasons.append("raf dokunuş")

    return min(30, score), reasons


def _score_pulse(direction: str) -> tuple[int, list[str]]:
    n = int(getattr(cfg, "PULSE_BARS_1M", 15))
    bars = get_bars_1m(n)
    if len(bars) < 4:
        return 0, []

    o0 = bars[0]["open"]
    c1 = bars[-1]["close"]
    if o0 <= 0:
        return 0, []
    chg_bps = (c1 - o0) / o0 * 10000.0
    greens = sum(1 for b in bars if b["close"] >= b["open"])
    ratio = greens / len(bars)

    reasons: list[str] = []
    score = 0
    from engine.structure_thresholds import pulse_min_bps

    pulse_min = pulse_min_bps(state.price or state.mark_price)
    if direction == "LONG":
        if chg_bps > pulse_min:
            score += 10
            reasons.append(f"nabız {chg_bps:+.0f}bps")
        if ratio >= 0.55:
            score += 8
            reasons.append(f"yeşil {ratio:.0%}")
    else:
        if chg_bps < -pulse_min:
            score += 10
            reasons.append(f"nabız {chg_bps:+.0f}bps")
        if ratio <= 0.45:
            score += 8
            reasons.append(f"kırmızı {1-ratio:.0%}")

    return min(18, score), reasons


def _score_flow(direction: str) -> tuple[int, list[str]]:
    from engine.structure_thresholds import cvd_slope_min_for_range, taker_min_for_range

    slope = _cvd_slope()
    taker = state.taker_ratio
    px = float(state.mark_price or state.price or 0)
    min_slope = cvd_slope_min_for_range(px)
    taker_min = taker_min_for_range(direction)
    reasons: list[str] = []
    score = 0

    if direction == "LONG":
        if slope >= min_slope:
            score += 18
            reasons.append(f"CVD↑ {slope:+.0f}/{getattr(cfg, 'RANGE_CVD_SLOPE_SEC', 90):.0f}s")
        elif slope > 0:
            score += 8
            reasons.append(f"CVD hafif↑ {slope:+.0f}")
        if taker >= taker_min:
            score += 12
            reasons.append(f"taker {taker:.0%}")
        elif taker >= 0.52:
            score += 5
    else:
        if slope <= -min_slope:
            score += 18
            reasons.append(f"CVD↓ {slope:+.0f}")
        elif slope < 0:
            score += 8
            reasons.append(f"CVD hafif↓ {slope:+.0f}")
        if taker <= 1.0 - taker_min:
            score += 12
            reasons.append(f"satış {1-taker:.0%}")
        elif taker <= 0.48:
            score += 5

    return min(30, score), reasons


def _score_distance(
    direction: str, ds: float, dr: float, *, prox_bps: float = 0.0
) -> tuple[int, list[str]]:
    prox = prox_bps or float(getattr(cfg, "RANGE_PROXIMITY_BPS", 35))
    d = ds if direction == "LONG" else dr
    if d > prox:
        return 0, []
    pts = int(max(8, 22 * (1.0 - d / prox)))
    return pts, [f"mesafe {d:.0f}/{prox:.0f}bps"]


def _range_entry_threshold(
    band_width_bps: float,
    chop_crosses: int,
    edge_confidence: float,
) -> float:
    from engine.structure_thresholds import range_min_score

    return range_min_score(band_width_bps, chop_crosses, edge_confidence)


def _range_htf_archetype_ok(
    direction: str,
    side: str,
    active_tag: str,
    *,
    lower_red: bool = False,
    upper_red: bool = False,
) -> tuple[bool, str]:
    """Sade destek/direnç range modeli: kanal içinde iki yön de trade edilebilir."""
    return True, ""


def _build_score(
    direction: str,
    price: float,
    support: float,
    resistance: float,
    ds: float,
    dr: float,
    *,
    active_level: float = 0.0,
    prox_bps: float = 0.0,
    edge_confidence: float = 0.65,
) -> tuple[int, list[str], float, float]:
    """Toplam skor (0–100) + edge_p, flow_p — geçiş: edge×flow eşiği."""
    reasons: list[str] = []
    eff = active_level or (support if direction == "LONG" else resistance)

    edge_pts = 0
    flow_pts = 0
    for part in (
        _score_distance(direction, ds, dr, prox_bps=prox_bps),
        _score_1m_long(support, eff)
        if direction == "LONG"
        else _score_1m_short(resistance, eff),
    ):
        edge_pts += part[0]
        reasons.extend(part[1])

    failed = int(_level_tests.get(_level_key(eff), {}).get("failed", 0))
    if failed > 0:
        edge_pts += min(12, 6 + failed * 3)
        reasons.append(f"fail={failed}")

    edge_pts += int(min(15, edge_confidence * 18))
    if edge_confidence >= 0.65:
        reasons.append(f"kenar güven {edge_confidence:.2f}")

    for part in (_score_pulse(direction), _score_flow(direction)):
        flow_pts += part[0]
        reasons.extend(part[1])

    if direction == "LONG" and state.oi_rising:
        flow_pts += 5
        reasons.append("OI↑")
    if direction == "SHORT" and not state.oi_rising:
        flow_pts += 5
        reasons.append("OI↓")

    edge_max = 70.0
    flow_max = 48.0
    edge_p = min(1.0, edge_pts / edge_max)
    flow_p = min(1.0, flow_pts / flow_max)
    composite = edge_p * flow_p * 100.0
    return min(100, int(round(composite))), reasons, edge_p, flow_p


def get_range_snapshot(price: float = 0.0) -> dict:
    px = price or state.mark_price or state.price or 0.0
    lv = get_active_levels()
    s = float(lv.get("tactical_floor_support") or lv.get("support", 0))
    r = float(lv.get("tactical_cap_resistance") or lv.get("resistance", 0))
    structural_s = float(lv.get("structural_major_support", 0) or 0)
    structural_r = float(lv.get("structural_major_resistance", 0) or 0)
    valid, msg = _range_valid(px, s, r)
    mid = (r + s) / 2.0 if r > s else 0.0

    side, side_msg, ds, dr = ("wait", "", 0.0, 0.0)
    active_level, active_tag = 0.0, ""
    width_bps = _band_width_bps(px, s, r) if valid else 0.0
    prox_eff = _effective_proximity_bps(width_bps) if valid else 0.0
    local_s, local_r = (0.0, 0.0)
    inner_s, inner_r = (0.0, 0.0)
    cont_dir = ""
    cont_msg = ""
    cont_level = 0.0
    pressure_dir = ""
    pressure_msg = ""
    pressure_level = 0.0
    if valid:
        if (
            getattr(cfg, "ENTRY_MODE", "break").lower() == "hybrid"
            and is_inside_band(px, s, r)
        ):
            cont_dir, cont_level, cont_msg = hybrid_continuation_candidate(px, s, r)
            if not cont_dir:
                pressure_dir, pressure_level, _pressure_inv, pressure_msg = hybrid_pressure_candidate(
                    px, s, r, lv
                )
        if cont_dir:
            side = "wait"
            side_msg = cont_msg or f"retest {cont_level:.2f}"
            active_level = cont_level
            active_tag = "breakout_retest"
        elif pressure_dir:
            side = "wait"
            side_msg = pressure_msg
            active_level = pressure_level
            active_tag = "pressure"
        else:
            side, side_msg, ds, dr, active_level, active_tag = _resolve_side(px, s, r)
        local_s, local_r = _local_shelves(s, r)
        inner_s, inner_r = _inner_swings(s, r, px)

    lv = get_active_levels(px)
    edge_conf = float(lv.get("min_edge_confidence", 0) or 0.65)
    chop = _band_oscillation(s, r) if valid else 0
    th_long = _range_entry_threshold(width_bps, chop, edge_conf)
    th_short = th_long

    long_sc, long_r, long_ep, long_fp = (0, [], 0.0, 0.0)
    short_sc, short_r, short_ep, short_fp = (0, [], 0.0, 0.0)
    if valid and side == "LONG":
        long_sc, long_r, long_ep, long_fp = _build_score(
            "LONG",
            px,
            s,
            r,
            ds,
            dr,
            active_level=active_level,
            prox_bps=prox_eff,
            edge_confidence=edge_conf,
        )
    elif valid and side == "SHORT":
        short_sc, short_r, short_ep, short_fp = _build_score(
            "SHORT",
            px,
            s,
            r,
            ds,
            dr,
            active_level=active_level,
            prox_bps=prox_eff,
            edge_confidence=edge_conf,
        )
    elif valid and side in ("wait", "chop"):
        long_sc, long_r, long_ep, long_fp = _build_score(
            "LONG", px, s, r, ds, dr, prox_bps=prox_eff, edge_confidence=edge_conf
        )
        short_sc, short_r, short_ep, short_fp = _build_score(
            "SHORT", px, s, r, ds, dr, prox_bps=prox_eff, edge_confidence=edge_conf
        )

    def _passes(sc: int, ep: float, fp: float, th: float) -> bool:
        min_ep = float(getattr(cfg, "RANGE_MIN_EDGE_P", 0.52))
        min_fp = float(getattr(cfg, "RANGE_MIN_FLOW_P", 0.45))
        prod = ep * fp * 100.0
        return sc >= th and ep >= min_ep and fp >= min_fp and prod >= th

    status_code = "KANAL_KAPALI"
    status_detail = ""
    if not valid:
        status_code = "KANAL_YOK"
        status_detail = msg
    elif cont_dir:
        status_code = "BREAKOUT_CONTINUE_LONG" if cont_dir == "LONG" else "BREAKOUT_CONTINUE_SHORT"
        status_detail = cont_msg or f"retest {cont_level:.2f}"
    elif pressure_dir:
        status_code = "PRESSURE_LONG" if pressure_dir == "LONG" else "PRESSURE_SHORT"
        status_detail = pressure_msg
    elif side == "chop":
        status_code = "CHOP"
        status_detail = side_msg
    elif side == "wait":
        status_code = "BAND_ICI_BEKLE"
        status_detail = side_msg
    elif side == "LONG":
        if _passes(long_sc, long_ep, long_fp, th_long):
            status_code = "TAKTIK_LONG_ADAY"
            status_detail = side_msg
        else:
            status_code = "TAKTIK_LONG_ZAYIF"
            status_detail = (
                f"{long_sc}/{th_long:.0f} edge={long_ep:.2f}xflow={long_fp:.2f}"
            )
    elif side == "SHORT":
        if _passes(short_sc, short_ep, short_fp, th_short):
            status_code = "TAKTIK_SHORT_ADAY"
            status_detail = side_msg
        else:
            status_code = "TAKTIK_SHORT_ZAYIF"
            status_detail = (
                f"{short_sc}/{th_short:.0f} edge={short_ep:.2f}xflow={short_fp:.2f}"
            )

    q25, _qmid, q75 = _band_quartiles(s, r) if r > s else (0.0, 0.0, 0.0)
    upper_red, _, red_h = _recent_zone_rejection(s, r, "SHORT")
    lower_red, _, red_l = _recent_zone_rejection(s, r, "LONG")
    return {
        "status": _status_line(status_code, status_detail),
        "status_code": status_code,
        "status_detail": status_detail,
        "side": side,
        "side_msg": side_msg,
        "active_level": active_level,
        "active_tag": active_tag,
        "dist_support_bps": round(ds, 1),
        "dist_resistance_bps": round(dr, 1),
        "prox_effective_bps": round(prox_eff, 1),
        "band_pct": round(_band_pct(px, s, r) * 100, 1) if r > s else 0,
        "support": s,
        "tactical_support": s,
        "structural_support": structural_s,
        "range_support": float(lv.get("range_support", 0) or s),
        "micro_support": float(lv.get("micro_support", 0) or 0),
        "deep_support": float(lv.get("deep_support", 0) or 0),
        "resistance": r,
        "tactical_resistance": r,
        "structural_resistance": structural_r,
        "mid": mid,
        "q25": q25,
        "q75": q75,
        "local_support": local_s,
        "local_resistance": local_r,
        "inner_swing_low": inner_s,
        "inner_swing_high": inner_r,
        "oscillation": _band_oscillation(s, r) if valid else 0,
        "upper_rejection": upper_red,
        "lower_rejection": lower_red,
        "rejection_note": red_h or red_l or "",
        "width_bps": round(width_bps, 1),
        "long_score": long_sc,
        "short_score": short_sc,
        "long_edge_p": round(long_ep, 2),
        "long_flow_p": round(long_fp, 2),
        "short_edge_p": round(short_ep, 2),
        "short_flow_p": round(short_fp, 2),
        "entry_threshold": round(th_long, 1),
        "edge_confidence": round(edge_conf, 3),
        "long_reasons": " | ".join(long_r[:5]),
        "short_reasons": " | ".join(short_r[:5]),
        "cvd_slope": round(_cvd_slope(), 0),
        "tp1_long": mid,
        "tp2_long": r * (1.0 - _bps_to_ratio(float(getattr(cfg, "RANGE_TP_BUFFER_BPS", 12)))) if r else 0,
        "tp1_short": mid,
        "tp2_short": s * (1.0 + _bps_to_ratio(float(getattr(cfg, "RANGE_TP_BUFFER_BPS", 12)))) if s else 0,
    }


def on_range_entry_filled(details: dict) -> None:
    direction = details.get("direction", "")
    s = float(details.get("range_support", 0))
    r = float(details.get("range_resistance", 0))

    if not direction or s <= 0 or r <= 0:
        return

    state.position_breakout = {
        "direction": direction,
        "entry_mode": "range",
        "range_mode": True,
        "sl_profile": "range_band",
        "break_level": s if direction == "LONG" else r,
        "range_support": s,
        "range_resistance": r,
        "structural_support": s,
        "structural_resistance": r,
        "active_support": s,
        "active_resistance": r,
        "tp1": float(details.get("tp1", 0)),
        "tp2": float(details.get("tp2", 0)),
        "tp1_break_confirmed": False,
        "tp1_reject_count": 0,
        "tp1_runner_ok": False,
        "entry_ts": time.time(),
    }
    log.info(
        f"Range pozisyon ({direction}): S={s:.2f} R={r:.2f}  "
        f"TP1={details.get('tp1'):.2f} TP2={details.get('tp2'):.2f}  SL={details.get('sl'):.2f}"
    )
    from engine.market_narrative import on_level_entered

    bl = s if direction == "LONG" else r
    on_level_entered(direction, bl)

    state.breakout_view = get_status_snapshot(state.price or state.mark_price)
    state.range_view = get_range_snapshot(state.price or state.mark_price)


def check_range_entry(price: float) -> Optional[dict]:
    global _last_status_log

    if price <= 0 or not cfg.AUTO_TRADE_ENABLED:
        return None

    from execution.executor import is_position_opening

    if is_position_opening():
        return None

    lv = get_active_levels()
    s = float(lv.get("tactical_floor_support") or lv.get("support", 0))
    r = float(lv.get("tactical_cap_resistance") or lv.get("resistance", 0))

    ok, msg = _range_valid(price, s, r)
    if not ok:
        state.no_entry_reason = _status_line("KANAL_YOK", msg)
        state.range_view = get_range_snapshot(price)
        return None

    if not is_inside_band(price, s, r):
        state.no_entry_reason = _status_line("KANAL_DISI", "kirilim sistemi")
        state.range_view = get_range_snapshot(price)
        return None

    if getattr(cfg, "ENTRY_MODE", "break").lower() == "hybrid":
        cont_dir, cont_level, cont_msg = hybrid_continuation_candidate(price, s, r)
        if cont_dir:
            code = "BREAKOUT_CONTINUE_LONG" if cont_dir == "LONG" else "BREAKOUT_CONTINUE_SHORT"
            state.no_entry_reason = _status_line(code, cont_msg or f"retest {cont_level:.2f}")
            state.range_view = get_range_snapshot(price)
            return None
        pressure_dir, pressure_level, _pressure_inv, pressure_msg = hybrid_pressure_candidate(
            price, s, r, lv
        )
        if pressure_dir:
            code = "PRESSURE_LONG" if pressure_dir == "LONG" else "PRESSURE_SHORT"
            state.no_entry_reason = _status_line(
                code,
                pressure_msg or f"pressure {pressure_level:.2f}",
            )
            state.range_view = get_range_snapshot(price)
            return None

    side, side_msg, ds, dr, active_level, active_tag = _resolve_side(price, s, r)
    state.range_view = get_range_snapshot(price)
    width_bps = _band_width_bps(price, s, r)
    prox_eff = _effective_proximity_bps(width_bps)

    if side in ("wait", "chop"):
        code = "CHOP" if side == "chop" else "BAND_ICI_BEKLE"
        state.no_entry_reason = _status_line(code, side_msg)
        return None

    ok_feed, feed_msg = feeds_ok()
    if not ok_feed:
        state.no_entry_reason = _status_line("FEED_BLOK", feed_msg)
        return None

    direction = side
    level = active_level if active_level > 0 else (s if direction == "LONG" else r)

    lower_red, _, _ = _recent_zone_rejection(s, r, "LONG")
    upper_red, _, _ = _recent_zone_rejection(s, r, "SHORT")
    ok_arch, arch_msg = _range_htf_archetype_ok(
        direction,
        side,
        active_tag,
        lower_red=lower_red,
        upper_red=upper_red,
    )
    if not ok_arch:
        state.no_entry_reason = _status_line("ARCHETYPE_BLOK", arch_msg)
        return None

    ok_room, room_msg = _range_major_clearance_ok(direction, price, s, r, lv)
    if not ok_room:
        state.no_entry_reason = _status_line("HEADROOM_YETERSIZ", room_msg)
        return None

    from engine.market_narrative import trade_entry_allowed, update_tick

    update_tick(price, r, s)
    ok_nar, nar_msg = trade_entry_allowed(direction, level, price)
    if not ok_nar:
        state.no_entry_reason = _status_line("NARRATIVE_BLOK", nar_msg)
        return None

    lv = get_active_levels(price)
    edge_conf = float(lv.get("min_edge_confidence", 0) or 0.65)
    chop = _band_oscillation(s, r)
    score, reasons, edge_p, flow_p = _build_score(
        direction,
        price,
        s,
        r,
        ds,
        dr,
        active_level=level,
        prox_bps=prox_eff,
        edge_confidence=edge_conf,
    )
    th = _range_entry_threshold(width_bps, chop, edge_conf)
    min_ep = float(getattr(cfg, "RANGE_MIN_EDGE_P", 0.52))
    min_fp = float(getattr(cfg, "RANGE_MIN_FLOW_P", 0.45))
    prod = edge_p * flow_p * 100.0
    if score < th or edge_p < min_ep or flow_p < min_fp or prod < th:
        code = "TAKTIK_LONG_ZAYIF" if direction == "LONG" else "TAKTIK_SHORT_ZAYIF"
        state.no_entry_reason = _status_line(
            code,
            f"skor={score}/{th:.0f} edge={edge_p:.2f}xflow={flow_p:.2f}={prod:.0f}"
            + (f" | {' | '.join(reasons)}" if reasons else ""),
        )
        now = time.time()
        if now - _last_status_log > 45:
            _last_status_log = now
            log.info(state.no_entry_reason)
        return None

    sl, tp1, tp2 = _calc_range_sl_tp(
        direction, price, s, r, width_bps=width_bps, edge_conf=edge_conf
    )
    sl_dist = abs(price - sl)
    tp1_dist = abs(tp1 - price)
    rr = tp1_dist / sl_dist if sl_dist > 0 else 0
    min_rr = float(getattr(cfg, "RANGE_MIN_RR", 1.2))
    if sl_dist <= 0 or rr < min_rr:
        state.no_entry_reason = _status_line(
            "RR_YETERSIZ", f"range rr={rr:.2f} min={min_rr}"
        )
        return None

    if direction == "LONG" and not (price < tp1 < tp2):
        state.no_entry_reason = _status_line("TP_HATA", "range long tp sirasi")
        return None
    if direction == "SHORT" and not (tp2 < tp1 < price):
        state.no_entry_reason = _status_line("TP_HATA", "range short tp sirasi")
        return None

    mark_level_entered(level)
    reason = (
        f"range {direction} {side_msg} skor={score}  "
        f"S={s:.2f} R={r:.2f} mid={tp1:.2f} band%{_band_pct(price,s,r)*100:.0f}  "
        f"cvdΔ={_cvd_slope():+.0f} taker={state.taker_ratio:.0%}  "
        f"{' | '.join(reasons)}"
    )

    log.info(
        f"RANGE ONAY: {direction} @ {price:.2f}  skor={score}  "
        f"SL={sl:.2f} TP1={tp1:.2f} TP2={tp2:.2f}  R:R={rr:.2f}"
    )

    details = {
        "direction": direction,
        "price": price,
        "signal_price": price,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "rr": round(rr, 2),
        "rr_tp2": round(abs(tp2 - price) / sl_dist, 2) if sl_dist > 0 else 0,
        "entry_reason": reason,
        "range_mode": True,
        "range_support": s,
        "range_resistance": r,
        "range_zone": side,
        "range_score": score,
        "dist_support_bps": ds,
        "dist_resistance_bps": dr,
        "range_active_level": level,
        "range_level_tag": active_tag,
        "prox_effective_bps": prox_eff,
    }
    if state.in_position and direction == state.pos_side:
        return None

    state.signal = direction
    state.signal_reason = reason
    state.no_entry_reason = ""
    return details
