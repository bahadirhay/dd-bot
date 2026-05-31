"""Kapalı 1m mumlar — nabız katmanı (grafikte son X dakika ile aynı)."""
from __future__ import annotations

from collections import deque

_bars_1m: deque = deque(maxlen=600)


def add_bar_1m(candle: dict) -> None:
    _bars_1m.append(candle)


def get_bars_1m(limit: int = 30) -> list:
    if limit <= 0:
        return list(_bars_1m)
    return list(_bars_1m)[-limit:]


def set_bars_1m(bars: list) -> None:
    _bars_1m.clear()
    for b in bars:
        _bars_1m.append(b)
