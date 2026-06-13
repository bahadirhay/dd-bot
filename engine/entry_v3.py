"""
engine/entry_v3.py

RANGE fade: band S/R ile TP/RR; SL = aktif seviye alti/ustu + bar-noise buffer.
BREAKOUT: SL = yapisal 15m swing (+ buffer).
Diger: liquidity grab, zone test, breakout retest.

DÜZELTME: CVD "confirmed" zorunluluğu kaldırıldı — CVD yön filtresi yeterli.
RANGE_BUY @ NEAR_SUPPORT: CVD BEAR değilse giriş yapılabilir (NEUTRAL dahil).
"""
from __future__ import annotations

from core.config import cfg
from core.state import state, effective_price
from core.logger import get_logger
from engine.cvd_v3 import update_cvd_snapshot
from engine.levels_v3 import get_levels_snapshot, update_levels
from engine.scenario_v3 import get_scenario_snapshot
from engine.structure_levels import _swing_prices, nearest_swing_above, nearest_swing_below
from engine.structure_thresholds import sl_buffer_bps, breakout_close_beyond
from engine.v3_common import bars_15m, bars_1m, calculate_channel_zone

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


def _fade_level_buffer_px(anchor: float, entry: float) -> float:
    """Fade invalidation — seviye otesi nefes (sl_buffer_bps / bar noise)."""
    px = float(entry or anchor or 0)
    if px <= 0:
        return 4.0
    anchor_px = float(anchor or px)
    return max(anchor_px * sl_buffer_bps(px) / 10000.0, px * 0.00025)


def _fade_sl_long(entry: float, support: float) -> float:
    """Fade LONG: destek alti + buffer (yapisal swing degil)."""
    if support <= 0 or entry <= 0:
        return entry * 0.9998
    buf = _fade_level_buffer_px(support, entry)
    sl = support - buf
    min_risk = entry * 0.0008
    if sl >= entry - min_risk:
        sl = entry - min_risk
    return sl


def _fade_sl_short(entry: float, resistance: float) -> float:
    """Fade SHORT: direnc ustu + buffer (yapisal swing degil)."""
    if resistance <= 0 or entry <= 0:
        return entry * 1.0002
    buf = _fade_level_buffer_px(resistance, entry)
    sl = resistance + buf
    min_risk = entry * 0.0008
    if sl <= entry + min_risk:
        sl = entry + min_risk
    return sl


def _structural_sl_long(entry: float, ref_level: float, fallback: float) -> float:
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


def _calc_score_short_below_support(
    entry: float,
    support: float,
    resistance: float,
) -> dict | None:
    """
    Skor SHORT: giris destek ustunde/yaninda — TP destek cizgisinde degil, ALTINDA.
    (Range SELL tp2=support yalnizca entry > support icin kucuk RR uretir.)
    """
    if entry <= 0 or support <= 0:
        return None
    r = resistance if resistance > support else support + max(entry * 0.015, 8.0)
    breath = entry * sl_buffer_bps(entry) / 10000.0
    band_w = max(r - support, breath * 3.0)
    ext_frac = float(getattr(cfg, "V3_SCORE_SHORT_TP_BAND_FRAC", 0.12) or 0.12)
    ext = max(breath * 1.25, band_w * ext_frac)
    tp2 = support - ext
    if tp2 >= entry - breath * 0.5:
        tp2 = entry - max(breath, entry * 0.0015)
    sl_frac = float(getattr(cfg, "V3_SCORE_SHORT_SL_BAND_FRAC", 0.035) or 0.035)
    reclaim_buffer = max(breath * 1.25, band_w * sl_frac)
    local_sl = support + reclaim_buffer
    if local_sl <= entry:
        local_sl = entry + max(breath, entry * 0.0008)
    # Score short invalidation is broken-support reclaim, not the far range resistance.
    sl = local_sl
    risk = max(sl - entry, 0.0001)
    reward = max(entry - tp2, 0.0001)
    tp1 = entry - risk * 0.5
    return {
        "direction": "SELL",
        "price": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "rr": reward / risk,
    }


