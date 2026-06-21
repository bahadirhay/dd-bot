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
    # MAKRO-YON kapisi (B'nin trend-korumasi, veriyle dogrulandi): guclu yonlu
    # makro harekette o yone fade ACMA. Efficiency-ratio yaramadi; yonsel makro
    # tam da B'yi olduren "guclu trendi fade" islemlerini keser. 25 gun: edge
    # korundu (+1086->+1060) isabet %45->%54; trend penceresi -429->-93.
    macro_bps = float(getattr(cfg, "V3_B_MACRO_BPS", 150) or 0)
    if sig and macro_bps > 0 and len(C) > 96 and C[-97] > 0:
        macro = (C[-1] - C[-97]) / C[-97] * 1e4   # ~24h egim
        if (sig == "SHORT" and macro > macro_bps) or (sig == "LONG" and macro < -macro_bps):
            out["macro_block"] = round(macro, 0)
            sig = None
    # GIRIS-TUTARLILIK kapisi: fiyat-z de yonu teyit etsin. Kompozit (funding agirlikli)
    # "oversold" derken fiyat zaten ortalamasinda ise, gercek-donus cikisi aninda
    # tetiklenir -> 0-1 dk churn. coh: fiyat gercekten sapmis olsun. Backtest +1293->+1330.
    coh = float(getattr(cfg, "V3_B_ENTRY_COH", 0.5) or 0)
    if sig and coh > 0 and px_z is not None:
        if (sig == "LONG" and px_z > -coh) or (sig == "SHORT" and px_z < coh):
            out["coh_block"] = round(px_z, 2)
            sig = None
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


def build_live_decision() -> dict | None:
    """B CANLI karar (V3_STRATEGY_B_ENABLED). Test edilen kurgu: stretch>=T giris,
    SL=60bps, mean-revert cikis (b_mean_reverted). RR>=2 icin tp2=120bps backstop
    (gercek cikis mean-revert). A KAPALI olur. None = B kapali (A devam eder)."""
    if not bool(getattr(cfg, "V3_STRATEGY_B_ENABLED", False)):
        return None
    if breaker_blocked():
        return {"action": "WAIT", "reason": "B devre kesici", "details": {}}
    sig = compute_signal()
    side = sig.get("signal")
    if not side:
        return {"action": "WAIT", "reason": f"B sinyal yok (gerginlik={sig.get('stretch')})",
                "details": {}}
    px = float(getattr(state, "mark_price", 0) or getattr(state, "price", 0) or 0)
    if px <= 0:
        return {"action": "WAIT", "reason": "fiyat yok", "details": {}}
    sl_bps = float(getattr(cfg, "V3_B_HARD_SL_BPS", 60) or 60)
    # SL SABIT 60bps (veriyle: vol-olcekli/dinamik SL DAHA KOTU +534 vs sabit +866).
    # TP1/TP2 ÇOK UZAK (V3_B_TP_FAR_BPS=300) — sadece RR>=2 kapisi + bot-cokme yedegi.
    # Asil cikis DINAMIK: mean'de %50 (close_partial) + peak-trail runner. Uzak TP runner'i
    # KESMEZ (trail cok once cikar); islevsel olarak "TP yok", dinamik trail kontrol eder.
    far = float(getattr(cfg, "V3_B_TP_FAR_BPS", 300) or 300)
    if side == "LONG":
        sl = px * (1 - sl_bps / 1e4); tp1 = px * (1 + far * 0.97 / 1e4); tp2 = px * (1 + far / 1e4)
    else:
        sl = px * (1 + sl_bps / 1e4); tp1 = px * (1 - far * 0.97 / 1e4); tp2 = px * (1 - far / 1e4)
    details = {"direction": side, "price": px, "sl": round(sl, 2), "tp1": round(tp1, 2),
               "tp2": round(tp2, 2), "rr": round(far / sl_bps, 2), "v3_mode": True,
               "v3_scenario": "STRATEGY_B", "v3_strategy": "B",
               "entry_reason": f"STRATEGY_B {side} gerginlik={sig.get('stretch')}"}
    return {"action": side, "reason": f"B {side} gerginlik={sig.get('stretch')}",
            "final_decision": side, "details": details, "direction_scores": {}}


# --- CANLI runner durumu (backtest ile birebir: peak'ten trail bps geri donus) ---
_live_runner: dict | None = None


def runner_active() -> bool:
    return _live_runner is not None


def runner_start(side: str, px: float) -> None:
    global _live_runner
    _live_runner = {"side": side, "peak": px, "bar": _bar_id()}


def runner_clear() -> None:
    global _live_runner
    _live_runner = None


