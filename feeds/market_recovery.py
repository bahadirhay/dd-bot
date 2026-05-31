"""
feeds/market_recovery.py — WS kopunca REST ile toparlama (ai-treding recovery backfill).

aggTrade / book / kline bayatlayınca public REST ile state güncellenir.
"""
from __future__ import annotations

import asyncio
import time

from core.config import cfg
from core.state import state, trade_is_fresh, data_is_fresh
from core.shutdown import is_stopping
from core.async_sleep import stoppable_sleep
from core.logger import get_logger
from core.futures_public_rest import get_agg_trades
import feeds.trade_feed as trade_feed
from feeds.trade_feed import apply_agg_trades_batch, _WINDOW_SEC

log = get_logger("Recovery")

_last_recovery_ts = 0.0
_outage_started: float | None = None
_market_ws_down_since: float | None = None
_market_ws_down_reason: str = ""
_last_wait_log_ts = 0.0

TRADE_STALE_SEC = float(getattr(cfg, "TRADE_STALE_SECONDS", 45.0))
POLL_SEC = float(getattr(cfg, "MARKET_RECOVERY_POLL_SEC", 5.0))
REST_DELAY_SEC = float(getattr(cfg, "MARKET_RECOVERY_REST_DELAY_SEC", 30.0))
RECOVERY_COOLDOWN_SEC = float(getattr(cfg, "MARKET_RECOVERY_COOLDOWN_SEC", 15.0))


def note_market_ws_disconnected(reason: str) -> None:
    global _market_ws_down_since, _market_ws_down_reason, _last_wait_log_ts

    if _market_ws_down_since is None:
        _market_ws_down_since = time.time()
        _last_wait_log_ts = 0.0
    _market_ws_down_reason = reason or _market_ws_down_reason
    log.warning(
        "[RECOVERY] Market WS koptu"
        + (f": {_market_ws_down_reason}" if _market_ws_down_reason else "")
        + f" — yeniden bağlanma bekleniyor, REST grace={REST_DELAY_SEC:.0f}s"
    )


def note_market_ws_connected() -> None:
    global _market_ws_down_since, _market_ws_down_reason, _outage_started, _last_wait_log_ts

    if _market_ws_down_since is not None:
        down_for = time.time() - _market_ws_down_since
        log.info(f"[RECOVERY] Market WS tekrar bağlı (kesinti ~{down_for:.1f}s)")
    _market_ws_down_since = None
    _market_ws_down_reason = ""
    _outage_started = None
    _last_wait_log_ts = 0.0


async def recover_trade_feed() -> bool:
    """Son 5 dk aggTrade REST → CVD/taker yenile."""
    start_ms = int((time.time() - _WINDOW_SEC) * 1000)
    trades = await get_agg_trades(start_time_ms=start_ms, limit=1000)
    n = apply_agg_trades_batch(trades, _WINDOW_SEC)
    if n > 0:
        log.info(
            f"[RECOVERY] aggTrade REST: {n} tick, "
            f"CVD5m={state.cvd_5m:+.0f} taker={state.taker_ratio:.0%}"
        )
        return True
    return False


async def run_recovery_once() -> None:
    """Tek tur REST toparlama."""
    global _last_recovery_ts, _outage_started, _last_wait_log_ts
    now = time.time()

    trade_age = now - state.trade_last_update if state.trade_last_update else 1e9
    if trade_feed.trade_transport_connected():
        if _outage_started is not None:
            mins = (now - _outage_started) / 60.0
            log.info(f"[RECOVERY] Veri akışı normale döndü (kesinti ~{mins:.1f} dk)")
            _outage_started = None
        return

    if _outage_started is None:
        _outage_started = now
        log.warning(
            f"[RECOVERY] aggTrade WS kopuk — son trade {trade_age:.0f}s once "
            f"(REST grace {REST_DELAY_SEC:.0f}s)"
        )

    outage_anchor = _market_ws_down_since or _outage_started
    outage_age = now - outage_anchor
    if outage_age < REST_DELAY_SEC:
        if now - _last_wait_log_ts >= 10:
            _last_wait_log_ts = now
            why = f" ({_market_ws_down_reason})" if _market_ws_down_reason else ""
            log.info(
                f"[RECOVERY] REST beklemede{why} — yeniden bağlanma penceresi "
                f"{outage_age:.0f}/{REST_DELAY_SEC:.0f}s"
            )
        return

    if now - _last_recovery_ts < RECOVERY_COOLDOWN_SEC:
        return
    _last_recovery_ts = now

    ok_any = False
    ok_any = await recover_trade_feed() or ok_any

    if ok_any and trade_is_fresh(TRADE_STALE_SEC) and data_is_fresh(20):
        log.info("[RECOVERY] Feed state güncellendi — kırılım girişi tekrar değerlendirilebilir")


async def run():
    """Arka plan: periyodik REST recovery (ai-treding _run_recovery_backfill benzeri)."""
    log.info(
        f"Market recovery poller başladı (her {POLL_SEC:.0f}s, "
        f"REST grace={REST_DELAY_SEC:.0f}s, aggTrade>{TRADE_STALE_SEC}s)"
    )
    await stoppable_sleep(5)
    while not is_stopping():
        try:
            await run_recovery_once()
        except Exception as e:
            log.error(f"Recovery tur hatası: {e}")
        try:
            await stoppable_sleep(POLL_SEC)
        except asyncio.CancelledError:
            break
