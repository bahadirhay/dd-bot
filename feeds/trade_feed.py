"""
feeds/trade_feed.py — aggTrade WebSocket
Her tick: fiyat güncelle, CVD hesapla, taker oranı hesapla
"""
import asyncio, json, time
import websockets
from websockets.exceptions import ConnectionClosed
from core.config import cfg
from core.state  import state, record_price_tick, record_metrics_sample
from core.shutdown import is_stopping, iter_ws_messages
from core.async_sleep import stoppable_sleep
from core.logger import get_logger

log = get_logger("TradeFeed")

URL = f"{cfg.WS_SINGLE}{cfg.SYMBOL_WS}@aggTrade"
_WINDOW_SEC = 300.0
ws_connected: bool = False
rest_fallback_active: bool = False
_first_msg_logged: bool = False
_rest_last_trade_id: int = 0
_rest_last_trade_ts: float = 0.0
_last_v3_cvd_ts: float = 0.0


def set_ws_connected(value: bool) -> None:
    global ws_connected
    ws_connected = bool(value)


def set_rest_fallback_active(value: bool) -> None:
    global rest_fallback_active
    rest_fallback_active = bool(value)


def trade_transport_connected() -> bool:
    return bool(ws_connected or rest_fallback_active)


def _recompute_cvd_window(cutoff: float):
    """Pencere hacmini tick listesinden yeniden hesapla (deque maxlen kayması önlemi)."""
    buy = sell = 0.0
    for t in state.ticks:
        if t["ts"] < cutoff:
            continue
        if t["delta"] > 0:
            buy += t["delta"]
        else:
            sell += -t["delta"]
    state.buy_vol_5m = buy
    state.sell_vol_5m = sell
    total = buy + sell
    state.cvd_5m = buy - sell
    state.taker_ratio = buy / total if total > 0 else 0.5


def _expire_ticks(cutoff: float):
    """5 dk penceresinden düşen tick'leri çıkar."""
    while state.ticks and state.ticks[0]["ts"] < cutoff:
        state.ticks.popleft()
    _recompute_cvd_window(cutoff)


def _maybe_update_v3_cvd() -> None:
    if not getattr(cfg, "STRATEGY_V3_ENABLED", False):
        return
    global _last_v3_cvd_ts
    now = time.time()
    if now - _last_v3_cvd_ts < 2.0:
        return
    _last_v3_cvd_ts = now
    try:
        from engine.cvd_v3 import update_cvd_snapshot

        update_cvd_snapshot()
    except Exception:
        pass


def handle_agg_trade_event(m: dict) -> None:
    """Tek aggTrade mesajı (combined WS veya eski tek stream)."""
    global _first_msg_logged
    if m.get("e") != "aggTrade":
        return

    price = float(m["p"])
    qty = float(m["q"])
    now = time.time()
    is_sell = bool(m["m"])
    delta = -qty if is_sell else qty

    state.cvd_raw += delta
    state.price = price
    state.trade_last_update = now
    state.last_update = now
    record_price_tick(price)
    state.ticks.append({
        "ts": now, "price": price,
        "qty": qty, "delta": delta,
    })

    cutoff = now - _WINDOW_SEC
    _expire_ticks(cutoff)
    record_metrics_sample()
    _maybe_update_v3_cvd()
    try:
        from engine.intra_15m import touch_forming_from_price

        touch_forming_from_price(price)
    except Exception:
        pass
    if not _first_msg_logged:
        _first_msg_logged = True
        log.info(f"aggTrade ilk mesaj alindi price={price:.2f} qty={qty:.4f}")


def handle_rest_agg_trade(trade: dict) -> None:
    handle_agg_trade_event(
        {
            "e": "aggTrade",
            "p": str(float(trade.get("price", 0) or 0)),
            "q": str(float(trade.get("qty", 0) or 0)),
            "m": bool(trade.get("is_sell", False)),
        }
    )


