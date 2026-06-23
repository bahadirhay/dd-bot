"""
engine/tmom_paper.py — 1h MOMENTUM trend-takip paper (shadow). GERCEK EMIR YOK.

Kullanici icgorusu: trend zaman dilimine bagli. 15m'de tum trend-takip OOS-negatifti;
1h'de momentum (N24=24h geri-bakis, esik 150bps) ILK OOS-pozitif trend yaklasimi:
net +893, OOS +418, 4/4 ceyrek (fee8+slip2). N24-30/THR100-150 kumesi OOS+. Dar plato,
kucuk orneklem -> PAPER ile canli dogrulama. D(MR/range) yaninda trend-ayagi adayi.

Sinyal: r = (close - close[-N]) / close[-N] ; r>+THR -> LONG, r<-THR -> SHORT (TRENDLE GIT).
Cikis: tepe-trail (kazanani kostur) | hard-SL | maxhold. (MR DEGIL — trend-takip.)
Paper-only; gercek emir YOK.
"""
from __future__ import annotations

import time

from core.config import cfg
from core.logger import get_logger
from core.state import state

log = get_logger("TMomPaper")

_pos: dict | None = None
_last_bar = 0


def _closes_1h(limit):
    try:
        from engine.v3_common import bars_1h
        b = bars_1h(limit + 4)
        return [float(x.get("close", 0) or 0) for x in b if float(x.get("close", 0) or 0) > 0]
    except Exception:
        return []


def _bar_id() -> int:
    return int(time.time() // 3600)


def paper_tick() -> None:
    global _pos, _last_bar
    if not bool(getattr(cfg, "V3_TMOM_PAPER", True)):
        return
    px = float(getattr(state, "mark_price", 0) or getattr(state, "price", 0) or 0)
    if px <= 0:
        return
    N = int(getattr(cfg, "V3_TMOM_N", 24) or 24)
    C = _closes_1h(N + 10)
    if len(C) < N + 1:
        return
    fee = 3.0
    try:
        from botlog.db import log_tmom_close, log_tmom_open

        if _pos is not None:
            side = _pos["side"]; ent = _pos["entry"]
            cur = ((px - ent) if side == "LONG" else (ent - px)) / ent * 1e4
            sl = float(getattr(cfg, "V3_TMOM_SL_BPS", 120) or 120)
            trail = float(getattr(cfg, "V3_TMOM_TRAIL_BPS", 80) or 80)
            mh_h = int(getattr(cfg, "V3_TMOM_MAXHOLD_H", 72) or 72)
            adverse = ((px - ent) if side == "SHORT" else (ent - px)) / ent * 1e4
            # tepe-trail (kazanani kostur)
            if side == "LONG":
                _pos["peak"] = max(_pos["peak"], px); retr = (_pos["peak"] - px) / ent * 1e4
            else:
                _pos["peak"] = min(_pos["peak"], px); retr = (px - _pos["peak"]) / ent * 1e4
            if adverse >= sl:
                log_tmom_close(_pos["id"], px, -sl - fee, "hard-SL"); _pos = None
            elif retr >= trail and cur > 0:
                log_tmom_close(_pos["id"], px, cur - fee, "trail"); _pos = None
            elif (_bar_id() - _pos["bar"]) >= mh_h:
                log_tmom_close(_pos["id"], px, cur - fee, "maxhold"); _pos = None
            return
        # flat: momentum sinyali?
        thr = float(getattr(cfg, "V3_TMOM_THR", 150) or 150)
        if C[-1 - N] <= 0:
            return
        r = (px - C[-1 - N]) / C[-1 - N] * 1e4   # guncel fiyat vs N saat onceki kapanis
        sig = "LONG" if r > thr else ("SHORT" if r < -thr else None)
        if not sig:
            return
        bid = _bar_id()
        if bid == _last_bar:
            return
        _last_bar = bid
        rid = log_tmom_open(sig, px, r)
        _pos = {"id": rid, "side": sig, "entry": px, "bar": bid, "peak": px}
        log.info(f"[TMOM-PAPER] {sig} @{px:.1f} mom={r:+.0f}bps (trend-takip)")
    except Exception as ex:
        log.warning(f"[TMOM-PAPER] tick: {ex}")