def _calc_score_long_above_resistance(
    entry: float,
    support: float,
    resistance: float,
) -> dict | None:
    """Skor LONG: giris direnc altinda — TP direnc USTUNDE."""
    if entry <= 0 or resistance <= 0:
        return None
    s = support if support < resistance else resistance - max(entry * 0.015, 8.0)
    breath = entry * sl_buffer_bps(entry) / 10000.0
    band_w = max(resistance - s, breath * 3.0)
    ext_frac = float(getattr(cfg, "V3_SCORE_LONG_TP_BAND_FRAC", 0.12) or 0.12)
    ext = max(breath * 1.25, band_w * ext_frac)
    tp2 = resistance + ext
    if tp2 <= entry + breath * 0.5:
        tp2 = entry + max(breath, entry * 0.0015)
    sl_frac = float(getattr(cfg, "V3_SCORE_SHORT_SL_BAND_FRAC", 0.035) or 0.035)
    reclaim_buffer = max(breath * 1.25, band_w * sl_frac)
    local_sl = resistance - reclaim_buffer
    if local_sl >= entry:
        local_sl = entry - max(breath, entry * 0.0008)
    # Score long invalidation is broken-resistance reclaim, not the far range support.
    sl = local_sl
    risk = max(entry - sl, 0.0001)
    reward = max(tp2 - entry, 0.0001)
    tp1 = entry + risk * 0.5
    return {
        "direction": "BUY",
        "price": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "rr": reward / risk,
    }


def _cap_tp1_reachable(direction: str, entry: float, tp1: float) -> float:
    """
    TP1 partial'i ULASILABILIR mesafeye cek (V3_TP1_MAX_BPS). Veri+backtest: TP1
    60-90bps'te (tam-band) neredeyse hic vurulmuyor; ~55bps tatli nokta. RR'yi
    bozmaz (RR tp2 ile hesaplanir); yalniz partial'i yakinlastirir, gerisi runner.
    """
    max_bps = float(getattr(cfg, "V3_TP1_MAX_BPS", 60.0) or 0)
    if max_bps <= 0 or entry <= 0 or tp1 <= 0:
        return tp1
    d = entry * max_bps / 1e4
    if direction == "SELL":
        return max(tp1, round(entry - d, 2))   # tp1<entry; mesafeyi kis
    return min(tp1, round(entry + d, 2))        # BUY: tp1>entry


def _calc_range_sell(entry: float, support: float, resistance: float) -> dict | None:
    if entry <= 0 or support <= 0 or resistance <= support or entry <= support:
        return None
    from engine.v3_common import range_channel_tp_ladder

    sl = _fade_sl_short(entry, resistance)
    levels = getattr(state, "v3_zone_layers", None) or {}
    tp1, tp2 = range_channel_tp_ladder(
        "SHORT",
        entry,
        support,
        resistance,
        levels={"zone_layers": levels},
        swing_lows=state.swing_lows_15m or [],
    )
    if tp1 <= 0 or tp2 <= 0 or tp1 >= entry or tp2 >= tp1:
        tp2 = support
        risk = max(sl - entry, 0.0001)
        tp1 = entry - risk
    risk = max(sl - entry, 0.0001)
    reward = max(entry - tp2, 0.0001)
    rr = reward / risk
    tp1 = _cap_tp1_reachable("SELL", entry, tp1)
    return {
        "direction": "SELL",
        "price": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "rr": rr,
        "sl_source": "fade_level",
        "sl_anchor": resistance,
    }