def runner_check(side: str, px: float) -> tuple[bool, str]:
    """Kalan %50 runner: peak'ten V3_B_RUNNER_TRAIL_BPS geri donunce kapat
    (backtest'le birebir). maxhold backstop. Donus: (kapat?, sebep)."""
    global _live_runner
    if _live_runner is None or px <= 0:
        return False, ""
    trail = float(getattr(cfg, "V3_B_RUNNER_TRAIL_BPS", 30) or 30)
    if side == "LONG":
        _live_runner["peak"] = max(_live_runner["peak"], px)
        retr = (_live_runner["peak"] - px) / px * 1e4
    else:
        _live_runner["peak"] = min(_live_runner["peak"], px)
        retr = (px - _live_runner["peak"]) / px * 1e4
    if retr >= trail:
        return True, f"runner-trail ({trail:.0f}bps geri donus)"
    mh = int(getattr(cfg, "V3_B_MAXHOLD_BARS", 16) or 16)
    if (_bar_id() - _live_runner["bar"]) >= mh * 2:
        return True, "runner-maxhold"
    return False, ""


def b_mean_reverted(side: str) -> bool:
    """B cikis: fiyat z-score ortalamaya dondu mu (z isaret degistirdi)."""
    pz_win = int(getattr(cfg, "V3_B_PRICE_Z_WIN", 32) or 32)
    C = _closes(pz_win + 2)
    if len(C) < pz_win:
        return False
    pz = _zscore(C[-pz_win:])
    if pz is None:
        return False
    return (side == "LONG" and pz >= 0) or (side == "SHORT" and pz <= 0)


def paper_tick() -> None:
    """Her karar dongusunde cagrilir. B sinyal verince SANAL pozisyon acar/kapatir
    ve b_paper tablosuna yazar — gercek emir ASLA gondermez. Canli sinyal birikimi
    icin (gercek para riski yok). V3_B_PAPER_LOG ile acilir.
    NOT: B CANLI ise (V3_STRATEGY_B_ENABLED) paper KAPALI (cift-kayit olmasin)."""
    global _paper_pos
    if bool(getattr(cfg, "V3_STRATEGY_B_ENABLED", False)):
        return
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
            side = _paper_pos["side"]; ent = _paper_pos["entry"]
            cur = ((px - ent) if side == "LONG" else (ent - px)) / ent * 1e4
            runner_on = bool(getattr(cfg, "V3_B_RUNNER_ENABLED", True))
            phase = _paper_pos.get("phase", "open")
            if phase == "open":
                ex, reason = should_exit(side, ent, _paper_pos["bar"], px)
                if ex and runner_on and "ortalamaya donus" in reason:
                    # MEAN'e ulasti: yariyi kilitle, kalanini RUNNER'a koy (devam yakala).
                    # Veri: full +866 -> runner +960 (+94bps, 1765->1800 gibi devamlari alir).
                    _paper_pos.update(phase="runner", half=cur, peak=px)
                    log.info(f"[B-PAPER] {side} mean'e ulasti pnl={cur:+.0f} — %50 al, runner basladi")
                elif ex:
                    pnl = cur - fee
                    log_b_paper_close(_paper_pos["id"], px, pnl, reason)
                    on_trade_closed(pnl); _paper_pos = None
                    log.info(f"[B-PAPER] KAPANDI {side} {ent:.2f}->{px:.2f} pnl={pnl:+.0f}bps ({reason})")
            else:  # runner: peak'ten trail kadar geri donunce / SL / 2x maxhold -> kapat
                trail = float(getattr(cfg, "V3_B_RUNNER_TRAIL_BPS", 30) or 30)
                sl = float(getattr(cfg, "V3_B_HARD_SL_BPS", 60) or 60)
                mh = int(getattr(cfg, "V3_B_MAXHOLD_BARS", 16) or 16)
                if side == "LONG":
                    _paper_pos["peak"] = max(_paper_pos["peak"], px); retr = (_paper_pos["peak"] - px) / ent * 1e4
                else:
                    _paper_pos["peak"] = min(_paper_pos["peak"], px); retr = (px - _paper_pos["peak"]) / ent * 1e4
                adverse = ((px - ent) if side == "SHORT" else (ent - px)) / ent * 1e4
                if retr >= trail or adverse >= sl or (_bar_id() - _paper_pos["bar"]) >= mh * 2:
                    blended = 0.5 * _paper_pos["half"] + 0.5 * cur - fee
                    log_b_paper_close(_paper_pos["id"], px, blended, "runner-trail")
                    on_trade_closed(blended); _paper_pos = None
                    log.info(f"[B-PAPER] RUNNER kapandi {side} blended={blended:+.0f}bps")
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
