"""
feeds/combined_market_ws.py — Tek WebSocket: aggTrade + bookTicker + kline 1m/15m/1h

ai-treding tarzı: çoklu ayrı WS yerine tek combined stream (handshake / limit baskısı azalır).
"""
from __future__ import annotations

import json
import time

import websockets
from websockets.exceptions import ConnectionClosed

from core.config import cfg
from core.shutdown import is_stopping, iter_ws_messages
from core.async_sleep import stoppable_sleep
from core.logger import get_logger
from feeds.ws_common import ws_connect_kwargs, reconnect_delay, close_ws_safely
from feeds.trade_feed import handle_agg_trade_event
from feeds.book_feed import handle_book_ticker_event
from feeds.kline_feed import handle_kline_event
from feeds.market_recovery import note_market_ws_connected, note_market_ws_disconnected

log = get_logger("MarketWS")

# Binance combined stream
STREAMS = "/".join(
    (
        f"{cfg.SYMBOL_WS}@aggTrade",
        f"{cfg.SYMBOL_WS}@bookTicker",
        f"{cfg.SYMBOL_WS}@kline_1m",
        f"{cfg.SYMBOL_WS}@kline_15m",
        f"{cfg.SYMBOL_WS}@kline_1h",
    )
)
URL = f"{cfg.WS_MULTI}{STREAMS}"

_msg_count = 0
_last_log_ts = 0.0
_first_msg_logged = False


async def _dispatch_payload(d: dict) -> None:
    global _msg_count, _last_log_ts
    et = d.get("e", "")
    if et == "aggTrade":
        handle_agg_trade_event(d)
    elif et == "bookTicker":
        handle_book_ticker_event(d)
    elif et == "kline":
        await handle_kline_event(d)
    elif "b" in d and "a" in d and not et:
        handle_book_ticker_event(d)

    _msg_count += 1
    now = time.time()
    if now - _last_log_ts > 120:
        _last_log_ts = now
        log.debug(f"Combined WS mesaj sayacı (son 2dk): {_msg_count}")


async def run():
    retry = 3.0
    log.info(f"Combined market WS: {URL[:80]}...")
    while not is_stopping():
        ws = None
        disconnect_reason = ""
        try:
            async with websockets.connect(URL, **ws_connect_kwargs()) as ws:
                retry = 3.0
                global _first_msg_logged
                _first_msg_logged = False
                note_market_ws_connected()
                log.info(
                    "Combined market stream aktif ✓ "
                    "(aggTrade+book+kline 1m/15m/1h — tek bağlantı)"
                )
                async for raw in iter_ws_messages(ws):
                    if is_stopping():
                        break
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    d = msg.get("data", msg)
                    await _dispatch_payload(d)
                if not is_stopping():
                    disconnect_reason = "stream_ended"
                    log.warning("Combined market WS akışı beklenmedik bitti")

        except ConnectionClosed as e:
            disconnect_reason = f"ConnectionClosed code={getattr(e, 'code', '?')}"
            log.warning(f"Combined market WS kapandı: {e}")
        except Exception as e:
            disconnect_reason = str(e)
            log.error(f"Combined market WS hata: {e}")
        finally:
            if disconnect_reason and not is_stopping():
                note_market_ws_disconnected(disconnect_reason)
            await close_ws_safely(ws)

        if is_stopping():
            break
        delay = reconnect_delay(
            retry,
            floor=float(getattr(cfg, "WS_RECONNECT_DELAY_SEC", 5.0)),
        )
        log.info(f"Combined market WS yeniden bağlanma ~{delay:.1f}s")
        await stoppable_sleep(delay)
        retry = min(retry * 2, 60.0)
