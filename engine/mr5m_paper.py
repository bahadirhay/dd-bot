"""
engine/mr5m_paper.py — 5m mean-reversion paper (shadow). GERCEK EMIR YOK.

Tahmin-edilebilirlik taramasi: ETH her ufukta mean-reverting (autocorr<0), ama
edge islem-basi en guclu 5m'de (+8.9bps, fee-sonrasi pozitif; 1m'de +1.6bps fee'ye
yenik, 15m daha zayif). 5m z-score MR walk-forward DOGRULANDI:
M72/K2.0/SL60 net +1186, TRAIN +391 / OOS +794, isabet %59 (15m B +1079/%51'i gecti).

Mantik = B/C ile AYNI, sadece ufuk farkli:
  Yon  = 5m fiyat z-score (M=72 pencere ~6h), |z|>=K(2.0) -> sapmaya ters.
  Giris filtre = 8h-slope ters-asiri (>150bps) ise acma (makro-yon kapisi).
  Cikis = ortalamaya donus (z=0): %50 + runner peak-trail 30bps | hard-SL 60bps | maxhold.
Paper-only; gercek emir YOK. B(15m) canli kalir, bu yaninda golge kosar.
"""
from __future__ import annotations

import time
from statistics import mean, pstdev

from core.config import cfg
from core.logger import get_logger
from core.state import state

log = get_logger("MR5mPaper")

_pos: dict | None = None
_last_bar = 0


def _closes_5m(limit):
    """5m kapanis serisi: 1m bar -> aggregate_5m."""
    try:
        from engine.v3_common import aggregate_5m, bars_1m

        b1 = bars_1m(limit * 5 + 40)
        b5 = aggregate_5m(b1)
        return [float(x.get("close", 0) or 0) for x in b5 if float(x.get("close", 0) or 0) > 0]
    except Exception:
        return []


def _bar_id() -> int:
    return int(time.time() // 300)


def paper_tick() -> None:
    global _pos, _last_bar
    if not bool(getattr(cfg, "V3_MR5M_PAPER", True)):
        return
    px = float(getattr(state, "mark_price", 0) or getattr(state, "price", 0) or 0)
    if px <= 0:
        return
    M = int(getattr(cfg, "V3_MR5M_M", 72) or 72)
    C = _closes_5m(M + 110)
    if len(C) < M + 1:
        return
    win = C[-M:]
    mu = mean(win); sd = pstdev(win)
    if sd <= 0:
        return
    z = (px - mu) / sd
    fee = 3.0
    try:
        from botlog.db import log_mr5m_close, log_mr5m_open

        if _pos is not None:
            side = _pos["side"]; ent = _pos["entry"]
            cur = ((px - ent) if side == "LONG" else (ent - px)) / ent * 1e4
            sl = float(getattr(cfg, "V3_MR5M_SL_BPS", 60) or 60)
            mh = int(getattr(cfg, "V3_MR5M_MAXHOLD", 16) or 16)
            adverse = ((px - ent) if side == "SHORT" else (ent - px)) / ent * 1e4
            # GERCEK-donus cikis: z=0 VE kar>=esik -> %100 kapat. Runner KALDIRILDI
            # (backtest: FULL +1197 > runner +1090). Sahte z=0 (zarar) -> bekle.
            min_prof = float(getattr(cfg, "V3_B_REVERT_MIN_PROFIT_BPS", 0.0) or 0.0)
            reverted = (side == "LONG" and z >= 0) or (side == "SHORT" and z <= 0)
            if adverse >= sl:
                log_mr5m_close(_pos["id"], px, cur - fee, "hard-SL"); _pos = None
            elif (_bar_id() - _pos["bar"]) >= mh:
                log_mr5m_close(_pos["id"], px, cur - fee, "maxhold"); _pos = None
            elif reverted and cur >= min_prof:
                log_mr5m_close(_pos["id"], px, cur - fee, "gercek-mean-revert"); _pos = None
            return
        # flat: sinyal?
        k = float(getattr(cfg, "V3_MR5M_K", 2.0) or 2.0)
        sig = "LONG" if z <= -k else ("SHORT" if z >= k else None)
        if not sig:
            return
        # makro-yon kapisi: 8h-slope (96 x 5m bar) ters-asiri ise acma
        mac = float(getattr(cfg, "V3_MR5M_MACRO_BPS", 150) or 0)
        if mac > 0 and len(C) > 96 and C[-97] > 0:
            mc = (C[-1] - C[-97]) / C[-97] * 1e4
            if (sig == "SHORT" and mc > mac) or (sig == "LONG" and mc < -mac):
                return
        bid = _bar_id()
        if bid == _last_bar:
            return
        _last_bar = bid
        rid = log_mr5m_open(sig, px, z)
        _pos = {"id": rid, "side": sig, "entry": px, "bar": bid}
        log.info(f"[MR5M-PAPER] {sig} @{px:.1f} z={z:+.2f} (mean={mu:.1f})")
    except Exception as ex:
        log.warning(f"[MR5M-PAPER] tick: {ex}")
