"""Kesilebilir asyncio.sleep — Ctrl+C sırasında bekleme bölünür."""
from __future__ import annotations

import asyncio

from core.shutdown import is_stopping


async def stoppable_sleep(seconds: float, *, step: float = 0.25) -> None:
    remaining = max(0.0, float(seconds))
    step = min(step, remaining) if remaining else step
    while remaining > 0 and not is_stopping():
        chunk = min(step, remaining)
        await asyncio.sleep(chunk)
        remaining -= chunk
