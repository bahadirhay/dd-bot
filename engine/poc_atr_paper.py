"""
engine/poc_atr_paper.py — D + ATR-uyarlanir SL A/B paper (shadow). GERCEK EMIR YOK.

Amac: Canli D (sabit 90bps SL) ile, AYNI girisi (POC dev>=85) ama SL=m*ATR kullanan
varyanti FORWARD kiyaslamak. Backtest'te ETH'te ATR x2.5 sabit-90'i gecmisti (+1459 vs
+522, OOS +2175 vs +1795) ama tek-rejim/cok-pencere zayif -> canli forward karar versin.

ATR = SL boyutlama (risk), giris-sinyali DEGIL -> "indikatorsuz" ilkesine aykiri degil.
SYMBOLS listesine coin eklemek serbest (ETH disindakiler icin beklenti dusuk: edge dogrulanmadi).
"""
from __future__ import annotations

import json
import time
import urllib.request
from statistics import mean

from core.config import cfg
from core.logger import get_logger

log = get_logger("PocAtrPaper")

# ETH (cipa, canli D A/B) + cift-olcu (ATR & std) robustluk barini gecen coinler:
# GLM/AVAX/ARB/INJ her iki olcude + ve >=3/5. SUI/OP elendi (tek-olcu/kirilgan).
SYMBOLS = ["ETHUSDT", "GLMUSDT", "AVAXUSDT", "ARBUSDT", "INJUSDT"]
M = 40                          # POC penceresi (canli D ile ayni)
# Vol-normalize: DEV ve SL her coinin KENDI ATR'sinin katlari (ATR=SL boyutlama, sinyal degil).
# Multipleler ETH'e cipalandi: ETH'te SL~2.5*ATR (en iyi cikan), DEV/SL orani canli D'nin 85/90'i.
ATR_P = 14
DEV_MULT = 2.36                # DEV_bps = 2.36 * ATR  (~85/90 * SL_MULT)
SL_MULT = 2.5                  # SL_bps  = 2.5  * ATR  (ETH'te sabit-90'i gecen carpan)
MAXHOLD = 16                   # bar (canli D ile ayni)
DEV_FLOOR = 40.0              # min giris esigi bps
SL_FLOOR = 30.0               # min SL bps
FEE = 3.0                       # paper kayit (bilgi); gercek net Binance'te olcum disi

_pos: dict[str, dict] = {}
_last_bar = 0
_restored = False
_cache: dict[str, tuple] = {}


