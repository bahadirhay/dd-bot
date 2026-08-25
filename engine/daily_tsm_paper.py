"""
engine/daily_tsm_paper.py — Strateji F: GUNLUK time-series-momentum trend-takip paper (shadow).
GERCEK EMIR YOK.

BUYUK bulgu (scripts/_daily_tsm_wf.py): trend-takip ETH'de GUNLUK barda CALISIR — walk-forward
OOS +59..+85%, Sharpe 0.81..1.17 (N=30-90 temiz plato). Intraday (15m/1h) whipsaw oldururdu;
zaman dilimi gunluk olunca trend tutuyor. D'ye (intraday range/MR) TAMAMLAYICI 2. edge.

Sinyal: gunluk momentum = (close - close[-N]) / close[-N] ; >0 LONG, <0 SHORT (long-short).
Cikis: yon donunce flip (TSM dogasi; intraday SL yok, haftalarca tutus). felaket-SL COIN'E OZEL
(asagida SYMBOLS + _SL_BY_SYMBOL). Gunde 1 kez kontrol/coin. Gunluk kapanislar Binance public
klines'tan cekilir. Paper-only; gercek emir YOK.

COK-COIN (2026-07-30, kullanici karari): 150-likit-coin genis tarama (scripts/_f_broadscan.py) +
6-aday detay (scripts/_f_percoin.py) sonrasi ETH/BNB/XLM/LINK secildi — SL300 ile 4 BAGIMSIZ
ceyregin HICBIRINDE (ETH,BNB) veya sadece 1 hafif ceyrekte (XLM,LINK) net zarar yok. AAVE/DOT
her ikisinde de bir ceyrekte AGIR (-49/-52DD, -36/-53DD) zarar cikardigi icin DISLANDI.
"""
from __future__ import annotations

import json
import time
import urllib.request

from core.config import cfg
from core.logger import get_logger
from core.state import state

log = get_logger("DailyTSM")

# COK-COIN evren (kullanici + genis-tarama karari, 2026-07-30). BNB flip-only DAHA IYI cikti
# (SL300 toplam-net'i dusuruyor, 0/4 ceyrek zarar zaten flip-only'de de yok) -> SL haritasinda YOK.
SYMBOLS = ["ETHUSDT", "BNBUSDT", "XLMUSDT", "LINKUSDT"]

# PER-COIN felaket-SL (bps). scripts/_f_percoin.py + _f_broadscan.py: SADECE guclu intraday-MR/
# trend-karisik coinleri stop'tan ROBUST fayda gorur (0-1/4 ceyrek zararli kalir). BNB flip-only
# zaten 0/4 zararli VE SL300'den daha yuksek net -> haritada YOK (0 = flip-only). Yeni coin
# eklerken once _f_percoin.py ile sina (kor curve-fit DEGIL).
_SL_BY_SYMBOL: dict[str, float] = {
    "ETHUSDT": 300.0,
    "XLMUSDT": 300.0,
    "LINKUSDT": 300.0,
}

_pos: dict[str, dict | None] = {s: None for s in SYMBOLS}
_restored: dict[str, bool] = {s: False for s in SYMBOLS}
_last_day: dict[str, int] = {s: 0 for s in SYMBOLS}
_blocked: dict[str, int] = {s: 0 for s in SYMBOLS}          # SL/trail sonrasi bloklu yon
_bars_cache: dict[str, tuple[int, list]] = {s: (0, []) for s in SYMBOLS}


