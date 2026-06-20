"""
engine/statband_paper.py — Istatistiksel-bant A paper (shadow). GERCEK EMIR YOK.

A'nin kok-fix'i: swing-kutu (fiyati kovalar, box-chase) yerine ISTATISTIKSEL BANT.
Yon = fiyat z-score (mean +- k*sigma'dan sapma), hedef = ortalama (reversion).
Backtest 25 gun: eski A -413 -> stat-bant +955 (B +873'u de gecti), 3/4 ceyrek,
makro sart (makrosuz -15). Filtre degil, KARAR-MANTIGI degisikligi.

Cikis: ortalamaya donus (z=0) + TP1/runner (mean'de %50, kalan trail) | hard-SL |
maxhold. Makro-yon kapisi: 24h egim ters-asiri ise acma. Paper-only; gercek emir YOK.
"""
from __future__ import annotations

import time
from statistics import mean, pstdev

from core.config import cfg
from core.logger import get_logger
from core.state import state

log = get_logger("StatBandPaper")

_pos: dict | None = None
_last_bar = 0


def _closes(limit):
    try:
        from engine.v3_common import bars_15m
        b = bars_15m(limit + 4)
        return [float(x.get("close", 0) or 0) for x in b if float(x.get("close", 0) or 0) > 0]
    except Exception:
        return []


def _bar_id() -> int:
    return int(time.time() // 900)


def paper_tick() -> None:
    global _pos, _last_bar
    if not bool(getattr(cfg, "V3_STATBAND_PAPER", True)):
        return
    px = float(getattr(state, "mark_price", 0) or getattr(state, "price", 0) or 0)
    if px <= 0:
        return
    M = int(getattr(cfg, "V3_SB_M", 24) or 24)
    C = _closes(M + 100)
    if len(C) < M + 1:
        return
    win = C[-M:]
    mu = mean(win); sd = pstdev(win)
    if sd <= 0:
        return
    z = (px - mu) / sd
    fee = 3.0
    try:
        from botlog.db import log_sb_close, log_sb_open

        if _pos is not None:
            side = _pos["side"]; ent = _pos["entry"]
            cur = ((px - ent) if side == "LONG" else (ent - px)) / ent * 1e4
            sl = float(getattr(cfg, "V3_SB_SL_BPS", 60) or 60)
            mh = int(getattr(cfg, "V3_SB_MAXHOLD", 16) or 16)
            adverse = ((px - ent) if side == "SHORT" else (ent - px)) / ent * 1e4
            # GERCEK-donus cikis: z=0 VE kar>=esik -> %100 kapat. Runner KALDIRILDI
            # (backtest: FULL +1197 > runner +1090). Sahte z=0 (zarar) -> bekle.
            min_prof = float(getattr(cfg, "V3_B_REVERT_MIN_PROFIT_BPS", 0.0) or 0.0)
            reverted = (side == "LONG" and z >= 0) or (side == "SHORT" and z <= 0)
            if adverse >= sl:
                log_sb_close(_pos["id"], px, cur - fee, "hard-SL"); _pos = None
            elif (_bar_id() - _pos["bar"]) >= mh:
                log_sb_close(_pos["id"], px, cur - fee, "maxhold"); _pos = None
            elif reverted and cur >= min_prof:
                log_sb_close(_pos["id"], px, cur - fee, "gercek-mean-revert"); _pos = None
            return
        # flat: sinyal?
        k = float(getattr(cfg, "V3_SB_K", 2.0) or 2.0)
        sig = "LONG" if z <= -k else ("SHORT" if z >= k else None)
        if not sig:
            return
        # makro-yon kapisi
        mac = float(getattr(cfg, "V3_SB_MACRO_BPS", 150) or 0)
        if mac > 0 and len(C) > 96 and C[-97] > 0:
            mc = (C[-1] - C[-97]) / C[-97] * 1e4
            if (sig == "SHORT" and mc > mac) or (sig == "LONG" and mc < -mac):
                return
        bid = _bar_id()
        if bid == _last_bar:
            return
        _last_bar = bid
        rid = log_sb_open(sig, px, z)
        _pos = {"id": rid, "side": sig, "entry": px, "bar": bid}
        log.info(f"[SB-PAPER] {sig} @{px:.1f} z={z:+.2f} (mean={mu:.1f})")
    except Exception as ex:
        log.warning(f"[SB-PAPER] tick: {ex}")
