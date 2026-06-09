"""
engine/trader.py — Piyasa oku → kırılım / onay ile pozisyon.

ENTRY_MODE=break: 15m sadece seviye; giriş bookTicker + breakout.
"""
from __future__ import annotations

import time

from core.config import cfg, is_paper_mode
from core.state import state
from core.logger import get_logger
from engine.trend import update_trend, trade_direction, STRENGTH_TRADE, on_15m_closed
from engine.signal import evaluate_signal, make_trade_details
from engine.entry_timer import arm

log = get_logger("Trader")


def _period_key() -> int:
    f = state.forming_15m or {}
    return int(f.get("period_start", 0))


def _already_traded_this_period() -> bool:
    if getattr(cfg, "ENTRY_MODE", "break").lower() in ("break", "realtime"):
        return False
    return getattr(state, "auto_trade_period", 0) == _period_key() and _period_key() > 0


def _mark_traded():
    state.auto_trade_period = _period_key()
    state.last_auto_trade_ts = time.time()


def _runner_reentry_block_message(direction: str) -> str:
    until = float(getattr(state, "runner_reentry_block_until", 0) or 0)
    now = time.time()
    if until <= now:
        if until > 0:
            state.runner_reentry_block_until = 0.0
            state.runner_reentry_block_reason = ""
        return ""
    left = max(until - now, 0.0)
    side = str(getattr(state, "last_close_side", "") or "?")
    px = float(getattr(state, "last_close_price", 0) or 0)
    reason = str(getattr(state, "runner_reentry_block_reason", "") or "")
    detail = reason or f"{side} runner SL sonrası yeni S/R sinyali bekleniyor"
    price_txt = f" exit={px:.2f}" if px > 0 else ""
    return f"{detail}; {direction} girişi {left:.0f}s kilitli{price_txt}"


def _loss_cooldown_message(direction: str) -> str:
    """Art arda kayıp sonrası giriş molası aktifse mesaj döndür, değilse ''."""
    if not bool(getattr(cfg, "V3_LOSS_COOLDOWN_ENABLED", True)):
        return ""
    until = float(getattr(state, "loss_cooldown_until", 0) or 0)
    now = time.time()
    if until <= now:
        if until > 0:
            state.loss_cooldown_until = 0.0
            state.loss_cooldown_reason = ""
        return ""
    left = max(until - now, 0.0)
    reason = str(getattr(state, "loss_cooldown_reason", "") or "")
    detail = reason or "art arda kayıp sonrası giriş molası"
    return f"{detail}; {direction} {left / 60.0:.0f}dk kilitli"


