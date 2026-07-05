"""
engine/dexit_paper.py — D CIKIS A/B SHADOW (full vs partial+trailing). GERCEK EMIR YOK.

Bulgu (bu seans): D poc_revert'te %100 kapatiyor. Alternatif: %50 poc_revert'te al + %50'yi trailing-stop
(40bps) ile tut. ETH 31g backtest: partial HEM train HEM OOS full'u gecti (OOS +232 vs +61, +5.3 vs +1.4/isl),
trail 25/40/60 hepsi kazandi (parametre-robust). Mekanizma: trailing, simetrik oynakligi asimetrik kazanca
cevirir (kilitle + geri-donusu kes). KANIT: OOS-pozitif ama tek 31g pencere, permutasyon YOK.

Bu shadow her canli D sinyalinde (compute_signal, ER-kapili -> canli D ile birebir) pozisyonu klines uzerinden
izler; poc_revert/SL/maxhold aninda HEM full HEM partial sonucunu hesaplayip loglar. Forward'da temiz A/B:
partial gercekten full'u geciyor mu. Gecerse canli D exit'i degistirmeyi tartisiriz. Emir YOK.
"""
from __future__ import annotations

import json
import time
import urllib.request

from core.config import cfg
from core.logger import get_logger

log = get_logger("DExitPaper")

SYMBOL = "ETHUSDT"
M = 40
SL = 90.0
MHOLD = 16
PCT = 0.5              # poc_revert'te kapatilan oran (kalan trailing)
TRAIL = 40.0          # bps; runner trailing-stop mesafesi
COST = 12.0           # taker round-trip (giris+cikis); fraksiyon bolmesi toplam fee'yi degistirmez

_pos: dict | None = None
_bar = 0
_restored = False
_cache: tuple = (0, [])


def _bar_id() -> int:
    return int(time.time() // 900)


def _klines(limit: int = 130) -> list | None:
    global _cache
    day = _bar_id()
    if _cache[0] == day and _cache[1]:
        return _cache[1]
    try:
        u = ("https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=15m&limit=%d" % (SYMBOL, limit))
        r = json.loads(urllib.request.urlopen(u, timeout=12).read())
        bars = [(float(x[2]), float(x[3]), float(x[4]), float(x[5])) for x in r]  # h,l,c,vol
        closed = bars[:-1]
        _cache = (day, closed)
        return closed
    except Exception as ex:
        log.warning(f"[DEXIT] klines: {ex}")
        return _cache[1] or None


def _poc(bars: list, i: int) -> float | None:
    nu = de = 0.0
    for j in range(i - M, i):
        v = bars[j][3] if bars[j][3] > 0 else 1.0
        nu += bars[j][2] * v
        de += v
    return nu / de if de > 0 else None


def _restore() -> None:
    global _pos, _restored
    if _restored:
        return
    _restored = True
    try:
        from botlog.db import get_open_dexit
        row = get_open_dexit(SYMBOL)
        if row:
            _pos = {"id": row["id"], "side": row["side"], "entry": row["entry"], "held": 0,
                    "phase": "active", "full_bps": None, "booked": 0.0, "peak": 0.0}
            log.info(f"[DEXIT] restore OPEN {row['side']} (id={row['id']})")
    except Exception as ex:
        log.warning(f"[DEXIT] restore: {ex}")


def paper_tick() -> None:
    global _pos, _bar
    if not bool(getattr(cfg, "V3_DEXIT_PAPER", True)):
        return
    _restore()
    bar = _bar_id()
    if bar == _bar:
        return
    _bar = bar

    from botlog.db import log_dexit_open, close_dexit
    bars = _klines()
    if not bars or len(bars) < M + 2:
        return
    i = len(bars) - 1
    h, l, c = bars[i][0], bars[i][1], bars[i][2]
    pc = _poc(bars, i)
    if not pc or c <= 0:
        return
    dev = (c - pc) / pc * 1e4

    # ACIK pozisyon -> her iki cikisi izle
    if _pos:
        side = _pos["side"]; ent = _pos["entry"]; _pos["held"] += 1
        cur = ((c - ent) if side == "LONG" else (ent - c)) / ent * 1e4
        adv = ((h - ent) if side == "SHORT" else (ent - l)) / ent * 1e4
        reverted = (dev <= 0) if side == "SHORT" else (dev >= 0)

        if _pos["phase"] == "active":
            if adv >= SL:                         # ikisi de SL yer
                close_dexit(_pos["id"], -SL - COST, -SL - COST, "sl")
                log.info(f"[DEXIT] SL {side} full=partial={-SL-COST:+.0f}")
                _pos = None
            elif reverted and cur >= 0:           # full kapanir; partial runner'a gecer
                _pos["full_bps"] = cur - COST
                _pos["booked"] = PCT * cur
                _pos["peak"] = cur
                _pos["phase"] = "runner"
            elif _pos["held"] >= MHOLD:            # revert olmadan maxhold -> ikisi ayni
                close_dexit(_pos["id"], cur - COST, cur - COST, "maxhold")
                _pos = None
            return

        # runner: kalan (1-PCT) trailing ile
        _pos["peak"] = max(_pos["peak"], cur)
        done = False; rgross = None; reason = ""
        if adv >= SL:
            rgross = -SL; reason = "runner_sl"; done = True
        elif cur <= _pos["peak"] - TRAIL:
            rgross = cur; reason = "runner_trail"; done = True
        elif _pos["held"] >= MHOLD:
            rgross = cur; reason = "runner_maxhold"; done = True
        if done:
            partial = _pos["booked"] + (1 - PCT) * rgross - COST
            close_dexit(_pos["id"], _pos["full_bps"], partial, reason)
            diff = partial - _pos["full_bps"]
            log.info(f"[DEXIT] {side} full {_pos['full_bps']:+.0f} vs partial {partial:+.0f} ({diff:+.0f}) {reason}")
            _pos = None
        return

    # FLAT -> canli D sinyali (birebir)
    try:
        from engine.poc_paper import compute_signal
        s = compute_signal()
    except Exception:
        return
    side = s.get("signal")
    if not side or not s.get("ready"):
        return
    ent = float(s.get("px") or c)
    rid = log_dexit_open(SYMBOL, side, ent)
    _pos = {"id": rid, "side": side, "entry": ent, "held": 0, "phase": "active",
            "full_bps": None, "booked": 0.0, "peak": 0.0}
    log.info(f"[DEXIT] OPEN {side} @{ent:.2f} dev={s.get('dev')} (full vs %{int(PCT*100)}+trail{TRAIL:.0f} A/B)")
