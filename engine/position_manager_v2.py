"""
engine/position_manager_v2.py — yeni strateji için minimal pozisyon takibi.
"""
from __future__ import annotations

from core.state import state
from core.logger import get_logger
from engine.v3_common import bars_15m

log = get_logger("PosMgrV2")


def _last_15m_close() -> float:
    bars = bars_15m(2)
    if not bars:
        return 0.0
    return float(bars[-1].get("close", 0) or 0)


def _persist_entry_anchors(entry_support: float, entry_resistance: float) -> None:
    try:
        import execution.executor as ex
        from botlog.db import update_trade_entry_anchors

        trade_id = int(getattr(ex, "_trade_id", 0) or 0)
        if trade_id > 0:
            update_trade_entry_anchors(trade_id, entry_support, entry_resistance)
    except Exception as e:
        log.debug(f"Giris destegi DB yazimi: {e}")


def on_entry_filled(details: dict) -> None:
    pb = dict(state.position_breakout or {})
    scenario = str(details.get("v3_scenario") or details.get("v2_scenario") or "")
    trigger = str(details.get("v3_entry_type") or details.get("v2_trigger") or "")
    support = float(details.get("v3_support") or details.get("v2_support") or 0)
    resistance = float(details.get("v3_resistance") or details.get("v2_resistance") or 0)
    if support <= 0:
        support = float(details.get("range_active_level") or details.get("break_level") or 0)
    strategy = "v3" if details.get("v3_mode") else "v2"
    entry_support = support
    entry_resistance = resistance
    thesis = {}
    if strategy == "v3":
        from engine.thesis_v3 import build_thesis

        thesis = build_thesis(details)
    pb.update(
        {
            "strategy": strategy,
            "entry_mode": strategy,
            "direction": str(details.get("direction") or ""),
            "scenario": scenario,
            "trigger": trigger,
            "break_level": float(details.get("break_level") or details.get("range_active_level") or 0),
            "active_support": support,
            "active_resistance": resistance,
            "entry_support": entry_support,
            "entry_resistance": entry_resistance,
            "thesis": thesis,
            "tp1": float(details.get("tp1") or 0),
            "tp2": float(details.get("tp2") or 0),
        }
    )
    state.position_breakout = pb
    if entry_support > 0 or entry_resistance > 0:
        _persist_entry_anchors(entry_support, entry_resistance)
        log.info(
            f"Giris destegi kaydedildi: S={entry_support:.2f} R={entry_resistance:.2f} "
            f"({scenario or trigger or strategy})"
        )
    if thesis:
        log.info(
            f"Tez kaydedildi: {thesis.get('scenario')} key={thesis.get('key_level'):.2f} "
            f"inv={thesis.get('invalidation_price'):.2f} cvd={thesis.get('invalidation_cvd')} "
            f"stale={thesis.get('invalidation_bars')}x15m min_prog={float(thesis.get('min_progress') or 0):.2%}"
        )


def restore_entry_anchors_from_db(
  trade_id: int,
  *,
  fallback_support: float = 0.0,
  fallback_resistance: float = 0.0,
) -> tuple[float, float]:
    from botlog.db import (
        get_trade_levels,
        parse_entry_anchors_from_notes,
        update_trade_entry_anchors,
    )

    notes = ""
    if trade_id > 0:
        lv = get_trade_levels(trade_id) or {}
        notes = str(lv.get("notes") or "")
    entry_support, entry_resistance = parse_entry_anchors_from_notes(notes)
    if entry_support <= 0 and fallback_support > 0:
        entry_support = fallback_support
    if entry_resistance <= 0 and fallback_resistance > 0:
        entry_resistance = fallback_resistance
    if trade_id > 0 and (entry_support > 0 or entry_resistance > 0):
        update_trade_entry_anchors(trade_id, entry_support, entry_resistance)
    return entry_support, entry_resistance


async def check_v2_position(executor) -> str | None:
    if not state.in_position:
        return None
    pb = dict(state.position_breakout or {})
    strategy = str(pb.get("strategy") or "")
    if strategy not in ("v2", "v3"):
        return None

    # V3: tez cikisi yalnizca 15m kapanisinda (thesis_v3.check_thesis_on_15m_close)
    if strategy == "v3":
        return None

    px = float(state.price or state.mark_price or 0)
    if px <= 0:
        return None

    entry_support = float(pb.get("entry_support") or 0)
    entry_resistance = float(pb.get("entry_resistance") or 0)
    last_close = _last_15m_close()

    try:
        from engine.levels_v2 import get_levels_snapshot
        from engine.structure_v2 import get_structure_snapshot

        levels = get_levels_snapshot(px)
        structure = get_structure_snapshot(px)
        support = float(levels.get("active_support") or 0)
        resistance = float(levels.get("active_resistance") or 0)
        gap = max(float(levels.get("merge_gap") or 0), px * 0.0015)
        s15m = str((structure.get("15m") or {}).get("structure") or "UNCLEAR")
        s5m = str((structure.get("5m") or {}).get("structure") or "UNCLEAR")
    except Exception:
        from engine.levels_v3 import get_levels_snapshot as get_levels_snapshot_v3
        from engine.structure_v3 import get_structure_snapshot as get_structure_snapshot_v3

        levels = get_levels_snapshot_v3(px)
        structure = get_structure_snapshot_v3()
        support = float(pb.get("active_support") or levels.get("active_support") or 0)
        resistance = float(pb.get("active_resistance") or levels.get("active_resistance") or 0)
        gap = max(px * 0.0015, 0.5)
        s15m = str((((structure.get("15m") or {}).get("direction")) or "UNCLEAR"))
        s5m = str((((structure.get("5m") or {}).get("direction")) or "UNCLEAR"))

    side = str(state.pos_side or "").upper()

    if side == "LONG":
        if entry_support > 0 and last_close > 0 and last_close < entry_support:
            log.info(
                f"V2 yapisal cikis LONG: 15m kapanis {last_close:.2f} < "
                f"giris destegi {entry_support:.2f}"
            )
            await executor.close_position("entry_support_break")
            return "ENTRY_SUPPORT_BREAK"
        if entry_support <= 0 and support > 0 and px < support - gap * 0.15:
            log.info(f"V2 yapisal cikis LONG: aktif destek alti {support:.2f}")
            await executor.close_position("v2_support_break")
            return "V2_SUPPORT_BREAK"
        if s15m == "DOWN" and s5m == "DOWN":
            log.info("V2 yapisal cikis LONG: 15m/5m DOWN")
            await executor.close_position("v2_structure_reverse")
            return "V2_STRUCTURE_REVERSE"
    elif side == "SHORT":
        if entry_resistance > 0 and last_close > 0 and last_close > entry_resistance:
            log.info(
                f"V2 yapisal cikis SHORT: 15m kapanis {last_close:.2f} > "
                f"giris direnci {entry_resistance:.2f}"
            )
            await executor.close_position("entry_resistance_break")
            return "ENTRY_RESISTANCE_BREAK"
        if entry_resistance <= 0 and resistance > 0 and px > resistance + gap * 0.15:
            log.info(f"V2 yapisal cikis SHORT: aktif direnc ustu {resistance:.2f}")
            await executor.close_position("v2_resistance_break")
            return "V2_RESISTANCE_BREAK"
        if s15m == "UP" and s5m == "UP":
            log.info("V2 yapisal cikis SHORT: 15m/5m UP")
            await executor.close_position("v2_structure_reverse")
            return "V2_STRUCTURE_REVERSE"
    return None
