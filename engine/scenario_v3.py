"""
engine/scenario_v3.py

Sade senaryo: zone + CVD yonu + 1 mum kirilim teyidi.
Kanal teyidi / 1h zorunlulugu yok.
Destek/direnc kirilimi: range_valid gerekmez (son bilinen band referansi).
"""
from __future__ import annotations

from core.config import cfg
from core.state import state, effective_price
from core.logger import get_logger
from engine.cvd_v3 import get_cvd_snapshot
from engine.levels_v3 import (
    band_is_stable,
    get_breakout_reference_levels,
    get_levels_snapshot,
    level_trade_ready,
    update_levels,
)
from engine.structure_thresholds import (
    break_threshold_price,
    breakout_close_beyond,
    close_broke_above,
    close_broke_below,
)
from engine.v3_common import bars_15m

log = get_logger("ScenarioV3")
_last_band_stable_log_key = ""


def _levels_ready(active: dict) -> bool:
    return bool(
        active.get("range_valid")
        and active.get("support") is not None
        and active.get("resistance") is not None
        and float(active.get("active_support", 0) or 0) > 0
        and float(active.get("active_resistance", 0) or 0) > 0
    )


def _detect_breakout(bars: list[dict], resistance: float, support: float) -> str | None:
    """Son 15m kapanisi seviye disinda (yapisal esik + 1 mum teyit)."""
    if not bars:
        return None
    close = float(bars[-1].get("close", 0) or 0)
    if close <= 0:
        return None
    if resistance > 0 and breakout_close_beyond(close, resistance, "LONG", close):
        return "BUY"
    if support > 0 and breakout_close_beyond(close, support, "SHORT", close):
        return "SELL"
    return None


def _last_close(bars: list[dict]) -> float:
    if not bars:
        return 0.0
    return float(bars[-1].get("close", 0) or 0)


def _close_below_support(bars: list[dict], support: float) -> bool:
    close = _last_close(bars)
    return close_broke_below(close, support, close)


def _close_above_resistance(bars: list[dict], resistance: float) -> bool:
    close = _last_close(bars)
    return close_broke_above(close, resistance, close)


def _cvd_trade_direction() -> str | None:
    cvd = get_cvd_snapshot() or {}
    direction = str(cvd.get("direction") or "NEUTRAL")
    if direction == "BULL":
        return "BUY"
    if direction == "BEAR":
        return "SELL"
    return None


def _cvd_bearish_confirmed(zone: str = "") -> bool:
    cvd = get_cvd_snapshot() or {}
    if str(cvd.get("direction") or "") != "BEAR":
        return False
    if cvd.get("confirmed"):
        return True
    if zone == "NEAR_RESISTANCE":
        return True  # divergence teyit
    return False


def _cvd_bullish_confirmed(zone: str = "") -> bool:
    cvd = get_cvd_snapshot() or {}
    if str(cvd.get("direction") or "") != "BULL":
        return False
    if cvd.get("confirmed"):
        return True
    if zone == "NEAR_SUPPORT":
        return True  # divergence teyit
    return False


def _try_breakout_scenario(
    bars15: list[dict], ref: dict, price: float, *, zone: str = ""
) -> dict | None:
    """range_valid olmadan: son bilinen destek/direnc kirilimi + CVD."""
    ref_s = float(ref.get("support") or 0)
    ref_r = float(ref.get("resistance") or 0)
    last_close = _last_close(bars15)

    if ref_s > 0 and _close_below_support(bars15, ref_s) and _cvd_bearish_confirmed(zone):
        thr = break_threshold_price(ref_s, "SHORT", last_close)
        detail = (
            f"Destek kirildi (range_valid yok sayilir): kapanis {last_close:.2f} < "
            f"esik {thr:.2f} (S={ref_s:.2f}), CVD satis."
        )
        log.info(
            f"[SCENARIO] BREAKOUT_SELL ref_S={ref_s:.2f} px={price:.2f} "
            f"close={last_close:.2f} kaynak={ref.get('source')}"
        )
        return {
            "name": "BREAKOUT_SELL",
            "detail": detail,
            "ref_support": ref_s,
            "ref_resistance": ref_r,
            "breakout_ignore_range": True,
        }

    if ref_r > 0 and _close_above_resistance(bars15, ref_r) and _cvd_bullish_confirmed(zone):
        thr = break_threshold_price(ref_r, "LONG", last_close)
        detail = (
            f"Direnc kirildi (range_valid yok sayilir): kapanis {last_close:.2f} > "
            f"esik {thr:.2f} (R={ref_r:.2f}), CVD alim."
        )
        log.info(
            f"[SCENARIO] BREAKOUT_BUY ref_R={ref_r:.2f} px={price:.2f} "
            f"close={last_close:.2f} kaynak={ref.get('source')}"
        )
        return {
            "name": "BREAKOUT_BUY",
            "detail": detail,
            "ref_support": ref_s,
            "ref_resistance": ref_r,
            "breakout_ignore_range": True,
        }

    return None


