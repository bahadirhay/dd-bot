"""
engine/execution_window_v3.py — Geriye uyumluluk; mantik execution_brain_v3'te.
"""
from __future__ import annotations

from engine.execution_brain_v3 import execution_window_open as execution_window_open
from engine.execution_brain_v3 import update_execution_anchor


def compute_execution_window(
    market_state: dict,
    price: float = 0,
    bars15: list[dict] | None = None,
) -> dict:
    events = market_state.get("events") or {}
    return update_execution_anchor(events, price, bars15)