def _calc_range_buy(entry: float, support: float, resistance: float) -> dict | None:
    if entry <= 0 or support <= 0 or resistance <= support or entry >= resistance:
        return None
    from engine.v3_common import range_channel_tp_ladder

    sl = _fade_sl_long(entry, support)
    levels = getattr(state, "v3_zone_layers", None) or {}
    tp1, tp2 = range_channel_tp_ladder(
        "LONG",
        entry,
        support,
        resistance,
        levels={"zone_layers": levels},
        swing_highs=state.swing_highs_15m or [],
    )
    if tp1 <= 0 or tp2 <= 0 or tp1 <= entry or tp2 <= tp1:
        tp2 = resistance
        risk = max(entry - sl, 0.0001)
        tp1 = entry + risk
    risk = max(entry - sl, 0.0001)
    reward = max(tp2 - entry, 0.0001)
    rr = reward / risk
    tp1 = _cap_tp1_reachable("BUY", entry, tp1)
    return {
        "direction": "BUY",
        "price": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "rr": rr,
        "sl_source": "fade_level",
        "sl_anchor": support,
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
        "sl_source": str(calc.get("sl_source") or "fade_level"),
        "sl_anchor": float(calc.get("sl_anchor", 0) or 0),
        "preview": False,
    }


def _range_preview(levels: dict, price: float) -> dict:
    """Senaryo WAIT olsa bile band RR onizlemesi (log icin SL/TP/RR dolu)."""
    s, r = _band_prices(levels)
    if s <= 0 or r <= s or price <= 0:
        return _invalid()
    zone = str(levels.get("zone") or calculate_channel_zone(price, s, r))
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


def _try_channel_zone_entry(
    levels: dict, price: float, zone: str, cvd: dict | None
) -> dict:
    """Senaryo WAIT olsa bile kanal traverse + range valid ise kenar girisi."""
    if not getattr(cfg, "V3_CHANNEL_ENTRY_ENABLED", True):
        return _invalid()
    if not levels.get("channel_traversed"):
        return _invalid()
    if zone == "NEAR_RESISTANCE":
        order_side, direction = "SHORT", "SELL"
    elif zone == "NEAR_SUPPORT":
        order_side, direction = "LONG", "BUY"
    else:
        return _invalid()

    from engine.range_validation_v3 import clean_range_scenario, validate_range_trade

    range_check = validate_range_trade(
        order_side,
        levels=levels,
        scenario=clean_range_scenario({}, order_side),
        cvd=cvd,
        px=price,
    )
    if not range_check.get("valid"):
        return _invalid()
    return _check_scenario_zone_entry(direction, levels, price, zone)


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
                f"[ENTRY] zone_entry RR={rr:.2f} < min={cfg.V3_MIN_RR_RATIO:.2f} — preview"
            )
    return signal


def _check_liquidity_grab(direction: str, active: dict) -> dict:
    closed_15m = bars_15m(10)
    if len(closed_15m) < 2:
        return _invalid()
    last = closed_15m[-1]
    prev = closed_15m[-2]
    if direction == "BUY":
        level_price = float((active.get("support") or {}).get("price", 0) or 0)
        if level_price <= 0:
            return _invalid()
        low = float(last.get("low", 0) or 0)
        close = float(last.get("close", 0) or 0)
        prev_low = float(prev.get("low", 0) or 0)
        if not (low < level_price and close > level_price and low < prev_low):
            return _invalid()
        entry = close
        sl = low * 0.9995
        risk = max(entry - sl, 0.0001)
        resistance_price = float((active.get("resistance") or {}).get("price", 0) or 0)
        tp2 = resistance_price if resistance_price > entry else entry + risk * 2.0
        tp1 = entry + risk
        rr = (tp2 - entry) / risk
        if rr >= cfg.V3_MIN_RR_RATIO:
            return {
                "valid": True,
                "direction": "BUY",
                "entry_type": "LIQ_GRAB",
                "price": entry,
                "sl": sl,
                "tp1": tp1,
                "tp2": tp2,
                "rr": rr,
                "preview": False,
            }
    else:
        level_price = float((active.get("resistance") or {}).get("price", 0) or 0)
        if level_price <= 0:
            return _invalid()
        high = float(last.get("high", 0) or 0)
        close = float(last.get("close", 0) or 0)
        prev_high = float(prev.get("high", 0) or 0)
        if not (high > level_price and close < level_price and high > prev_high):
            return _invalid()
        entry = close
        sl = high * 1.0005
        risk = max(sl - entry, 0.0001)
        support_price = float((active.get("support") or {}).get("price", 0) or 0)
        tp2 = support_price if 0 < support_price < entry else entry - risk * 2.0
        tp1 = entry - risk
        rr = (entry - tp2) / risk
        if rr >= cfg.V3_MIN_RR_RATIO:
            return {
                "valid": True,
                "direction": "SELL",
                "entry_type": "LIQ_GRAB",
                "price": entry,
                "sl": sl,
                "tp1": tp1,
                "tp2": tp2,
                "rr": rr,
                "preview": False,
            }
    return _invalid()


