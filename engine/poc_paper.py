"""
engine/poc_paper.py — Strateji D: Hacim Profili / POC reversion paper (shadow). GERCEK EMIR YOK.

6GB 8-yaklasim testinin KAZANANI (scripts/_eight_approaches.py): net +1935, OOS +1192,
3/4 ceyrek — funding-sentiment'i (+989) ve diger 6 yaklasimi gecti. Saglam olan her sey
mean-reversion; POC = hacim-agirlikli ortalamaya donus (basit fiyat-z'den daha iyi cipa).

Sinyal: POC = son M(40) 15m barin hacim-agirlikli ort fiyati.
  sapma = (fiyat - POC)/POC ;  <= -DEV_BPS -> LONG ; >= +DEV_BPS -> SHORT.
Cikis: GERCEK-donus — fiyat POC'a doner (sapma 0'i gecer) VE pozisyon kar>=esik -> %100 kapat.
  Sahte donus (kar yok) -> bekle. Hard-SL 60bps | maxhold. Runner YOK.
Paper-only; gercek emir YOK. B canli yaninda golge kosar.
"""
from __future__ import annotations

import time
from statistics import mean

from core.config import cfg
from core.logger import get_logger
from core.state import state

log = get_logger("POCPaper")

_pos: dict | None = None
_last_bar = 0
_last_d_entry_bar = 0  # 15m bar-kapanis canli giris kapisi (intrabar tekrar girisi onler)


def _bars(limit):
    """Son N 15m bar (close + volume)."""
    try:
        from engine.v3_common import bars_15m

        b = bars_15m(limit + 4)
        out = []
        for x in b:
            cl = float(x.get("close", 0) or 0)
            if cl <= 0:
                continue
            vol = float(x.get("volume", 0) or 0)
            out.append((cl, vol))
        return out
    except Exception:
        return []


