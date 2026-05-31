"""
engine/entry_v3.py

RANGE/BREAKOUT: band S/R ile TP/RR; SL = yapisal 15m swing (+ buffer), yoksa S/R disi.
Diger: liquidity grab, zone test, breakout retest.
"""
from __future__ import annotations

from core.config import cfg
from core.state import state, effective_price
from core.logger import get_logger
from engine.cvd_v3 import update_cvd_snapshot
from engine.levels_v3 import get_levels_snapshot, update_levels, zone_for_price
from engine.scenario_v3 import get_scenario_snapshot
from engine.structure_levels import _swing_prices, nearest_swing_above, nearest_swing_below
from engine.structure_thresholds import sl_buffer_bps, breakout_close_beyond
from engine.v3_common import bars_15m, bars_1m

log = get_logger("EntryV3")


def _invalid(**extra) -> dict:
    base = {
        "valid": False,
        "direction": "",
        "entry_type": "",
        "price": 0.0,
        "sl": 0.0,
        "tp1": 0.0,
        "tp2": 0.0,
        "rr": 0.0,
        "preview": False,
    }
    base.update(extra)
    return base


def _band_prices(levels: dict) -> tuple[float, float]:
    s = float(levels.get("active_support") or 0)
    r = float(levels.get("active_resistance") or 0)
    if s <= 0:
        s = float((levels.get("support") or {}).get("price", 0) or 0)
    if r <= 0:
        r = float((levels.get("resistance") or {}).get("price", 0) or 0)
    return s, r


def _nearest_swing_low_below_level(level: float) -> float:
    if level <= 0:
        return 0.0
    below = sorted(
        (p for p in _swing_prices(state.swing_lows_15m or []) if p < level * 0.9998),
        reverse=True,
    )
    return below[0] if below else 0.0


def _nearest_swing_high_above_level(level: float) -> float:
    if level <= 0:
        return 0.0
    above = sorted(
        (p for p in _swing_prices(state.swing_highs_15m or []) if p > level * 1.0002),
    )
    return above[0] if above else 0.0


def _structural_sl_long(entry: float, ref_level: float, fallback: float) -> float:
    """
    LONG SL: ref (destek / kirilan direnc) altindaki en yakin 15m swing dip + buffer.
    Yapısal seviye yoksa fallback (S/R disi yuzde).
    """
    if entry <= 0:
        return fallback
    buf = entry * sl_buffer_bps(entry) / 10000.0
    swing = _nearest_swing_low_below_level(ref_level) if ref_level > 0 else 0.0
    if swing <= 0:
        swing = nearest_swing_below(entry, state.swing_lows_15m or [])
    if swing > 0:
        sl = swing - buf
        if 0 < sl < entry:
            return min(sl, fallback)
    return fallback


def _structural_sl_short(entry: float, ref_level: float, fallback: float) -> float:
    """
    SHORT SL: ref (direnc / kirilan destek) ustundeki en yakin 15m swing tepe + buffer.
    """
    if entry <= 0:
        return fallback
    buf = entry * sl_buffer_bps(entry) / 10000.0
    swing = _nearest_swing_high_above_level(ref_level) if ref_level > 0 else 0.0
    if swing <= 0:
        swing = nearest_swing_above(entry, state.swing_highs_15m or [])
    if swing > 0:
        sl = swing + buf
        if sl > entry:
            return max(sl, fallback)
    return fallback


def _calc_range_sell(entry: float, support: float, resistance: float) -> dict | None:
    if entry <= 0 or support <= 0 or resistance <= support or entry <= support:
        return None
    fallback_sl = max(resistance * 1.001, entry * 1.0002)
    sl = _structural_sl_short(entry, resistance, fallback_sl)
    tp2 = support
    risk = max(sl - entry, 0.0001)
    tp1 = entry - risk
    rr = (entry - tp2) / risk
    return {
        "direction": "SELL",
        "price": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "rr": rr,
    }


def _calc_range_buy(entry: float, support: float, resistance: float) -> dict | None:
    if entry <= 0 or support <= 0 or resistance <= support or entry >= resistance:
        return None
    fallback_sl = min(support * 0.999, entry * 0.9998)
    sl = _structural_sl_long(entry, support, fallback_sl)
    tp2 = resistance
    risk = max(entry - sl, 0.0001)
    tp1 = entry + risk
    rr = (tp2 - entry) / risk
    return {
        "direction": "BUY",
        "price": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "rr": rr,
    }


