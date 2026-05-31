"""feeds/liquidation_feed.py — forceOrder tasfiye stream"""
import asyncio, json, time
import websockets
from websockets.exceptions import ConnectionClosed
from core.config import cfg
from core.state  import state
from core.shutdown import is_stopping, iter_ws_messages
from core.async_sleep import stoppable_sleep
from core.logger import get_logger

log = get_logger("LiqFeed")
# Tasfiye stream tüm sembolleri verir
URL = f"{cfg.WS_SINGLE}!forceOrder@arr"


async def run():
    retry = 3
    while not is_stopping():
        try:
            from feeds.ws_common import ws_connect_kwargs

            async with websockets.connect(URL, **ws_connect_kwargs()) as ws:
                retry = 3
                log.info("forceOrder stream aktif ✓")
                async for raw in iter_ws_messages(ws):
                    if is_stopping():
                        break
                    msg = json.loads(raw)
                    d   = msg.get("data", msg)
                    o   = d.get("o", d)
                    if o.get("s") != cfg.SYMBOL:
                        continue
                    px = float(o.get("p", 0))
                    qty = float(o.get("q", 0))
                    side = str(o.get("S", "")).upper()
                    usd = abs(px * qty)
                    liq = {
                        "ts": time.time(),
                        "side": side,
                        "qty": qty,
                        "price": px,
                    }
                    state.liquidations.append(liq)
                    from feeds.liq_clusters import record_liquidation
                    # SELL forceOrder = long pozisyon tasfiyesi
                    is_long_liq = side == "SELL"
                    record_liquidation(liq["ts"], px, usd, is_long_liq)
                    log.info(
                        f"TAFSİYE: {liq['side']}  "
                        f"{liq['qty']:.3f} ETH @ {liq['price']:.2f}"
                    )
                    try:
                        from botlog.journal import on_liquidation
                        on_liquidation(liq["side"], liq["qty"], liq["price"])
                    except Exception:
                        pass
        except ConnectionClosed as e:
            log.warning(f"forceOrder kapandı: {e}")
        except Exception as e:
            log.error(f"forceOrder hata: {e}")
        if is_stopping():
            break
        await stoppable_sleep(retry)
        retry = min(retry * 2, 30)