def apply_agg_trades_batch(trades: list[dict], window_sec: float = _WINDOW_SEC) -> int:
    """REST recovery — son pencere tick'lerini yeniden yükle."""
    if not trades:
        return 0
    now = time.time()
    cutoff = now - window_sec
    fresh = [t for t in trades if t["ts"] >= cutoff]
    if not fresh:
        return 0

    state.ticks.clear()
    for t in sorted(fresh, key=lambda x: x["ts"]):
        state.ticks.append({
            "ts": t["ts"],
            "price": t.get("price", 0),
            "qty": t["qty"],
            "delta": t["delta"],
        })
        state.price = t.get("price") or state.price

    _recompute_cvd_window(cutoff)
    state.trade_last_update = now
    state.last_update = now
    if state.ticks:
        record_price_tick(state.ticks[-1].get("price") or state.price)
    record_metrics_sample()
    return len(fresh)


async def bootstrap_cvd_from_rest(*, force: bool = False) -> int:
    """Baslangicta veya WS kopunca son 5 dk aggTrade REST ile CVD/taker doldur."""
    global _rest_last_trade_id, _rest_last_trade_ts, _first_msg_logged
    from core.futures_public_rest import get_agg_trades
    from core.state import trade_is_fresh

    if not force and trade_is_fresh(30) and len(state.ticks) >= 50:
        return len(state.ticks)

    try:
        bootstrap = await get_agg_trades(limit=1000)
    except Exception as e:
        log.error(f"aggTrade REST bootstrap hata: {e}")
        return 0
    if not bootstrap:
        log.warning("aggTrade REST bootstrap bos dondu")
        return 0

    n = apply_agg_trades_batch(bootstrap, _WINDOW_SEC)
    if bootstrap:
        _rest_last_trade_id = max(int(t.get("id", 0) or 0) for t in bootstrap)
        _rest_last_trade_ts = max(float(t.get("ts", 0) or 0) for t in bootstrap)
    if n > 0:
        _first_msg_logged = True
        last_px = float(state.ticks[-1].get("price", 0) or 0) if state.ticks else 0.0
        log.info(
            f"aggTrade REST bootstrap: {n} tick, "
            f"CVD5m={state.cvd_5m:+.0f} taker={state.taker_ratio:.0%} "
            f"son fiyat={last_px:.2f}"
        )
        _maybe_update_v3_cvd()
    return n


async def _run_rest_fallback(*, skip_bootstrap: bool = False) -> None:
    global _rest_last_trade_id, _rest_last_trade_ts
    from core.futures_public_rest import get_agg_trades
    from feeds.market_recovery import note_market_ws_connected

    set_ws_connected(False)
    set_rest_fallback_active(True)
    note_market_ws_connected()
    log.info("aggTrade REST poll aktif (2s aralik)")

    if not skip_bootstrap:
        try:
            await bootstrap_cvd_from_rest()
        except Exception as e:
            log.error(f"aggTrade REST bootstrap hata: {e}")

    poll_sec = float(getattr(cfg, "TRADE_REST_POLL_SEC", 2.0))
    while not is_stopping():
        try:
            start_ts = max(_rest_last_trade_ts - 2.0, time.time() - 10.0)
            trades = await get_agg_trades(
                limit=1000,
                start_time_ms=int(start_ts * 1000),
            )
            fresh = [
                t for t in trades
                if int(t.get("id", 0) or 0) > _rest_last_trade_id
            ]
            fresh.sort(key=lambda x: int(x.get("id", 0) or 0))
            for trade in fresh:
                handle_rest_agg_trade(trade)
                _rest_last_trade_id = max(_rest_last_trade_id, int(trade.get("id", 0) or 0))
                _rest_last_trade_ts = max(_rest_last_trade_ts, float(trade.get("ts", 0) or 0))
        except Exception as e:
            log.error(f"aggTrade REST fallback hata: {e}")
        await stoppable_sleep(poll_sec)

    set_rest_fallback_active(False)


async def _close_if_no_first_message(ws, timeout_sec: float) -> None:
    await stoppable_sleep(timeout_sec)
    if is_stopping():
        return
    if ws_connected and not _first_msg_logged:
        try:
            from feeds.ws_common import close_ws_safely

            await close_ws_safely(ws)
        except Exception:
            pass


