"""feeds/ws_common.py — Paylaşılan WebSocket bağlantı ayarları."""
from __future__ import annotations

import asyncio
import random

# ai-treding api_client: requests timeout 30s — handshake için de geniş pencere
WS_OPEN_TIMEOUT = 30
WS_PING_INTERVAL = 20
WS_PING_TIMEOUT = 15
WS_CLOSE_TIMEOUT = 5
WS_MAX_SIZE = 2**22


def ws_connect_kwargs() -> dict:
    return {
        "ping_interval": WS_PING_INTERVAL,
        "ping_timeout": WS_PING_TIMEOUT,
        "close_timeout": WS_CLOSE_TIMEOUT,
        "open_timeout": WS_OPEN_TIMEOUT,
        "max_size": WS_MAX_SIZE,
    }


def reconnect_delay(base: float, cap: float = 60.0, floor: float = 5.0) -> float:
    """Üstel backoff + küçük jitter (eşzamanlı reconnect fırtınasını azaltır)."""
    wait_base = max(base, floor)
    jitter = random.uniform(0, min(2.0, wait_base * 0.2))
    return min(wait_base + jitter, cap)


async def close_ws_safely(ws) -> None:
    """
    Reconnect öncesi websocket nesnesini güvenli kapat.
    websocket-client benzeri nesnelerde sock=None durumu varsa yeniden kapatmaya çalışma;
    websockets tarafında da kapalı/transport'u düşmüş bağlantıyı sessizce temizle.
    """
    if ws is None:
        return

    sock = getattr(ws, "sock", None)
    transport = getattr(ws, "transport", None)
    closed = bool(getattr(ws, "closed", False))
    if closed and sock is None and transport is None:
        return

    try:
        close_coro = ws.close()
        if asyncio.iscoroutine(close_coro):
            await close_coro
    except Exception:
        pass
