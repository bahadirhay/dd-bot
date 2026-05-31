"""
execution/position_lifecycle.py — Pozisyon kapanışı tek kapı (seviye sıfırlama dahil).

Bot close_position, paper_close, exchange user stream → buradan geçer.
Kapanışta borsadaki algo SL/TP ve açık emirler iptal edilir.
"""
from __future__ import annotations

from core.state import state
from core.logger import get_logger

log = get_logger("Lifecycle")

_finalizing = False


def finalize_position_closed(
    reason: str = "",
    source: str = "bot",
    *,
    exit_px: float = 0.0,
) -> None:
    """
    state sıfırla + breakout seviyelerini swing'e çek.
    idempotent — user stream + executor aynı anda çağırsa tek işlem.
    """
    global _finalizing
    if _finalizing:
        return
    if not state.in_position and not state.position_breakout:
        return

    _finalizing = True
    try:
        prev_side = state.pos_side or "?"
        log.info(
            f"Pozisyon finalize [{source}]: {prev_side}  sebep={reason or '—'}"
        )
        state.last_close_reason = reason
        state.last_close_source = source

        try:
            from engine.market_narrative import record_trade_exit

            pb = state.position_breakout or {}
            record_trade_exit(
                direction=state.pos_side or str(pb.get("direction", "")),
                reason=reason,
                entry=float(state.pos_entry or 0),
                exit_px=float(
                    exit_px or state.mark_price or state.price or 0
                ),
                break_level=float(pb.get("break_level") or 0),
            )
        except Exception as ex:
            log.warning(f"Çıkış sonrası narrative: {ex}")

        state.reset_position()
    finally:
        _finalizing = False


async def async_finalize_position_closed(
    reason: str = "",
    source: str = "bot",
    *,
    exit_price: float = 0.0,
    pnl: float | None = None,
) -> None:
    """Önce DB kapanış + borsa emir temizliği, sonra state sıfırla."""
    from core.config import is_paper_mode

    if state.in_position:
        try:
            from botlog.db import record_position_close

            record_position_close(
                reason or "closed",
                exit_price=exit_price,
                pnl=pnl,
                source=source,
            )
        except Exception as ex:
            log.warning(f"Trade DB kapanış: {ex}")

    if not is_paper_mode():
        try:
            from execution.protection_orders import cancel_all_open_protection_orders

            await cancel_all_open_protection_orders(reason or source)
        except Exception as ex:
            log.warning(f"Koruma emirleri iptal hatası: {ex}")

    if state.in_position or state.position_breakout:
        finalize_position_closed(
            reason, source, exit_px=float(exit_price or 0)
        )
