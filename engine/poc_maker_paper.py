"""
engine/poc_maker_paper.py — D MAKER-LIMIT giris SHADOW. GERCEK EMIR YOK.

Bulgu (maker-execution-jul2026): D'nin ince edge'ini taker(market) maliyeti yiyor; maker-limit
giris 7/8 likit coinde OOS dahil market'i gecti. Ama dolum modeli backtest'te IDEALIZE'di.
Bu shadow CANLI forward'da olcer: D sinyalinde kapanisin 5bps altina/ustune LIMIT koy ->
sonraki barlarda dolar(FILLED, o fiyattan giris) ya da kacar(MISSED). Amac: gercek fill-rate +
maker-shadow (poc_maker_paper) vs canli-D (trades, taker) forward kiyasi.

Durum makinesi: PENDING(limit kondu) -> FILLED(deger)/MISSED(pencere doldu) -> CLOSED(cikis).
"""
from __future__ import annotations

import json
import time
import urllib.request

from core.config import cfg
from core.logger import get_logger

log = get_logger("PocMakerPaper")

SYMBOL = "ETHUSDT"          # canli D ile A/B icin ETH
M = 40
DEV = 85.0
SL = 300.0                 # canli D ile hizali (ATR-SL ~300)
MHOLD = 64
OFFSET = 5.0               # limit, kapanistan bu kadar bps uzakta (LONG alt / SHORT ust)
FILL_WINDOW = 3            # bar; bu kadar barda dolmazsa MISS
MAKER_COST = 8.0          # maker giris + taker cikis + slip
TAKER_COST = 12.0        # market-giris A/B (taker round-trip) — maker vs market farkini olcer

_pending: dict | None = None   # {id,side,sig,lim,waited}
_pos: dict | None = None       # {id,side,fill,held}
_last_bar = 0
_restored = False
_cache: tuple = (0, [])