def _build_range_entry(
    direction: str,
    entry: float,
    support: float,
    resistance: float,
    *,
    entry_type: str = "ZONE_SCENARIO",
    require_min_rr: bool = True,
) -> dict:
    calc = (
        _calc_range_sell(entry, support, resistance)
        if direction == "SELL"
        else _calc_range_buy(entry, support, resistance)
    )
    if not calc:
        return _invalid()
    rr = float(calc["rr"])
    valid = not require_min_rr or rr >= cfg.V3_MIN_RR_RATIO
    return {
        "valid": valid,
        "direction": calc["direction"],
        "entry_type": entry_type,
        "price": calc["price"],
        "sl": calc["sl"],
        "tp1": calc["tp1"],
        "tp2": calc["tp2"],
        "rr": rr,
        "preview": False,
    }


def _range_preview(levels: dict, price: float) -> dict:
    """Senaryo WAIT olsa bile band RR onizlemesi (log icin SL/TP/RR dolu)."""
    s, r = _band_prices(levels)
    if s <= 0 or r <= s or price <= 0:
        return _invalid()
    zone = str(levels.get("zone") or zone_for_price(s, r, price))
    if zone == "NEAR_RESISTANCE":
        calc = _calc_range_sell(price, s, r)
        if calc:
            return _invalid(
                valid=False,
                preview=True,
                entry_type="PREVIEW_SELL",
                direction="SELL",
                price=calc["price"],
                sl=calc["sl"],
                tp1=calc["tp1"],
                tp2=calc["tp2"],
                rr=calc["rr"],
            )
    if zone == "NEAR_SUPPORT":
        calc = _calc_range_buy(price, s, r)
        if calc:
            return _invalid(
                valid=False,
                preview=True,
                entry_type="PREVIEW_BUY",
                direction="BUY",
                price=calc["price"],
                sl=calc["sl"],
                tp1=calc["tp1"],
                tp2=calc["tp2"],
                rr=calc["rr"],
            )
    return _invalid()


def _check_scenario_zone_entry(
    direction: str, levels: dict, price: float, zone: str
) -> dict:
    """
    RANGE_SELL @ NEAR_RESISTANCE / RANGE_BUY @ NEAR_SUPPORT.
    Giris=mevcut fiyat, SL=yapisal swing (ref=S/R), TP2=karsi band.
    """
    support, resistance = _band_prices(levels)
    if support <= 0 or resistance <= support:
        return _invalid()
    if direction == "SELL" and zone != "NEAR_RESISTANCE":
        return _invalid()
    if direction == "BUY" and zone != "NEAR_SUPPORT":
        return _invalid()

    entry = price if price > 0 else float(state.mark_price or state.price or 0)
    if entry <= 0:
        return _invalid()

    signal = _build_range_entry(direction, entry, support, resistance)
    if not signal.get("valid"):
        rr = float(signal.get("rr", 0) or 0)
        if rr > 0:
            log.debug(
                f"[ENTRY] RANGE {direction} RR={rr:.2f} < {cfg.V3_MIN_RR_RATIO} "
                f"px={entry:.2f} S={support:.2f} R={resistance:.2f}"
            )
    return signal


