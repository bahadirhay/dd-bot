"""
engine/obi_paper.py — D + ORDER BOOK IMBALANCE (OBI) mikro-yapi SHADOW. GERCEK EMIR YOK.

Soru (kullanici): D tetiklendiginde emir defteri o yonu DESTEKLEMIYORSA islem daha mi kotu?
Yani OBI, D'yi filtrelemek icin degerli bir BAGIMSIZ sinyal mi (fiyat-turevi degil)?

Klines-CVD (taker-delta) daha once test edildi -> tutmadi + zaten fiyata bagimli zayif golge.
GERCEK OBI (bid/ask DERINLIK dengesizligi) tarihsel veriyle test EDILEMEZ (depth snapshot yok) ->
bu shadow CANLI'da her D sinyalinde o anki OBI'yi kaydeder, sonra islem sonucuyla eslestirir.
Birkac hafta sonra: "OBI D-yonune KARSI olan islemler daha mi kotu?" -> filtre degerli mi.

OBI = (mid +-BAND_BPS bandindaki bid_qty - ask_qty) / (toplam).  + = alis baskisi, - = satis baskisi.
Cikis D ile birebir (poc_revert+min_prof / SL / maxhold). Gercek emir YOK; pnl kapanis-bazli
(mutlak dogruluk degil; amac OBI-hizali vs OBI-karsi GRUPLARINI ayni yontemle kiyaslamak).
"""
from __future__ import annotations

import json
import time
import urllib.request

from core.config import cfg
from core.logger import get_logger

log = get_logger("OBIPaper")

SYMBOL = "ETHUSDT"
SL = 300.0
MHOLD = 64
MIN_PROF = 12.0
BAND_BPS = 10.0        # OBI hesap bandi: mid'in +-10 bps'i (yakin derinlik)
DEPTH_LIMIT = 50       # emir defteri seviye sayisi

_pos: dict | None = None
_last_bar = 0
_restored = False


def _bar_id() -> int:
    return int(time.time() // 900)


def _fetch_obi() -> tuple[float, float, float] | None:
    """Canli emir defterinden OBI. (obi, bid_qty, ask_qty) — obi in [-1,+1]."""
    try:
        u = ("https://fapi.binance.com/fapi/v1/depth?symbol=%s&limit=%d"
             % (SYMBOL, DEPTH_LIMIT))
        d = json.loads(urllib.request.urlopen(u, timeout=8).read())
        bids = [(float(p), float(q)) for p, q in d.get("bids", [])]
        asks = [(float(p), float(q)) for p, q in d.get("asks", [])]
        if not bids or not asks:
            return None
        mid = (bids[0][0] + asks[0][0]) / 2.0
        lo = mid * (1 - BAND_BPS / 1e4)
        hi = mid * (1 + BAND_BPS / 1e4)
        bq = sum(q for p, q in bids if p >= lo)
        aq = sum(q for p, q in asks if p <= hi)
        tot = bq + aq
        obi = (bq - aq) / tot if tot > 0 else 0.0
        return round(obi, 4), round(bq, 2), round(aq, 2)
    except Exception as ex:
        log.warning(f"[OBI] depth cek: {ex}")
        return None


def _restore() -> None:
    global _pos, _restored
    if _restored:
        return
    _restored = True
    try:
        from botlog.db import get_open_obi

        row = get_open_obi()
        if row:
            _pos = {"id": row["id"], "side": row["side"], "entry": row["entry"], "held": 0}
            log.info(f"[OBI] acik kayit geri yuklendi: {row['side']} @{row['entry']:.2f}")
    except Exception as ex:
        log.warning(f"[OBI] restore: {ex}")


def paper_tick() -> None:
    """Gercek emir YOK. D sinyalinde OBI snapshot + D-cikisiyla sonuc eslestir."""
    global _pos, _last_bar
    if not bool(getattr(cfg, "V3_OBI_PAPER", True)):
        return
    _restore()
    bar = _bar_id()
    if bar == _last_bar:
        return
    _last_bar = bar

    try:
        from engine.poc_paper import compute_signal, poc_mean_reverted
        from botlog.db import log_obi_open, log_obi_close
    except Exception:
        return

    s = compute_signal()
    if not s.get("ready"):
        return
    px = float(s.get("px") or 0)
    if px <= 0:
        return

    # 1) ACIK kayit -> D cikisiyla yonet (kapanis-bazli; iki grup ayni yontemle olculur)
    if _pos:
        side = _pos["side"]; ent = _pos["entry"]; _pos["held"] += 1
        cur = ((px - ent) if side == "LONG" else (ent - px)) / ent * 1e4
        if cur <= -SL:
            log_obi_close(_pos["id"], px, round(-SL, 1), "sl"); _pos = None; return
        if poc_mean_reverted(side) and cur >= MIN_PROF:
            log_obi_close(_pos["id"], px, round(cur, 1), "poc_revert"); _pos = None; return
        if _pos["held"] >= MHOLD:
            log_obi_close(_pos["id"], px, round(cur, 1), "maxhold"); _pos = None; return
        return

    # 2) FLAT + D sinyali -> OBI snapshot al, kaydet
    side = s.get("signal")
    if not side:
        return
    r = _fetch_obi()
    if r is None:
        return
    obi, bq, aq = r
    # OBI D-yonune hizali mi? SHORT (dev+) icin negatif-OBI (satis) hizali; LONG icin pozitif.
    aligned = (obi < 0) if side == "SHORT" else (obi > 0)
    rid = log_obi_open(side, px, obi, bq, aq, 1 if aligned else 0)
    _pos = {"id": rid, "side": side, "entry": px, "held": 0}
    log.info(f"[OBI] {side} @{px:.2f} obi={obi:+.2f} (bid {bq:.0f}/ask {aq:.0f}) "
             f"{'HIZALI' if aligned else 'KARSI'}")
