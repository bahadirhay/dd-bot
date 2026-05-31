"""feeds/book_feed.py — bookTicker WebSocket"""
import asyncio, json, time
import websockets
from websockets.exceptions import ConnectionClosed
from core.config import cfg
from core.state  import state, record_price_tick
from core.shutdown import is_stopping, iter_ws_messages
from core.async_sleep import stoppable_sleep
from core.logger import get_logger

log = get_logger("BookFeed")
URL = f"{cfg.WS_SINGLE}{cfg.SYMBOL_WS}@bookTicker"


def handle_book_ticker_event(d: dict) -> None:
    """bookTicker mesajı."""
    bid = d.get("b")
    ask = d.get("a")
    if bid:
        state.bid = float(bid)
    if ask:
        state.ask = float(ask)
    if state.bid > 0 and state.ask > 0:
        mid = (state.bid + state.ask) / 2.0
        state.mark_price = mid
        state.book_last_update = time.time()
        state.last_update = state.book_last_update
        record_price_tick(mid)
        try:
            from engine.entry_timer import on_price_tick
            asyncio.create_task(on_price_tick(mid))
        except Exception as e:
            log.error(f"breakout tick hata: {e}")


async def run():
    retry = 3
    log.info(f"bookTicker URL: {URL}")
    while not is_stopping():
        try:
            from feeds.ws_common import ws_connect_kwargs

            async with websockets.connect(URL, **ws_connect_kwargs()) as ws:
                retry = 3
                log.info("bookTicker aktif ✓ (tek stream — combined önerilir)")
                async for raw in iter_ws_messages(ws):
                    if is_stopping():
                        break
                    m = json.loads(raw)
                    handle_book_ticker_event(m.get("data", m))
        except ConnectionClosed as e:
            log.warning(f"bookTicker kapandı: {e}")
        except Exception as e:
            log.error(f"bookTicker hata: {e}")
        if is_stopping():
            break
        await stoppable_sleep(retry)
        retry = min(retry * 2, 30)
