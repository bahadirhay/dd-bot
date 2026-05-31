"""
main.py — Ana orkestratör (Windows uyumlu)

Akış: WebSocket veri → trend analizi → otomatik pozisyon (paper/canlı)

Ctrl+C: feed'leri iptal eder ve çıkar (ikinci Ctrl+C zorla kapatır).

Değişiklikler (iyileştirme paketi v1):
  - load_performance_context: startup'ta SQLite'tan geçmiş performans okunur
  - DailyLossGuard: günlük %3 kayıp veya 10 işlem limitinde trade engeli
  - v3_update_safe: V3 güncelleme hatası artık sessiz geçmiyor, state.v3_stale set ediliyor
  - get_effective_risk: UNCLEAR yapıda azaltılmış risk çarpanı
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(__file__))

from core.config import cfg, is_paper_mode
from core.state import state
from core.logger import get_logger
from core.shutdown import (
    request_stop,
    wait_stop,
    is_stopping,
    register_loop,
    on_stop,
    reset_stop,
)
from botlog.db import init as db_init

import feeds.kline_feed as kline_feed
import feeds.rest_market_feed as rest_market_feed
from feeds.combined_market_ws import run as run_combined_market
from feeds.trade_feed import run as run_trade_feed
from feeds.rest_market_feed import run as run_rest_market
from feeds.market_recovery import run as run_market_recovery
from feeds.liquidation_feed import run as run_liqs
from feeds.oi_feed import run as run_oi
from engine.structure import add_bar_15m, add_bar_1h
from engine.cvd_engine import on_bar_close as cvd_on_bar
from engine.entry_timer import on_1m_bar, set_callback
from engine.trader import on_15m_market, on_1m_market, execute_entry
from engine.trend import update_trend
from execution.risk import calculate as calc_risk
from execution.executor import (
    get_balance,
    open_position,
    setup_api,
    refresh_balance,
    fetch_balance,
)
from core.config import reload_keys
from execution.position_manager import check as check_position
from botlog.analyzer import run_scheduled_analysis
from feeds.health import run_watchdog
from feeds.user_stream import run as run_user_stream
from feeds.chart_backfill import (
    backfill_price_history,
    backfill_15m_bars,
    backfill_1h_bars,
)

# ── YENİ: iyileştirme paketi ──────────────────────────────────────────────────
from botlog.performance_context import load_performance_context
from botlog.db_backup import maybe_backup
from core.daily_loss_guard import get_guard
from core.error_handler import guard as err
from engine.v3_guard import v3_update_safe, is_v3_stale
from engine.adaptive_risk import get_effective_risk, liq_filter_ok
# ─────────────────────────────────────────────────────────────────────────────

log = get_logger("Main")

_active_tasks: list[asyncio.Task] = []


def _cancel_active_tasks() -> None:
    for t in _active_tasks:
        if not t.done():
            t.cancel()


async def _run_task_forever(name: str, coro_factory) -> None:
    """Görev çökerse veya erken biterse yeniden dene; yalnızca durdurma ile çık."""
    from core.async_sleep import stoppable_sleep

    backoff = 3.0
    while not is_stopping():
        try:
            await coro_factory()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error(f"Görev {name} hata: {e}")
        else:
            if not is_stopping():
                log.warning(f"Görev {name} erken bitti — {backoff:.0f}s sonra yeniden")
        if is_stopping():
            break
        await stoppable_sleep(backoff)
        backoff = min(backoff * 1.5, 30.0)


def _spawn_worker(name: str, coro_factory) -> asyncio.Task:
    return asyncio.create_task(_run_task_forever(name, coro_factory), name=name)


def _start_dashboard():
    try:
        from dashboard.app import run as dash_run
        log.info("Dashboard: http://localhost:8050")
        dash_run(host="0.0.0.0", port=8050, debug=False)
    except ImportError:
        log.warning(
            "Dashboard kütüphaneleri eksik.\n"
            "Kur: pip install dash dash-bootstrap-components plotly"
        )
    except Exception as e:
        log.error(f"Dashboard hata: {e}")


def _install_signal_handlers():
    def _handler(signum, _frame):
        if is_stopping():
            log.warning("Zorla kapatılıyor (ikinci Ctrl+C)...")
            request_stop(force=True)
            os._exit(0)
        request_stop()
        log.info("Durdurma istendi (Ctrl+C) — WebSocket'ler kapatılıyor...")

    signal.signal(signal.SIGINT, _handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handler)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _handler)


async def _on_15m(candle: dict):
    from engine.intra_15m import finalize_on_15m_close

    add_bar_15m(candle)
    cvd_on_bar(candle)
    await on_15m_market(candle)
    finalize_on_15m_close(candle)

    with err.silent("dashboard 15m yayını"):   # kasıtlı sessiz — dashboard çökmesin
        from dashboard.binance_chart import publish_bot_bars_to_cache
        publish_bot_bars_to_cache()


async def _on_1h(candle: dict):
    add_bar_1h(candle)

    if getattr(cfg, "STRATEGY_V3_ENABLED", False):
        # YENİ: v3_update_safe — hata olursa state.v3_stale=True, işlem engellenir
        await v3_update_safe("1h")

    with err.warn("journal 1h"):
        from botlog.journal import on_bar
        on_bar("1h", candle, f"yapi 1h={state.structure_1h}")


async def _on_entry_confirmed(details: dict):
    # YENİ: günlük kayıp limiti kontrolü
    daily_guard = get_guard()
    if not daily_guard.can_trade():
        log.warning("execute_entry: günlük limit → giriş iptal")
        return

    # YENİ: V3 stale kontrolü
    if getattr(cfg, "STRATEGY_V3_ENABLED", False) and is_v3_stale():
        log.warning("execute_entry: V3 stale → giriş iptal")
        return

    # YENİ: UNCLEAR yapıda azaltılmış risk
    effective_risk, min_rr = get_effective_risk()
    details["risk_pct_override"] = effective_risk
    details["min_rr_override"] = min_rr

    if details.get("range_mode"):
        src = "range"
    elif details.get("break_mode"):
        src = "breakout"
    else:
        src = "1m-confirm"

    await execute_entry(details, source=src)

    # YENİ: trade açıldı kaydı
    daily_guard.record_trade_open()


async def _on_1m(candle: dict):
    from engine.intra_15m import on_1m_tick
    from engine.bars_1m import add_bar_1m

    add_bar_1m(candle)
    on_1m_tick(candle)

    if cfg.PULSE_BARS_1M and not is_stopping():
        try:
            update_trend("1m")
        except Exception:
            pass

    with err.warn("journal 1m"):
        from botlog.journal import on_bar
        chg = (
            (candle["close"] - candle["open"]) / candle["open"] * 100
            if candle.get("open")
            else 0
        )
        on_bar("1m", candle, f"1m ({chg:+.2f}%) trend={state.trend_view.get('bias', '?')}")

    await on_1m_market(candle)

    if getattr(cfg, "ENTRY_MODE", "break").lower() == "confirm":
        await on_1m_bar(candle)

    await check_position(_make_exec())


def _make_exec():
    import execution.executor as _ex

    class Ex:
        check_tp1_hit = staticmethod(_ex.check_tp1_hit)
        on_tp1_hit = staticmethod(_ex.on_tp1_hit)
        sync_position_state = staticmethod(_ex.sync_position_state)
        move_to_breakeven = staticmethod(_ex.move_to_breakeven)
        close_position = staticmethod(_ex.close_position)

    return Ex()


async def _balance_loop():
    while not is_stopping():
        await asyncio.sleep(30)
        if is_stopping():
            break
        if is_paper_mode():
            from execution.paper import paper_sync_position
            if state.in_position:
                await paper_sync_position()
            await fetch_balance()
        elif cfg.API_KEY:
            await refresh_balance()


async def _account_sync_loop():
    """Canlı: equity + algo emirler | Paper: anlık uPnL."""
    await asyncio.sleep(3)
    while not is_stopping():
        if is_paper_mode():
            from execution.paper import paper_sync_position
            if state.in_position:
                await paper_sync_position()
            else:
                from execution.paper import _mark_unrealized
                _mark_unrealized()
        elif cfg.API_KEY:
            try:
                from execution.account_sync import refresh_account_snapshot
                await refresh_account_snapshot()
            except Exception as e:
                log.debug(f"Account sync: {e}")
        try:
            await asyncio.wait_for(wait_stop(), timeout=5.0)
            break
        except asyncio.TimeoutError:
            pass


async def _journal_loop():
    from botlog.journal import maybe_sample_tick

    await asyncio.sleep(3)
    while not is_stopping():
        try:
            maybe_sample_tick()
        except Exception as e:
            log.error(f"Journal hata: {e}")
        try:
            await asyncio.wait_for(
                wait_stop(),
                timeout=max(5.0, float(cfg.JOURNAL_SAMPLE_SEC)),
            )
            break
        except asyncio.TimeoutError:
            pass


async def _regime_loop():
    await asyncio.sleep(5)
    while not is_stopping():
        try:
            from core.state import record_metrics_sample
            update_trend("tick")
            record_metrics_sample()
            if getattr(cfg, "STRATEGY_V3_ENABLED", False):
                from engine.cvd_v3 import update_cvd_snapshot
                update_cvd_snapshot()
        except Exception as e:
            log.error(f"Trend loop hata: {e}")
        try:
            await asyncio.wait_for(wait_stop(), timeout=30.0)
            break
        except asyncio.TimeoutError:
            pass


async def _cancel_all(tasks: list[asyncio.Task]) -> None:
    for t in tasks:
        if not t.done():
            t.cancel()
    if not tasks:
        return
    try:
        await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=8.0,
        )
    except asyncio.TimeoutError:
        log.warning("Bazı görevler 8 sn içinde kapanmadı — süreç sonlandırılıyor.")


async def _main_loop():
    global _active_tasks

    reset_stop()
    loop = asyncio.get_running_loop()
    register_loop(loop)
    on_stop(_cancel_active_tasks)

    db_init()
    reload_keys()

    state.exchange_reconciled = False
    state.startup_grace_until = time.time() + 180

    await setup_api()

    if cfg.API_KEY and not is_paper_mode():
        from execution.account_sync import reconcile_startup_exchange
        await reconcile_startup_exchange()
    else:
        state.exchange_reconciled = True

    # YENİ: geçmiş performansı oku, adaptif parametreleri uygula
    await load_performance_context()

    # YENİ: günlük kayıp guard'ı başlat
    daily_guard = get_guard()
    log.info(
        f"DailyGuard: max_loss={daily_guard._max_loss_pct}% "
        f"max_trades={daily_guard._max_trades}"
    )

    n15 = await backfill_15m_bars(96)
    await backfill_1h_bars(48)
    await backfill_price_history()

    from feeds.trade_feed import bootstrap_cvd_from_rest
    await bootstrap_cvd_from_rest()

    if n15:
        from engine.structure import _update_structure_15m, _update_structure_1h
        _update_structure_15m()
        _update_structure_1h()
        update_trend("startup")

        if getattr(cfg, "STRATEGY_V3_ENABLED", False):
            from engine.levels_v3 import update_levels
            from engine.structure_v3 import update_structure
            from engine.cvd_v3 import update_cvd_snapshot
            from engine.decision_v3 import update_decision
            update_levels()
            update_structure()
            update_cvd_snapshot()
            update_decision()
        else:
            from engine.breakout import refresh_levels
            from engine.breakout_readiness import log_swing_readiness
            refresh_levels(force=bool(state.in_position))
            log_swing_readiness("startup")

        from engine.breakout import get_active_levels
        from engine.market_narrative import reconcile_startup
        from core.state import effective_price

        lv = get_active_levels()
        px = effective_price() or state.mark_price or 0.0
        reconcile_startup(px, float(lv.get("resistance") or 0), float(lv.get("support") or 0))

    log.info(
        f"Yapı güncellendi: 15m={state.structure_15m} 1h={state.structure_1h}"
    )
    state.startup_grace_until = time.time() + 20

    trade_mode = (
        "PAPER"
        if is_paper_mode()
        else ("CANLI" if cfg.API_KEY else "İZLEME-auto")
    )

    log.info("=" * 56)
    log.info(" ETH/USDT — Trend + Otomatik Trade Botu")
    log.info(
        f" Trade : {trade_mode} AUTO={cfg.AUTO_TRADE_ENABLED} "
        f"mod={cfg.ENTRY_MODE} impulse1m={cfg.IMPULSE_1M_TRADE} "
        f"v3={getattr(cfg, 'STRATEGY_V3_ENABLED', False)}"
    )

    tm = float(getattr(cfg, "TRADE_MARGIN_USD", 0) or 0)
    if tm > 0:
        log.info(
            f" Boyut : {tm:.0f} USDT marjin × {cfg.LEVERAGE}x "
            f"(max %{cfg.MAX_MARGIN_PCT:.0f} equity)"
        )
    else:
        log.info(f" Boyut : RISK_PCT={cfg.RISK_PCT}% (SL mesafesine göre)")

    em = getattr(cfg, "ENTRY_MODE", "break")
    if getattr(cfg, "STRATEGY_V3_ENABLED", False):
        log.info(
            " Giris : V3 — seviye/band zone + CVD + RANGE/BREAKOUT giris + RR>=2.0 "
            "(1h yapi bilgi)"
        )
        log.info(
            " Akis : levels -> structure -> cvd -> scenario -> entry -> decision"
        )
    elif em in ("break", "realtime"):
        log.info(
            f" Giriş : KIRILIM — yapı eşikleri (kanal oranı) "
            f"+ {cfg.BREAK_HOLD_SEC}s tutma + flow hesap"
        )
        log.info(
            f" 15m : sadece seviye | 1h filtre: {state.structure_1h} "
            f"(UNCLEAR→RR≥{cfg.BREAK_MIN_RR_UNCLEAR})"
        )
    elif em == "range":
        log.info(
            f" Giriş : KANAL v2 — bps mesafe + 1m red + CVD eğimi "
            f"skor≥{cfg.RANGE_MIN_SCORE} TP1=mid"
        )
        log.info(
            f" Kanal : min genişlik {cfg.RANGE_MIN_WIDTH_BPS}bps "
            f"R:R≥{cfg.RANGE_MIN_RR}"
        )
    elif em == "hybrid":
        log.info(
            f" Giriş : HİBRİT — band içi RANGE (skor≥{cfg.RANGE_MIN_SCORE}), "
            f"dışı KIRILIM"
        )
    else:
        log.info(f" Yapı kapısı: 1h+15m (REQUIRE_HTF_ALIGN={cfg.REQUIRE_HTF_ALIGN})")
        log.info(f" Trend eşik: güç≥{60} + yapı uyumu")

    if getattr(cfg, "NARRATIVE_ENABLED", True):
        log.info(
            f" Anlatı : yapısal hesap (gürültü+swing bacak+hedef oranı, % yok) "
            f"+ {cfg.NARRATIVE_EXTENDED_MIN_BARS}×15m"
        )

    log.info(
        f" Journal : her {cfg.JOURNAL_SAMPLE_SEC:.0f}s DB | "
        f"aciklama: python scripts/explain.py \"2026-05-21 15:00\""
    )

    if is_paper_mode():
        log.info(f" Bakiye : ${state.paper_balance:,.2f} USDT (simüle)")
    elif state.api_ok:
        log.info(f" API : OK ${state.real_balance:.2f} USDT")

    market_mode = str(getattr(cfg, "MARKET_DATA_MODE", "aggtrade_ws_rest") or "").lower()

    if market_mode == "aggtrade_ws_rest":
        log.info(" Hesap : REST account sync + exchange protection orders")
        log.info(" Veri : aggTrade WS + REST pollers (book/1m/15m/1h/OI/funding)")
        if cfg.API_KEY and not is_paper_mode() and cfg.USER_STREAM_ENABLED:
            log.info(" User stream: kapali (aggtrade_ws_rest modunda kullanılmıyor)")
    else:
        if cfg.API_KEY and not is_paper_mode() and cfg.USER_STREAM_ENABLED:
            log.info(" User stream: SL/TP/borsa kapanışı (listenKey)")
        log.info(" Veri : combined WS (1 bağlantı) + REST recovery")

    log.info(f" Dashboard: http://localhost:8050")
    log.info(" Durdurmak: Ctrl+C (takılırsa bir kez daha Ctrl+C)")
    log.info("=" * 56)

    threading.Thread(target=_start_dashboard, daemon=True).start()
    await asyncio.sleep(2)

    kline_feed.on_15m_close = _on_15m
    kline_feed.on_1h_close = _on_1h
    kline_feed.on_1m_close = _on_1m
    rest_market_feed.on_15m_close = _on_15m
    rest_market_feed.on_1h_close = _on_1h
    rest_market_feed.on_1m_close = _on_1m

    set_callback(_on_entry_confirmed)

    workers = [
        _spawn_worker("recovery", run_market_recovery),
        _spawn_worker("oi", run_oi),
    ]

    if market_mode == "aggtrade_ws_rest":
        workers.extend([
            _spawn_worker("aggTrade", run_trade_feed),
            _spawn_worker("marketREST", run_rest_market),
        ])
    else:
        workers.append(_spawn_worker("marketWS", run_combined_market))

    if bool(getattr(cfg, "LIQ_WS_ENABLED", False)):
        workers.append(_spawn_worker("liq", run_liqs))

    if cfg.API_KEY and not is_paper_mode() and cfg.USER_STREAM_ENABLED:
        workers.append(_spawn_worker("userStream", run_user_stream))

    workers.extend([
        _spawn_worker("health", run_watchdog),
        _spawn_worker("analyzer", run_scheduled_analysis),
        _spawn_worker("balance", _balance_loop),
        _spawn_worker("accountSync", _account_sync_loop),
        _spawn_worker("journal", _journal_loop),
        _spawn_worker("regime", _regime_loop),
    ])

    stop_task = asyncio.create_task(wait_stop(), name="stop")
    _active_tasks = workers + [stop_task]

    try:
        await stop_task
    finally:
        _active_tasks = []
        await _cancel_all(workers + [stop_task])
        try:
            from core.futures_public_rest import close_public_rest_session
            from execution.executor import close_api_http_session
            await close_public_rest_session()
            await close_api_http_session()
        except Exception:
            pass
        from core.instance_lock import release
        release()
        log.info("Bot durdu.")


def main():
    from core.instance_lock import acquire, release

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    acquire()
    _install_signal_handlers()

    try:
        asyncio.run(_main_loop())
    except KeyboardInterrupt:
        log.info("Bot durduruldu.")
    except Exception as e:
        log.error(f"Kritik hata: {e}")
        raise
    finally:
        release()


if __name__ == "__main__":
    main()