async def execute_entry(details: dict, source: str = "breakout") -> bool:
    """Risk planı + borsa/paper emri."""
    from core.config import reload_keys
    from execution.risk import calculate as calc_risk
    from execution.executor import get_equity_for_risk, open_position, reverse_position
    from utils.notifier import notify_open

    if not cfg.AUTO_TRADE_ENABLED:
        if getattr(cfg, "STRATEGY_V3_ENABLED", False) and details.get("v3_mode"):
            from engine.no_trade_log_v3 import log_execute_block

            log_execute_block(
                "auto_trade_off",
                f"sinyal {details.get('direction')} hazir",
                source=source,
            )
        else:
            log.info(f"Otomatik trade kapalı — sinyal: {details['direction']} ({source})")
        return False

    if not getattr(state, "exchange_reconciled", True):
        log.warning("Startup senkronu bitmeden giriş yapılmıyor")
        return False
    if time.time() < getattr(state, "startup_grace_until", 0):
        log.debug("Startup grace — yeni giriş ertelendi")
        return False

    reload_keys()
    direction = (details.get("direction") or "").upper()
    if not direction:
        return False

    runner_block = _runner_reentry_block_message(direction)
    if runner_block:
        log.info(f"Giriş atlandı — {runner_block} ({source})")
        state.no_entry_reason = f"[RUNNER_REENTRY_WAIT] {runner_block}"
        if getattr(cfg, "STRATEGY_V3_ENABLED", False) and details.get("v3_mode"):
            from engine.no_trade_log_v3 import log_execute_block

            log_execute_block("runner_reentry_wait", runner_block, source=source)
        return False

    loss_block = _loss_cooldown_message(direction)
    if loss_block:
        log.info(f"Giriş atlandı — {loss_block} ({source})")
        state.no_entry_reason = f"[LOSS_COOLDOWN] {loss_block}"
        if getattr(cfg, "STRATEGY_V3_ENABLED", False) and details.get("v3_mode"):
            from engine.no_trade_log_v3 import log_execute_block

            log_execute_block("loss_cooldown", loss_block, source=source)
        return False

    is_reverse = bool(
        state.in_position and state.pos_side and direction != state.pos_side
    )

    from execution.executor import same_direction_position_open

    if is_reverse:
        log.info(
            f"Ters sinyal: {state.pos_side} → {direction}  kaynak={source}"
        )
    else:
        blocked, reason = await same_direction_position_open(direction)
        if blocked:
            log.info(f"Giriş atlandı — {reason} ({source})")
            state.no_entry_reason = reason
            return False

    level = float(
        details.get("break_level") or details.get("range_active_level") or 0
    )
    px = float(
        details.get("price")
        or details.get("signal_price")
        or state.mark_price
        or state.price
        or 0
    )
    v3_scn = str(details.get("v3_scenario") or "")
    v3_range_direct = bool(details.get("v3_mode")) and v3_scn in ("RANGE_BUY", "RANGE_SELL")
    if (
        not is_reverse
        and level > 0
        and px > 0
        and not details.get("v2_mode")
        and not v3_range_direct
    ):
        from engine.market_narrative import trade_entry_allowed

        ok_nar, nar_msg = trade_entry_allowed(direction, level, px)
        if not ok_nar:
            log.info(f"Giriş atlandı — {nar_msg} ({source})")
            state.no_entry_reason = nar_msg
            return False

    balance = await get_equity_for_risk()
    if balance <= 0 and not is_paper_mode():
        log.warning("Bakiye yok — pozisyon açılamadı")
        return False

    min_rr = float(cfg.MIN_RR)
    if details.get("v3_mode"):
        # V3 sinyali RR'yi entry katmaninda (details["rr"]) TP2 bazli hesaplar.
        # Risk katmani TP1 bazli RR kullandigi icin burada tekrar TP1 filtrelemek V3'te yanlis red uretir.
        signal_rr = float(details.get("rr", 0) or 0)
        if signal_rr < float(getattr(cfg, "V3_MIN_RR_RATIO", 2.0)):
            state.no_entry_reason = (
                f"V3 RR yetersiz: {signal_rr:.2f} < {float(getattr(cfg, 'V3_MIN_RR_RATIO', 2.0)):.2f}"
            )
            log.info(f"Giriş atlandı — {state.no_entry_reason} ({source})")
            return False
        min_rr = 0.0
    elif details.get("break_mode"):
        min_rr = float(getattr(cfg, "BREAK_TP1_MIN_RR", 1.2))

    plan = calc_risk(
        direction,
        details["sl"],
        details["tp1"],
        details["tp2"],
        balance,
        entry_price=float(details.get("price") or details.get("signal_price") or 0),
        min_rr=min_rr,
    )
    if not plan.ok():
        log.warning(f"Risk planı red: {plan.warnings}")
        state.no_entry_reason = "; ".join(plan.warnings)
        return False

    prefix = f"[TERS {source}]" if is_reverse else f"[{source}]"
    details["entry_reason"] = f"{prefix} {details.get('entry_reason', '')}".strip()

    if is_reverse:
        ok = await reverse_position(plan)
    else:
        ok = await open_position(plan)

    if ok:
        if details.get("v3_mode") or details.get("v2_mode"):
            from engine.position_manager_v2 import on_entry_filled as on_v2_entry_filled

            on_v2_entry_filled(details)
        elif details.get("pressure_mode"):
            from engine.breakout import on_pressure_entry_filled

            on_pressure_entry_filled(details)
        elif details.get("break_mode"):
            from engine.breakout import on_entry_filled

            on_entry_filled(details)
        elif details.get("range_mode"):
            from engine.range_trade import on_range_entry_filled

            on_range_entry_filled(details)
        _mark_traded()
        mode = "İZLEME" if is_paper_mode() else "CANLI"
        verb = "TERS ÇEVRİLDİ" if is_reverse else "POZİSYON AÇILDI"
        log.info(
            f"{verb} ({mode}) {direction} @ {plan.entry:.2f}  "
            f"kaynak={source}  {details.get('entry_reason', '')}"
        )
        await notify_open(plan, reason=details["entry_reason"])
    return ok


async def _manage_open_position() -> None:
    from core.config import reload_keys
    from execution.risk import calculate as calc_risk
    from execution.executor import get_equity_for_risk, reverse_position
    from utils.notifier import notify_open

    tv = state.trend_view or update_trend("pos")
    side = state.pos_side
    if not side:
        return

    if side == "LONG" and tv["bias"] == "DOWN" and tv["strength"] >= STRENGTH_TRADE:
        new_dir = "SHORT"
    elif side == "SHORT" and tv["bias"] == "UP" and tv["strength"] >= STRENGTH_TRADE:
        new_dir = "LONG"
    else:
        return

    direction, details = make_trade_details(new_dir)
    if direction == "FLAT":
        return

    reload_keys()
    balance = await get_equity_for_risk()
    plan = calc_risk(direction, details["sl"], details["tp1"], details["tp2"], balance)
    if not plan.ok():
        return

    log.info(f"TREND TERS: {side} → {direction}  ({tv['summary']})")
    await reverse_position(plan)
    _mark_traded()
    await notify_open(plan, reason=f"TERS trend: {tv['summary']}")