def _check_zone_test(direction: str, active: dict, price: float) -> dict:
    if price <= 0:
        return _invalid()
    if direction == "BUY":
        support_price = float((active.get("support") or {}).get("price", 0) or 0)
        if support_price <= 0:
            return _invalid()
        proximity = float(getattr(cfg, "V3_CHANNEL_BAND_PCT", 0.003) or 0.003)
        if not (support_price * (1 - proximity) <= price <= support_price * (1 + proximity)):
            return _invalid()
        entry = price
        resistance_price = float((active.get("resistance") or {}).get("price", 0) or 0)
        sl = support_price * 0.999
        risk = max(entry - sl, 0.0001)
        tp2 = resistance_price if resistance_price > entry else entry + risk * 2.0
        tp1 = entry + risk
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
        if resistance_price <= 0:
            return _invalid()
        proximity = float(getattr(cfg, "V3_CHANNEL_BAND_PCT", 0.003) or 0.003)
        if not (resistance_price * (1 - proximity) <= price <= resistance_price * (1 + proximity)):
            return _invalid()
        entry = price
        support_price = float((active.get("support") or {}).get("price", 0) or 0)
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
        channel_sig = _try_channel_zone_entry(levels, price, zone, cvd)
        if channel_sig.get("valid"):
            state.v3_entry_signal = channel_sig
            return state.v3_entry_signal
        state.v3_entry_signal = _range_preview(levels, price)
        return state.v3_entry_signal

    if getattr(cfg, "V3_EXEC_WINDOW_GATE_ENTRY", True) and not getattr(
        cfg, "V3_SCORE_DECISION_ENABLED", True
    ):
        from engine.market_state_v3 import get_market_state
        from engine.trade_verdict_v3 import trade_entry_allowed

        ms = get_market_state()
        direction = "BUY" if "BUY" in name else "SELL"
        ok_t, tmsg = trade_entry_allowed(ms, name, direction)
        if not ok_t:
            log.debug(f"[ENTRY] verdict: {tmsg}")
            state.v3_entry_signal = _invalid(stale_execution=True)
            return state.v3_entry_signal

    direction = "BUY" if "BUY" in name else "SELL"
    if state.in_position and allow_in_position:
        side = str(state.pos_side or "").upper()
        same_direction = (side == "LONG" and direction == "BUY") or (side == "SHORT" and direction == "SELL")
        if same_direction:
            state.v3_entry_signal = _invalid()
            return state.v3_entry_signal

    # ── DÜZELTME: CVD "confirmed" zorunluluğu kaldırıldı ──────────────────────
    # Eski kod: if not cvd.get("confirmed"): → preview dönüp çıkıyordu
    # Yeni kod: CVD yön filtresi yeterli — BULL/BEAR kontrolü yapılır
    # RANGE_BUY'da CVD BEAR ise engelle, NEUTRAL ise geç
    # BREAKOUT'ta hâlâ confirmed gerekli (momentum teyidi şart)
    cvd_dir = str(cvd.get("direction") or "NEUTRAL")
    cvd_confirmed = cvd.get("confirmed", False)

    if not getattr(cfg, "V3_SCORE_DECISION_ENABLED", True):
        if "BREAKOUT" in name:
            if not cvd_confirmed:
                prev = _range_preview(levels, price)
                state.v3_entry_signal = prev
                return state.v3_entry_signal

        if direction == "BUY" and cvd_dir == "BEAR":
            log.debug(f"[ENTRY] RANGE_BUY engellendi: CVD={cvd_dir}")
            state.v3_entry_signal = _invalid()
            return state.v3_entry_signal
        if direction == "SELL" and cvd_dir == "BULL":
            log.debug(f"[ENTRY] RANGE_SELL engellendi: CVD={cvd_dir}")
            state.v3_entry_signal = _invalid()
            return state.v3_entry_signal
    # ─────────────────────────────────────────────────────────────────────────

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


