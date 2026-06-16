"""
engine/strategy_b_v3.py — Strateji B: Funding + fiyat MEAN-REVERSION (S/R'siz, indikatorsuz).

Veri kanıtı (25 gun, 2237x15m bar, walk-forward 7/7 pozitif):
  - Tum tahmin edici ozellikler NEGATIF IC -> piyasa mean-reverting (uc-fade).
  - En guclu+stabil sinyal: funding (IC -0.084), sonra fiyat z-score, 4h momentum.
  - Order-flow (CVD/taker) IC ~0 -> KULLANILMAZ. Indikator YOK.

Sinyal: gerginlik = ort[ z(funding,96) , z(mom4h,96) , z(fiyat,32) ].
  gerginlik >= +T -> SHORT ;  <= -T -> LONG   (uc-fade)
Cikis: fiyat z-score ortalamaya doner (isaret 0'i gecer) | maxhold | hard-SL.
Risk: ardisik-kayip devre kesici (N kayip -> M bar dur). Sinirsiz trend riski
      hard-SL ile kapali. Boyut: kucuk sabit (DD sermayenin kucuk %'i).

NOT: Bu modul KARAR URETIR; executor'a baglanmasi ayri adim. V3_STRATEGY_B_ENABLED
varsayilan FALSE — once paper/kucuk-boyut dogrulamasi.
"""
from __future__ import annotations

import time
from statistics import mean, pstdev

from core.config import cfg
from core.logger import get_logger
from core.state import state

log = get_logger("StrategyB")

# Rolling funding gecmisi (15m bar basina bir ornek)
_funding_hist: list[float] = []
_last_bar_id: int = 0
_hist_bootstrapped = False

# Devre kesici + aktif pozisyon takibi
_consec_losses = 0
_cooldown_until_bar = 0


