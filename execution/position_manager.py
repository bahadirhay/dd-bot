"""
execution/position_manager.py
Her 1m: TP1, TP1 retest eylemi, trend/CVD çıkışı.
Yapısal kırılım çıkışı yalnızca bookTicker (entry_timer) — burada yok.
"""
from __future__ import annotations
import time
from typing import Optional

from core.config import cfg, is_paper_mode
from core.state import state, data_is_fresh
from core.logger import get_logger
from engine.trend import update_trend, STRENGTH_TRADE
from utils.notifier import notify_tp1

log = get_logger("PosMgr")


async def check(executor) -> Optional[str]:
    if not state.in_position:
        return None

    if time.time() < getattr(state, "startup_grace_until", 0):
        return None

    if is_paper_mode() or cfg.API_KEY:
        still_open = await executor.sync_position_state()
        if not still_open:
            return "PAPER_CLOSED" if is_paper_mode() else "EXCHANGE_CLOSED"

    if not is_paper_mode() and cfg.API_KEY:
        try:
            from execution.protection_orders import manage_position_sl

            await manage_position_sl()
        except Exception as e:
            log.debug(f"SL yönetimi: {e}")

    if executor.check_tp1_hit():
        qty_closed = await executor.on_tp1_hit()
        log.info(f"TP1 DOLDU @ {state.price:.2f} → runner (SL 15m veya TP1)")
        if qty_closed > 0:
            await notify_tp1(state.price, qty_closed, state.pos_entry)
        return "TP1_HIT"

    strategy = str((state.position_breakout or {}).get("strategy") or "")
    if strategy in ("v2", "v3"):
        from engine.position_manager_v2 import check_v2_position

        ret = await check_v2_position(executor)
        if ret:
            return ret
        if not data_is_fresh(10):
            log.warning("Veri 10sn'den eski → kapatılıyor")
            await executor.close_position("stale_data")
            return "STALE_DATA"
        return None

    px = state.price or state.mark_price
    if px > 0:
        from engine.breakout import update_position_context, handle_tp1_retest

        update_position_context(px)
        ret = await handle_tp1_retest(executor)
        if ret:
            return ret

    tv = update_trend("posmgr")
    side = state.pos_side
    if side == "LONG" and tv["bias"] == "DOWN" and tv["strength"] >= STRENGTH_TRADE:
        log.info(f"Trend ters (DOWN güç={tv['strength']}) → LONG kapatılıyor")
        await executor.close_position("trend_reverse")
        return "TREND_REVERSE"
    if side == "SHORT" and tv["bias"] == "UP" and tv["strength"] >= STRENGTH_TRADE:
        log.info(f"Trend ters (UP güç={tv['strength']}) → SHORT kapatılıyor")
        await executor.close_position("trend_reverse")
        return "TREND_REVERSE"

    cvd = state.cvd_5m
    if state.pos_side == "LONG" and cvd < -200:
        log.info(f"CVD sert negatif ({cvd:.0f}) → kapatılıyor")
        await executor.close_position("cvd_reverse")
        return "CVD_REVERSE"
    if state.pos_side == "SHORT" and cvd > 200:
        log.info(f"CVD sert pozitif ({cvd:.0f}) → kapatılıyor")
        await executor.close_position("cvd_reverse")
        return "CVD_REVERSE"

    if not data_is_fresh(10):
        log.warning("Veri 10sn'den eski → kapatılıyor")
        await executor.close_position("stale_data")
        return "STALE_DATA"

    return None
