"""REST market feed — fiyat ve kapali mumlar REST polling ile beslenir."""
from __future__ import annotations

import time

from core.config import cfg
from core.state import state
from core.shutdown import is_stopping
from core.async_sleep import stoppable_sleep
from core.logger import get_logger
from core.futures_public_rest import get_book_ticker, get_klines
from feeds.book_feed import handle_book_ticker_event

log = get_logger("MarketREST")

on_15m_close = None
on_1h_close = None
on_1m_close = None

_last_seen_ts: dict[str, float] = {"1m": 0.0, "15m": 0.0, "1h": 0.0}


def _bootstrap_last_seen() -> None:
    if _last_seen_ts["1m"] <= 0:
        try:
            from engine.bars_1m import get_bars_1m

            bars = get_bars_1m(1)
            if bars:
                _last_seen_ts["1m"] = float(bars[-1].get("ts", 0) or 0)
        except Exception:
            pass

    if _last_seen_ts["15m"] <= 0 or _last_seen_ts["1h"] <= 0:
        try:
            from engine.structure import get_bars_15m, get_bars_1h

            bars_15m = get_bars_15m(1)
            bars_1h = get_bars_1h(1)
            if bars_15m:
                _last_seen_ts["15m"] = float(bars_15m[-1].get("ts", 0) or 0)
            if bars_1h:
                _last_seen_ts["1h"] = float(bars_1h[-1].get("ts", 0) or 0)
        except Exception:
            pass


async def _dispatch(cb, candle: dict, label: str) -> None:
    if cb is None:
        return
    try:
        await cb(candle)
    except Exception as e:
        log.error(f"REST {label} callback hata: {e}", exc_info=True)


async def _poll_price() -> bool:
    bt = await get_book_ticker()
    if not bt or bt.get("bid", 0) <= 0 or bt.get("ask", 0) <= 0:
        return False
    handle_book_ticker_event(
        {
            "b": str(bt["bid"]),
            "a": str(bt["ask"]),
            "e": "bookTicker",
        }
    )
    return True


_INTERVAL_SEC = {"1m": 60, "15m": 900, "1h": 3600}


async def _poll_interval(interval: str, limit: int, cb, label: str) -> bool:
    bars = await get_klines(interval, limit=limit)
    if not bars:
        return False

    now = time.time()
    state.kline_last_update = now
    state.last_update = now

    step = _INTERVAL_SEC.get(interval, 900)
    last_seen = float(_last_seen_ts.get(interval, 0) or 0)
    newest = max(float(b.get("ts", 0) or 0) for b in bars)

    if last_seen > 0 and newest > last_seen + step * (limit + 1):
        extra_limit = min(96, int((newest - last_seen) / step) + 3)
        extra = await get_klines(interval, limit=extra_limit)
        if extra:
            merged = {float(b.get("ts", 0) or 0): b for b in bars}
            for b in extra:
                merged[float(b.get("ts", 0) or 0)] = b
            bars = sorted(merged.values(), key=lambda x: float(x.get("ts", 0) or 0))

    bars.sort(key=lambda x: float(x.get("ts", 0) or 0))
    for candle in bars:
        ts = float(candle.get("ts", 0) or 0)
        if last_seen > 0 and ts < last_seen:
            continue
        await _dispatch(cb, candle, label)
        if ts >= last_seen:
            _last_seen_ts[interval] = ts
    if interval == "15m" and bars:
        try:
            from dashboard.binance_chart import publish_bot_bars_to_cache

            publish_bot_bars_to_cache()
        except Exception:
            pass
    return True


async def run() -> None:
    if str(getattr(cfg, "MARKET_DATA_MODE", "") or "").lower() != "aggtrade_ws_rest":
        log.info("REST market poller atlandı (MARKET_DATA_MODE aggtrade_ws_rest değil)")
        while not is_stopping():
            await stoppable_sleep(300)
        return

    _bootstrap_last_seen()
    next_price = 0.0
    next_1m = 0.0
    next_15m = 0.0
    next_1h = 0.0

    log.info(
        "REST market poller aktif ✓ "
        "(fiyat=REST, 1m/15m/1h kapali mum=REST, anlik akis=aggTrade WS)"
    )

    while not is_stopping():
        now = time.time()
        try:
            if now >= next_price:
                await _poll_price()
                next_price = now + float(getattr(cfg, "REST_PRICE_POLL_SEC", 5.0))

            if now >= next_1m:
                await _poll_interval("1m", 3, on_1m_close, "1m")
                next_1m = now + float(getattr(cfg, "REST_KLINE_1M_POLL_SEC", 5.0))

            if now >= next_15m:
                await _poll_interval("15m", 3, on_15m_close, "15m")
                next_15m = now + float(getattr(cfg, "REST_KLINE_15M_POLL_SEC", 20.0))

            if now >= next_1h:
                await _poll_interval("1h", 3, on_1h_close, "1h")
                next_1h = now + float(getattr(cfg, "REST_KLINE_1H_POLL_SEC", 60.0))
        except Exception as e:
            log.error(f"REST market poller hata: {e}")

        await stoppable_sleep(1.0)
