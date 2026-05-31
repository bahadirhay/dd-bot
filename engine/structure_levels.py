"""
Yapı tabanlı SL/TP — invalidation + yakın swing (TP1) + extension/likidite (TP2).
"""
from __future__ import annotations

from typing import Any, List, Tuple

from core.config import cfg
from core.logger import get_logger

log = get_logger("StructLevels")


def _swing_prices(swing_list: list) -> List[float]:
    out: List[float] = []
    for h in swing_list or []:
        try:
            p = float(h.get("price", h) if isinstance(h, dict) else h)
        except (TypeError, ValueError):
            continue
        if p > 0:
            out.append(p)
    return out


def nearest_swing_below(price: float, swing_lows: list) -> float:
    """Girişe en yakın 15m dip (en yüksek fiyat < price)."""
    below = sorted(
        (p for p in _swing_prices(swing_lows) if p < price * 0.9995),
        reverse=True,
    )
    return below[0] if below else 0.0


def nearest_swing_above(price: float, swing_highs: list) -> float:
    """Girişe en yakın 15m tepe (en düşük fiyat > price)."""
    above = sorted(
        (p for p in _swing_prices(swing_highs) if p > price * 1.0005),
    )
    return above[0] if above else 0.0


def nearest_swing_above_min_bps(
    price: float, swing_highs: list, min_bps: float
) -> float:
    """Kırılım sonrası hedef: break seviyesinden en az min_bps uzak tepe."""
    if price <= 0 or min_bps <= 0:
        return nearest_swing_above(price, swing_highs)
    floor_px = price * (1.0 + min_bps / 10000.0)
    above = sorted(
        (p for p in _swing_prices(swing_highs) if p >= floor_px),
    )
    return above[0] if above else 0.0


def nearest_swing_below_min_bps(
    price: float, swing_lows: list, min_bps: float
) -> float:
    """SHORT kırılım sonrası: break altında en az min_bps uzak dip."""
    if price <= 0 or min_bps <= 0:
        return nearest_swing_below(price, swing_lows)
    ceil_px = price * (1.0 - min_bps / 10000.0)
    below = sorted(
        (p for p in _swing_prices(swing_lows) if p <= ceil_px),
        reverse=True,
    )
    return below[0] if below else 0.0


def next_swing_below(price: float, swing_lows: list) -> float:
    """Verilen fiyatın altındaki bir sonraki dip (TP2 için)."""
    below = sorted(
        (p for p in _swing_prices(swing_lows) if p < price * 0.9995),
        reverse=True,
    )
    return below[0] if below else 0.0


def next_swing_above(price: float, swing_highs: list) -> float:
    above = sorted(
        (p for p in _swing_prices(swing_highs) if p > price * 1.0005),
    )
    return above[0] if above else 0.0


def _liq_tp2_target(
    state: Any, direction: str, entry: float, below_price: float = 0.0
) -> float:
    clusters = list(getattr(state, "liq_top_clusters", None) or [])
    if not clusters or entry <= 0:
        return 0.0
    min_usd = float(getattr(cfg, "FS_TP2_LIQ_MIN_USD", 15_000.0))
    best = None
    for c in clusters:
        try:
            px = float(c.get("price", 0))
            usd = float(c.get("usd", 0))
        except (TypeError, ValueError):
            continue
        if px <= 0 or usd < min_usd:
            continue
        if direction == "LONG":
            if px <= entry or (below_price > 0 and px <= below_price):
                continue
            if best is None or px < best[0]:
                best = (px, usd)
        else:
            if px >= entry or (below_price > 0 and px >= below_price):
                continue
            if best is None or px > best[0]:
                best = (px, usd)
    return round(best[0], 2) if best else 0.0