def _bar_id() -> int:
    return int(time.time() // 900)


def _poc(rows) -> float:
    """Hacim-agirlikli ortalama fiyat. Hacim yoksa esit-agirlik (basit ort)."""
    num = den = 0.0
    for cl, vol in rows:
        w = vol if vol > 0 else 1.0
        num += cl * w; den += w
    if den <= 0:
        return mean([c for c, _ in rows]) if rows else 0.0
    return num / den


def compute_signal() -> dict:
    """Donus: {ready, dev, poc, px, signal}. signal: LONG/SHORT/None."""
    out = {"ready": False, "dev": None, "poc": None, "px": None, "signal": None}
    px = float(getattr(state, "mark_price", 0) or getattr(state, "price", 0) or 0)
    if px <= 0:
        return out
    M = int(getattr(cfg, "V3_POC_M", 40) or 40)
    rows = _bars(M + 110)
    if len(rows) < M + 1:
        return out
    poc = _poc(rows[-M:])
    if poc <= 0:
        return out
    dev = (px - poc) / poc * 1e4
    dev_t = float(getattr(cfg, "V3_POC_DEV_BPS", 50) or 50)
    sig = "LONG" if dev <= -dev_t else ("SHORT" if dev >= dev_t else None)
    # makro-yon kapisi (opsiyonel; varsayilan KAPALI, backtest +1935 makrosuz)
    mac = float(getattr(cfg, "V3_POC_MACRO_BPS", 0) or 0)
    if sig and mac > 0 and len(rows) > 96 and rows[-97][0] > 0:
        mc = (rows[-1][0] - rows[-97][0]) / rows[-97][0] * 1e4
        if (sig == "SHORT" and mc > mac) or (sig == "LONG" and mc < -mac):
            sig = None
    out.update({"ready": True, "dev": round(dev, 1), "poc": round(poc, 2), "px": px, "signal": sig})
    return out


def poc_mean_reverted(side: str) -> bool:
    """POC'a donus: LONG (POC altinda acildi) -> dev>=0 ; SHORT -> dev<=0."""
    s = compute_signal()
    if not s.get("ready") or s.get("dev") is None:
        return False
    dev = s["dev"]
    return (side == "LONG" and dev >= 0) or (side == "SHORT" and dev <= 0)


def build_live_decision() -> dict | None:
    """D CANLI karar (V3_STRATEGY_D_ENABLED). POC sapmasi giris, SL=60bps, far-TP
    (gercek cikis POC-donus + kar>=esik, trader'da). None = D kapali."""
    if not bool(getattr(cfg, "V3_STRATEGY_D_ENABLED", False)):
        return None
    s = compute_signal()
    side = s.get("signal")
    if not side:
        return {"action": "WAIT", "reason": f"D sinyal yok (dev={s.get('dev')})", "details": {}}
    # 15m BAR-KAPANIS giris kapisi (slippage fix): yeni pozisyon 15m bar basina en fazla 1
    # kez (intrabar YOK). DEV85+15m-kapanis = +1854 (slippage-saglam); 1m intrabar negatif.
    global _last_d_entry_bar
    if bool(getattr(cfg, "V3_B_BARCLOSE_ENTRY", True)):
        cur_bar = _bar_id()
        if cur_bar == _last_d_entry_bar:
            return {"action": "WAIT", "reason": "D 15m bar-kapanis bekle (intrabar giris yok)",
                    "details": {}}
        _last_d_entry_bar = cur_bar
    px = s["px"]
    sl_bps = float(getattr(cfg, "V3_POC_SL_BPS", 60) or 60)
    far = float(getattr(cfg, "V3_POC_TP_FAR_BPS", 300) or 300)
    if side == "LONG":
        sl = px * (1 - sl_bps / 1e4); tp1 = px * (1 + far * 0.97 / 1e4); tp2 = px * (1 + far / 1e4)
    else:
        sl = px * (1 + sl_bps / 1e4); tp1 = px * (1 - far * 0.97 / 1e4); tp2 = px * (1 - far / 1e4)
    details = {"direction": side, "price": px, "sl": round(sl, 2), "tp1": round(tp1, 2),
               "tp2": round(tp2, 2), "rr": round(far / sl_bps, 2), "v3_mode": True,
               "v3_scenario": "STRATEGY_D", "v3_strategy": "D",
               "entry_reason": f"STRATEGY_D {side} dev={s.get('dev')}"}
    return {"action": side, "reason": f"D {side} dev={s.get('dev')}",
            "final_decision": side, "details": details, "direction_scores": {}}


def paper_tick() -> None:
    global _pos, _last_bar
    if not bool(getattr(cfg, "V3_POC_PAPER", True)):
        return
    if bool(getattr(cfg, "V3_STRATEGY_D_ENABLED", False)):
        return  # D CANLI: gercek emir uretiliyor, paper-shadow kapali
    px = float(getattr(state, "mark_price", 0) or getattr(state, "price", 0) or 0)
    if px <= 0:
        return
    M = int(getattr(cfg, "V3_POC_M", 40) or 40)
    rows = _bars(M + 100)
    if len(rows) < M + 1:
        return
    poc = _poc(rows[-M:])
    if poc <= 0:
        return
    dev = (px - poc) / poc * 1e4
    fee = 3.0
    try:
        from botlog.db import log_poc_close, log_poc_open

        if _pos is not None:
            side = _pos["side"]; ent = _pos["entry"]
            cur = ((px - ent) if side == "LONG" else (ent - px)) / ent * 1e4
            sl = float(getattr(cfg, "V3_POC_SL_BPS", 60) or 60)
            mh = int(getattr(cfg, "V3_POC_MAXHOLD", 16) or 16)
            adverse = ((px - ent) if side == "SHORT" else (ent - px)) / ent * 1e4
            min_prof = float(getattr(cfg, "V3_B_REVERT_MIN_PROFIT_BPS", 0.0) or 0.0)
            # POC'a donus: LONG (POC altinda acildi) -> dev>=0 ; SHORT -> dev<=0
            reverted = (side == "LONG" and dev >= 0) or (side == "SHORT" and dev <= 0)
            if adverse >= sl:
                log_poc_close(_pos["id"], px, cur - fee, "hard-SL"); _pos = None
            elif (_bar_id() - _pos["bar"]) >= mh:
                log_poc_close(_pos["id"], px, cur - fee, "maxhold"); _pos = None
            elif reverted and cur >= min_prof:
                log_poc_close(_pos["id"], px, cur - fee, "gercek-poc-donus"); _pos = None
            return
        # flat: sinyal?
        dev_t = float(getattr(cfg, "V3_POC_DEV_BPS", 50) or 50)
        sig = "LONG" if dev <= -dev_t else ("SHORT" if dev >= dev_t else None)
        if not sig:
            return
        # makro-yon kapisi (opsiyonel; backtest +1935 makrosuz, varsayilan KAPALI)
        mac = float(getattr(cfg, "V3_POC_MACRO_BPS", 0) or 0)
        if mac > 0 and len(rows) > 96 and rows[-97][0] > 0:
            mc = (rows[-1][0] - rows[-97][0]) / rows[-97][0] * 1e4
            if (sig == "SHORT" and mc > mac) or (sig == "LONG" and mc < -mac):
                return
        bid = _bar_id()
        if bid == _last_bar:
            return
        _last_bar = bid
        rid = log_poc_open(sig, px, dev)
        _pos = {"id": rid, "side": sig, "entry": px, "bar": bid}
        log.info(f"[POC-PAPER] {sig} @{px:.1f} dev={dev:+.0f}bps (POC={poc:.1f})")
    except Exception as ex:
        log.warning(f"[POC-PAPER] tick: {ex}")
