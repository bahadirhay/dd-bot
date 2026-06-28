"""
engine/daily_tsm_paper.py — Strateji F: GUNLUK time-series-momentum trend-takip paper (shadow).
GERCEK EMIR YOK.

BUYUK bulgu (scripts/_daily_tsm_wf.py): trend-takip ETH'de GUNLUK barda CALISIR — walk-forward
OOS +59..+85%, Sharpe 0.81..1.17 (N=30-90 temiz plato). Intraday (15m/1h) whipsaw oldururdu;
zaman dilimi gunluk olunca trend tutuyor. D'ye (intraday range/MR) TAMAMLAYICI 2. edge.

Sinyal: gunluk momentum = (close - close[-N]) / close[-N] ; >0 LONG, <0 SHORT (long-short).
Cikis: yon donunce flip (TSM dogasi; intraday SL yok, haftalarca tutus). -%60 DD goze alinir.
Gunde 1 kez kontrol. Gunluk kapanislar Binance public klines'tan cekilir (snapshot 31 gun yetmez).
Paper-only; gercek emir YOK.
"""
from __future__ import annotations

import json
import time
import urllib.request

from core.config import cfg
from core.logger import get_logger
from core.state import state

log = get_logger("DailyTSM")

_pos: dict | None = None
_restored = False
_last_day = 0
_closes_cache: tuple[int, list] = (0, [])


def _bar_id() -> int:
    return int(time.time() // 86400)  # gunluk


def _restore_pos() -> None:
    """Restart sonrasi acik F pozisyonunu DB'den geri yukle (cok-gunluk tutus).
    F gunluk trend — restart pozisyonu sifirlamamali, yoksa her restart kari siler."""
    global _pos, _restored
    if _restored:
        return
    _restored = True
    try:
        from botlog.db import get_open_ftsm

        row = get_open_ftsm()
        if row:
            _pos = {"id": row["id"], "side": row["side"], "entry": row["entry"]}
            log.info(
                f"[F-TSM] acik pozisyon geri yuklendi: {row['side']} @{row['entry']:.1f} "
                f"(id={row['id']}) — restart'ta sifirlanmadi"
            )
    except Exception as ex:
        log.warning(f"[F-TSM] restore: {ex}")


def _daily_closes(sym: str, limit: int = 200) -> list:
    """ETH gunluk kapanislar (Binance public klines). Gunde 1 kez cek, cachele."""
    global _closes_cache
    day = _bar_id()
    if _closes_cache[0] == day and _closes_cache[1]:
        return _closes_cache[1]
    try:
        u = ("https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=1d&limit=%d"
             % (sym, limit))
        r = json.loads(urllib.request.urlopen(u, timeout=15).read())
        cl = [float(x[4]) for x in r if float(x[4]) > 0]
        _closes_cache = (day, cl)
        return cl
    except Exception as ex:
        log.warning(f"[F-TSM] klines cek: {ex}")
        return _closes_cache[1]


def paper_tick() -> None:
    global _pos, _last_day
    if not bool(getattr(cfg, "V3_FTSM_PAPER", True)):
        return
    _restore_pos()  # restart sonrasi acik pozisyonu bir kez geri yukle
    day = _bar_id()
    if day == _last_day:
        return  # gunde 1 kez
    px = float(getattr(state, "mark_price", 0) or getattr(state, "price", 0) or 0)
    if px <= 0:
        return
    N = int(getattr(cfg, "V3_FTSM_N", 40) or 40)
    sym = str(getattr(cfg, "V3_FTSM_SYMBOL", "ETHUSDT") or "ETHUSDT")
    C = _daily_closes(sym, N + 60)
    if len(C) < N + 1 or C[-1 - N] <= 0:
        return
    _last_day = day
    mom = (C[-1] - C[-1 - N]) / C[-1 - N] * 100  # %
    sig = "LONG" if mom > 0 else "SHORT"
    fee = 3.0
    try:
        from botlog.db import log_ftsm_close, log_ftsm_open

        if _pos is None:
            rid = log_ftsm_open(sig, px, mom)
            _pos = {"id": rid, "side": sig, "entry": px}
            log.info(f"[F-TSM] {sig} @{px:.1f} mom={mom:+.1f}%% (gunluk trend)")
        elif _pos["side"] != sig:
            ent = _pos["entry"]
            cur = ((px - ent) if _pos["side"] == "LONG" else (ent - px)) / ent * 1e4
            log_ftsm_close(_pos["id"], px, cur - fee, "trend-flip")
            log.info(f"[F-TSM] {_pos['side']} kapandi {cur:+.0f}bps -> {sig} flip")
            rid = log_ftsm_open(sig, px, mom)
            _pos = {"id": rid, "side": sig, "entry": px}
    except Exception as ex:
        log.warning(f"[F-TSM] tick: {ex}")
