"""
feeds/user_stream.py — Binance Futures user data stream (listenKey).

Exchange-side: SL/TP/likidasyon → bot state ve seviye sıfırlama.
Keepalive başarısız → WebSocket kapatılır, dış döngü yeniden başlar.
"""
from __future__ import annotations

import asyncio
import json
import time

import aiohttp
import websockets
from websockets.exceptions import ConnectionClosed

from core.config import cfg, is_paper_mode
from core.state import state
from core.shutdown import is_stopping, iter_ws_messages
from core.async_sleep import stoppable_sleep
from core.logger import get_logger

log = get_logger("UserStream")

_listen_key: str = ""
_restart_requested: bool = False
_ws_ref: websockets.WebSocketClientProtocol | None = None


def _request_stream_restart(reason: str) -> None:
    """Keepalive veya WS hata — ana döngü yeniden bağlansın."""
    global _restart_requested, _ws_ref
    _restart_requested = True
    log.warning(f"User stream yeniden başlatma istendi: {reason}")
    ws = _ws_ref
    _ws_ref = None
    if ws is not None and not ws.closed:
        asyncio.create_task(_close_ws(ws))


async def _close_ws(ws) -> None:
    from feeds.ws_common import close_ws_safely

    await close_ws_safely(ws)


async def _close_listen_key(listen_key: str) -> None:
    """Eski oturumu kapat — çift bot / flapping bağlantı çakışmasını azaltır."""
    if not listen_key or not cfg.API_KEY:
        return
    headers = {"X-MBX-APIKEY": cfg.API_KEY}
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.delete(
                f"{cfg.REST}/fapi/v1/listenKey",
                headers=headers,
                params={"listenKey": listen_key},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status != 200:
                    data = await r.json(content_type=None)
                    log.debug(f"listenKey DELETE {r.status}: {data}")
    except Exception as e:
        log.debug(f"listenKey DELETE exception: {e!r}")


async def _create_listen_key() -> str:
    if not cfg.API_KEY:
        return ""
    headers = {"X-MBX-APIKEY": cfg.API_KEY}
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.post(
                f"{cfg.REST}/fapi/v1/listenKey",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                data = await r.json(content_type=None)
                if r.status != 200:
                    log.warning(f"listenKey POST HTTP {r.status}: {data}")
                    return ""
                if isinstance(data, dict) and data.get("code"):
                    log.warning(f"listenKey POST hata: {data}")
                    return ""
                key = (data or {}).get("listenKey", "")
                return str(key) if key else ""
    except Exception as e:
        log.warning(f"listenKey POST exception: {e!r}")
        return ""


async def _keepalive_ping(listen_key: str) -> bool:
    """PUT listenKey — False = expire riski, stream yeniden başlat."""
    if not listen_key:
        return False
    headers = {"X-MBX-APIKEY": cfg.API_KEY}
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.put(
                f"{cfg.REST}/fapi/v1/listenKey",
                headers=headers,
                params={"listenKey": listen_key},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                data = await r.json(content_type=None) if r.content_length else {}
                if r.status != 200:
                    log.warning(f"listenKey keepalive HTTP {r.status}: {data}")
                    return False
                if isinstance(data, dict) and int(data.get("code", 0) or 0) < 0:
                    log.warning(f"listenKey keepalive API: {data}")
                    return False
        log.info(f"listenKey keepalive OK (HTTP 200) listenKey={listen_key[:8]}...")
        return True
    except Exception as e:
        log.warning(f"listenKey keepalive exception: {e!r}")
        return False


async def _keepalive_loop(listen_key: str) -> None:
    """Arka planda PUT; başarısız olursa WS'i kapat → run() yeniden bağlanır."""
    interval = float(getattr(cfg, "USER_STREAM_KEEPALIVE_SEC", 30 * 60))
    while not is_stopping() and not _restart_requested:
        await stoppable_sleep(interval)
        if is_stopping() or _restart_requested:
            break
        if listen_key != _listen_key:
            break
        if not await _keepalive_ping(listen_key):
            _request_stream_restart("keepalive_failed")


def _order_type_label(o: dict) -> str:
    ot = str(o.get("o", ""))
    cp = o.get("cp", False)
    if cp and "STOP" in ot:
        return "SL"
    if cp and "TAKE_PROFIT" in ot:
        return "TP"
    if cp:
        return "CLOSE"
    return ot


async def _handle_order_update(msg: dict) -> None:
    o = msg.get("o") or {}
    if o.get("s") != cfg.SYMBOL:
        return

    status = str(o.get("X", ""))
    if status != "FILLED":
        return

    side = str(o.get("S", ""))
    otype = _order_type_label(o)
    rp = float(o.get("rp", 0) or 0)
    avg = float(o.get("ap", 0) or o.get("p", 0) or 0)
    close_pos = bool(o.get("cp", False))
    ts = time.time()

    log.info(
        f"USER STREAM emir: {otype} {side} FILLED  "
        f"avg={avg:.2f}  rp={rp:+.4f}  closePosition={close_pos}  ts={ts:.3f}"
    )

    if not state.in_position:
        return

    if otype == "TP" or (
        close_pos and state.pos_tp1 > 0 and abs(avg - state.pos_tp1) < state.pos_tp1 * 0.002
    ):
        if not state.pos_tp1_hit:
            state.pos_tp1_hit = True
            log.info("USER STREAM: TP1 doldu (exchange onayı)")

    if close_pos or otype in ("SL", "CLOSE", "MARKET"):
        qty_after = await _fetch_position_amt()
        if qty_after >= 0 and qty_after < 0.001:
            from execution.position_lifecycle import async_finalize_position_closed

            if otype == "SL":
                creason = "stop_loss"
            elif otype == "TP":
                creason = "take_profit"
            elif otype == "MARKET":
                creason = "market_close"
            else:
                creason = f"exchange_{otype.lower()}"

            t0 = time.time()
            await async_finalize_position_closed(
                creason,
                source="user_stream",
                exit_price=avg,
                pnl=rp if rp != 0 else None,
            )
            log.info(f"USER STREAM finalize gecikme ~{(time.time()-t0)*1000:.0f}ms")


async def _fetch_position_amt() -> float:
    try:
        from execution.executor import get_position_qty
        return await get_position_qty()
    except Exception:
        return -1.0


async def _handle_account_update(msg: dict) -> None:
    if not state.in_position:
        return
    if time.time() < getattr(state, "startup_grace_until", 0):
        return
    # Hedge modda boş SHORT satırı (pa=0) gerçek LONG'u kapatmış gibi görünmesin
    qty = await _fetch_position_amt()
    if qty >= 0 and qty < 0.001:
        from execution.position_lifecycle import async_finalize_position_closed

        await async_finalize_position_closed(
            "exchange_closed",
            source="user_stream",
        )
        log.info("USER STREAM: pozisyon borsada kapandı (REST doğrulama)")


async def _dispatch(msg: dict) -> None:
    et = msg.get("e", "")
    if et == "ORDER_TRADE_UPDATE":
        await _handle_order_update(msg)
    elif et == "ACCOUNT_UPDATE":
        await _handle_account_update(msg)


async def _run_stream_session(listen_key: str) -> None:
    """Tek WS oturumu; keepalive ile paralel."""
    global _ws_ref, _restart_requested

    url = f"{cfg.WS_SINGLE}{listen_key}"
    keepalive_task = asyncio.create_task(_keepalive_loop(listen_key))
    ws = None
    try:
        from feeds.ws_common import ws_connect_kwargs, close_ws_safely

        async with websockets.connect(url, **ws_connect_kwargs()) as ws:
            _ws_ref = ws
            log.info("User data stream aktif ✓")
            async for raw in iter_ws_messages(ws):
                if is_stopping() or _restart_requested:
                    break
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                await _dispatch(msg)
    finally:
        await close_ws_safely(ws)
        _ws_ref = None
        keepalive_task.cancel()
        try:
            await keepalive_task
        except asyncio.CancelledError:
            pass


async def run():
    global _listen_key, _restart_requested

    if is_paper_mode() or not cfg.API_KEY:
        log.info("User stream atlanıyor (paper veya API yok)")
        while not is_stopping():
            await stoppable_sleep(60)
        return

    if not getattr(cfg, "USER_STREAM_ENABLED", True):
        log.info("User stream kapalı (USER_STREAM_ENABLED=false)")
        while not is_stopping():
            await stoppable_sleep(60)
        return

    ka_sec = int(getattr(cfg, "USER_STREAM_KEEPALIVE_SEC", 30 * 60))
    log.info(
        f"User stream başlıyor — keepalive her {ka_sec // 60} dk "
        f"(expire ~60 dk, başarısız → otomatik yeniden bağlan)"
    )

    retry = 5
    while not is_stopping():
        _restart_requested = False
        old_key = _listen_key
        try:
            if old_key:
                await _close_listen_key(old_key)
            _listen_key = await _create_listen_key()
            if not _listen_key:
                log.warning("listenKey alınamadı — 30s sonra tekrar")
                await stoppable_sleep(30)
                continue

            log.info("User data stream bağlanıyor (SL/TP/exchange kapanış)")
            await _run_stream_session(_listen_key)

            if _restart_requested:
                log.info("User stream oturumu kapatıldı — listenKey yenileniyor")
                _listen_key = ""
                retry = 5
            elif not is_stopping():
                log.warning("User stream beklenmedik kapandı — yeniden bağlanıyor")

        except ConnectionClosed as e:
            log.warning(f"User stream WS kapandı: {e}")
        except Exception as e:
            log.error(f"User stream hata: {e}")

        if is_stopping():
            break
        # Hızlı kapan-aç döngüsü (çift instance) Binance limitine takılır
        from feeds.ws_common import reconnect_delay

        delay = reconnect_delay(
            retry,
            floor=float(getattr(cfg, "WS_RECONNECT_DELAY_SEC", 5.0)),
        )
        log.info(f"User stream yeniden bağlanma ~{delay:.1f}s")
        await stoppable_sleep(delay)
        retry = min(retry * 2, 60)

    _listen_key = ""
    log.info("User stream durdu")