def _check_liquidity_grab(direction: str, active: dict) -> dict:
    closed_15m = bars_15m(10)
    if len(closed_15m) < 3:
        return _invalid()
    last = closed_15m[-1]
    body = abs(float(last.get("close", 0) or 0) - float(last.get("open", 0) or 0))
    if direction == "BUY":
        support_price = float((active.get("support") or {}).get("price", 0) or 0)
        if (
            support_price > 0
            and float(last.get("low", 0) or 0) < support_price
            and float(last.get("close", 0) or 0) > support_price
        ):
            wick = min(float(last.get("open", 0) or 0), float(last.get("close", 0) or 0)) - float(
                last.get("low", 0) or 0
            )
            if body > 0 and wick >= body * cfg.V3_WICK_STRENGTH_MULTIPLIER:
                entry = float(last.get("close", 0) or 0)
                sl = float(last.get("low", 0) or 0) * 0.9995
                risk = max(entry - sl, 0.0001)
                tp1 = entry + risk
                tp2 = entry + risk * 2.0
                rr = (tp2 - entry) / risk
                if rr >= cfg.V3_MIN_RR_RATIO:
                    return {
                        "valid": True,
                        "direction": "BUY",
                        "entry_type": "LIQUIDITY_GRAB",
                        "price": entry,
                        "sl": sl,
                        "tp1": tp1,
                        "tp2": tp2,
                        "rr": rr,
                        "preview": False,
                    }
    else:
        resistance_price = float((active.get("resistance") or {}).get("price", 0) or 0)
        if (
            resistance_price > 0
            and float(last.get("high", 0) or 0) > resistance_price
            and float(last.get("close", 0) or 0) < resistance_price
        ):
            wick = float(last.get("high", 0) or 0) - max(
                float(last.get("open", 0) or 0), float(last.get("close", 0) or 0)
            )
            if body > 0 and wick >= body * cfg.V3_WICK_STRENGTH_MULTIPLIER:
                entry = float(last.get("close", 0) or 0)
                sl = float(last.get("high", 0) or 0) * 1.0005
                risk = max(sl - entry, 0.0001)
                tp1 = entry - risk
                tp2 = entry - risk * 2.0
                rr = (entry - tp2) / risk
                if rr >= cfg.V3_MIN_RR_RATIO:
                    return {
                        "valid": True,
                        "direction": "SELL",
                        "entry_type": "LIQUIDITY_GRAB",
                        "price": entry,
                        "sl": sl,
                        "tp1": tp1,
                        "tp2": tp2,
                        "rr": rr,
                        "preview": False,
                    }
    return _invalid()


def _check_zone_test(direction: str, active: dict, price: float) -> dict:
    closed_1m = bars_1m(10)
    if len(closed_1m) < 2:
        return _invalid()
    last = closed_1m[-1]
    prev = closed_1m[-2]
    body = abs(float(last.get("close", 0) or 0) - float(last.get("open", 0) or 0))
    if direction == "BUY":
        support_price = float((active.get("support") or {}).get("price", 0) or 0)
        near_support = support_price > 0 and abs(price - support_price) / max(support_price, 1.0) < 0.003
        if not near_support:
            return _invalid()
        lower_wick = min(float(last.get("open", 0) or 0), float(last.get("close", 0) or 0)) - float(
            last.get("low", 0) or 0
        )
        is_rejection = body > 0 and lower_wick >= body * 1.5
        is_engulfing = (
            float(last.get("close", 0) or 0) > float(last.get("open", 0) or 0)
            and float(last.get("close", 0) or 0) > float(prev.get("open", 0) or 0)
            and float(last.get("open", 0) or 0) < float(prev.get("close", 0) or 0)
        )
        if is_rejection or is_engulfing:
            entry = float(last.get("close", 0) or 0)
            sl = support_price * 0.999
            risk = max(entry - sl, 0.0001)
            tp1 = entry + risk
            tp2 = entry + risk * 2.0
            rr = (tp2 - entry) / risk
            if rr >= cfg.V3_MIN_RR_RATIO:
                return {
                    "valid": True,
                    "direction": "BUY",
                    "entry_type": "ZONE_TEST",
                    "price": entry,
                    "sl": sl,
                    "tp1": tp1,
                    "tp2": tp2,
                    "rr": rr,
                    "preview": False,
                }
    else:
        resistance_price = float((active.get("resistance") or {}).get("price", 0) or 0)
        near_resistance = (
            resistance_price > 0 and abs(price - resistance_price) / max(resistance_price, 1.0) < 0.003
        )
        if not near_resistance:
            return _invalid()
        upper_wick = float(last.get("high", 0) or 0) - max(
            float(last.get("open", 0) or 0), float(last.get("close", 0) or 0)
        )
        is_rejection = body > 0 and upper_wick >= body * 1.5
        is_engulfing = (
            float(last.get("close", 0) or 0) < float(last.get("open", 0) or 0)
            and float(last.get("close", 0) or 0) < float(prev.get("open", 0) or 0)
            and float(last.get("open", 0) or 0) > float(prev.get("close", 0) or 0)
        )
        if is_rejection or is_engulfing:
            entry = float(last.get("close", 0) or 0)
            sl = resistance_price * 1.001
            risk = max(sl - entry, 0.0001)
            tp1 = entry - risk
            tp2 = entry - risk * 2.0
            rr = (entry - tp2) / risk
            if rr >= cfg.V3_MIN_RR_RATIO:
                return {
                    "valid": True,
                    "direction": "SELL",
                    "entry_type": "ZONE_TEST",
                    "price": entry,
                    "sl": sl,
                    "tp1": tp1,
                    "tp2": tp2,
                    "rr": rr,
                    "preview": False,
                }
    return _invalid()


