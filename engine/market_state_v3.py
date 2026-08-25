"""
engine/market_state_v3.py — Tek piyasa durumu.

Pipeline (tek otorite):
  STRUCTURE → LIQUIDITY → EVENT → COLLAPSE (karar)
                           → EXECUTION FILTER (zaman/kalite, karar degil)
                           → layers
"""
from __future__ import annotations

from core.config import cfg
from core.logger import get_logger
from core.state import state
from engine.event_engine_v3 import (
    build_event_timeline,
    decision_signals,
    event_log_line,
)
from engine.execution_brain_v3 import build_execution_filter, execution_filter_log_line
from engine.trade_verdict_v3 import attach_trade_verdict, verdict_log_line
from engine.liquidity_engine_v3 import compute_liquidity, liquidity_log_line
from engine.structure_engine_v3 import compute_structure, structure_log_line
from engine.state_collapse_v3 import collapse_log_line, collapse_market_state
from engine.zone_layers_v3 import build_trade_map, build_zone_layers, layer_to_level_dict, layers_log_line
from engine.v3_common import bars_15m

log = get_logger("MarketStateV3")
_last_state_key = ""


def update_market_state(
    price: float = 0,
    *,
    zones: list[dict] | None = None,
    bars15: list[dict] | None = None,
) -> dict:
    if not getattr(cfg, "STRATEGY_V3_ENABLED", True):
        return {}

    px = float(price or state.mark_price or state.price or 0)
    n_struct = int(getattr(cfg, "V3_STRUCTURE_BARS", 96) or 96)
    bars = list(bars15 or bars_15m(max(n_struct, 96)))

    structure = compute_structure(px, bars[-128:] if len(bars) > 128 else bars)
    liquidity = compute_liquidity(px, structure, bars)
    events = build_event_timeline(px, structure, liquidity, bars)

    # COLLAPSE = tek karar otoritesi (inertia + trap dahil)
    collapse = collapse_market_state(structure, liquidity, events, px)

    execution_filter = build_execution_filter(
        events, px, bars, liquidity=liquidity, collapse=collapse
    )

    signals = decision_signals(events, structure, collapse)

    zone_list = list(zones or getattr(state, "v3_zones", None) or [])
    layers = build_zone_layers(zone_list, bars, px, liquidity.get("pools") or [])
    trade_map = build_trade_map(
        {
            "bias": structure.get("bias"),
            "pattern": structure.get("pattern"),
            "summary": structure.get("summary"),
            "is_lower_high": structure.get("is_lower_high"),
            "compression": structure.get("compression"),
        },
        layers,
        px,
    )

    ms = {
        "price": px,
        "pipeline": "structure→liquidity→events→collapse→exec_filter→verdict→layers",
        "authority": "unified_verdict",
        "structure": structure,
        "liquidity": liquidity,
        "events": events,
        "collapse": collapse,
        "execution_filter": execution_filter,
        "signals": signals,
        "execution_window": execution_filter.get("time_validity") or {},
        "urgency": execution_filter.get("urgency") or {},
        "event_monitor": bool(execution_filter.get("event_monitor")),
        "execution_observable": bool(execution_filter.get("observable")),
        "trade_probability": execution_filter.get("trade_probability", 0),
        "layers": layers,
        "trade_map": trade_map,
        "windows": {
            "structure": n_struct,
            "liquidity_micro": liquidity.get("micro_bars"),
            "liquidity_macro": liquidity.get("macro_bars"),
            "events": events.get("bars_used"),
        },
    }
    ms = attach_trade_verdict(ms)

    state.v3_market_state = ms
    state.v3_liquidity_pools = liquidity.get("pools") or []
    state.v3_liquidity_bias = liquidity.get("bias") or {}
    state.v3_market_story = {
        "pattern": structure.get("pattern"),
        "bias": structure.get("bias"),
        "summary": structure.get("summary"),
        "compression": structure.get("compression"),
        "is_lower_high": structure.get("is_lower_high"),
        "weak_bounce": structure.get("weak_bounce"),
    }
    state.v3_zone_layers = layers
    state.v3_trade_map = trade_map
    state.v3_structure = {
        "1h": {
            "direction": structure.get("dir_1h", "UNCLEAR"),
            "range_locked": structure.get("range_locked", False),
        },
        "alignment": {
            "effective_bias": structure.get("bias"),
            "direction": structure.get("dir_1h"),
        },
        "effective_bias": structure.get("bias"),
        "market_story": state.v3_market_story,
    }
    state.v3_range_locked = bool(structure.get("range_locked"))

    global _last_state_key
    key = (
        f"{collapse.get('mode')}|{collapse.get('dominant_bias')}|"
        f"{collapse.get('state_score')}|{px:.0f}"
    )
    if key != _last_state_key:
        _last_state_key = key
        log.info(structure_log_line(structure))
        log.info(liquidity_log_line(liquidity))
        log.info(event_log_line(events))
        log.info(collapse_log_line(collapse))
        log.info(execution_filter_log_line(ms.get("execution_filter")))
        log.info(verdict_log_line(ms.get("trade_verdict")))
        log.info(layers_log_line(layers))
        if signals.get("reasons"):
            log.info(f"[SIGNALS] {' ; '.join(signals['reasons'])}")
        if execution_filter.get("event_monitor"):
            opp = execution_filter.get("opportunity") or {}
            log.info(
                f"[EVENT-MONITOR] tier={opp.get('tier')} setup={opp.get('half_life_score', 0):.0f} "
                f"— trade yok, erken reversal / kirilgan izleme"
            )

    return ms


def get_market_state() -> dict:
    return dict(getattr(state, "v3_market_state", None) or {})


def apply_state_to_active_band(snap: dict, price: float) -> dict:
    ms = snap.get("market_state") or get_market_state()
    layers = ms.get("layers") or snap.get("zone_layers") or {}
    px = float(price or snap.get("price", 0) or 0)
    sm = layers.get("supply_major")
    dw = layers.get("demand_weak")
    dl = layers.get("demand_liq")
    if not sm:
        return snap
    sup_layer = dw
    if dl:
        dl_lo = float(dl.get("low", 0) or 0)
        dw_c = float((dw or {}).get("center", 0) or 0)
        if dl_lo > 0 and (dw_c <= 0 or dl_lo < dw_c * 0.998):
            sup_layer = dl
    if not sup_layer:
        return snap
    sup = layer_to_level_dict(sup_layer, "support")
    res = layer_to_level_dict(sm, "resistance")
    if float(sup["price"]) >= float(res["price"]):
        return snap
    from engine.levels_v3 import _finalize_active_pair

    active = _finalize_active_pair(sup, res, px)
    active["layer_band"] = True
    active["lifecycle_band"] = True
    active["unified_state"] = True
    mid = layers.get("supply_mid")
    liq = layers.get("demand_liq")
    if mid:
        active["mid_supply_low"] = float(mid.get("low", 0) or 0)
        active["mid_supply_high"] = float(mid.get("high", 0) or 0)
    if liq:
        active["liquidity_support_low"] = float(liq.get("low", 0) or 0)
        active["liquidity_support_high"] = float(liq.get("high", 0) or 0)
    snap["active"] = active
    return snap


def gate_scenario_with_state(scenario_name: str, side: str) -> tuple[bool, str]:
    """Tek kapi: trade_verdict_v3 (collapse + exec birlesik)."""
    from engine.trade_verdict_v3 import trade_entry_allowed

    ms = get_market_state()
    if not ms:
        return True, ""
    return trade_entry_allowed(ms, scenario_name, side)