def _apply_tp1_cap(
    direction: str,
    entry: float,
    tp1: float,
    tp2: float,
    *,
    quiet: bool = False,
) -> tuple[float, float]:
    from engine.structure_thresholds import tp1_max_distance_bps

    max_bps = tp1_max_distance_bps(entry, entry)
    if max_bps <= 0 or entry <= 0 or tp1 <= 0:
        return tp1, tp2

    if direction == "SHORT":
        floor_tp1 = entry * (1.0 - max_bps / 10000.0)
        if tp1 < floor_tp1:
            old = tp1
            tp1 = round(floor_tp1, 2)
            if not quiet:
                log.info(
                    f"TP1 yakinlastirildi: {old:.2f} -> {tp1:.2f} (max {max_bps:.0f}bps)"
                )
            if tp2 >= tp1:
                tp2 = _default_tp2_below(direction, entry, tp1, 0.0, 0.0, None)
    else:
        ceil_tp1 = entry * (1.0 + max_bps / 10000.0)
        if tp1 > ceil_tp1:
            old = tp1
            tp1 = round(ceil_tp1, 2)
            if not quiet:
                log.info(
                    f"TP1 yakinlastirildi: {old:.2f} -> {tp1:.2f} (max {max_bps:.0f}bps)"
                )
            if tp2 <= tp1:
                tp2 = _default_tp2_below(direction, entry, tp1, 0.0, 0.0, None)
    return tp1, tp2


def _ensure_tp1_min_rr(
    direction: str,
    entry: float,
    sl: float,
    tp1: float,
    min_rr: float,
) -> float:
    """TP1 çok yakınsa min R:R kadar aç."""
    risk = abs(entry - sl)
    if risk <= 0 or tp1 <= 0 or min_rr <= 0:
        return tp1
    reward = abs(tp1 - entry)
    if reward / risk >= min_rr:
        return tp1
    if direction == "LONG":
        return round(entry + risk * min_rr * 1.005, 2)
    return round(entry - risk * min_rr * 1.005, 2)


def _finalize_breakout_tp1(
    direction: str,
    entry: float,
    sl: float,
    tp1: float,
    min_rr: float,
) -> float:
    """
    Kırılım TP1: önce min R:R, sonra bps cap — cap R:R'ı bozuyorsa cap uygulanmaz.
    Geç girişte (fiyat seviyeden uzak) yapısal SL geniş kalır; TP1 swing hedefine açılır.
    """
    tp1 = _ensure_tp1_min_rr(direction, entry, sl, tp1, min_rr)
    risk = abs(entry - sl)
    if risk <= 0:
        return tp1
    capped, _ = _apply_tp1_cap(direction, entry, tp1, 0.0, quiet=True)
    if abs(capped - entry) / risk >= min_rr:
        if capped != tp1:
            log.info(
                f"TP1 cap uygulandi: {tp1:.2f} -> {capped:.2f} "
                f"(R:R={abs(capped - entry) / risk:.2f})"
            )
        return capped
    if capped != tp1:
        log.info(
            f"TP1 cap atlandi (R:R korunuyor): hedef {tp1:.2f}, "
            f"cap {capped:.2f} min {min_rr:.1f} gerekirdi"
        )
    return tp1


def _default_tp2_below(
    direction: str,
    entry: float,
    tp1: float,
    support: float,
    resistance: float,
    state: Any | None,
) -> float:
    ext = float(getattr(cfg, "BREAK_TP2_MIN_EXTENSION_BPS", 100)) / 10000.0
    buf = float(getattr(cfg, "RANGE_TP_BUFFER_BPS", 12)) / 10000.0
    if direction == "SHORT":
        base = tp1 * (1.0 - ext)
        if support > 0 and support < tp1:
            base = min(base, support * (1.0 + buf))
        return round(base, 2)
    base = tp1 * (1.0 + ext)
    if resistance > 0 and resistance > tp1:
        base = max(base, resistance * (1.0 - buf))
    return round(base, 2)


