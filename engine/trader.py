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


def _startup_warmup_block() -> str:
    """
    Açılış ısınması — veri tazeliği kapısı (timer değil, data-readiness).

    Cold start'ta state.ticks REST bootstrap'tan dolu ama BAYAT; canlı order-flow
    henüz oluşmadı. Bu yüzden ilk girişten önce:
      • en az V3_STARTUP_WARMUP_MIN_SEC canlı akış (order-flow penceresi ısınsın)
      • + ilk canlı 5m mum kapanışı (taze in-session yapı hesaplansın)
    İkisi sağlanınca kalıcı olarak açılır (bir kez). Seans ortasını etkilemez.
    """
    if not bool(getattr(cfg, "V3_STARTUP_WARMUP_ENABLED", True)):
        return ""
    if getattr(state, "startup_warmup_done", False):
        return ""
    start = float(getattr(state, "session_start_ts", 0) or 0)
    if start <= 0:
        state.startup_warmup_done = True
        return ""
    now = time.time()
    elapsed = now - start
    min_sec = float(getattr(cfg, "V3_STARTUP_WARMUP_MIN_SEC", 150) or 150)
    need_5m = bool(getattr(cfg, "V3_STARTUP_REQUIRE_5M_CLOSE", True))
    five_m_closed = int(now // 300) > int(start // 300)
    if elapsed >= min_sec and (five_m_closed or not need_5m):
        state.startup_warmup_done = True
        log.info(
            f"Acilis isinmasi tamam: {elapsed:.0f}s canli akis + 5m kapanis "
            f"-> girisler aktif"
        )
        return ""
    kalan = max(0.0, min_sec - elapsed)
    parts = []
    if kalan > 0:
        parts.append(f"order-flow ısınması {kalan:.0f}s")
    if need_5m and not five_m_closed:
        parts.append("ilk canlı 5m kapanışı bekleniyor")
    return "açılış ısınması: " + ", ".join(parts) + " (veri tazeleniyor)"


def _reclaim_trigger(side: str, candle: dict | None = None) -> bool:
    """
    Intra-bar seviye-reclaim olayı: fiyat destek/dirençte + 1m order-flow teyidi
    + mikro-onay (1m kapanış seviyenin doğru tarafında). Sadece TETİK görevi —
    asıl giriş kararı (RR/edge/tradeability/min-SL) decision pipeline'da verildi.
    """
    if not bool(getattr(cfg, "V3_RECLAIM_ENTRY_ENABLED", True)):
        return False
    side = (side or "").upper()
    px = float(state.mark_price or state.price or 0)
    if px <= 0 or side not in ("LONG", "SHORT"):
        return False
    try:
        from engine.breakout import get_active_levels
        from engine.cvd_v3 import get_cvd_snapshot
        from engine.v3_common import bars_1m

        lv = get_active_levels(px) or {}
        ref_s = float(lv.get("support") or 0)
        ref_r = float(lv.get("resistance") or 0)
        cdir = str((get_cvd_snapshot() or {}).get("direction") or "").upper()
        near = float(getattr(cfg, "V3_RECLAIM_NEAR_PCT", 0.0025) or 0.0025)

        last = candle if candle else ((bars_1m(2) or [{}])[-1])
        c = float(last.get("close", 0) or 0)
        o = float(last.get("open", 0) or 0)

        if side == "LONG" and ref_s > 0:
            at_level = px <= ref_s * (1.0 + near)
            flow_ok = cdir == "BULL"                 # 1m akış alıma döndü
            micro = c > 0 and c >= ref_s and c >= o  # kapanış destek üstü + yeşil
            return at_level and flow_ok and micro
        if side == "SHORT" and ref_r > 0:
            at_level = px >= ref_r * (1.0 - near)
            flow_ok = cdir == "BEAR"
            micro = c > 0 and c <= ref_r and c <= o
            return at_level and flow_ok and micro
    except Exception as ex:
        log.warning(f"reclaim trigger: {ex}")
    return False


async def _maybe_runner_reversal_exit() -> bool:
    """
    RANGE runner: kendi hedefine (SHORT→destek, LONG→direnç) ulaştı VE order-flow
    ters döndüyse runner'ı market'te kapat. Hem kârı kilitler hem slot'u boşaltır
    (ters yön açılabilsin). Yalnız TP1 sonrası; dönüş teyidi yoksa pozisyon kalır.
    """
    if not bool(getattr(cfg, "V3_RUNNER_REVERSAL_EXIT_ENABLED", True)):
        return False
    if not (state.in_position and state.pos_tp1_hit):
        return False
    side = (state.pos_side or "").upper()
    mark = float(state.mark_price or state.price or 0)
    if mark <= 0 or side not in ("LONG", "SHORT"):
        return False
    try:
        from engine.breakout import get_active_levels
        from engine.cvd_v3 import get_cvd_snapshot

        lv = get_active_levels(mark) or {}
        ref_s = float(lv.get("support") or 0)
        ref_r = float(lv.get("resistance") or 0)
        cvd = get_cvd_snapshot() or {}
        cum = float(cvd.get("cumulative", 0) or 0)
        cdir = str(cvd.get("direction") or "").upper()
        near = float(getattr(cfg, "V3_RUNNER_TARGET_NEAR_PCT", 0.002) or 0.002)
        thr = float(getattr(cfg, "V3_RUNNER_REVERSAL_CVD", 4000) or 4000)

        hit_target = reversed_flow = False
        if side == "SHORT" and ref_s > 0:
            hit_target = mark <= ref_s * (1.0 + near)
            reversed_flow = (cdir == "BULL") or (cum >= thr)
        elif side == "LONG" and ref_r > 0:
            hit_target = mark >= ref_r * (1.0 - near)
            reversed_flow = (cdir == "BEAR") or (cum <= -thr)

        if hit_target and reversed_flow:
            from execution.executor import close_position

            log.info(
                f"[RUNNER-REVERSAL] {side} hedefe ulastı "
                f"(mark={mark:.2f} S={ref_s:.2f} R={ref_r:.2f}) + akış ters "
                f"(cvd={cum:+.0f} {cdir}) → runner kapatılıyor, slot serbest"
            )
            await close_position(reason="runner_target_reversal")
            return True
    except Exception as ex:
        log.warning(f"runner reversal exit: {ex}")
    return False


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

    warmup = _startup_warmup_block()
    if warmup:
        log.info(f"Giriş atlandı — {warmup} ({source})")
        state.no_entry_reason = f"[STARTUP_WARMUP] {warmup}"
        if getattr(cfg, "STRATEGY_V3_ENABLED", False) and details.get("v3_mode"):
            from engine.no_trade_log_v3 import log_execute_block

            log_execute_block("startup_warmup", warmup, source=source)
        return False

    reload_keys()
    direction = (details.get("direction") or "").upper()
    if not direction:
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
        # RANGE runner: hedefe ulaşıp akış ters dönerse kapat (slot serbest)
        if await _maybe_runner_reversal_exit():
            update_trend("1m")
            return
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
        # Intra-bar seviye-reclaim girişi: karar zaten LONG/SHORT + tüm kapıları
        # geçtiyse VE fiyat destek/dirençte akış teyidiyle reclaim yaptıysa,
        # 15m kapanışı beklemeden gir (bounce'u kenardan yakala).
        act = str(snap.get("action") or "")
        if act in ("LONG", "SHORT") and _reclaim_trigger(act, candle):
            details = dict(snap.get("details") or {})
            if details:
                log.info(
                    f"[RECLAIM] {act} seviye-reclaim + akış teyidi → intra-bar giriş"
                )
                await execute_entry(details, source="v3-reclaim")
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