def update_scenario() -> dict:
    global _last_band_stable_log_key

    price = float(effective_price() or state.mark_price or state.price or 0)
    levels = get_levels_snapshot(price)
    zone = str(levels.get("zone") or "MID_RANGE")
    ref = get_breakout_reference_levels(price)
    bars15 = bars_15m(40)
    scenario = {"name": "WAIT", "detail": "Kosullar olusmadi."}

    active_s = float(levels.get("active_support") or 0)
    active_r = float(levels.get("active_resistance") or 0)
    ref_s = float(ref.get("support") or 0)
    ref_r = float(ref.get("resistance") or 0)
    check_s = active_s if active_s > 0 else ref_s
    check_r = active_r if active_r > 0 else ref_r

    stable, stab_reason = band_is_stable(bars15, check_s, check_r)
    ref_structural_break = _detect_breakout(bars15, ref_r, ref_s) is not None

    if not stable and not ref_structural_break:
        scenario = {
            "name": "WAIT",
            "detail": f"Band yerlesmiyor — {stab_reason}",
            "band_stable": False,
            "band_stability": stab_reason,
        }
        log_key = stab_reason
        if log_key != _last_band_stable_log_key:
            _last_band_stable_log_key = log_key
            log.info(f"[SCENARIO] Band stabil degil: {stab_reason}")
        state.v3_scenario = scenario
        return scenario

    if not _levels_ready(levels) and not ref_structural_break:
        scenario["detail"] = "Aktif destek/direnc yok veya range gecersiz."
        state.v3_scenario = scenario
        return scenario

    breakout_early = _try_breakout_scenario(bars15, ref, price, zone=zone)
    if breakout_early:
        state.v3_scenario = breakout_early
        return breakout_early

    if not _levels_ready(levels):
        scenario["detail"] = "Aktif destek/direnc yok veya range gecersiz."
        state.v3_scenario = scenario
        return scenario

    resistance = float(levels.get("active_resistance", 0) or 0)
    support = float(levels.get("active_support", 0) or 0)

    breakout = _detect_breakout(bars15, resistance, support)
    if breakout:
        scenario = {
            "name": f"BREAKOUT_{breakout}",
            "detail": f"Kirilim: son kapanis destek/direnc disinda ({breakout}).",
            "ref_support": support,
            "ref_resistance": resistance,
        }
        log.info(
            f"[SCENARIO] BREAKOUT_{breakout} S={support:.2f} R={resistance:.2f} px={price:.2f}"
        )
        state.v3_scenario = scenario
        return scenario

    cvd_dir = _cvd_trade_direction()
    cvd_snap = get_cvd_snapshot() or {}
    last_close = _last_close(bars15)
    if zone == "NEAR_SUPPORT":
        ready, ready_detail = level_trade_ready(
            bars15, levels, "BUY", cvd=cvd_snap
        )
        if ready:
            scenario = {
                "name": "RANGE_BUY",
                "detail": (
                    f"Destek bolgesi ({support:.2f}) — 4 kosul OK ({ready_detail})."
                ),
            }
        elif cvd_dir == "SELL":
            if _close_below_support(bars15, support) and _cvd_bearish_confirmed(zone):
                thr = break_threshold_price(support, "SHORT", last_close)
                scenario = {
                    "name": "BREAKOUT_SELL",
                    "detail": (
                        f"Destek kirildi: kapanis {last_close:.2f} < esik {thr:.2f} "
                        f"(S={support:.2f}), CVD satis."
                    ),
                    "ref_support": support,
                    "ref_resistance": resistance,
                }
                log.info(
                    f"[SCENARIO] BREAKOUT_SELL destek alti kapanis "
                    f"S={support:.2f} esik={thr:.2f} px={price:.2f} close={last_close:.2f}"
                )
            else:
                thr = break_threshold_price(support, "SHORT", last_close)
                scenario = {
                    "name": "WAIT",
                    "detail": (
                        f"Destek bolgesi + CVD satis ama kapanis kirilim esiginde/ustunde "
                        f"({last_close:.2f} >= {thr:.2f}, S={support:.2f}) — kirilim bekleniyor."
                    ),
                }
        else:
            scenario = {
                "name": "WAIT",
                "detail": ready_detail if not ready else "Destek bolgesinde CVD notr — yon bekleniyor.",
            }
            if not ready:
                log.info(f"[SCENARIO] RANGE_BUY engellendi: {ready_detail}")
    elif zone == "NEAR_RESISTANCE":
        ready, ready_detail = level_trade_ready(
            bars15, levels, "SELL", cvd=cvd_snap
        )
        if ready:
            scenario = {
                "name": "RANGE_SELL",
                "detail": (
                    f"Direnc bolgesi ({resistance:.2f}) — 4 kosul OK ({ready_detail})."
                ),
            }
        elif cvd_dir == "BUY":
            if _close_above_resistance(bars15, resistance) and _cvd_bullish_confirmed(zone):
                thr = break_threshold_price(resistance, "LONG", last_close)
                scenario = {
                    "name": "BREAKOUT_BUY",
                    "detail": (
                        f"Direnc kirildi: kapanis {last_close:.2f} > esik {thr:.2f} "
                        f"(R={resistance:.2f}), CVD alim."
                    ),
                    "ref_support": support,
                    "ref_resistance": resistance,
                }
                log.info(
                    f"[SCENARIO] BREAKOUT_BUY direnc ustu kapanis "
                    f"R={resistance:.2f} esik={thr:.2f} px={price:.2f} close={last_close:.2f}"
                )
            else:
                thr = break_threshold_price(resistance, "LONG", last_close)
                scenario = {
                    "name": "WAIT",
                    "detail": (
                        f"Direnc bolgesi + CVD alim ama kapanis kirilim esiginde/altinda "
                        f"({last_close:.2f} <= {thr:.2f}, R={resistance:.2f}) — kirilim bekleniyor."
                    ),
                }
        else:
            scenario = {
                "name": "WAIT",
                "detail": ready_detail if not ready else "Direnc bolgesinde CVD notr — yon bekleniyor.",
            }
            if not ready:
                log.info(f"[SCENARIO] RANGE_SELL engellendi: {ready_detail}")
    else:
        scenario = {"name": "WAIT", "detail": "Band ortasi — destek veya dirence yaklasma bekleniyor."}

    state.v3_scenario = scenario
    return scenario


def get_scenario_snapshot(price: float = 0.0) -> dict:
    snap = state.v3_scenario or {}
    if not snap:
        update_levels()
        snap = update_scenario()
    return snap