def _check_breakout_close(direction: str, active: dict) -> dict:
    """Kirilim: son 15m kapanisi yapisal esik disinda (tez ile ayni matematik)."""
    closed_15m = bars_15m(5)
    if len(closed_15m) < 1:
        return _invalid()
    last = closed_15m[-1]
    close = float(last.get("close", 0) or 0)
    if direction == "BUY":
        level_price = float((active.get("resistance") or {}).get("price", 0) or 0)
        if level_price <= 0 or not breakout_close_beyond(close, level_price, "LONG", close):
            return _invalid()
        entry = close
        sl = _structural_sl_long(entry, level_price, level_price * 0.999)
        risk = max(entry - sl, 0.0001)
    else:
        level_price = float((active.get("support") or {}).get("price", 0) or 0)
        if level_price <= 0 or not breakout_close_beyond(close, level_price, "SHORT", close):
            return _invalid()
        entry = close
        sl = _structural_sl_short(entry, level_price, level_price * 1.001)
        risk = max(sl - entry, 0.0001)
    if direction == "BUY":
        tp1 = entry + risk
        tp2 = entry + risk * 2.0
        rr = (tp2 - entry) / risk
    else:
        tp1 = entry - risk
        tp2 = entry - risk * 2.0
        rr = (entry - tp2) / risk
    if rr >= cfg.V3_MIN_RR_RATIO:
        return {
            "valid": True,
            "direction": direction,
            "entry_type": "BREAKOUT_CLOSE",
            "price": entry,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "rr": rr,
            "preview": False,
        }
    return _invalid()


def _check_retest(direction: str, active: dict) -> dict:
    closed_15m = bars_15m(10)
    if len(closed_15m) < 2:
        return _invalid()
    last = closed_15m[-1]
    body = abs(float(last.get("close", 0) or 0) - float(last.get("open", 0) or 0))
    if direction == "BUY":
        level_price = float((active.get("resistance") or {}).get("price", 0) or 0)
        if (
            level_price > 0
            and float(last.get("low", 0) or 0) <= level_price
            and float(last.get("close", 0) or 0) > level_price
        ):
            lower_wick = min(float(last.get("open", 0) or 0), float(last.get("close", 0) or 0)) - float(
                last.get("low", 0) or 0
            )
            if body > 0 and lower_wick >= body * 1.5:
                entry = float(last.get("close", 0) or 0)
                sl = float(last.get("low", 0) or 0) * 0.9995
                risk = max(entry - sl, 0.0001)
                tp1 = entry + risk
                tp2 = entry + risk * 2.0
                rr = (tp2 - entry) / risk
                if rr >= cfg.V3_MIN_RR_RATIO:
                    return {
                        "valid": True,
                        "direction": "BUY",
                        "entry_type": "RETEST",
                        "price": entry,
                        "sl": sl,
                        "tp1": tp1,
                        "tp2": tp2,
                        "rr": rr,
                        "preview": False,
                    }
    else:
        level_price = float((active.get("support") or {}).get("price", 0) or 0)
        if (
            level_price > 0
            and float(last.get("high", 0) or 0) >= level_price
            and float(last.get("close", 0) or 0) < level_price
        ):
            upper_wick = float(last.get("high", 0) or 0) - max(
                float(last.get("open", 0) or 0), float(last.get("close", 0) or 0)
            )
            if body > 0 and upper_wick >= body * 1.5:
                entry = float(last.get("close", 0) or 0)
                sl = float(last.get("high", 0) or 0) * 1.0005
                risk = max(sl - entry, 0.0001)
                tp1 = entry - risk
                tp2 = entry - risk * 2.0
                rr = (entry - tp2) / risk
                if rr >= cfg.V3_MIN_RR_RATIO:
                    return {
                        "valid": True,
                        "direction": "SELL",
                        "entry_type": "RETEST",
                        "price": entry,
                        "sl": sl,
                        "tp1": tp1,
                        "tp2": tp2,
                        "rr": rr,
                        "preview": False,
                    }
    return _invalid()


