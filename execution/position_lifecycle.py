"""
execution/position_lifecycle.py — Pozisyon kapanışı tek kapı (seviye sıfırlama dahil).

Bot close_position, paper_close, exchange user stream → buradan geçer.
Kapanışta borsadaki algo SL/TP ve açık emirler iptal edilir.
"""
from __future__ import annotations

import time

from core.state import state
from core.logger import get_logger

log = get_logger("Lifecycle")

_finalizing = False


def _normalize_close_reason(reason: str, exit_price: float = 0.0) -> str:
    raw = str(reason or "closed")
    if raw == "stop_loss" and state.pos_tp1_hit:
        return "runner_sl"
    if raw not in ("exchange_closed", "exchange_closed_poll"):
        return raw
    if not state.pos_tp1_hit:
        return raw

    # TP1 sonrası aktif TP2 yoksa kalan pozisyonun beklenen kapanışı trailing SL'dir.
    # Sync/poll event'i emir tipini taşımaz; SL'e yakınsa açıkça runner SL olarak yaz.
    try:
        from core.config import cfg

        if bool(getattr(cfg, "SEND_TP2_ORDER", False)):
            return raw
    except Exception:
        pass

    sl = float(state.pos_sl or 0)
    xp = float(exit_price or state.mark_price or state.price or 0)
    entry = float(state.pos_entry or 0)
    if sl > 0 and xp > 0 and entry > 0:
        diff_bps = abs(xp - sl) / entry * 10000.0
        if diff_bps <= 80:
            return "runner_sl_sync"
    return "runner_closed_sync"


def _arm_runner_reentry_block(reason: str, side: str, exit_px: float) -> None:
    if not str(reason or "").startswith("runner_"):
        return
    try:
        from core.config import cfg

        cd = float(getattr(cfg, "RUNNER_SL_REENTRY_COOLDOWN_SEC", 300) or 300)
    except Exception:
        cd = 300.0
    if cd <= 0:
        return
    state.runner_reentry_block_until = time.time() + cd
    state.runner_reentry_block_reason = (
        f"{side} runner kapandı; yeni S/R sinyali için {cd:.0f}s bekleniyor"
    )
    log.info(
        f"Runner re-entry kilidi: {side} {cd:.0f}s "
        f"exit={float(exit_px or 0):.2f} reason={reason}"
    )


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
        xpx = float(exit_px or state.mark_price or state.price or 0)
        log.info(
            f"Pozisyon finalize [{source}]: {prev_side}  sebep={reason or '—'}"
        )
        state.last_close_reason = reason
        state.last_close_source = source
        state.last_close_side = prev_side
        state.last_close_price = xpx
        _arm_runner_reentry_block(reason, prev_side, xpx)

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

        try:
            from core.config import cfg
            from engine.adaptation_v3 import record_trade_outcome

            if getattr(cfg, "STRATEGY_V3_ENABLED", True):
                entry = float(state.pos_entry or 0)
                xpx = float(exit_px or state.mark_price or state.price or 0)
                pnl_pct = 0.0
                if entry > 0 and xpx > 0:
                    side = str(state.pos_side or "").upper()
                    if side == "LONG":
                        pnl_pct = (xpx - entry) / entry * 100.0
                    elif side == "SHORT":
                        pnl_pct = (entry - xpx) / entry * 100.0
                snap = getattr(state, "v3_trade_brain_snapshot", None) or {}
                ms = snap if snap else getattr(state, "v3_market_state", None) or {}
                collapse = ms.get("collapse") or {}
                record_trade_outcome(
                    won=pnl_pct > 0,
                    pnl_pct=pnl_pct,
                    market_state=ms,
                    controller=str(
                        snap.get("controller") or collapse.get("controller") or ""
                    ),
                )
        except Exception as ex:
            log.warning(f"V3 adaptation: {ex}")

        _arm_loss_cooldown(reason, prev_side, xpx)

        state.reset_position()
    finally:
        _finalizing = False


def _arm_loss_cooldown(reason: str, side: str, exit_px: float) -> None:
    """
    Kayıp-sonrası artan cooldown (overtrading/churn freni).

    Art arda kayıpta bekleme süresi katlanarak artar (base × ardışık, tavanlı).
    Kazançta sayaç sıfırlanır ve cooldown temizlenir. Disiplinli trader mantığı:
    chop'ta üst üste stop yiyince geri çekil, piyasanın çözülmesini bekle.
    """
    try:
        from core.config import cfg

        if not bool(getattr(cfg, "V3_LOSS_COOLDOWN_ENABLED", True)):
            return
        entry = float(state.pos_entry or 0)
        xp = float(exit_px or state.mark_price or state.price or 0)
        if entry <= 0 or xp <= 0:
            return
        s = str(side or "").upper()
        if s == "LONG":
            pnl_pct = (xp - entry) / entry * 100.0
        elif s == "SHORT":
            pnl_pct = (entry - xp) / entry * 100.0
        else:
            return

        loss_thr = float(getattr(cfg, "V3_LOSS_COOLDOWN_MIN_PNL_PCT", -0.05) or -0.05)
        if pnl_pct > loss_thr:
            # kazanç / nötr → sayaç sıfırla, kilidi temizle
            state.consecutive_losses = 0
            state.loss_cooldown_until = 0.0
            state.loss_cooldown_reason = ""
            return

        n = int(getattr(state, "consecutive_losses", 0) or 0) + 1
        state.consecutive_losses = n
        base = float(getattr(cfg, "V3_LOSS_COOLDOWN_BASE_SEC", 600) or 600)
        max_mult = float(getattr(cfg, "V3_LOSS_COOLDOWN_MAX_MULT", 4) or 4)
        mult = min(n, max_mult)
        cd = base * mult
        import time as _t

        state.loss_cooldown_until = _t.time() + cd
        state.loss_cooldown_reason = (
            f"{n} ardışık kayıp (son {s} {pnl_pct:+.2f}%); "
            f"{cd / 60.0:.0f}dk giriş molası"
        )
        log.info(f"Kayıp cooldown: {state.loss_cooldown_reason} reason={reason}")
    except Exception as ex:
        log.warning(f"loss cooldown arm: {ex}")


async def async_finalize_position_closed(
    reason: str = "",
    source: str = "bot",
    *,
    exit_price: float = 0.0,
    pnl: float | None = None,
) -> None:
    """Önce DB kapanış + borsa emir temizliği, sonra state sıfırla."""
    from core.config import is_paper_mode

    reason = _normalize_close_reason(reason, exit_price)

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
            reason,
            source,
            exit_px=float(exit_price or state.mark_price or state.price or 0),
        )