def _resolve_tp2(
    direction: str,
    entry: float,
    tp1: float,
    support: float,
    resistance: float,
    state: Any,
    swing_highs: list,
    swing_lows: list,
) -> float:
    """TP2: likidite / sonraki swing / band ucu — TP1'den min mesafe."""
    min_ext = float(getattr(cfg, "BREAK_TP2_MIN_EXTENSION_BPS", 100)) / 10000.0
    buf = float(getattr(cfg, "RANGE_TP_BUFFER_BPS", 12)) / 10000.0
    cands: list[float] = []

    if direction == "SHORT":
        floor2 = tp1 * (1.0 - min_ext)
        liq = _liq_tp2_target(state, direction, entry, below_price=tp1)
        if liq > 0 and liq < tp1:
            cands.append(liq)
        swing2 = next_swing_below(tp1, swing_lows)
        if swing2 > 0 and swing2 < tp1:
            cands.append(swing2)
        if support > 0 and support < floor2:
            cands.append(support * (1.0 + buf))
        cands.append(floor2)
        valid = [c for c in cands if c > 0 and c < tp1 * (1.0 - 0.0001)]
        return round(min(valid), 2) if valid else round(floor2, 2)

    ceil2 = tp1 * (1.0 + min_ext)
    liq = _liq_tp2_target(state, direction, entry, below_price=0)
    if liq > tp1:
        cands.append(liq)
    swing2 = next_swing_above(tp1, swing_highs)
    if swing2 > tp1:
        cands.append(swing2)
    if resistance > 0 and resistance > ceil2:
        cands.append(resistance * (1.0 - buf))
    cands.append(ceil2)
    valid = [c for c in cands if c > tp1 * (1.0 + 0.0001)]
    return round(max(valid), 2) if valid else round(ceil2, 2)


def _resolve_tp1_candidates(
    direction: str,
    entry: float,
    break_level: float,
    swing_highs: list,
    swing_lows: list,
) -> float:
    ext = float(getattr(cfg, "BREAK_TP1_EXTENSION_BPS", 30)) / 10000.0
    cands: list[float] = []

    if direction == "SHORT":
        if break_level > 0:
            bl_tp = break_level * (1.0 - ext)
            if bl_tp < entry:
                cands.append(bl_tp)
        sw = nearest_swing_below(entry, swing_lows)
        if 0 < sw < entry:
            cands.append(sw)
        if not cands:
            cands.append(entry * (1.0 - ext))
        return round(max(cands), 2)

    if break_level > entry:
        cands.append(break_level * (1.0 + ext))
    sw = nearest_swing_above(entry, swing_highs)
    if sw > entry:
        cands.append(sw)
    if not cands:
        cands.append(entry * (1.0 + ext))
    return round(min(cands), 2)


def calc_break_levels(
    direction: str,
    entry: float,
    break_level: float,
    support: float,
    resistance: float,
    state: Any,
) -> Tuple[float, float, float, float]:
    """
    Kırılım girişi: SL = kırılan seviye, TP1 = yakın hedef + cap, TP2 = extension.
    """
    from engine.structure_thresholds import sl_buffer_bps

    buf = entry * sl_buffer_bps(entry) / 10000.0
    min_risk = entry * 0.0015
    min_rr = float(getattr(cfg, "BREAK_TP1_MIN_RR", 1.2))
    sh = state.swing_highs_15m or []
    sl_list = state.swing_lows_15m or []

    if direction == "SHORT":
        inv = break_level if break_level > 0 else resistance
        sl = (inv + buf) if inv > entry else entry * 1.008
        sl = max(sl, entry + min_risk)
        tp1 = _resolve_tp1_candidates(direction, entry, inv, sh, sl_list)
    else:
        inv = break_level if break_level > 0 else support
        sl = (inv - buf) if 0 < inv < entry else entry * 0.992
        sl = min(sl, entry - min_risk)
        tp1 = _resolve_tp1_candidates(direction, entry, inv, sh, sl_list)

    entry = round(entry, 2)
    sl = round(sl, 2)
    tp1 = _finalize_breakout_tp1(
        direction, entry, sl, round(tp1, 2), min_rr
    )
    tp2 = _resolve_tp2(
        direction, entry, tp1, support, resistance, state, sh, sl_list
    )
    if direction == "SHORT" and tp2 >= tp1:
        tp2 = _default_tp2_below(direction, entry, tp1, support, resistance, state)
    elif direction == "LONG" and tp2 <= tp1:
        tp2 = round(tp1 * 1.008, 2)

    return entry, sl, round(tp1, 2), round(tp2, 2)


