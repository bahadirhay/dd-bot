from __future__ import annotations
"""
engine/entry_timer.py — Giriş zamanlaması.

ENTRY_MODE=break: bookTicker → kırılım.
ENTRY_MODE=range: destek/direnç fade (TP band içi).
ENTRY_MODE=hybrid: band içi range, dışı break.
ENTRY_MODE=confirm: 1m mum onayı.
"""
import asyncio
import time

from core.config import cfg
from core.state import state
from core.logger import get_logger

log = get_logger("EntryTimer")

_signal_details = None
_bars_since_signal = 0
_entry_callback = None
_tick_lock = asyncio.Lock()


def set_callback(fn):
    global _entry_callback
    _entry_callback = fn


def arm(details: dict):
    global _signal_details, _bars_since_signal
    _signal_details = details
    _bars_since_signal = 0
    state.waiting_entry = True
    state.waiting_dir = details["direction"]
    state.waiting_since = time.time()
    state.entry_bars_left = cfg.ENTRY_TIMEOUT
    log.info(
        f"Giriş bekleniyor (confirm): {details['direction']}  "
        f"max {cfg.ENTRY_TIMEOUT} × 1m"
    )


async def on_price_tick(price: float) -> None:
    """bookTicker — giriş + yapısal çıkış (tek kaynak)."""
    if price <= 0:
        return
    if not getattr(state, "exchange_reconciled", True):
        return
    in_grace = time.time() < getattr(state, "startup_grace_until", 0)
    mode = getattr(cfg, "ENTRY_MODE", "break").lower()
    v3_enabled = bool(getattr(cfg, "STRATEGY_V3_ENABLED", False))
    if mode not in ("break", "realtime", "range", "hybrid"):
        return
    if state.waiting_entry:
        return

    from engine.breakout import _lock

    from execution.executor import is_position_opening

    async with _lock():
        if is_position_opening():
            return

        if state.in_position:
            from engine.breakout import (
                check_structural_exit,
                update_position_context,
                mark_structural_close_started,
            )

            update_position_context(price)
            if in_grace:
                return

            reason = check_structural_exit(price)
            if reason:
                mark_structural_close_started()
                await _structural_close(reason)
                return

            flip_details = None
            if (not v3_enabled) and mode in ("range", "hybrid"):
                from engine.range_trade import check_range_entry

                flip_details = check_range_entry(price)
            if (not v3_enabled) and (not flip_details) and mode in ("break", "realtime", "hybrid"):
                from engine.breakout import check_breakout

                flip_details = check_breakout(price)
            if flip_details and _entry_callback:
                ok = await _entry_callback(flip_details)
                if ok:
                    return
            return

        if in_grace:
            return

        if v3_enabled:
            # V3 acikken legacy range/breakout tick giris motorunu tamamen kapat.
            return

        details = None
        if mode in ("range", "hybrid"):
            from engine.range_trade import check_range_entry

            details = check_range_entry(price)

        if not details and mode in ("break", "realtime", "hybrid"):
            from engine.breakout import check_breakout

            details = check_breakout(price)

        if not details or not _entry_callback:
            return
        await _entry_callback(details)


async def _structural_close(reason: str) -> None:
    from execution.executor import close_position

    log.info(f"YAPISAL ÇIKIŞ (bookTicker): {reason}  side={state.pos_side}")
    await close_position(reason)


async def on_1m_bar(candle: dict):
    global _signal_details, _bars_since_signal

    if getattr(cfg, "ENTRY_MODE", "break").lower() != "confirm":
        return
    if not state.waiting_entry or _signal_details is None:
        return

    from core.state import effective_price
    _bars_since_signal += 1
    state.entry_bars_left = cfg.ENTRY_TIMEOUT - _bars_since_signal
    direction = _signal_details["direction"]

    if _bars_since_signal > cfg.ENTRY_TIMEOUT:
        log.info(f"Giriş zaman aşımı — {direction} (confirm)")
        _cancel()
        return

    cvd_5m = state.cvd_5m
    taker = state.taker_ratio
    bar_delta = candle.get("delta", 0)

    if direction == "LONG":
        momentum_ok = cvd_5m > cfg.CVD_MIN and taker >= cfg.TAKER_MIN
        pullback_ok = (
            bar_delta > 0
            and cvd_5m > 0
            and effective_price() > _signal_details.get("sl", 0)
        )
    else:
        momentum_ok = cvd_5m < -cfg.CVD_MIN and (1 - taker) >= cfg.TAKER_MIN
        pullback_ok = (
            bar_delta < 0
            and cvd_5m < 0
            and effective_price() < _signal_details.get("sl", float("inf"))
        )

    if direction == "LONG" and cvd_5m < 0:
        _cancel()
        return
    if direction == "SHORT" and cvd_5m > 0:
        _cancel()
        return

    if momentum_ok or pullback_ok:
        reason = "momentum" if momentum_ok else "pullback"
        details = _signal_details.copy()
        details["entry_reason"] = reason
        _cancel()
        if _entry_callback:
            await _entry_callback(details)


def _cancel():
    global _signal_details, _bars_since_signal
    _signal_details = None
    _bars_since_signal = 0
    state.waiting_entry = False
    state.waiting_dir = ""
    state.entry_bars_left = 0
