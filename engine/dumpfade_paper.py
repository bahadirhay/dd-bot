"""
engine/dumpfade_paper.py — Cross-sectional DUMP-FADE maker SHADOW. GERCEK EMIR YOK.

Bulgu (bu seans, 521-coin/~500g gunluk backtest): likit coin gunluk <=-12.5% dump -> ertesi gun
open'in ~%3 ALTINA buy-limit (LIKIDITE VER, panigi al) -> gun-sonu kapat -> maker-limit +276bps/islem,
p=0.000, TRAIN+OOS pozitif. Taker/market girisi -6bps (OLU) -> illikidite/execution DUVARINI maker deliyor.
Kavramsal: illikit edge = likidite primi; market ODER, limit KAZANIR. Kucuk size ($58) illikit-primi yakalar.

Cross-sectional MR, D'ye (tek-coin 15m) complementary. Bu shadow GUNLUK bir kez, likit coin evrenini
tarar; tamamlanan D+1 gununu retrospektif loglar (dolum: gun-LOW <= limit? cikis: gun-close). Gercek emir
YOK -> canli forward'da gercek limit-dolum + bounce dogrulanacak. KALDIRAC/STOP ayri mesele (worst -38%).
"""
from __future__ import annotations

import json
import time
import urllib.request

from core.config import cfg
from core.logger import get_logger

log = get_logger("DumpFadePaper")

DUMP_PCT = 12.5           # gunluk <=-bu% -> dump
OFFSET_BPS = 300.0        # ertesi gun open'in bu kadar bps ALTINA buy-limit (likidite ver)
MAKER_FEE = 10.0          # giris+cikis maker (~5+5 bps)
MIN_QVOL = 50e6           # likidite filtresi: 24h quote-vol >= $50M
TOP_N = 60               # taranacak en likit coin sayisi
_last_day = 0


def _day_key() -> int:
    return int(time.time() // 86400)


def _get(u: str):
    return json.loads(urllib.request.urlopen(u, timeout=15).read())


def _liquid_universe() -> list[str]:
    """24h quote-vol'e gore en likit TOP_N USDT-perp (MIN_QVOL uzeri)."""
    try:
        t = _get("https://fapi.binance.com/fapi/v1/ticker/24hr")
        rows = [(x["symbol"], float(x.get("quoteVolume", 0) or 0)) for x in t
                if str(x.get("symbol", "")).endswith("USDT")]
        rows = [(s, v) for s, v in rows if v >= MIN_QVOL]
        rows.sort(key=lambda z: -z[1])
        return [s for s, _ in rows[:TOP_N]]
    except Exception as ex:
        log.warning(f"[DUMPFADE] evren: {ex}")
        return []


def _daily(sym: str, limit: int = 4):
    try:
        r = _get(f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1d&limit={limit}")
        # (open_time, o, h, l, c)
        return [(int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4])) for x in r]
    except Exception:
        return None


def paper_tick() -> None:
    """Gunluk bir kez: likit evreni tara, tamamlanan dump-fade (D dump, D+1 fill/exit) logla."""
    global _last_day
    if not bool(getattr(cfg, "V3_DUMPFADE_PAPER", True)):
        return
    dk = _day_key()
    if dk == _last_day:
        return
    _last_day = dk
    try:
        from botlog.db import log_dumpfade, dumpfade_seen
    except Exception:
        return
    universe = _liquid_universe()
    if not universe:
        return
    logged = 0
    for sym in universe:
        try:
            bars = _daily(sym, 4)
            if not bars or len(bars) < 3:
                continue
            # son TAMAMLANMIS gun D+1 = bars[-2] (bars[-1] forming). Dump gunu D = bars[-3].
            D = bars[-3]; D1 = bars[-2]
            dkey = int(D1[0] // 86400000)     # D+1 gun anahtari (fill/exit gunu)
            if dumpfade_seen(sym, dkey):
                continue
            o_d, c_d = D[1], D[4]
            if o_d <= 0:
                continue
            dump = (c_d - o_d) / o_d * 100
            if dump > -DUMP_PCT:
                continue                      # bu coin o gun yeterince dumplemedi
            o1, l1, c1 = D1[1], D1[3], D1[4]
            if o1 <= 0:
                continue
            lim = o1 * (1 - OFFSET_BPS / 1e4)
            filled = l1 <= lim
            pnl = ((c1 - lim) / lim * 1e4 - MAKER_FEE) if filled else 0.0
            log_dumpfade(sym, dkey, dump, o1, lim, l1, c1, filled, pnl)
            logged += 1
            if filled:
                log.info(f"[DUMPFADE] {sym} dump{dump:.0f}% -> limit@{lim:.4g} DOLDU close@{c1:.4g} = {pnl:+.0f}bps")
            else:
                log.info(f"[DUMPFADE] {sym} dump{dump:.0f}% -> limit@{lim:.4g} KACTI (dip {l1:.4g})")
        except Exception as ex:
            log.debug(f"[DUMPFADE] {sym}: {ex}")
    if logged:
        log.info(f"[DUMPFADE] gunluk tarama: {logged} yeni dump-fade olayi loglandi ({len(universe)} coin tarandi)")