def _bar_id() -> int:
    return int(time.time() // 900)


def _compute_signal() -> dict:
    """Canli D'nin sinyali (ER-kapisi + DEV esigi dahil) — birebir sadakat icin."""
    from engine.poc_paper import compute_signal
    return compute_signal()


def _poc_reverted(side: str) -> bool:
    """Canli D'nin POC-donus cikisi."""
    from engine.poc_paper import poc_mean_reverted
    return poc_mean_reverted(side)


def _klines(limit: int = 130) -> list | None:
    global _cache
    day = _bar_id()
    if _cache[0] == day and _cache[1]:
        return _cache[1]
    try:
        u = ("https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=15m&limit=%d" % (SYMBOL, limit))
        r = json.loads(urllib.request.urlopen(u, timeout=12).read())
        bars = [(float(x[2]), float(x[3]), float(x[4]), float(x[5])) for x in r]  # h,l,c,vol
        closed = bars[:-1]
        _cache = (day, closed)
        return closed
    except Exception as ex:
        log.warning(f"[MAKER] klines: {ex}")
        return _cache[1] or None


def _poc(bars: list, i: int) -> float | None:
    nu = de = 0.0
    for j in range(i - M, i):
        v = bars[j][3] if bars[j][3] > 0 else 1.0
        nu += bars[j][2] * v
        de += v
    return nu / de if de > 0 else None


def _restore() -> None:
    global _pending, _pos, _restored
    if _restored:
        return
    _restored = True
    try:
        from botlog.db import get_open_maker
        row = get_open_maker(SYMBOL)
        if not row:
            return
        if row["status"] == "PENDING":
            _pending = {"id": row["id"], "side": row["side"], "sig": row["sig"], "lim": row["lim"], "waited": 0}
        elif row["status"] == "FILLED":
            _pos = {"id": row["id"], "side": row["side"], "fill": row["fill"] or row["lim"], "sig": row["sig"] or row["fill"] or row["lim"], "held": 0}
        log.info(f"[MAKER] restore: {row['status']} {row['side']} (id={row['id']})")
    except Exception as ex:
        log.warning(f"[MAKER] restore: {ex}")


def paper_tick() -> None:
    global _pending, _pos, _last_bar
    if not bool(getattr(cfg, "V3_POC_MAKER_PAPER", True)):
        return
    _restore()
    bar = _bar_id()
    if bar == _last_bar:
        return
    _last_bar = bar
    from botlog.db import log_maker_open, update_maker_status

    bars = _klines()
    if not bars or len(bars) < M + 2:
        return
    i = len(bars) - 1
    h, l, c = bars[i][0], bars[i][1], bars[i][2]
    pc = _poc(bars, i)
    if not pc or c <= 0:
        return
    dev = (c - pc) / pc * 1e4

    # 1) ACIK pozisyon -> cikis yonet. A/B: market-giris(sig_px, taker) counterfactual da loglanir.
    if _pos:
        side = _pos["side"]; fill = _pos["fill"]; sig = _pos.get("sig") or fill; _pos["held"] += 1
        cur = ((c - fill) if side == "LONG" else (fill - c)) / fill * 1e4
        adv = ((fill - l) if side == "LONG" else (h - fill)) / fill * 1e4
        # market-giris (sig_px) ayni cikisa: (poc_revert/maxhold -> c ; sl -> tam-SL) - taker
        mkt_cur = ((c - sig) if side == "LONG" else (sig - c)) / sig * 1e4
        if adv >= SL:
            update_maker_status(_pos["id"], "CLOSED", exit_px=fill * (1 - SL / 1e4) if side == "LONG" else fill * (1 + SL / 1e4),
                                pnl_bps=round(-SL - MAKER_COST, 1), reason="sl", market_bps=round(-SL - TAKER_COST, 1))
            log.info(f"[MAKER] SL {side} -{SL:.0f}bps")
            _pos = None
        elif _poc_reverted(side) and cur >= 0:
            update_maker_status(_pos["id"], "CLOSED", exit_px=c, pnl_bps=round(cur - MAKER_COST, 1), reason="poc_revert",
                                market_bps=round(mkt_cur - TAKER_COST, 1))
            log.info(f"[MAKER] POC-donus {side} maker +{cur:.0f} vs market +{mkt_cur:.0f}bps")
            _pos = None
        elif _pos["held"] >= MHOLD:
            update_maker_status(_pos["id"], "CLOSED", exit_px=c, pnl_bps=round(cur - MAKER_COST, 1), reason="maxhold",
                                market_bps=round(mkt_cur - TAKER_COST, 1))
            _pos = None
        return

    # 2) BEKLEYEN limit -> bu barda doldu mu / kacti mi
    if _pending:
        side = _pending["side"]; lim = _pending["lim"]; _pending["waited"] += 1
        filled = (l <= lim) if side == "LONG" else (h >= lim)
        if filled:
            update_maker_status(_pending["id"], "FILLED", fill_px=lim)
            _pos = {"id": _pending["id"], "side": side, "fill": lim, "sig": _pending.get("sig") or lim, "held": 0}
            log.info(f"[MAKER] FILLED {side} @{lim:.2f} (limit doldu)")
            _pending = None
        elif _pending["waited"] >= FILL_WINDOW:
            update_maker_status(_pending["id"], "MISSED")
            log.info(f"[MAKER] MISSED {side} (limit {lim:.2f} {FILL_WINDOW} barda dolmadi)")
            _pending = None
        return

    # 3) FLAT -> sinyal: canli D ile BIREBIR. compute_signal ER-kapisi + dogru DEV esigini
    #    (V3_POC_DEV_BPS) uygular -> maker shadow = canli D SINYALI + maker execution.
    #    Boylece kiyas yalniz EXECUTION farkini olcer (ER-kapisi eksikligi bozmaz).
    s = _compute_signal()
    side = s.get("signal")
    if not side or not s.get("ready"):
        return
    sig_px = float(s.get("px") or c)
    lim = sig_px * (1 - OFFSET / 1e4) if side == "LONG" else sig_px * (1 + OFFSET / 1e4)
    rid = log_maker_open(SYMBOL, side, sig_px, lim)
    _pending = {"id": rid, "side": side, "sig": sig_px, "lim": lim, "waited": 0}
    log.info(f"[MAKER] LIMIT kondu {side} sig={sig_px:.2f} lim={lim:.2f} dev={s.get('dev')} (ER-kapili)")
