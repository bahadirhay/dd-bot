"""
feeds/liq_clusters.py — Tasfiye fiyat kovaları (mmbot3 _rollup_liq_top_clusters).
"""
from __future__ import annotations

import time
from collections import deque

from core.config import cfg
from core.state import state

_events: deque = deque(maxlen=8000)


def record_liquidation(ts: float, price: float, usd: float, is_long_liq: bool):
    if price <= 0 or usd <= 0:
        return
    _events.append((ts, price, usd, is_long_liq))
    _rollup(time.time())


def _rollup(now: float | None = None):
    now = now or time.time()
    win = max(30.0, float(getattr(cfg, "LIQ_CLUSTER_WINDOW_SEC", 300.0)))
    bkt = max(0.5, float(getattr(cfg, "LIQ_BUCKET_USD", 5.0)))
    top_n = int(getattr(cfg, "LIQ_CLUSTER_TOP_N", 6))

    while _events and _events[0][0] < now - win:
        _events.popleft()

    agg: dict[float, tuple[float, float]] = {}
    for _ts, apx, usd, is_long in _events:
        pk = round(apx / bkt) * bkt
        lo, sh = agg.get(pk, (0.0, 0.0))
        if is_long:
            lo += usd
        else:
            sh += usd
        agg[pk] = (lo, sh)

    rows: list[tuple[float, float, str]] = []
    for pk, (lo, sh) in agg.items():
        if lo >= sh and lo > 0:
            rows.append((pk, lo, "LONG"))
        elif sh > lo:
            rows.append((pk, sh, "SHORT"))
        elif lo > 0:
            rows.append((pk, lo, "LONG"))

    rows.sort(key=lambda x: -x[1])
    state.liq_top_clusters = [
        {"price": round(pk, 2), "usd": round(usd, 0), "side": who}
        for pk, usd, who in rows[: max(1, top_n)]
    ]