def _bar_id() -> int:
    return int(time.time() // 900)


def _zscore(series: list[float], cur: float | None = None) -> float | None:
    """z-score: (cur - mean) / pstdev. cur None ise serinin son elemani."""
    vals = [v for v in series if v is not None]
    if len(vals) < max(8, len(series) // 2):
        return None
    c = vals[-1] if cur is None else cur
    if c is None:
        return None
    mu = mean(vals)
    sd = pstdev(vals)
    return (c - mu) / sd if sd > 0 else None


def _bootstrap_funding() -> None:
    """Ilk calismada funding gecmisini DB'den doldur (96 x 15m ~ 24h hazirlik)."""
    global _hist_bootstrapped, _funding_hist
    if _hist_bootstrapped:
        return
    _hist_bootstrapped = True
    try:
        import json
        import sqlite3

        win = int(getattr(cfg, "V3_B_FUNDING_WINDOW", 96) or 96)
        c = sqlite3.connect("file:%s?mode=ro" % cfg.DB_PATH, uri=True)
        rows = c.execute(
            "SELECT ts, payload_json FROM market_snapshots "
            "WHERE payload_json LIKE '%funding_rate%' ORDER BY ts DESC LIMIT ?",
            (win * 60,),
        ).fetchall()
        c.close()
        by_bar: dict[int, float] = {}
        for ts, pj in rows:
            try:
                fr = json.loads(pj).get("funding_rate")
            except Exception:
                fr = None
            if fr is None:
                continue
            b = int(ts // 900)
            if b not in by_bar:  # bar basina ilk (en yeni) deger
                by_bar[b] = float(fr)
        _funding_hist = [by_bar[k] for k in sorted(by_bar)][-win:]
        log.info(f"[B] funding gecmisi backfill: {len(_funding_hist)} bar")
    except Exception as ex:
        log.warning(f"[B] funding backfill: {ex}")


def _push_funding() -> None:
    """Yeni 15m bar'da guncel funding'i gecmise ekle (bar basina bir kez)."""
    global _last_bar_id
    bid = _bar_id()
    if bid == _last_bar_id:
        return
    _last_bar_id = bid
    fr = float(getattr(state, "funding_rate", 0.0) or 0.0)
    _funding_hist.append(fr)
    win = int(getattr(cfg, "V3_B_FUNDING_WINDOW", 96) or 96)
    if len(_funding_hist) > win + 5:
        del _funding_hist[: len(_funding_hist) - (win + 5)]


def _closes(limit: int) -> list[float]:
    try:
        from engine.v3_common import bars_15m

        bars = bars_15m(limit + 4)
        return [float(b.get("close", 0) or 0) for b in bars if float(b.get("close", 0) or 0) > 0]
    except Exception:
        return []


def compute_signal() -> dict:
    """Donus: {ready, stretch, signal, px_z, parts}. signal: LONG/SHORT/None."""
    _bootstrap_funding()
    _push_funding()
    out = {"ready": False, "stretch": None, "signal": None, "px_z": None, "parts": {}}

    fwin = int(getattr(cfg, "V3_B_FUNDING_WINDOW", 96) or 96)
    pz_win = int(getattr(cfg, "V3_B_PRICE_Z_WIN", 32) or 32)
    mom_n = int(getattr(cfg, "V3_B_MOM_BARS", 16) or 16)  # 4h
    C = _closes(fwin + mom_n + 5)
    if len(C) < max(pz_win, mom_n) + 2:
        return out

    # z(fiyat, 32)
    px_z = _zscore(C[-pz_win:])
    # z(mom4h, 96): mom4h serisi
    mom_series = [
        (C[i] - C[i - mom_n]) / C[i - mom_n]
        for i in range(mom_n, len(C))
        if C[i - mom_n] > 0
    ]
    mom_z = _zscore(mom_series[-fwin:]) if len(mom_series) >= 8 else None
    # z(funding, 96)
    fnd_z = _zscore(_funding_hist[-fwin:]) if len(_funding_hist) >= 8 else None

    parts = {k: v for k, v in (("funding", fnd_z), ("mom4h", mom_z), ("price", px_z))
             if v is not None}
    if not parts:
        return out
    stretch = sum(parts.values()) / len(parts)

    T = float(getattr(cfg, "V3_B_STRETCH_T", 1.2) or 1.2)
    sig = "SHORT" if stretch >= T else ("LONG" if stretch <= -T else None)
    out.update({"ready": True, "stretch": round(stretch, 3), "signal": sig,
                "px_z": round(px_z, 3) if px_z is not None else None,
                "parts": {k: round(v, 2) for k, v in parts.items()}})
    return out


def breaker_blocked() -> bool:
    """Ardisik-kayip devre kesici aktif mi (yeni giris yok)."""
    return _bar_id() < _cooldown_until_bar


def on_trade_closed(pnl: float) -> None:
    """B pozisyonu kapaninca devre kesiciyi guncelle."""
    global _consec_losses, _cooldown_until_bar
    n = int(getattr(cfg, "V3_B_BREAKER_LOSSES", 3) or 3)
    cool = int(getattr(cfg, "V3_B_BREAKER_COOL_BARS", 8) or 8)
    if pnl < 0:
        _consec_losses += 1
    else:
        _consec_losses = 0
    if n > 0 and _consec_losses >= n:
        _cooldown_until_bar = _bar_id() + cool
        _consec_losses = 0
        log.info(f"[B] devre kesici: {n} ardisik kayip -> {cool} bar dur")


def should_exit(side: str, entry_px: float, entry_bar: int, mark: float) -> tuple[bool, str]:
    """Cikis kurali: mean-revert (z->0) | maxhold | hard-SL. (TP yok, ortalamaya donus.)"""
    side = (side or "").upper()
    if entry_px <= 0 or mark <= 0 or side not in ("LONG", "SHORT"):
        return False, ""
    sl = float(getattr(cfg, "V3_B_HARD_SL_BPS", 60) or 60)
    adverse = (mark - entry_px) / entry_px * 1e4 if side == "SHORT" else (entry_px - mark) / entry_px * 1e4
    if adverse >= sl:
        return True, f"hard-SL {sl:.0f}bps"
    maxhold = int(getattr(cfg, "V3_B_MAXHOLD_BARS", 16) or 16)
    if entry_bar and (_bar_id() - entry_bar) >= maxhold:
        return True, f"maxhold {maxhold} bar"
    # ortalamaya donus: fiyat z-score isaret degistirdi (uc kapandi)
    pz_win = int(getattr(cfg, "V3_B_PRICE_Z_WIN", 32) or 32)
    C = _closes(pz_win + 2)
    if len(C) >= pz_win:
        pz = _zscore(C[-pz_win:])
        if pz is not None and ((side == "SHORT" and pz <= 0) or (side == "LONG" and pz >= 0)):
            return True, "ortalamaya donus (z=0)"
    return False, ""


# --- PAPER (shadow) modu: gercek emir YOK, sinyal+sanal PnL DB'ye yazilir ---
_paper_pos: dict | None = None


def paper_tick() -> None:
    """Her karar dongusunde cagrilir. B sinyal verince SANAL pozisyon acar/kapatir
    ve b_paper tablosuna yazar — gercek emir ASLA gondermez. Canli sinyal birikimi
    icin (gercek para riski yok). V3_B_PAPER_LOG ile acilir."""
    global _paper_pos
    if not bool(getattr(cfg, "V3_B_PAPER_LOG", True)):
        return
    px = float(getattr(state, "mark_price", 0) or getattr(state, "price", 0) or 0)
    if px <= 0:
        return
    try:
        from botlog.db import log_b_paper_close, log_b_paper_open

        fee = 3.0
        if _paper_pos is None:
            if breaker_blocked():
                return
            sig = compute_signal()
            if not sig.get("ready") or not sig.get("signal"):
                return
            rid = log_b_paper_open(sig["signal"], px, sig["stretch"])
            _paper_pos = {"id": rid, "side": sig["signal"], "entry": px, "bar": _bar_id()}
            log.info(f"[B-PAPER] {sig['signal']} @ {px:.2f} gerginlik={sig['stretch']} "
                     f"parts={sig.get('parts')}")
        else:
            ex, reason = should_exit(_paper_pos["side"], _paper_pos["entry"],
                                     _paper_pos["bar"], px)
            if ex:
                side = _paper_pos["side"]; ent = _paper_pos["entry"]
                pnl = ((px - ent) if side == "LONG" else (ent - px)) / ent * 1e4 - fee
                log_b_paper_close(_paper_pos["id"], px, pnl, reason)
                on_trade_closed(pnl)
                log.info(f"[B-PAPER] KAPANDI {side} {ent:.2f}->{px:.2f} "
                         f"pnl={pnl:+.0f}bps ({reason})")
                _paper_pos = None
    except Exception as ex:
        log.warning(f"[B-PAPER] tick: {ex}")


def evaluate() -> dict:
    """Ust seviye: B aktif mi + devre kesici + sinyal. executor entegrasyonu icin."""
    if not bool(getattr(cfg, "V3_STRATEGY_B_ENABLED", False)):
        return {"enabled": False, "signal": None}
    if breaker_blocked():
        return {"enabled": True, "signal": None, "blocked": "devre kesici"}
    sig = compute_signal()
    sig["enabled"] = True
    return sig