def build_entry_for_score_side(
    side: str,
    levels: dict,
    price: float,
    scenario: dict | None = None,
) -> dict:
    """
    Skor tabanli karar: yon secildikten sonra giris (CVD/verdict veto yok).
    TREND_CONTINUATION: kirilmis destek/direnc referansi ile RR.
    """
    direction = "BUY" if str(side).upper() in ("LONG", "BUY") else "SELL"
    s, r = _band_prices(levels)
    sc = scenario or {}
    ref_s = float(sc.get("ref_support") or levels.get("active_support") or s or 0)
    ref_r = float(sc.get("ref_resistance") or levels.get("active_resistance") or r or 0)
    mode = str(levels.get("decision_mode") or "")
    entry = price if price > 0 else float(state.mark_price or state.price or 0)

    if mode == "TREND_CONTINUATION" or (ref_s <= 0 and ref_r <= ref_s):
        if direction == "SELL":
            sup = ref_s or s
            res = ref_r if ref_r > sup else sup + max(entry * 0.015, 8.0)
            calc = _calc_score_short_below_support(entry, sup, res)
            if calc:
                rr = float(calc["rr"])
                sig = {
                    "valid": rr >= cfg.V3_MIN_RR_RATIO,
                    "direction": "SELL",
                    "entry_type": "TREND_CONTINUATION",
                    "price": calc["price"],
                    "sl": calc["sl"],
                    "tp1": calc["tp1"],
                    "tp2": calc["tp2"],
                    "rr": rr,
                    "preview": False,
                }
            else:
                sig = _invalid()
        else:
            res = ref_r or r
            sup = ref_s if ref_s < res else res - max(entry * 0.015, 8.0)
            calc = _calc_score_long_above_resistance(entry, sup, res)
            if calc:
                rr = float(calc["rr"])
                sig = {
                    "valid": rr >= cfg.V3_MIN_RR_RATIO,
                    "direction": "BUY",
                    "entry_type": "TREND_CONTINUATION",
                    "price": calc["price"],
                    "sl": calc["sl"],
                    "tp1": calc["tp1"],
                    "tp2": calc["tp2"],
                    "rr": rr,
                    "preview": False,
                }
            else:
                sig = _invalid()
        if sig.get("valid") or float(sig.get("rr", 0) or 0) > 0:
            return sig
        return _range_preview(levels, entry)

    zone = str(levels.get("zone") or calculate_channel_zone(entry, s, r))
    if direction == "SELL":
        sig = _invalid()
        if s > 0 and entry >= s * 0.9995:
            calc = _calc_score_short_below_support(entry, s, r)
            if calc:
                rr = float(calc["rr"])
                sig = {
                    "valid": rr >= cfg.V3_MIN_RR_RATIO,
                    "direction": "SELL",
                    "entry_type": "SCORE_SHORT_BELOW_S",
                    "price": calc["price"],
                    "sl": calc["sl"],
                    "tp1": calc["tp1"],
                    "tp2": calc["tp2"],
                    "rr": rr,
                    "preview": False,
                }
        # Broken-support SHORT: fiyat destek altinda ama çok fazla değil (<%3)
        # SL = destek + buffer, TP = band genişliğinin oransal uzantısı
        if float(sig.get("rr", 0) or 0) <= 0 and s > 0 and 0 < entry < s:
            breath = entry * sl_buffer_bps(entry) / 10000.0
            band_w = max(r - s, breath * 4) if r > s else max(entry * 0.015, 8.0)
            broken_sl = s + max(breath * 2.0, band_w * 0.04)
            broken_tp2 = entry - max(breath * 2.0, band_w * 0.15)
            risk = max(broken_sl - entry, 0.0001)
            reward = max(entry - broken_tp2, 0.0001)
            broken_rr = reward / risk
            if broken_sl > entry and broken_tp2 < entry and broken_rr >= 1.0:
                sig = {
                    "valid": broken_rr >= cfg.V3_MIN_RR_RATIO,
                    "direction": "SELL",
                    "entry_type": "BROKEN_SUPPORT_SHORT",
                    "price": entry,
                    "sl": round(broken_sl, 2),
                    "tp1": round(entry - risk, 2),
                    "tp2": round(broken_tp2, 2),
                    "rr": broken_rr,
                    "preview": False,
                }
        if float(sig.get("rr", 0) or 0) <= 0:
            sig = _check_scenario_zone_entry("SELL", levels, entry, zone)
        if float(sig.get("rr", 0) or 0) <= 0:
            sig = _build_range_entry(
                "SELL", entry, s, r, entry_type="SCORE_SELL", require_min_rr=False
            )
    else:
        sig = _invalid()
        if r > 0 and entry <= r * 1.0005:
            calc = _calc_score_long_above_resistance(entry, s, r)
            if calc:
                rr = float(calc["rr"])
                sig = {
                    "valid": rr >= cfg.V3_MIN_RR_RATIO,
                    "direction": "BUY",
                    "entry_type": "SCORE_LONG_ABOVE_R",
                    "price": calc["price"],
                    "sl": calc["sl"],
                    "tp1": calc["tp1"],
                    "tp2": calc["tp2"],
                    "rr": rr,
                    "preview": False,
                }
        # Broken-resistance LONG: fiyat direnc üstünde ama çok fazla değil (<%3)
        if float(sig.get("rr", 0) or 0) <= 0 and r > 0 and entry > r:
            breath = entry * sl_buffer_bps(entry) / 10000.0
            band_w = max(r - s, breath * 4) if s > 0 and r > s else max(entry * 0.015, 8.0)
            broken_sl = r - max(breath * 2.0, band_w * 0.04)
            broken_tp2 = entry + max(breath * 2.0, band_w * 0.15)
            risk = max(entry - broken_sl, 0.0001)
            reward = max(broken_tp2 - entry, 0.0001)
            broken_rr = reward / risk
            if broken_sl < entry and broken_tp2 > entry and broken_rr >= 1.0:
                sig = {
                    "valid": broken_rr >= cfg.V3_MIN_RR_RATIO,
                    "direction": "BUY",
                    "entry_type": "BROKEN_RESISTANCE_LONG",
                    "price": entry,
                    "sl": round(broken_sl, 2),
                    "tp1": round(entry + risk, 2),
                    "tp2": round(broken_tp2, 2),
                    "rr": broken_rr,
                    "preview": False,
                }
        if float(sig.get("rr", 0) or 0) <= 0:
            sig = _check_scenario_zone_entry("BUY", levels, entry, zone)
        if float(sig.get("rr", 0) or 0) <= 0:
            sig = _build_range_entry(
                "BUY", entry, s, r, entry_type="SCORE_BUY", require_min_rr=False
            )
    if not sig.get("valid"):
        prev = _range_preview(levels, entry)
        if str(prev.get("direction") or "").upper() == direction:
            return prev
    return sig


def get_entry_snapshot(*, allow_in_position: bool = False) -> dict:
    return update_entry(allow_in_position=allow_in_position)
