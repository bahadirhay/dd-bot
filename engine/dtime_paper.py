"""
engine/dtime_paper.py — D SAAT-ATRIBUSYON SHADOW. GERCEK EMIR YOK.

Her D sinyalini UTC-saat etiketiyle loglar + full-exit sonucunu klines'te izler. Amac: forward'da
SAAT-BASI D-net'i olcmek. Bulgu (6000-bar): US-acilis 13-15 UTC yuksek-vol/trend (D whipsaw), gece
16 + 23-02 yuksek-MR (D edge guclu). Backtest saat-filtresi TRAIN+%17 ama OOS-neutral -> canliya
ALINMADI; bu shadow gercekten saat-bazli fark var mi forward-dogrular. Deploy DEGIL, olcum.
"""
from __future__ import annotations

import json
import time
import urllib.request

from core.config import cfg
from core.logger import get_logger

log = get_logger("DTimePaper")

M = 40
SL = 300.0
MHOLD = 64
_pos: dict | None = None
_bar = 0
_restored = False
_cache: tuple = (0, [])


def _bar_id() -> int:
    return int(time.time() // 900)


def _klines(limit: int = 130):
    global _cache
    day = _bar_id()
    if _cache[0] == day and _cache[1]:
        return _cache[1]
    try:
        u = ("https://fapi.binance.com/fapi/v1/klines?symbol=ETHUSDT&interval=15m&limit=%d" % limit)
        r = json.loads(urllib.request.urlopen(u, timeout=12).read())
        closed = [(float(x[2]), float(x[3]), float(x[4])) for x in r][:-1]
        _cache = (day, closed)
        return closed
    except Exception:
        return _cache[1] or None


def _restore():
    global _pos, _restored
    if _restored:
        return
    _restored = True
    try:
        from botlog.db import get_open_dtime
        row = get_open_dtime()
        if row:
            _pos = {"id": row["id"], "side": row["side"], "entry": row["entry"], "held": 0}
    except Exception:
        pass


def paper_tick() -> None:
    global _pos, _bar
    if not bool(getattr(cfg, "V3_DTIME_PAPER", True)):
        return
    _restore()
    bar = _bar_id()
    if bar == _bar:
        return
    _bar = bar
    from botlog.db import log_dtime_open, close_dtime
    bars = _klines()
    if not bars or len(bars) < M + 2:
        return
    i = len(bars) - 1
    h, l, c = bars[i]

    # ACIK -> full-exit (poc_revert %100 / SL / maxhold) izle
    if _pos:
        side = _pos["side"]; ent = _pos["entry"]; _pos["held"] += 1
        cur = ((c - ent) if side == "LONG" else (ent - c)) / ent * 1e4
        adv = ((h - ent) if side == "SHORT" else (ent - l)) / ent * 1e4
        try:
            from engine.poc_paper import poc_mean_reverted
            rev = poc_mean_reverted(side)
        except Exception:
            rev = False
        if adv >= SL:
            close_dtime(_pos["id"], -SL - 12, "sl"); _pos = None
        elif rev and cur >= 0:
            close_dtime(_pos["id"], cur - 12, "poc_revert"); _pos = None
        elif _pos["held"] >= MHOLD:
            close_dtime(_pos["id"], cur - 12, "maxhold"); _pos = None
        return

    # FLAT -> D sinyali (canli D ile birebir)
    try:
        from engine.poc_paper import compute_signal
        s = compute_signal()
    except Exception:
        return
    side = s.get("signal")
    if not side or not s.get("ready"):
        return
    ent = float(s.get("px") or c)
    hour = int((time.time() // 3600) % 24)   # UTC saat
    rid = log_dtime_open(hour, side, ent)
    _pos = {"id": rid, "side": side, "entry": ent, "held": 0}
    log.info(f"[DTIME] {side} @{ent:.2f} saat={hour:02d} UTC (saat-atribusyon)")
