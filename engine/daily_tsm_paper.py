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
_blocked = 0            # SL sonrasi bloklu yon (+1 LONG / -1 SHORT); sinyal donunce kalkar
_bars_cache: tuple[int, list] = (0, [])


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
            _pos = {"id": row["id"], "side": row["side"], "entry": row["entry"], "peak": row["entry"]}
            log.info(
                f"[F-TSM] acik pozisyon geri yuklendi: {row['side']} @{row['entry']:.1f} "
                f"(id={row['id']}) — restart'ta sifirlanmadi"
            )
    except Exception as ex:
        log.warning(f"[F-TSM] restore: {ex}")


def _daily_bars(sym: str, limit: int = 200) -> list:
    """ETH gunluk barlar (high,low,close) — Binance public klines. Gunde 1 kez cek, cachele.
    SL/trail icin H/L de lazim (yalniz close yetmez)."""
    global _bars_cache
    day = _bar_id()
    if _bars_cache[0] == day and _bars_cache[1]:
        return _bars_cache[1]
    try:
        u = ("https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=1d&limit=%d"
             % (sym, limit))
        r = json.loads(urllib.request.urlopen(u, timeout=15).read())
        bars = [(float(x[2]), float(x[3]), float(x[4])) for x in r if float(x[4]) > 0]
        _bars_cache = (day, bars)
        return bars
    except Exception as ex:
        log.warning(f"[F-TSM] klines cek: {ex}")
        return _bars_cache[1]


def paper_tick() -> None:
    global _pos, _last_day, _blocked
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
    bars = _daily_bars(sym, N + 60)
    if len(bars) < N + 1 or bars[-1 - N][2] <= 0:
        return
    C = [b[2] for b in bars]
    _last_day = day
    mom = (C[-1] - C[-1 - N]) / C[-1 - N] * 100  # %
    sig = "LONG" if mom > 0 else "SHORT"
    want = 1 if mom > 0 else -1
    fee = 3.0
    sl_bps = float(getattr(cfg, "V3_FTSM_SL_BPS", 0) or 0)
    trail_bps = float(getattr(cfg, "V3_FTSM_TRAIL_BPS", 0) or 0)
    # son KAPANMIS gunun H/L'i (SL/trail bu gunun asiri hareketiyle test edilir)
    d_high, d_low, d_close = bars[-1]
    try:
        from botlog.db import log_ftsm_close, log_ftsm_open

        # 0) sinyal blokli yonden dondu -> blok kalk
        if _blocked and want != _blocked:
            _blocked = 0

        # 1) ACIK pozisyon -> once koruma (SL/trail), sonra flip
        if _pos is not None:
            ent = _pos["entry"]
            side = _pos["side"]
            # felaket-SL: entry'den aleyhte asiri hareket (gun-ici)
            if sl_bps > 0:
                adv = ((ent - d_low) if side == "LONG" else (d_high - ent)) / ent * 1e4
                if adv >= sl_bps:
                    log_ftsm_close(_pos["id"], ent * (1 - sl_bps / 1e4) if side == "LONG"
                                   else ent * (1 + sl_bps / 1e4), -sl_bps - fee, "sl")
                    log.info(f"[F-TSM] {side} SL -{sl_bps:.0f}bps (entry'den felaket) -> blok (donene dek)")
                    _blocked = 1 if side == "LONG" else -1
                    _pos = None
            # trailing: tepe-fiyattan geri cekilme (yalniz karda)
            if _pos is not None and trail_bps > 0:
                peak = _pos.get("peak", ent)
                peak = max(peak, d_high) if side == "LONG" else min(peak, d_low)
                _pos["peak"] = peak
                cur = ((d_close - ent) if side == "LONG" else (ent - d_close)) / ent * 1e4
                retr = ((peak - d_close) if side == "LONG" else (d_close - peak)) / ent * 1e4
                if cur > 0 and retr >= trail_bps:
                    pnl = ((d_close - ent) if side == "LONG" else (ent - d_close)) / ent * 1e4
                    log_ftsm_close(_pos["id"], d_close, pnl - fee, "trail")
                    log.info(f"[F-TSM] {side} trailing +{pnl:.0f}bps (tepe-{trail_bps:.0f}) -> blok (donene dek)")
                    _blocked = 1 if side == "LONG" else -1
                    _pos = None

        # 2) FLAT + bloklu-degil -> sinyal yonune gir
        if _pos is None:
            if _blocked == want:
                return  # bu yon bloklu, sinyal donene kadar bekle
            rid = log_ftsm_open(sig, px, mom)
            _pos = {"id": rid, "side": sig, "entry": px, "peak": px}
            log.info(f"[F-TSM] {sig} @{px:.1f} mom={mom:+.1f}%% (gunluk trend)")
            return

        # 3) ACIK + sinyal ters -> flip
        if _pos["side"] != sig:
            ent = _pos["entry"]
            cur = ((px - ent) if _pos["side"] == "LONG" else (ent - px)) / ent * 1e4
            log_ftsm_close(_pos["id"], px, cur - fee, "trend-flip")
            log.info(f"[F-TSM] {_pos['side']} kapandi {cur:+.0f}bps -> {sig} flip")
            rid = log_ftsm_open(sig, px, mom)
            _pos = {"id": rid, "side": sig, "entry": px, "peak": px}
    except Exception as ex:
        log.warning(f"[F-TSM] tick: {ex}")
