"""
engine/funding_paper.py — FUNDING-KONTRARYAN SHADOW. GERCEK EMIR YOK.

Bulgu (positioning-funding-jul2026, 7GB tam-tarama, oturumun tek yeni DOGRULANMIS edge'i):
  funding p85-uzeri (kalabalik LONG) -> maker-limit SHORT; p15-alti (kalabalik SHORT) -> maker-limit LONG.
  15m, ~2 saat tut (mh8). Permutasyon p=0.033 (drift-kontrollu, bagimsiz olay), 3/3 zaman-fold POZITIF,
  long+short DENGELI (30S/28L). Literaturde bilinen crowding mean-reversion -> bespoke degil a-priori makul.
  TEZ: KONUMLANMA (funding=pozisyon/kaldirac) ONGORUCU; anlik AKIS (taker/cvd) es-zamanli.

LONG-yetenekli (flow'dan cikaramadigimiz yon). Execution modeli xflow_paper ILE AYNI (klines H/L, maker-limit,
kacan=MISSED). CANLI forward'da olcer: gercek fill-rate + net dogrulanan edge'i koruyor mu. Emir YOK.
KALIBRE: tek 43g pencere, n58; esikler o donemin dagilimi (funding rejim-kayarsa recalibrate).

Durum makinesi: PENDING -> FILLED/MISSED -> CLOSED. Ayni funding-bandinda tekrar girmez (band-cikisinda arm).
"""
from __future__ import annotations

import json
import time
import urllib.request

from core.config import cfg
from core.logger import get_logger
from core.state import state

log = get_logger("FundingPaper")

SYMBOL = "ETHUSDT"
FUND_HI = 0.00008        # >=bu (kalabalik long) -> SHORT   (43g p85 ~ +0.008%/8h)
FUND_LO = -0.00005       # <=bu (kalabalik short) -> LONG    (43g p15)
OFFSET = 5.0            # maker limit bps (LONG alt / SHORT ust)
FILL_WINDOW = 3         # bar; dolmazsa MISS
MHOLD = 8              # bar (15m*8 = ~2 saat; permutasyonun test ettigi ufuk)
SL = 90.0
COST = 8.0

_pending: dict | None = None
_pos: dict | None = None
_bar = 0
_armed = True           # ayni funding-bandinda tekrar tetiklememe
_restored = False
_cache: tuple = (0, [])


def _bar_id() -> int:
    return int(time.time() // 900)


def _klines(limit: int = 20) -> list | None:
    global _cache
    day = _bar_id()
    if _cache[0] == day and _cache[1]:
        return _cache[1]
    try:
        u = ("https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=15m&limit=%d" % (SYMBOL, limit))
        r = json.loads(urllib.request.urlopen(u, timeout=12).read())
        bars = [(float(x[2]), float(x[3]), float(x[4])) for x in r]  # h,l,c
        closed = bars[:-1]
        _cache = (day, closed)
        return closed
    except Exception as ex:
        log.warning(f"[FUND] klines: {ex}")
        return _cache[1] or None


def _restore() -> None:
    global _pending, _pos, _restored, _armed
    if _restored:
        return
    _restored = True
    try:
        from botlog.db import get_open_funding
        row = get_open_funding(SYMBOL)
        if not row:
            return
        _armed = False
        if row["status"] == "PENDING":
            _pending = {"id": row["id"], "side": row["side"], "funding": row["funding"], "lim": row["lim"], "waited": 0}
        elif row["status"] == "FILLED":
            _pos = {"id": row["id"], "side": row["side"], "fill": row["fill"] or row["lim"], "held": 0}
        log.info(f"[FUND] restore: {row['status']} {row['side']} (id={row['id']})")
    except Exception as ex:
        log.warning(f"[FUND] restore: {ex}")


def paper_tick() -> None:
    global _pending, _pos, _bar, _armed
    if not bool(getattr(cfg, "V3_FUNDING_PAPER", True)):
        return
    _restore()
    bar = _bar_id()
    if bar == _bar:
        return
    _bar = bar

    from botlog.db import log_funding_open, update_funding_status
    bars = _klines()
    if not bars or len(bars) < 2:
        return
    i = len(bars) - 1
    h, l, c = bars[i][0], bars[i][1], bars[i][2]
    if c <= 0:
        return

    # 1) ACIK pozisyon -> cikis (SL ters, yoksa maxhold)
    if _pos:
        side = _pos["side"]; fill = _pos["fill"]; _pos["held"] += 1
        cur = ((c - fill) if side == "LONG" else (fill - c)) / fill * 1e4
        adv = ((fill - l) if side == "LONG" else (h - fill)) / fill * 1e4
        if adv >= SL:
            update_funding_status(_pos["id"], "CLOSED", exit_px=fill * (1 - SL / 1e4) if side == "LONG" else fill * (1 + SL / 1e4),
                                  pnl_bps=round(-SL - COST, 1), reason="sl")
            log.info(f"[FUND] SL {side} -{SL:.0f}bps")
            _pos = None
        elif _pos["held"] >= MHOLD:
            update_funding_status(_pos["id"], "CLOSED", exit_px=c, pnl_bps=round(cur - COST, 1), reason="maxhold")
            log.info(f"[FUND] maxhold {side} +{cur:.0f}bps (net {cur-COST:+.0f})")
            _pos = None
        return

    # 2) BEKLEYEN limit -> doldu mu / kacti mi (LONG alt->dususe deger / SHORT ust->yukselise deger)
    if _pending:
        side = _pending["side"]; lim = _pending["lim"]; _pending["waited"] += 1
        filled = (l <= lim) if side == "LONG" else (h >= lim)
        if filled:
            update_funding_status(_pending["id"], "FILLED", fill_px=lim)
            _pos = {"id": _pending["id"], "side": side, "fill": lim, "held": 0}
            log.info(f"[FUND] FILLED {side} @{lim:.2f}")
            _pending = None
        elif _pending["waited"] >= FILL_WINDOW:
            update_funding_status(_pending["id"], "MISSED")
            log.info(f"[FUND] MISSED {side} (limit {lim:.2f} {FILL_WINDOW} barda dolmadi)")
            _pending = None
        return

    # 3) FLAT -> funding uc bandi? (arm/disarm: ayni bandda tek tetik)
    fund = float(state.funding_rate or 0)
    in_hi = fund >= FUND_HI
    in_lo = fund <= FUND_LO
    if not (in_hi or in_lo):
        _armed = True
        return
    if not _armed:
        return
    side = "SHORT" if in_hi else "LONG"
    lim = c * (1 - OFFSET / 1e4) if side == "LONG" else c * (1 + OFFSET / 1e4)
    rid = log_funding_open(SYMBOL, side, fund, c, lim)
    _pending = {"id": rid, "side": side, "funding": fund, "lim": lim, "waited": 0}
    _armed = False
    log.info(f"[FUND] LIMIT kondu {side} sig={c:.2f} lim={lim:.2f} funding={fund:.6f} (kontraryan)")
