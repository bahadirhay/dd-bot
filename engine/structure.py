from __future__ import annotations
"""
engine/structure.py — Swing tespiti + mmbot3 yapı analizi
"""
from collections import deque

from core.config import cfg
from core.state import state
from core.logger import get_logger
from engine.structure_analyzer import (
    STRUCT_DOWN,
    STRUCT_NEUTRAL,
    STRUCT_UP,
    update_structure,
)

log = get_logger("Structure")

_bars_15m: deque = deque(maxlen=200)
_bars_1h: deque = deque(maxlen=200)


def add_bar_15m(candle: dict):
    ts = float(candle.get("ts", 0) or 0)
    if _bars_15m and float(_bars_15m[-1].get("ts", 0) or 0) == ts:
        _bars_15m[-1] = candle
    elif not _bars_15m or ts > float(_bars_15m[-1].get("ts", 0) or 0):
        _bars_15m.append(candle)
    _update_structure_15m()


def add_bar_1h(candle: dict):
    ts = float(candle.get("ts", 0) or 0)
    if _bars_1h and float(_bars_1h[-1].get("ts", 0) or 0) == ts:
        _bars_1h[-1] = candle
    elif not _bars_1h or ts > float(_bars_1h[-1].get("ts", 0) or 0):
        _bars_1h.append(candle)
    _update_structure_1h()


def get_bars_15m(limit: int = 96) -> list:
    """Dashboard: son N adet 15m mum (Binance ile aynı zaman dilimi)."""
    if limit <= 0:
        return list(_bars_15m)
    return list(_bars_15m)[-limit:]


def get_bars_1h(limit: int = 48) -> list:
    """Son N adet 1h mum — makro yapı / anchor seviye."""
    if limit <= 0:
        return list(_bars_1h)
    return list(_bars_1h)[-limit:]


def _detect_swings(bars: list, lookback: int) -> tuple[list, list]:
    n = len(bars)
    highs, lows = [], []

    for i in range(lookback, n - lookback):
        window_h = [bars[j]["high"] for j in range(i - lookback, i + lookback + 1)]
        window_l = [bars[j]["low"] for j in range(i - lookback, i + lookback + 1)]

        if bars[i]["high"] == max(window_h):
            highs.append({"ts": bars[i]["ts"], "price": bars[i]["high"], "idx": i})

        if bars[i]["low"] == min(window_l):
            lows.append({"ts": bars[i]["ts"], "price": bars[i]["low"], "idx": i})

    return highs, lows


def _determine_structure(highs: list, lows: list) -> str:
    if len(highs) < 2 or len(lows) < 2:
        return "UNCLEAR"

    last_highs = highs[-3:]
    last_lows = lows[-3:]

    h_up = all(
        last_highs[i]["price"] > last_highs[i - 1]["price"]
        for i in range(1, len(last_highs))
    )
    l_up = all(
        last_lows[i]["price"] > last_lows[i - 1]["price"]
        for i in range(1, len(last_lows))
    )
    h_dn = all(
        last_highs[i]["price"] < last_highs[i - 1]["price"]
        for i in range(1, len(last_highs))
    )
    l_dn = all(
        last_lows[i]["price"] < last_lows[i - 1]["price"]
        for i in range(1, len(last_lows))
    )

    if h_up and l_up:
        return "UP"
    if h_dn and l_dn:
        return "DOWN"
    return "UNCLEAR"


def _apply_structure_snapshot(snap, timeframe: str):
    if timeframe == "15m":
        state.struct_bias_15m = snap.bias
        state.struct_invalidation = snap.invalidation
        state.struct_tp1_target = snap.tp1_target
        if snap.bias == STRUCT_UP:
            state.structure_15m = "UP"
        elif snap.bias == STRUCT_DOWN:
            state.structure_15m = "DOWN"
        else:
            state.structure_15m = _determine_structure(
                state.swing_highs_15m, state.swing_lows_15m
            )
    elif timeframe == "1h":
        if snap.bias == STRUCT_UP:
            state.structure_1h = "UP"
        elif snap.bias == STRUCT_DOWN:
            state.structure_1h = "DOWN"
        else:
            state.structure_1h = _determine_structure(
                state.swing_highs_1h, state.swing_lows_1h
            )


def _update_structure_15m():
    bars = list(_bars_15m)
    if len(bars) < cfg.SWING_LB_15M * 2 + 5:
        return
    highs, lows = _detect_swings(bars, cfg.SWING_LB_15M)
    state.swing_highs_15m = highs
    state.swing_lows_15m = lows

    sh = [h["price"] for h in highs]
    sl = [l["price"] for l in lows]
    close = float(bars[-1].get("close", 0) or 0)
    prev = state.struct_bias_15m or STRUCT_NEUTRAL
    snap = update_structure(sh, sl, close, prev)
    _apply_structure_snapshot(snap, "15m")
    try:
        from botlog.journal import on_structure_updated
        on_structure_updated()
    except Exception:
        pass


def _update_structure_1h():
    bars = list(_bars_1h)
    if len(bars) < cfg.SWING_LB_1H * 2 + 5:
        return
    highs, lows = _detect_swings(bars, cfg.SWING_LB_1H)
    state.swing_highs_1h = highs
    state.swing_lows_1h = lows

    sh = [h["price"] for h in highs]
    sl = [l["price"] for l in lows]
    close = float(bars[-1].get("close", 0) or 0)
    snap = update_structure(sh, sl, close, STRUCT_NEUTRAL)
    _apply_structure_snapshot(snap, "1h")
    try:
        from botlog.journal import on_structure_updated
        on_structure_updated()
    except Exception:
        pass


def _resolve_entry(direction: str, entry_price: float | None = None) -> float:
    if entry_price and entry_price > 0:
        return float(entry_price)
    entry = state.ask if direction == "LONG" else state.bid
    if entry <= 0:
        entry = state.price or state.mark_price
    return float(entry or 0)


def get_sl_level(direction: str, entry_price: float | None = None) -> float:
    from engine.structure_levels import calc_trade_levels

    entry = _resolve_entry(direction, entry_price)
    _, sl, _, _ = calc_trade_levels(direction, entry, state)
    return sl


def get_tp1_level(direction: str, entry_price: float | None = None) -> float:
    from engine.structure_levels import calc_trade_levels

    entry = _resolve_entry(direction, entry_price)
    _, _, tp1, _ = calc_trade_levels(direction, entry, state)
    return tp1


def get_tp2_level(direction: str, entry_price: float | None = None) -> float:
    from engine.structure_levels import calc_trade_levels

    entry = _resolve_entry(direction, entry_price)
    _, _, _, tp2 = calc_trade_levels(direction, entry, state)
    return tp2