async def on_15m_market(candle: dict) -> None:
    """15m kapandı — seviyeler + rejim bilgisi; break modunda giriş YOK."""
    on_15m_closed(candle)

    if state.in_position and state.pos_tp1_hit:
        close_15m = float(candle.get("close", 0) or 0)
        if close_15m > 0:
            from execution.protection_orders import apply_15m_trailing_sl

            await apply_15m_trailing_sl(close_15m)

    if getattr(cfg, "STRATEGY_V3_ENABLED", False):
        from engine.levels_v3 import update_levels
        from engine.structure_v3 import update_structure
        from engine.cvd_v3 import update_cvd_snapshot
        from engine.decision_v3 import update_decision, log_decision_diag
        from engine.thesis_v3 import check_thesis_on_15m_close
        import execution.executor as executor_mod

        close_15m = float(candle.get("close", 0) or 0)
        update_levels()
        update_structure()
        update_cvd_snapshot()

        if state.in_position and close_15m > 0:
            await check_thesis_on_15m_close(executor_mod, close_15m)

        snap = update_decision()
        state.no_entry_reason = str(snap.get("reason") or snap.get("action") or "")
        log_decision_diag(snap, tag="15m-kapanis", force=True)

        # Ters sinyal: pozisyon açıkken geçerli ters thesis → hemen ters çevir.
        # (Thesis fail + kapatma durumunda not state.in_position kolu devreye girer.)
        if (
            state.in_position
            and snap.get("reverse_signal")
            and snap.get("action") in ("LONG", "SHORT")
        ):
            details = dict(snap.get("details") or {})
            if details:
                log.info(
                    f"[V3 TERS] {state.pos_side} → {snap['action']} — "
                    f"ters thesis geçerli, pozisyon çevriliyor"
                )
                await execute_entry(details, source="v3-ters")
            return

        if not state.in_position and snap.get("action") in ("LONG", "SHORT"):
            details = dict(snap.get("details") or {})
            if details:
                await execute_entry(details, source="v3")
        return

    from engine.breakout import refresh_levels

    refresh_levels()

    if state.in_position:
        await _manage_open_position()
        return

    mode = getattr(cfg, "ENTRY_MODE", "break").lower()

    if mode in ("break", "realtime"):
        from engine.operation_state import get_operation_view

        evaluate_signal()
        op = get_operation_view(state.price or state.mark_price)
        state.no_entry_reason = (
            state.no_entry_reason
            or op.get("headline")
            or f"Kırılım modu — seviye hazır; aktif={state.breakout_view.get('status', '?')}"
        )
        return

    if mode in ("range", "hybrid"):
        from engine.operation_state import get_operation_view

        op = get_operation_view(state.price or state.mark_price)
        state.range_view = op.get("range") or state.range_view
        state.no_entry_reason = (
            state.no_entry_reason
            or op.get("headline")
            or f"{mode.upper()} — {state.range_view.get('status', '?')}"
        )
        return

    if state.waiting_entry:
        return

    direction, details = evaluate_signal()
    if direction == "FLAT":
        return

    if mode == "confirm":
        arm(details)
        log.info("Giriş planı — 1m onay bekleniyor (ENTRY_MODE=confirm)")
        return

    if _already_traded_this_period():
        return

    await execute_entry(details, source="15m-trend")


async def on_1m_market(candle: dict) -> None:
    """1m — trend güncelle; sadece eski trend/impulse modunda giriş."""
    if state.in_position and state.pos_tp1_hit:
        from execution.protection_orders import apply_5m_runner_sl_confirm

        await apply_5m_runner_sl_confirm(candle)

    update_trend("1m")

    if state.in_position:
        if getattr(cfg, "STRATEGY_V3_ENABLED", False):
            return
        await _manage_open_position()
        return

    if getattr(cfg, "STRATEGY_V3_ENABLED", False):
        if state.waiting_entry:
            return
        from engine.structure_v3 import update_structure
        from engine.cvd_v3 import update_cvd_snapshot
        from engine.decision_v3 import update_decision

        update_structure()
        update_cvd_snapshot()
        snap = update_decision()
        state.no_entry_reason = str(snap.get("reason") or snap.get("action") or "")
        return

    mode = getattr(cfg, "ENTRY_MODE", "break").lower()
    if mode in ("break", "realtime"):
        return

    if state.waiting_entry:
        return

    if not cfg.AUTO_TRADE_ENABLED or not cfg.IMPULSE_1M_TRADE:
        return
    if mode != "trend":
        return
    if _already_traded_this_period():
        return

    tv = state.trend_view or {}
    if not tv.get("trade_ok"):
        return
    if cfg.REQUIRE_HTF_ALIGN and not tv.get("structure_aligned"):
        return
    if tv.get("phase") not in ("drop", "rise"):
        return

    direction = trade_direction()
    if direction == "FLAT":
        return

    direction, details = make_trade_details(direction)
    if direction == "FLAT":
        return

    log.info(f"IMPULSE giriş: {direction}  ({tv['summary']})")
    await execute_entry(details, source=f"1m-{tv['phase']}")