def _bar_id() -> int:
    return int(time.time() // 86400)  # gunluk


def _restore_pos(sym: str) -> None:
    """Restart sonrasi acik F pozisyonunu (bu sembol icin) DB'den geri yukle (cok-gunluk tutus).
    F gunluk trend — restart pozisyonu sifirlamamali, yoksa her restart kari siler."""
    if _restored.get(sym):
        return
    _restored[sym] = True
    try:
        from botlog.db import get_open_ftsm

        row = get_open_ftsm(sym)
        if row:
            _pos[sym] = {"id": row["id"], "side": row["side"], "entry": row["entry"], "peak": row["entry"]}
            log.info(
                f"[F-TSM] {sym} acik pozisyon geri yuklendi: {row['side']} @{row['entry']:.4g} "
                f"(id={row['id']}) — restart'ta sifirlanmadi"
            )
    except Exception as ex:
        log.warning(f"[F-TSM] {sym} restore: {ex}")


def _daily_bars(sym: str, limit: int = 200) -> list:
    """Gunluk barlar (high,low,close) — Binance public klines. Gunde 1 kez cek, cachele.
    SL/trail icin H/L de lazim (yalniz close yetmez)."""
    day = _bar_id()
    cached_day, cached_bars = _bars_cache.get(sym, (0, []))
    if cached_day == day and cached_bars:
        return cached_bars
    try:
        u = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1d&limit={limit}"
        r = json.loads(urllib.request.urlopen(u, timeout=15).read())
        bars = [(float(x[2]), float(x[3]), float(x[4])) for x in r if float(x[4]) > 0]
        _bars_cache[sym] = (day, bars)
        return bars
    except Exception as ex:
        log.warning(f"[F-TSM] {sym} klines cek: {ex}")
        return cached_bars


def _tick_symbol(sym: str, ref_px: float) -> None:
    """Tek sembol icin gunluk kontrol dongusu (eski tek-coin mantik, sembole parametrelendi)."""
    day = _bar_id()
    if day == _last_day.get(sym, 0):
        return  # gunde 1 kez
    N = int(getattr(cfg, "V3_FTSM_N", 40) or 40)
    bars = _daily_bars(sym, N + 60)
    if len(bars) < N + 1 or bars[-1 - N][2] <= 0:
        return
    C = [b[2] for b in bars]
    _last_day[sym] = day
    mom = (C[-1] - C[-1 - N]) / C[-1 - N] * 100  # %
    sig = "LONG" if mom > 0 else "SHORT"
    want = 1 if mom > 0 else -1
    fee = 3.0
    sl_bps = _SL_BY_SYMBOL.get(sym, 0.0)
    trail_bps = 0.0  # cok-coin evreninde trailing dogrulanmadi (bkz memory f-dd-protection), kapali
    d_high, d_low, d_close = bars[-1]
    px = ref_px if (sym == str(getattr(cfg, "V3_FTSM_SYMBOL", "ETHUSDT") or "ETHUSDT") and ref_px > 0) else d_close
    try:
        from botlog.db import log_ftsm_close, log_ftsm_open

        if _blocked.get(sym) and want != _blocked[sym]:
            _blocked[sym] = 0

        pos = _pos.get(sym)
        if pos is not None:
            ent = pos["entry"]
            side = pos["side"]
            if sl_bps > 0:
                adv = ((ent - d_low) if side == "LONG" else (d_high - ent)) / ent * 1e4
                if adv >= sl_bps:
                    log_ftsm_close(pos["id"], ent * (1 - sl_bps / 1e4) if side == "LONG"
                                   else ent * (1 + sl_bps / 1e4), -sl_bps - fee, "sl")
                    log.info(f"[F-TSM] {sym} {side} SL -{sl_bps:.0f}bps (entry'den felaket) -> blok (donene dek)")
                    _blocked[sym] = 1 if side == "LONG" else -1
                    _pos[sym] = None
                    pos = None
            if pos is not None and trail_bps > 0:
                peak = pos.get("peak", ent)
                peak = max(peak, d_high) if side == "LONG" else min(peak, d_low)
                pos["peak"] = peak
                cur = ((d_close - ent) if side == "LONG" else (ent - d_close)) / ent * 1e4
                retr = ((peak - d_close) if side == "LONG" else (d_close - peak)) / ent * 1e4
                if cur > 0 and retr >= trail_bps:
                    pnl = ((d_close - ent) if side == "LONG" else (ent - d_close)) / ent * 1e4
                    log_ftsm_close(pos["id"], d_close, pnl - fee, "trail")
                    log.info(f"[F-TSM] {sym} {side} trailing +{pnl:.0f}bps (tepe-{trail_bps:.0f}) -> blok")
                    _blocked[sym] = 1 if side == "LONG" else -1
                    _pos[sym] = None
                    pos = None

        if pos is None:
            if _blocked.get(sym) == want:
                return  # bu yon bloklu, sinyal donene kadar bekle
            rid = log_ftsm_open(sig, px, mom, sym)
            _pos[sym] = {"id": rid, "side": sig, "entry": px, "peak": px}
            log.info(f"[F-TSM] {sym} {sig} @{px:.4g} mom={mom:+.1f}% (gunluk trend)")
            return

        if pos["side"] != sig:
            ent = pos["entry"]
            cur = ((px - ent) if pos["side"] == "LONG" else (ent - px)) / ent * 1e4
            log_ftsm_close(pos["id"], px, cur - fee, "trend-flip")
            log.info(f"[F-TSM] {sym} {pos['side']} kapandi {cur:+.0f}bps -> {sig} flip")
            rid = log_ftsm_open(sig, px, mom, sym)
            _pos[sym] = {"id": rid, "side": sig, "entry": px, "peak": px}
    except Exception as ex:
        log.warning(f"[F-TSM] {sym} tick: {ex}")


def paper_tick() -> None:
    if not bool(getattr(cfg, "V3_FTSM_PAPER", True)):
        return
    main_sym = str(getattr(cfg, "V3_FTSM_SYMBOL", "ETHUSDT") or "ETHUSDT")
    main_px = float(getattr(state, "mark_price", 0) or getattr(state, "price", 0) or 0)
    for sym in SYMBOLS:
        _restore_pos(sym)
        ref_px = main_px if sym == main_sym else 0.0  # digerleri icin gunluk kapanis kullanilir
        _tick_symbol(sym, ref_px)
