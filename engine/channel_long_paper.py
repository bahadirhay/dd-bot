"""
engine/channel_long_paper.py — Kanal-LONG paper (shadow). GERCEK EMIR YOK.

Kullanici sezgisi: range'de destekten yukari donus = LONG (kanal-ici). Backtest
ortalamada negatif dedi AMA tek tek bounce'lar (1747->1765 +100) kazaniyor. Bu
celiskiyi CANLI veriyle cozmek icin: kosullar olusunca SANAL long acar, chlong_paper
tablosuna yazar. Birkac gun sonra net pozitifse gercege alinir, degilse dokunulmaz.

Kurulum (saf hesap, A-tarzi kanal): RANGE (efficiency-ratio<esik) + fiyat son N x15m
araligin alt %25'inde (destek) + DONUS-TEYIDI (son K barda yeni dip yok = baz tuttu).
Cikis: karsi kenar (TP) | hard-SL destek alti | maxhold.
"""
from __future__ import annotations

import time

from core.config import cfg
from core.logger import get_logger
from core.state import state

log = get_logger("ChLongPaper")

_pos: dict | None = None
_last_bar = 0


def _bars():
    try:
        from engine.v3_common import bars_15m
        return bars_15m(60)
    except Exception:
        return []


def _eff(C, m=48):
    if len(C) < m + 1:
        return 1.0
    seg = C[-(m + 1):]
    net = abs(seg[-1] - seg[0])
    tot = sum(abs(seg[i] - seg[i - 1]) for i in range(1, len(seg)))
    return net / tot if tot > 0 else 1.0


def paper_tick() -> None:
    """Her dongude: kosul olusursa sanal long ac/kapat, chlong_paper'a yaz. Risk YOK."""
    global _pos, _last_bar
    if not bool(getattr(cfg, "V3_CHLONG_PAPER", True)):
        return
    px = float(getattr(state, "mark_price", 0) or getattr(state, "price", 0) or 0)
    if px <= 0:
        return
    bars = _bars()
    if len(bars) < 30:
        return
    H = [float(b.get("high", 0) or 0) for b in bars]
    L = [float(b.get("low", 0) or 0) for b in bars]
    C = [float(b.get("close", 0) or 0) for b in bars]
    try:
        from botlog.db import log_chlong_close, log_chlong_open

        if _pos is not None:
            ent = _pos["entry"]; sup = _pos["support"]; tp = _pos["tp"]
            sl_bps = float(getattr(cfg, "V3_CHLONG_SL_BPS", 50) or 50)
            res = reason = None
            if (ent - px) / ent * 1e4 >= sl_bps:
                res = -sl_bps; reason = "hard-SL"
            elif px >= tp:
                res = (px - ent) / ent * 1e4; reason = "karsi-kenar TP"
            elif _bar_id() - _pos["bar"] >= int(getattr(cfg, "V3_CHLONG_MAXHOLD", 16) or 16):
                res = (px - ent) / ent * 1e4; reason = "maxhold"
            if res is not None:
                log_chlong_close(_pos["id"], px, res - 3.0, reason)
                log.info(f"[CHLONG-PAPER] KAPANDI {ent:.1f}->{px:.1f} {res-3.0:+.0f}bps ({reason})")
                _pos = None
            return
        # flat: kurulum?
        bid = _bar_id()
        N = 24; edge = 0.25; K = 2
        hh = max(H[-N:]); ll = min(L[-N:]); rng = hh - ll
        if rng <= 0:
            return
        pos = (px - ll) / rng
        if pos > edge:
            return  # destek bolgesinde degil
        if _eff(C) >= float(getattr(cfg, "V3_TREND_STR_MAX", 0.35) or 0.35):
            return  # trend, range degil
        # donus-teyidi: son K kapali barda yeni dip yok (baz tuttu)
        if len(L) >= K + 1 and min(L[-K:]) < min(L[-(K + 2):-K]) - 1e-9:
            return  # hala yeni dip yapiyor (knife) -> bekle
        # tek bar bir sinyal (bar basina)
        if bid == _last_bar:
            return
        _last_bar = bid
        tp = px + (hh - px) * 0.6  # karsi kenara dogru %60
        rid = log_chlong_open(px, ll)
        _pos = {"id": rid, "entry": px, "support": ll, "tp": tp, "bar": bid}
        log.info(f"[CHLONG-PAPER] LONG @{px:.1f} destek={ll:.1f} tp={tp:.1f} (range bounce)")
    except Exception as ex:
        log.warning(f"[CHLONG-PAPER] tick: {ex}")


def _bar_id() -> int:
    return int(time.time() // 900)