def calc_structure_levels(
    direction: str,
    entry: float,
    invalidation: float,
    tp1_target: float,
    state: Any,
) -> Tuple[float, float, float, float]:
    """Trend/sinyal — yapısal TP1 + cap + min R:R (cap öncelikli)."""
    from engine.structure_thresholds import sl_buffer_bps

    buf = entry * sl_buffer_bps(entry) / 10000.0
    min_risk = entry * 0.0015
    sh = state.swing_highs_15m or []
    sl_list = state.swing_lows_15m or []

    if direction == "LONG":
        sl = (invalidation - buf) if invalidation > 0 else entry * 0.992
        sl = min(sl, entry - min_risk)
        if tp1_target > entry:
            tp1 = tp1_target
        else:
            tp1 = nearest_swing_above(entry, sh) or entry * 1.004
    else:
        sl = (invalidation + buf) if invalidation > entry else entry * 1.008
        sl = max(sl, entry + min_risk)
        if 0 < tp1_target < entry:
            tp1 = tp1_target
        else:
            tp1 = nearest_swing_below(entry, sl_list) or entry * 0.996

    entry = round(entry, 2)
    sl = round(sl, 2)
    tp1 = round(tp1, 2)
    min_rr = float(cfg.MIN_RR)
    tp1 = _ensure_tp1_min_rr(direction, entry, sl, tp1, min_rr)
    tp1, _ = _apply_tp1_cap(direction, entry, tp1, 0.0)
    tp2 = _resolve_tp2(
        direction, entry, tp1, 0.0, 0.0, state, sh, sl_list
    )
    return entry, sl, tp1, tp2


def calc_trade_levels(
    direction: str, entry: float, state: Any
) -> Tuple[float, float, float, float]:
    from engine.structure_analyzer import invalidation_tp1_for_direction

    inv = float(getattr(state, "struct_invalidation", 0.0) or 0.0)
    tp1_tgt = float(getattr(state, "struct_tp1_target", 0.0) or 0.0)
    bias = getattr(state, "struct_bias_15m", "") or ""

    sh = [h["price"] for h in (state.swing_highs_15m or [])]
    sl = [l["price"] for l in (state.swing_lows_15m or [])]

    if direction == "LONG" and bias != "UP":
        inv, tp1_tgt = invalidation_tp1_for_direction(direction, entry, sh, sl)
    elif direction == "SHORT" and bias != "DOWN":
        inv, tp1_tgt = invalidation_tp1_for_direction(direction, entry, sh, sl)

    return calc_structure_levels(direction, entry, inv, tp1_tgt, state)


def recalc_open_position_tps(state: Any) -> Tuple[float, float]:
    """Açık pozisyon için güncel TP1/TP2 (kırılım kuralları + aktif seviyeler)."""
    if not state.in_position or state.pos_entry <= 0:
        return 0.0, 0.0
    pb = state.position_breakout or {}
    side = state.pos_side
    entry = float(state.pos_entry)

    from engine.breakout import get_active_levels

    lv = get_active_levels()
    s = float(
        pb.get("range_support")
        or pb.get("active_support")
        or lv.get("support")
        or 0
    )
    r = float(
        pb.get("range_resistance")
        or pb.get("active_resistance")
        or lv.get("resistance")
        or 0
    )
    bl = float(pb.get("break_level") or 0)
    if not bl:
        if side == "SHORT":
            bl = r if r > entry else (s if s > entry else 0.0)
        else:
            bl = s if 0 < s < entry else (r if r < entry else 0.0)

    if pb.get("break_mode") or bl > 0:
        _, _, tp1, tp2 = calc_break_levels(side, entry, bl, s, r, state)
        return tp1, tp2

    _, _, tp1, tp2 = calc_trade_levels(side, entry, state)
    return tp1, tp2


def cap_tp1_distance(
    direction: str, entry: float, tp1: float, tp2: float
) -> tuple[float, float]:
    """Geriye uyumluluk — position_sl / eski importlar."""
    tp1, tp2 = _apply_tp1_cap(direction, entry, tp1, tp2)
    if tp2 <= 0:
        return tp1, tp2
    min_ext = float(getattr(cfg, "BREAK_TP2_MIN_EXTENSION_BPS", 100)) / 10000.0
    if direction == "SHORT" and tp2 >= tp1:
        tp2 = round(tp1 * (1.0 - min_ext), 2)
    elif direction == "LONG" and tp2 <= tp1:
        tp2 = round(tp1 * (1.0 + min_ext), 2)
    return tp1, tp2