async def run():
    ws_enabled = bool(getattr(cfg, "TRADE_WS_ENABLED", True))
    if not ws_enabled:
        log.info("aggTrade WS kapali (TRADE_WS_ENABLED=false) — yalnizca REST poll")
        await bootstrap_cvd_from_rest()
        await _run_rest_fallback(skip_bootstrap=True)
        return

    retry = 3
    log.info(f"aggTrade URL: {URL}")
    await bootstrap_cvd_from_rest()
    while not is_stopping():
        ws = None
        disconnect_reason = ""
        connected_at = 0.0
        first_msg_task = None
        should_fallback = False
        try:
            from feeds.ws_common import ws_connect_kwargs, reconnect_delay, close_ws_safely
            from feeds.market_recovery import (
                note_market_ws_connected,
                note_market_ws_disconnected,
            )

            async with websockets.connect(URL, **ws_connect_kwargs()) as ws:
                retry = 3
                global _first_msg_logged
                _first_msg_logged = False
                set_rest_fallback_active(False)
                set_ws_connected(True)
                connected_at = time.time()
                note_market_ws_connected()
                log.info("aggTrade stream aktif (tek websocket)")
                first_msg_task = asyncio.create_task(
                    _close_if_no_first_message(
                        ws,
                        float(getattr(cfg, "TRADE_WS_FIRST_MSG_TIMEOUT_SEC", 12.0)),
                    )
                )
                async for raw in iter_ws_messages(ws):
                    if is_stopping():
                        break
                    msg = json.loads(raw)
                    handle_agg_trade_event(msg.get("data", msg))
                if not is_stopping():
                    first_timeout = float(getattr(cfg, "TRADE_WS_FIRST_MSG_TIMEOUT_SEC", 12.0))
                    if not _first_msg_logged and (time.time() - connected_at) >= first_timeout:
                        disconnect_reason = "no_first_message"
                        log.warning("aggTrade WS baglandi ama ilk mesaj gelmedi — fallback")
                    elif not disconnect_reason:
                        disconnect_reason = "stream_ended"
                        log.warning("aggTrade akisi beklenmedik bitti")

        except ConnectionClosed as e:
            disconnect_reason = f"ConnectionClosed code={getattr(e, 'code', '?')}"
            log.warning(f"aggTrade kapandı: {e} — {retry}s sonra yeniden")
        except Exception as e:
            disconnect_reason = str(e)
            log.error(f"aggTrade hata: {e}")
        finally:
            first_timeout = float(getattr(cfg, "TRADE_WS_FIRST_MSG_TIMEOUT_SEC", 12.0))
            if (
                not _first_msg_logged
                and connected_at > 0
                and (time.time() - connected_at) >= first_timeout
            ):
                should_fallback = True
                if disconnect_reason != "no_first_message":
                    disconnect_reason = "no_first_message"
                    log.warning("aggTrade WS ilk mesaji almadan kapandi — REST fallback")
            set_ws_connected(False)
            if first_msg_task is not None and not first_msg_task.done():
                first_msg_task.cancel()
                try:
                    await first_msg_task
                except Exception:
                    pass
            if disconnect_reason and not is_stopping():
                note_market_ws_disconnected(disconnect_reason)
            try:
                await close_ws_safely(ws)
            except Exception:
                pass

        if is_stopping():
            break
        if should_fallback or disconnect_reason == "no_first_message":
            log.info(
                "aggTrade WS mesaj vermiyor — REST poll devraldi. "
                "12s WS beklemesini atlamak icin .env: TRADE_WS_ENABLED=false"
            )
            await _run_rest_fallback(skip_bootstrap=True)
            break
        delay = reconnect_delay(
            retry,
            floor=float(getattr(cfg, "WS_RECONNECT_DELAY_SEC", 5.0)),
        )
        log.info(f"aggTrade yeniden baglanma ~{delay:.1f}s")
        await stoppable_sleep(delay)
        retry = min(retry * 2, 30)
