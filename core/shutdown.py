"""
core/shutdown.py — Ctrl+C ile temiz kapanış (Windows + asyncio uyumlu).

asyncio.Event import anında oluşturulunca Windows'ta sinyal ile senkron kalabiliyor;
threading.Event + loop.call_soon_threadsafe ile iptal güvenilir.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Callable

_stop = threading.Event()
_force = False
_loop: asyncio.AbstractEventLoop | None = None
_on_stop: Callable[[], None] | None = None


def register_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def on_stop(callback: Callable[[], None]) -> None:
    """Ctrl+C: asyncio tarafında görev iptali vb."""
    global _on_stop
    _on_stop = callback


def request_stop(force: bool = False) -> None:
    global _force
    if force:
        _force = True
    _stop.set()
    loop = _loop
    cb = _on_stop
    if loop is not None and loop.is_running() and cb is not None:
        try:
            loop.call_soon_threadsafe(cb)
        except RuntimeError:
            pass


def is_stopping() -> bool:
    return _stop.is_set()


def force_exit_requested() -> bool:
    return _force


def reset_stop() -> None:
    """Test / yeniden başlatma."""
    global _force
    _force = False
    _stop.clear()


async def wait_stop() -> None:
    while not _stop.is_set():
        await asyncio.sleep(0.15)


async def iter_ws_messages(ws):
    """
    ws.recv() için tek bir pending task tut.
    Her 1s'de bir is_stopping kontrolü yapar ama recv coroutine'ini tekrar tekrar
    iptal etmez; bu, Windows + websockets tarafında sessiz takılma / okuma bozulması
    ihtimalini azaltır.
    """
    recv_task: asyncio.Task | None = None
    while not is_stopping():
        try:
            if recv_task is None:
                recv_task = asyncio.create_task(ws.recv())
            done, _pending = await asyncio.wait({recv_task}, timeout=1.0)
            if not done:
                continue
            msg = await recv_task
            recv_task = None
        except asyncio.TimeoutError:
            continue
        except (StopAsyncIteration, EOFError):
            break
        yield msg
    if recv_task is not None and not recv_task.done():
        recv_task.cancel()
        try:
            await recv_task
        except Exception:
            pass
