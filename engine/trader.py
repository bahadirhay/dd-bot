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

# Cikis debounce sayaclari: anlik (tek-tick) gurultude kapatma. Cikis kosulu
# art arda V3_EXIT_CONFIRM_TICKS kez dogrulaninca tetiklenir; kosul bozulunca sifirlanir.
_exit_confirm = {"runner_rev": 0, "flow_exit": 0, "score_exit": 0}

# Korumali cikistan sonra re-entry cooldown: ac-kapa cigini (24x LONG loop) onler.
_last_protect_exit_ts = 0.0


def _mark_protect_exit() -> None:
    global _last_protect_exit_ts
    _last_protect_exit_ts = time.time()


def _reentry_blocked() -> tuple[bool, float]:
    cd = float(getattr(cfg, "V3_REENTRY_COOLDOWN_SEC", 120) or 0)
    if cd <= 0:
        return False, 0.0
    elapsed = time.time() - _last_protect_exit_ts
    return (elapsed < cd), max(0.0, cd - elapsed)


def _confirm_tick(key: str, condition: bool) -> bool:
    """Ardisik-teyit sayaci (zaman degil, sayi). condition art arda N kez dogru -> True."""
    n = int(getattr(cfg, "V3_EXIT_CONFIRM_TICKS", 3) or 3)
    if condition:
        _exit_confirm[key] = _exit_confirm.get(key, 0) + 1
    else:
        _exit_confirm[key] = 0
    return _exit_confirm[key] >= max(n, 1)


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
    Açılış ısınması — koruma amaçlı kısa pencere (veri eksikliği değil).

    REST bootstrap CVD/taker/hacmi zaten yükler; bu kapı yalnızca canlı aggTrade
    akışının birkaç saniye oturması içindir. Varsayılan: 30s, 5m kapanışı zorunlu değil.
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
    min_sec = float(getattr(cfg, "V3_STARTUP_WARMUP_MIN_SEC", 30) or 30)
    need_5m = bool(getattr(cfg, "V3_STARTUP_REQUIRE_5M_CLOSE", False))
    five_m_closed = int(now // 300) > int(start // 300)
    if elapsed >= min_sec and (five_m_closed or not need_5m):
        state.startup_warmup_done = True
        extra = " + 5m kapanis" if need_5m else ""
        log.info(
            f"Acilis isinmasi tamam: {elapsed:.0f}s canli akis{extra} -> girisler aktif"
        )
        try:
            from engine.good_signal_stats_v3 import maybe_log_summary

            maybe_log_summary(force=True)
        except Exception:
            pass
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

        # Hedef = GERCEK pivot bandi (levels_v3, pivot-otoriteli). Breakout'un dar
        # cookie bandi runner'i hedefe varmadan kapatip kari erken kilitliyordu.
        ref_s = ref_r = 0.0
        try:
            from engine.levels_v3 import get_levels_snapshot

            snap = get_levels_snapshot(mark) or {}
            ref_s = float(snap.get("active_support") or 0)
            ref_r = float(snap.get("active_resistance") or 0)
        except Exception:
            pass
        if not (ref_s > 0 and ref_r > ref_s):
            lv = get_active_levels(mark) or {}
            ref_s = float(lv.get("support") or 0)
            ref_r = float(lv.get("resistance") or 0)
        cvd = get_cvd_snapshot() or {}
        cum = float(cvd.get("cumulative", 0) or 0)
        cdir = str(cvd.get("direction") or "").upper()
        near = float(getattr(cfg, "V3_RUNNER_TARGET_NEAR_PCT", 0.002) or 0.002)
        thr = float(getattr(cfg, "V3_RUNNER_REVERSAL_CVD", 4000) or 4000)

        # Akis-ters icin ANLAMLI buyukluk sart: tek-tick zayif etiket (or. cvd=+1031)
        # kazancli runner'i kapatmasin. cum >= min_cum (yon teyidi) gerekir.
        min_cum = float(getattr(cfg, "V3_RUNNER_REV_MIN_CUM", 2500) or 2500)
        hit_target = reversed_flow = False
        if side == "SHORT" and ref_s > 0:
            hit_target = mark <= ref_s * (1.0 + near)
            reversed_flow = (cdir == "BULL" and cum >= min_cum) or (cum >= thr)
        elif side == "LONG" and ref_r > 0:
            hit_target = mark >= ref_r * (1.0 - near)
            reversed_flow = (cdir == "BEAR" and cum <= -min_cum) or (cum <= -thr)

        # Ardisik teyit: kosul art arda N tick dogru olunca kapat (anlik gurultu degil).
        if _confirm_tick("runner_rev", hit_target and reversed_flow):
            from execution.executor import close_position

            log.info(
                f"[RUNNER-REVERSAL] {side} hedefe ulastı "
                f"(mark={mark:.2f} S={ref_s:.2f} R={ref_r:.2f}) + akış ters "
                f"(cvd={cum:+.0f} {cdir}, {_exit_confirm['runner_rev']} tick teyit) "
                f"→ runner kapatılıyor, slot serbest"
            )
            _mark_protect_exit()
            await close_position(reason="runner_target_reversal")
            return True
    except Exception as ex:
        log.warning(f"runner reversal exit: {ex}")
    return False


async def _maybe_protective_exit() -> bool:
    """
    Pre-TP1 koruma (borsa SL'sinden ONCE devreye girer, SL'yi kaldirmaz):
      (c) Felaket tavani: fiyat girise gore V3_HARD_CAP_PCT% aleyhe -> market kapat.
      (b) Reversal-oncelikli cikis: order-flow (CVD) sert ters donduyse, pozisyon
          zararda/basabas iken kapat. Boylece dar SL'ye carpmadan akis bozulunca cikar;
          "SL'ye degip yonu kacirdik" yerine akis-temelli cikis. Karda iken bu islemi
          runner_target_reversal yonetir (burasi yalniz adverse>0).
    """
    if not bool(getattr(cfg, "V3_PROTECTIVE_EXIT_ENABLED", True)):
        return False
    if not state.in_position:
        return False
    side = (state.pos_side or "").upper()
    mark = float(state.mark_price or state.price or 0)
    entry = float(state.pos_entry or 0)
    if mark <= 0 or entry <= 0 or side not in ("LONG", "SHORT"):
        return False
    adverse = (mark - entry) / entry if side == "SHORT" else (entry - mark) / entry
    try:
        from execution.executor import close_position

        cap = float(getattr(cfg, "V3_HARD_CAP_PCT", 1.5) or 1.5) / 100.0
        if cap > 0 and adverse >= cap:
            log.warning(
                f"[HARD-CAP] {side} giris={entry:.2f} mark={mark:.2f} "
                f"aleyhte=%{adverse * 100:.2f} >= %{cap * 100:.2f} -> market kapat"
            )
            _mark_protect_exit()
            await close_position(reason="hard_cap")
            return True

        if adverse > 0:  # yalniz zararda/basabas; karda runner mantigi yonetir
            from engine.cvd_v3 import get_cvd_snapshot

            cvd = get_cvd_snapshot() or {}
            cum = float(cvd.get("cumulative", 0) or 0)
            cdir = str(cvd.get("direction") or "").upper()
            thr = float(getattr(cfg, "V3_REVERSAL_EXIT_CVD", 3000) or 3000)
            reversed_flow = (
                (side == "SHORT" and cdir == "BULL" and cum >= thr)
                or (side == "LONG" and cdir == "BEAR" and cum <= -thr)
            )
            # (b) Akis sert ters + ardisik teyit -> SL beklemeden kapat.
            if _confirm_tick("flow_exit", reversed_flow):
                log.info(
                    f"[FLOW-REVERSAL] {side} akis sert ters (cvd={cum:+.0f} {cdir}, "
                    f"{_exit_confirm['flow_exit']} tick teyit) aleyhte=%{adverse * 100:.2f} "
                    f"-> SL beklemeden kapat"
                )
                _mark_protect_exit()
                await close_position(reason="flow_reversal_exit")
                return True

            # Skor-farkinda erken cikis: tez ARTIK desteklemiyorsa (prob_side zayif)
            # ve bu ardisik N tick surduyse cik. Guclu skorda TUT (hard-cap'e kadar
            # nefes). Anlik prob dalgalanmasi tek-tick'te tetiklemez (debounce).
            # MIN TUTUS: taze pozisyonu hemen kapatma -> ac-kapa cigini onler.
            min_hold = float(getattr(cfg, "V3_SCORE_EXIT_MIN_HOLD_SEC", 60) or 0)
            pos_age = time.time() - float(getattr(state, "pos_open_ts", 0) or 0)
            if min_hold > 0 and pos_age < min_hold:
                return False
            exit_th = float(getattr(cfg, "V3_SCORE_EXIT_PROB", 0.55) or 0.55)
            ds = (getattr(state, "v3_decision", None) or {}).get("direction_scores") or {}
            key = "prob_short_pct" if side == "SHORT" else "prob_long_pct"
            prob_side = float(ds.get(key, 0) or 0) / 100.0
            score_weak = 0 < prob_side < exit_th
            if _confirm_tick("score_exit", score_weak):
                log.info(
                    f"[SCORE-EXIT] {side} tez zayifladi prob=%{prob_side * 100:.1f} "
                    f"< %{exit_th * 100:.0f} ({_exit_confirm['score_exit']} tick) "
                    f"aleyhte=%{adverse * 100:.2f} -> erken cik"
                )
                _mark_protect_exit()
                await close_position(reason="score_weak_exit")
                return True
        else:
            # Pozisyon karda/basabas ustu: cikis sayaclarini sifirla (gurultu birikmesin)
            _exit_confirm["flow_exit"] = 0
            _exit_confirm["score_exit"] = 0
    except Exception as ex:
        log.warning(f"protective exit: {ex}")
    return False


def _v3_execute_block(
    details: dict, source: str, blocker: str, detail: str
) -> None:
    if not details.get("v3_mode"):
        return
    try:
        from engine.no_trade_log_v3 import log_execute_block

        log_execute_block(blocker, detail, source=source, details=details)
    except Exception:
        pass


async def execute_entry(details: dict, source: str = "breakout") -> bool:
    """Risk planı + borsa/paper emri."""
    from core.config import reload_keys
    from execution.risk import calculate as calc_risk
    from execution.executor import get_equity_for_risk, open_position, reverse_position
    from utils.notifier import notify_open

    if not cfg.AUTO_TRADE_ENABLED:
        if getattr(cfg, "STRATEGY_V3_ENABLED", False) and details.get("v3_mode"):
            _v3_execute_block(
                details,
                source,
                "auto_trade_off",
                f"sinyal {details.get('direction')} hazir",
            )
        else:
            log.info(f"Otomatik trade kapalı — sinyal: {details['direction']} ({source})")
        return False

    if not getattr(state, "exchange_reconciled", True):
        log.warning("Startup senkronu bitmeden giriş yapılmıyor")
        _v3_execute_block(details, source, "reconcile", "startup senkronu bitmedi")
        return False
    if time.time() < getattr(state, "startup_grace_until", 0):
        log.debug("Startup grace — yeni giriş ertelendi")
        _v3_execute_block(details, source, "grace", "startup grace")
        return False

    blocked_cd, kalan = _reentry_blocked()
    if blocked_cd:
        msg = f"re-entry cooldown {kalan:.0f}s (korumali cikis sonrasi ac-kapa onlendi)"
        log.info(f"Giriş atlandı — {msg} ({source})")
        state.no_entry_reason = msg
        _v3_execute_block(details, source, "reentry_cooldown", msg)
        return False

    warmup = _startup_warmup_block()
    if warmup:
        log.info(f"Giriş atlandı — {warmup} ({source})")
        state.no_entry_reason = f"[STARTUP_WARMUP] {warmup}"
        _v3_execute_block(details, source, "startup_warmup", warmup)
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
            _v3_execute_block(details, source, "position", reason)
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
            _v3_execute_block(details, source, "narrative", nar_msg)
            return False

    balance = await get_equity_for_risk()
    if balance <= 0 and not is_paper_mode():
        log.warning("Bakiye yok — pozisyon açılamadı")
        _v3_execute_block(details, source, "balance", "bakiye yok")
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
            _v3_execute_block(details, source, "rr", state.no_entry_reason)
            return False
        min_rr = 0.0
    elif details.get("break_mode"):
        min_rr = float(getattr(cfg, "BREAK_TP1_MIN_RR", 1.2))

    # (a) SL'yi gurultu bolgesinin otesine genislet (felaket tavani mesafesi).
    # Boyut risk-bazli oldugu icin calc_risk otomatik kuculur. Genis SL RR'yi
    # dusurdugunden RR'yi YENIDEN dogrula -> sub-min-RR setuplar dusurulur
    # (gizli kotu islem yok). "SL gurultude vuruluyor" sorununun cozumu.
    if details.get("v3_mode") and bool(getattr(cfg, "V3_WIDEN_SL_TO_CAP", True)):
        cap = float(getattr(cfg, "V3_HARD_CAP_PCT", 1.5) or 1.5) / 100.0
        e = float(details.get("price") or details.get("signal_price") or px or 0)
        cur_sl = float(details.get("sl") or 0)
        if e > 0 and cur_sl > 0 and cap > 0:
            wide = e * (1 - cap) if direction == "LONG" else e * (1 + cap)
            new_sl = min(cur_sl, wide) if direction == "LONG" else max(cur_sl, wide)
            if abs(new_sl - cur_sl) >= 0.01:
                tp2 = float(details.get("tp2") or 0)
                risk = abs(e - new_sl)
                reward = abs(tp2 - e)
                rr2 = reward / risk if risk > 0 else 0.0
                min_need = float(getattr(cfg, "V3_MIN_RR_RATIO", 2.0) or 2.0)
                if rr2 < min_need:
                    state.no_entry_reason = (
                        f"Genis SL ile RR yetersiz: {rr2:.2f} < {min_need:.2f} "
                        f"(SL %{cap * 100:.1f} tavan)"
                    )
                    log.info(f"Giris atlandi — {state.no_entry_reason} ({source})")
                    _v3_execute_block(details, source, "rr", state.no_entry_reason)
                    return False
                details["sl"] = round(new_sl, 2)
                details["rr"] = rr2
                log.info(
                    f"[WIDE-SL] {direction} SL {cur_sl:.2f}->{new_sl:.2f} "
                    f"(%{cap * 100:.1f} tavan) RR={rr2:.2f} (boyut risk-bazli kuculur)"
                )

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
        _v3_execute_block(details, source, "risk", state.no_entry_reason)
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
    if state.in_position:
        # Pre-TP1 koruma: felaket tavani (c) + reversal-oncelikli cikis (b).
        # Borsa SL'sinden once devreye girer; her tick calisir.
        if await _maybe_protective_exit():
            update_trend("1m")
            return

    if state.in_position and state.pos_tp1_hit:
        # RANGE runner: hedefe ulaşıp akış ters dönerse kapat (slot serbest)
        if await _maybe_runner_reversal_exit():
            update_trend("1m")
            return
        from execution.protection_orders import (
            apply_5m_runner_sl_confirm,
            apply_structural_runner_trail,
        )

        await apply_5m_runner_sl_confirm(candle)
        # Yapisal trail: SL'yi karsi-swing (lower-high/higher-low) arkasina tasi.
        # Yapi bozulmadikca iceride kal -> gurultu sicramasinda erken atilma yok.
        await apply_structural_runner_trail()

    update_trend("1m")

    if state.in_position:
        if getattr(cfg, "STRATEGY_V3_ENABLED", False):
            if getattr(cfg, "V3_CHANNEL_AUTHORITY", False):
                from engine.decision_v3 import update_decision

                snap = update_decision(flow_tag="1m")
                if (
                    snap.get("reverse_signal")
                    and snap.get("action") in ("LONG", "SHORT")
                    and snap.get("details")
                ):
                    log.info(
                        f"[CHANNEL FLIP] {state.pos_side} → {snap['action']} — "
                        f"{snap.get('reason', '')}"
                    )
                    await execute_entry(
                        dict(snap["details"]), source="v3-channel-flip"
                    )
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
