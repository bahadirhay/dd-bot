"""
Katman 1 — Yapı (mmbot3 signals/structure_analyzer.py)
Swing kırılımı → invalidation (SL ref) + tp1_target (sonraki direnç/destek).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

from core.config import cfg

STRUCT_UP = "UP"
STRUCT_DOWN = "DOWN"
STRUCT_NEUTRAL = "NEUTRAL"


@dataclass
class StructureSnapshot:
    bias: str
    invalidation: float
    tp1_target: float
    last_swing_high: float = 0.0
    last_swing_low: float = 0.0


def _next_resistance(swing_highs: List[float], price: float) -> float:
    above = sorted(x for x in swing_highs if x > price * 1.0002)
    return above[0] if above else 0.0


def _next_support(swing_lows: List[float], price: float) -> float:
    below = sorted((x for x in swing_lows if x < price * 0.9998), reverse=True)
    return below[0] if below else 0.0


def update_structure(
    swing_highs: List[float],
    swing_lows: List[float],
    close: float,
    prev_bias: str,
) -> StructureSnapshot:
    sh = [float(x) for x in (swing_highs or []) if float(x) > 0]
    sl = [float(x) for x in (swing_lows or []) if float(x) > 0]
    if close <= 0 or not sh or not sl:
        return StructureSnapshot(STRUCT_NEUTRAL, 0.0, 0.0)

    eps = float(getattr(cfg, "FS_STRUCT_BREAK_BPS", 8.0)) / 10000.0
    from engine.structure_thresholds import sl_buffer_bps

    buf = sl_buffer_bps(close) / 10000.0

    last_sh = sh[-1]
    last_sl = sl[-1]
    prev_sh = sh[-2] if len(sh) >= 2 else last_sh
    prev_sl = sl[-2] if len(sl) >= 2 else last_sl

    broke_high = close > last_sh * (1.0 + eps)
    broke_low = close < last_sl * (1.0 - eps)
    new_high_vs_prev = close > prev_sh * (1.0 + eps)
    new_low_vs_prev = close < prev_sl * (1.0 - eps)

    bias = prev_bias or STRUCT_NEUTRAL

    if bias == STRUCT_UP:
        if broke_low:
            bias = STRUCT_NEUTRAL
        else:
            inv = last_sl * (1.0 - buf)
            tp1 = _next_resistance(sh, close) or last_sh
            return StructureSnapshot(STRUCT_UP, inv, tp1, last_sh, last_sl)

    if bias == STRUCT_DOWN:
        if broke_high:
            bias = STRUCT_NEUTRAL
        else:
            inv = last_sh * (1.0 + buf)
            tp1 = _next_support(sl, close) or last_sl
            return StructureSnapshot(STRUCT_DOWN, inv, tp1, last_sh, last_sl)

    if broke_high and new_high_vs_prev:
        inv = last_sl * (1.0 - buf)
        tp1 = _next_resistance(sh, close) or close * 1.005
        return StructureSnapshot(STRUCT_UP, inv, tp1, last_sh, last_sl)

    if broke_low and new_low_vs_prev:
        inv = last_sh * (1.0 + buf)
        tp1 = _next_support(sl, close) or close * 0.995
        return StructureSnapshot(STRUCT_DOWN, inv, tp1, last_sh, last_sl)

    return StructureSnapshot(STRUCT_NEUTRAL, 0.0, 0.0, last_sh, last_sl)


def invalidation_tp1_for_direction(
    direction: str,
    entry: float,
    swing_highs: List[float],
    swing_lows: List[float],
) -> tuple[float, float]:
    """Yön bazlı invalidation + TP1 hedefi (analyzer mantığı)."""
    from engine.structure_thresholds import sl_buffer_bps

    buf = sl_buffer_bps(close) / 10000.0
    sh = [float(x) for x in swing_highs if float(x) > 0]
    sl = [float(x) for x in swing_lows if float(x) > 0]

    if direction == "LONG" and sl:
        inv = sl[-1] * (1.0 - buf)
        tp1 = _next_resistance(sh, entry) or (sh[-1] if sh else entry * 1.005)
        return inv, tp1
    if direction == "SHORT" and sh:
        inv = sh[-1] * (1.0 + buf)
        tp1 = _next_support(sl, entry) or (sl[-1] if sl else entry * 0.995)
        return inv, tp1
    return 0.0, 0.0
