"""
feeds/kline_feed.py — 15m + 1h + 1m kline WebSocket
Çoklu stream formatı kullanır.
"""
import asyncio, json, time
import websockets
from websockets.exceptions import ConnectionClosed
from core.config import cfg
from core.state import state
from core.shutdown import is_stopping, iter_ws_messages
from core.async_sleep import stoppable_sleep
from core.logger import get_logger

log = get_logger("KlineFeed")

on_15m_close = None
on_1h_close  = None
on_1m_close  = None

STREAMS = (
    f"{cfg.SYMBOL_WS}@kline_1m"
    f"/{cfg.SYMBOL_WS}@kline_15m"
    f"/{cfg.SYMBOL_WS}@kline_1h"
)
URL = f"{cfg.WS_MULTI}{STREAMS}"


async def handle_kline_event(d: dict) -> None:
    """Tek kline olayı (combined veya multi stream)."""
    if d.get("e") != "kline":
        return

    state.kline_last_update = time.time()
    k = d["k"]

    if not k["x"]:
        return

    interval = k["i"]
    candle = {
        "ts": k["t"] / 1000,
        "open": float(k["o"]),
        "high": float(k["h"]),
        "low": float(k["l"]),
        "close": float(k["c"]),
        "volume": float(k["v"]),
        "buy_vol": float(k["V"]),
    }
    candle["sell_vol"] = candle["volume"] - candle["buy_vol"]
    candle["delta"] = candle["buy_vol"] - candle["sell_vol"]

    if interval == "1m" and on_1m_close:
        await _dispatch(on_1m_close, candle, "1m")
    elif interval == "15m" and on_15m_close:
        await _dispatch(on_15m_close, candle, "15m")
    elif interval == "1h" and on_1h_close:
        await _dispatch(on_1h_close, candle, "1h")


async def _dispatch(cb, candle: dict, label: str):
    """create_task yerine await — event loop şişmesini önler."""
    try:
        await cb(candle)
    except Exception as e:
        log.error(f"Kline {label} callback hata: {e}", exc_info=True)


async def run():
    retry = 3
    log.info(f"Kline URL: {URL}")
    while not is_stopping():
        try:
            from feeds.ws_common import ws_connect_kwargs

            async with websockets.connect(URL, **ws_connect_kwargs()) as ws:
                retry = 3
                log.info("Kline stream aktif (1m/15m/1h) ✓ (combined önerilir)")
                async for raw in iter_ws_messages(ws):
                    if is_stopping():
                        break
                    msg = json.loads(raw)
                    await handle_kline_event(msg.get("data", msg))

        except ConnectionClosed as e:
            log.warning(f"Kline kapandı: {e} — {retry}s sonra yeniden")
        except Exception as e:
            log.error(f"Kline hata: {e}")

        if is_stopping():
            break
        await stoppable_sleep(retry)
        retry = min(retry * 2, 30)