def update_entry(*, allow_in_position: bool = False) -> dict:
    price = float(effective_price() or state.mark_price or state.price or 0)
    levels = get_levels_snapshot(price)
    if not levels.get("active_support") and not (levels.get("support") or {}).get("price"):
        levels = get_levels_snapshot(price) or {}
        if not levels.get("range_valid"):
            update_levels()
            levels = get_levels_snapshot(price)

    scenario = get_scenario_snapshot(price)
    zone = str(levels.get("zone") or "MID_RANGE")
    s_px, r_px = _band_prices(levels)
    ref_s = float(scenario.get("ref_support") or 0)
    ref_r = float(scenario.get("ref_resistance") or 0)
    if ref_s > 0:
        s_px = ref_s
    if ref_r > 0:
        r_px = ref_r
    active = {
        "support": levels.get("support") or {"price": s_px},
        "resistance": levels.get("resistance") or {"price": r_px},
    }
    name = str(scenario.get("name") or "")
    breakout_side = ""
    if name.startswith("BREAKOUT_"):
        breakout_side = "BUY" if "BUY" in name else "SELL"
    cvd = update_cvd_snapshot(zone=zone, breakout_side=breakout_side)

    if state.in_position and not allow_in_position:
        state.v3_entry_signal = _invalid()
        return state.v3_entry_signal

    trade_names = (
        "RANGE_BUY",
        "RANGE_SELL",
        "FAILED_BREAK_BUY",
        "FAILED_BREAK_SELL",
        "BREAKOUT_BUY",
        "BREAKOUT_SELL",
    )

    if name not in trade_names:
        state.v3_entry_signal = _range_preview(levels, price)
        return state.v3_entry_signal

    direction = "BUY" if "BUY" in name else "SELL"
    if state.in_position and allow_in_position:
        side = str(state.pos_side or "").upper()
        same_direction = (side == "LONG" and direction == "BUY") or (side == "SHORT" and direction == "SELL")
        if same_direction:
            # Pozisyon acikken ayni yonde yeniden sinyal uretme.
            state.v3_entry_signal = _invalid()
            return state.v3_entry_signal
    if not cvd.get("confirmed"):
        prev = _range_preview(levels, price) if "RANGE" in name else _invalid()
        state.v3_entry_signal = prev
        return state.v3_entry_signal
    cvd_dir = str(cvd.get("direction") or "NEUTRAL")
    if direction == "BUY" and cvd_dir == "BEAR":
        state.v3_entry_signal = _invalid()
        return state.v3_entry_signal
    if direction == "SELL" and cvd_dir == "BULL":
        state.v3_entry_signal = _invalid()
        return state.v3_entry_signal

    signal = _invalid()
    if "RANGE" in name:
        signal = _check_scenario_zone_entry(direction, levels, price, zone)
        if signal.get("valid"):
            log.info(
                f"[ENTRY] {signal.get('entry_type')} {direction} "
                f"px={signal.get('price'):.2f} SL={signal.get('sl'):.2f} "
                f"TP2={signal.get('tp2'):.2f} RR={signal.get('rr'):.2f}"
            )

    def _better(a: dict, b: dict) -> dict:
        if a.get("valid"):
            return a
        if b.get("valid"):
            return b
        return a if float(a.get("rr", 0) or 0) >= float(b.get("rr", 0) or 0) else b

    if not signal.get("valid"):
        signal = _better(signal, _check_liquidity_grab(direction, active))
    if not signal.get("valid"):
        signal = _better(signal, _check_zone_test(direction, active, price))
    if (not signal.get("valid")) and "BREAKOUT" in name:
        signal = _better(signal, _check_breakout_close(direction, active))
    if (not signal.get("valid")) and "BREAKOUT" in name:
        signal = _better(signal, _check_retest(direction, active))

    if not signal.get("valid") and float(signal.get("rr", 0) or 0) <= 0:
        signal = _range_preview(levels, price)

    state.v3_entry_signal = signal if signal else _invalid()
    return state.v3_entry_signal


def get_entry_snapshot(*, allow_in_position: bool = False) -> dict:
    return update_entry(allow_in_position=allow_in_position)
