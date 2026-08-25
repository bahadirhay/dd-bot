"""
engine/market_reading_v3.py — Geriye uyumluluk: tek durum modeline delege eder.
"""
from __future__ import annotations

from core.config import cfg
from core.state import state
from engine.market_state_v3 import (
    apply_state_to_active_band,
    get_market_state,
    update_market_state,
)


def apply_market_reading_to_snap(
    snap: dict,
    price: float,
    bars15: list[dict] | None = None,
) -> dict:
    zones = snap.get("zones") or []
    ms = update_market_state(price, zones=zones, bars15=bars15)
    snap["market_state"] = ms
    snap["market_story"] = state.v3_market_story
    snap["zone_layers"] = state.v3_zone_layers
    snap["trade_map"] = state.v3_trade_map
    snap["liquidity_pools"] = state.v3_liquidity_pools
    sr_only = getattr(cfg, "V3_SR_ENABLED", True) and getattr(cfg, "V3_SR_ONLY", True)
    if sr_only and getattr(cfg, "V3_SR_ACTIVE_BAND", True):
        return snap
    if sr_only and not getattr(cfg, "V3_LAYER_BAND_ACTIVE", False):
        return snap
    return apply_state_to_active_band(snap, price)


def get_market_story() -> dict:
    return dict(getattr(state, "v3_market_story", None) or {})


def get_zone_layers() -> dict:
    return dict(getattr(state, "v3_zone_layers", None) or {})


def get_trade_map() -> dict:
    return dict(getattr(state, "v3_trade_map", None) or {})
