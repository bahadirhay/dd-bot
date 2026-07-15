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
DUMP_CAP = 30.0           # <=-bu% ise ATLA (rug/delist, fade degil — ilk canli tarama LAB-78%% yakaladi)
OFFSET_BPS = 300.0        # ertesi gun open'in bu kadar bps ALTINA buy-limit (likidite ver)
MAKER_FEE = 10.0          # giris+cikis maker (~5+5 bps)
MIN_QVOL = 25e6          # KALICI likidite: 30-gun ORT quote-vol >= $25M. 5M COK DUSUKTU -> canli forward'da
                          # small-cap gurultusu + IDEALIZE-fill sahte-kazanci sokuyordu (EVAA -26%%->+2943 SAHTE,
                          # XPIN/CLO gercek falling-knife -456). 25M: illikit-fake elenir, dolum GERCEKCI,
                          # hala illikidite-primi var (BTC/ETH degil, orta-likit altlar). Tier-test 25-50M %55-63 win.
MIN_DAYS = 60            # yeni-listing haric (>=60 gunluk bar)
CAND_N = 300            # aday listesi genis (mid-likit coinler top-150'nin altinda kalabiliyor)
_last_day = 0


def _day_key() -> int:
    return int(time.time() // 86400)


def _get(u: str):
    return json.loads(urllib.request.urlopen(u, timeout=15).read())


def _candidates() -> list[str]:
    """24h-vol'e gore GENIS aday listesi (top ~150). Asil KALICI-likidite filtresi paper_tick'te
    (30-gun ort hacim + listing-yasi) — 24h-snapshot yeni-coin patlamasini sokuyordu."""
    try:
        t = _get("https://fapi.binance.com/fapi/v1/ticker/24hr")
        rows = [(x["symbol"], float(x.get("quoteVolume", 0) or 0)) for x in t
                if str(x.get("symbol", "")).endswith("USDT")]
        rows.sort(key=lambda z: -z[1])
        return [s for s, _ in rows[:CAND_N]]
    except Exception as ex:
        log.warning(f"[DUMPFADE] evren: {ex}")
        return []


def _daily(sym: str, limit: int = 66):
    try:
        r = _get(f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1d&limit={limit}")
        # (open_time, o, h, l, c, quote_vol[idx7])
        return [(int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[7])) for x in r]
    except Exception:
        return None


def paper_tick() -> None:
    """Gunluk bir kez tarama -> AYRI THREAD'de (senkron urllib ~150 coin event-loop'u BLOKLAMASIN;
    aksi halde 00:00 UTC'de feed donar -> D pozisyonu stale_data ile kapanirdi). Gercek emir YOK."""
    global _last_day
    if not bool(getattr(cfg, "V3_DUMPFADE_PAPER", True)):
        return
    dk = _day_key()
    if dk == _last_day:
        return
    _last_day = dk
    import threading
    threading.Thread(target=_run_scan, name="dumpfade-scan", daemon=True).start()


def _run_scan() -> None:
    """~150-240 coini tara (senkron urllib); AYRI thread'de calisir -> event-loop bloklanmaz."""
    try:
        from botlog.db import log_dumpfade, dumpfade_seen
    except Exception:
        return
    universe = _candidates()
    if not universe:
        return
    logged = 0; scanned = 0
    for sym in universe:
        try:
            bars = _daily(sym, 66)
            if not bars or len(bars) < MIN_DAYS:
                continue                      # yeni-listing -> atla
            vols = [b[5] for b in bars[-31:-1] if b[5] > 0]   # son ~30 tam gun ort hacim
            if not vols or (sum(vols) / len(vols)) < MIN_QVOL:
                continue                      # KALICI-likit degil -> atla
            scanned += 1
            # son TAMAMLANMIS gun D+1 = bars[-2] (bars[-1] forming). Dump gunu D = bars[-3].
            D = bars[-3]; D1 = bars[-2]
            dkey = int(D1[0] // 86400000)     # D+1 gun anahtari (fill/exit gunu)
            if dumpfade_seen(sym, dkey):
                continue
            o_d, c_d = D[1], D[4]
            if o_d <= 0:
                continue
            dump = (c_d - o_d) / o_d * 100
            if dump > -DUMP_PCT or dump <= -DUMP_CAP:
                continue                      # yeterince dumplemedi VEYA rug/delist bolgesi (<=-30%)
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
    if logged or scanned:
        log.info(f"[DUMPFADE] gunluk tarama: {logged} yeni olay | {scanned} kalici-likit coin (>= {MIN_DAYS}g, 30g-ort >{MIN_QVOL/1e6:.0f}M)")