def _bar_id() -> int:
    return int(time.time() // 900)  # 15m


def _klines(sym: str, limit: int = 130) -> list | None:
    day = _bar_id()
    c = _cache.get(sym)
    if c and c[0] == day:
        return c[1]
    try:
        u = ("https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=15m&limit=%d"
             % (sym, limit))
        r = json.loads(urllib.request.urlopen(u, timeout=12).read())
        bars = [(float(x[2]), float(x[3]), float(x[4]), float(x[5])) for x in r]  # h,l,c,vol
        closed = bars[:-1]  # son bar formasyonda -> at, yalniz kapanmis barlar
        _cache[sym] = (day, closed)
        return closed
    except Exception as ex:
        log.warning(f"[ATR-PAPER] {sym} klines: {ex}")
        return c[1] if c else None


def _poc(bars: list, i: int) -> float | None:
    nu = de = 0.0
    for j in range(i - M, i):
        v = bars[j][3] if bars[j][3] > 0 else 1.0
        nu += bars[j][2] * v
        de += v
    return nu / de if de > 0 else None


def _atr_bps(bars: list, i: int) -> float:
    tr = []
    for j in range(max(1, i - ATR_P), i):
        h, l, c = bars[j][0], bars[j][1], bars[j][2]
        pc = bars[j - 1][2]
        t = max(h - l, abs(h - pc), abs(l - pc))
        if c > 0:
            tr.append(t / c * 1e4)
    return mean(tr) if tr else 0.0


def _restore() -> None:
    global _restored
    if _restored:
        return
    _restored = True
    try:
        from botlog.db import get_open_atr
        for sym in SYMBOLS:
            row = get_open_atr(sym)
            if row:
                _pos[sym] = {"id": row["id"], "side": row["side"],
                             "entry": row["entry"], "sl": row["sl"], "open_ts": time.time()}
                log.info(f"[ATR-PAPER] {sym} acik pozisyon geri yuklendi: {row['side']} @{row['entry']:.4f}")
    except Exception as ex:
        log.warning(f"[ATR-PAPER] restore: {ex}")


def _tick_symbol(sym: str) -> None:
    from botlog.db import log_atr_open, log_atr_close

    bars = _klines(sym)
    if not bars or len(bars) < M + ATR_P + 2:
        return
    i = len(bars) - 1
    h, l, c = bars[i][0], bars[i][1], bars[i][2]
    pc = _poc(bars, i)
    if not pc or c <= 0:
        return
    dev = (c - pc) / pc * 1e4
    atr = _atr_bps(bars, i)
    dev_thr = max(DEV_MULT * atr, DEV_FLOOR)   # vol-normalize giris esigi
    pos = _pos.get(sym)

    if pos:
        side = pos["side"]; ent = pos["entry"]; sl = pos["sl"]
        cur = ((c - ent) if side == "LONG" else (ent - c)) / ent * 1e4
        # SL: bar menzili SL'i gectiyse (intrabar fitil)
        adv = ((h - ent) if side == "SHORT" else (ent - l)) / ent * 1e4
        sl_bps = (abs(ent - sl) / ent * 1e4) if sl > 0 else 90.0
        held = (time.time() - pos.get("open_ts", time.time())) / 900.0
        if adv >= sl_bps:
            log_atr_close(pos["id"], sl, round(-sl_bps - FEE, 1), "atr_sl")
            log.info(f"[ATR-PAPER] {sym} SL {side} -{sl_bps:.0f}bps")
            _pos.pop(sym, None)
        elif ((side == "LONG" and dev >= 0) or (side == "SHORT" and dev <= 0)) and cur >= 0:
            log_atr_close(pos["id"], c, round(cur - FEE, 1), "poc_revert")
            log.info(f"[ATR-PAPER] {sym} POC-donus {side} +{cur:.0f}bps")
            _pos.pop(sym, None)
        elif held >= MAXHOLD:
            log_atr_close(pos["id"], c, round(cur - FEE, 1), "maxhold")
            _pos.pop(sym, None)
        return

    # flat -> giris (vol-normalize esik: |dev| >= dev_thr)
    side = "LONG" if dev <= -dev_thr else ("SHORT" if dev >= dev_thr else None)
    if not side:
        return
    sl_bps = max(SL_MULT * atr, SL_FLOOR)
    sl = c * (1 - sl_bps / 1e4) if side == "LONG" else c * (1 + sl_bps / 1e4)
    rid = log_atr_open(sym, side, c, dev, sl)
    _pos[sym] = {"id": rid, "side": side, "entry": c, "sl": sl, "open_ts": time.time()}
    log.info(f"[ATR-PAPER] {sym} {side} @{c:.4f} dev={dev:+.0f}/{dev_thr:.0f} SL={sl_bps:.0f}bps(ATRx{SL_MULT})")


def paper_tick() -> None:
    global _last_bar
    if not bool(getattr(cfg, "V3_POC_ATR_PAPER", True)):
        return
    _restore()
    bar = _bar_id()
    if bar == _last_bar:
        return  # 15m bar basina bir kez
    _last_bar = bar
    for sym in SYMBOLS:
        try:
            _tick_symbol(sym)
        except Exception as ex:
            log.warning(f"[ATR-PAPER] {sym} tick: {ex}")
