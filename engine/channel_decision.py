"""
engine/channel_decision.py — Tek karar otoritesi (kanal + breakout).

Fade: seviye + akis (entry_score). Breakout: yapi + momentum (structure_score).
Mid-range: WAIT. MarketState yalnizca bilgi.
"""
from __future__ import annotations

from statistics import mean
from typing import Any

from core.config import cfg
from core.logger import get_logger
from core.state import state, effective_price

log = get_logger("ChannelDecision")

ENTRY_MODULES = ("zone", "liquidity", "cvd")
STRUCTURE_MODULES = ("structure", "trend", "event", "volume")


def pivot_sr_from_levels(levels: dict, price: float) -> tuple[float, float]:
    """
    Grafik merged pivot S/R — ladder genisletme / histerezis bandi degil.
    Fiyatin altindaki en yakin destek + ustundeki en yakin direnc.
    """
    px = float(price or 0)
    s = float(levels.get("active_support") or 0)
    r = float(levels.get("active_resistance") or 0)
    merged = levels.get("all_levels") or []
    if px <= 0 or not merged:
        return s, r
    sup_below = [
        float(l.get("price", 0) or 0)
        for l in merged
        if str(l.get("kind")) == "support"
        and 0 < float(l.get("price", 0) or 0) < px
    ]
    res_above = [
        float(l.get("price", 0) or 0)
        for l in merged
        if str(l.get("kind")) == "resistance"
        and float(l.get("price", 0) or 0) > px
    ]
    if not sup_below or not res_above:
        return s, r
    ps, pr = max(sup_below), min(res_above)
    min_w = px * float(getattr(cfg, "V3_BAND_MIN_WIDTH_PCT", 0.008) or 0.008)
    if pr > ps and (pr - ps) >= min_w * 0.5:
        return ps, pr
    return s, r


def _sync_channel_zone_to_state(zone: str) -> None:
    vl = getattr(state, "v3_levels", None) or {}
    active = vl.get("active")
    if not isinstance(active, dict):
        return
    active = dict(active)
    active["zone"] = zone
    state.v3_levels = {**vl, "active": active}


def channel_zone(support: float, resistance: float, price: float) -> str:
    """Geriye uyumluluk — arguman sirasi eski API."""
    from engine.v3_common import calculate_channel_zone

    return calculate_channel_zone(price, support, resistance)


def apply_frozen_channel_levels(levels: dict, price: float) -> dict:
    """Breakout icin band kilidi; zone + fade geometrisi Levels trade band otoritesi."""
    from engine.v3_common import calculate_channel_zone, trade_band_sr

    out = dict(levels)
    px = float(price or 0)
    trade_s, trade_r = trade_band_sr(out)
    if trade_s <= 0 or trade_r <= trade_s or px <= 0:
        out["zone"] = "MID_RANGE"
        out["channel_authority"] = True
        return out

    frozen_s = float(getattr(state, "v3_channel_frozen_s", 0) or 0)
    frozen_r = float(getattr(state, "v3_channel_frozen_r", 0) or 0)
    channel_valid = bool(getattr(state, "v3_channel_valid", False))
    band_tol = max(px * 0.001, 2.0)

    if channel_valid and frozen_s > 0 and frozen_r > frozen_s:
        breakout = detect_breakout_5m(frozen_s, frozen_r, px)
        if breakout:
            log.info(
                f"[CHANNEL] kanal invalid — breakout {breakout} "
                f"(S={frozen_s:.2f} R={frozen_r:.2f})"
            )
            state.v3_channel_valid = False
            state.v3_channel_frozen_s = 0.0
            state.v3_channel_frozen_r = 0.0
            channel_valid = False

    if channel_valid and (
        abs(frozen_s - trade_s) > band_tol or abs(frozen_r - trade_r) > band_tol
    ):
        log.info(
            f"[CHANNEL] trade band senkron S {frozen_s:.2f}->{trade_s:.2f} "
            f"R {frozen_r:.2f}->{trade_r:.2f}"
        )
        state.v3_channel_frozen_s = trade_s
        state.v3_channel_frozen_r = trade_r
        frozen_s, frozen_r = trade_s, trade_r

    if not channel_valid:
        state.v3_channel_frozen_s = trade_s
        state.v3_channel_frozen_r = trade_r
        state.v3_channel_valid = True
        frozen_s, frozen_r = trade_s, trade_r
        log.info(
            f"[CHANNEL] yeni band kilitlendi S={frozen_s:.2f} R={frozen_r:.2f}"
        )

    out["active_support"] = trade_s
    out["active_resistance"] = trade_r
    out["channel_frozen_support"] = frozen_s
    out["channel_frozen_resistance"] = frozen_r
    if trade_s < px < trade_r:
        out["range_valid"] = True

    zone = calculate_channel_zone(px, trade_s, trade_r)
    out["zone"] = zone
    out["channel_authority"] = True
    _sync_channel_zone_to_state(zone)
    return out


def channel_reject_code(decision: dict) -> str:
    from engine.reject_reason_v3 import (
        CHANNEL_MID_RANGE,
        CHANNEL_SCORE_LOW,
        CVD_BLOCK,
        NO_ACTIVE_LEVEL,
        RR_TOO_LOW,
    )

    reason = str(decision.get("reason") or "").lower()
    zone = str(decision.get("zone") or "").upper()
    if zone == "MID_RANGE" or "mid_range" in reason or "orta bölge" in reason:
        return CHANNEL_MID_RANGE
    if "rr yetersiz" in reason:
        return RR_TOO_LOW
    if "cvd" in reason:
        return CVD_BLOCK
    if "score" in reason:
        return CHANNEL_SCORE_LOW
    if "kanal kurulamadi" in reason or "destek/direnc yok" in reason:
        return NO_ACTIVE_LEVEL
    return CHANNEL_MID_RANGE if zone == "MID_RANGE" else CHANNEL_SCORE_LOW


def _aggregate_breakout_bars() -> list[dict]:
    from engine.v3_common import aggregate_5m, bars_1m

    tf = int(getattr(cfg, "V3_BREAKOUT_TF_MIN", 5) or 5)
    src = bars_1m(max(120, tf * 20))
    if tf <= 3:
        from engine.v3_common import aggregate_3m

        return aggregate_3m(src)
    return aggregate_5m(src)


def last_closed_tf_bar(bars: list[dict]) -> dict | None:
    if len(bars) < 2:
        return None
    return bars[-2]


def detect_breakout_5m(
    support: float, resistance: float, price: float
) -> str | None:
    """Son kapanan 5m/3m mum + buffer + min mesafe + momentum."""
    from engine.structure_thresholds import (
        break_threshold_price,
        breakout_close_beyond,
    )

    bars = _aggregate_breakout_bars()
    bar = last_closed_tf_bar(bars)
    if not bar:
        return None
    close = float(bar.get("close", 0) or 0)
    if close <= 0:
        return None

    band = max(resistance - support, 0.0)
    min_frac = float(getattr(cfg, "V3_BREAKOUT_MIN_DIST_FRAC", 0.08) or 0.08)
    min_dist = band * min_frac if band > 0 else price * 0.001

    if resistance > 0 and breakout_close_beyond(close, resistance, "LONG", price):
        if close - resistance < min_dist:
            return None
        if not _breakout_momentum_ok("LONG", bar, bars):
            return None
        return "LONG"

    if support > 0 and breakout_close_beyond(close, support, "SHORT", price):
        if support - close < min_dist:
            return None
        if not _breakout_momentum_ok("SHORT", bar, bars):
            return None
        return "SHORT"

    return None


def _breakout_momentum_ok(side: str, bar: dict, bars: list[dict]) -> bool:
    o = float(bar.get("open", 0) or 0)
    c = float(bar.get("close", 0) or 0)
    if o <= 0 or c <= 0:
        return False
    bodies = [
        abs(float(b.get("close", 0) or 0) - float(b.get("open", 0) or 0))
        for b in bars[-6:-1]
        if float(b.get("close", 0) or 0) > 0
    ]
    avg_body = mean(bodies) if bodies else abs(c - o)
    body = abs(c - o)
    if side == "LONG":
        return c > o and body >= avg_body * 0.45
    return c < o and body >= avg_body * 0.45


def cvd_fade_filter(side: str, cvd: dict) -> tuple[bool, str, float]:
    """
    Fade: BULL/NEUTRAL gecer; BEAR ceza; asiri uc veto.
    Donus: (allowed, note, penalty_points)
    """
    side = side.upper()
    br = float(cvd.get("buy_ratio", 0.5) or 0.5)
    direction = str(cvd.get("direction") or "NEUTRAL").upper()
    extreme = float(getattr(cfg, "V3_CVD_EXTREME_VETO_RATIO", 0.35) or 0.35)

    # Akis-teyit kapisi (veri: akisa-ters 93 islem net -4.19; akis-uyumlu ~basabas).
    # Fade icin akis o yonu AKTIF desteklemeli — "asiri ters degil" yetmez.
    confirm = bool(getattr(cfg, "V3_FADE_FLOW_CONFIRM", True))
    conf_ratio = float(getattr(cfg, "V3_FADE_FLOW_RATIO", 0.50) or 0.50)

    if side == "LONG":
        if br < extreme:
            return False, f"CVD asiri satis (buy_ratio={br:.2f})", 0.0
        if confirm and br < conf_ratio:
            return False, f"akis teyit yok: LONG ama buy_ratio={br:.2f}<{conf_ratio:.2f}", 0.0
        if direction == "BEAR":
            return True, "CVD BEAR ceza", 8.0
        return True, "CVD OK", 0.0

    if br > (1.0 - extreme):
        return False, f"CVD asiri alis (buy_ratio={br:.2f})", 0.0
    if confirm and br > (1.0 - conf_ratio):
        return False, f"akis teyit yok: SHORT ama buy_ratio={br:.2f}>{1.0 - conf_ratio:.2f}", 0.0
    if direction == "BULL":
        return True, "CVD BULL ceza", 8.0
    return True, "CVD OK", 0.0


def _trend_continuation(price: float, structure: dict, cvd: dict) -> tuple[str, float, str]:
    """
    Trend-devam girisi (blue-sky/yeni-tepe): fiyat son swing-high'i kirar + yapi
    AYNI yonde (fractal/trend) + OI yukseliyor (yeni para) -> trend yonunde gir,
    ride. Fade DEGIL — trendle AYNI yon (override felaketinin tersi). Donus:
    (side, kirilan_seviye, not).
    Veri (06-15): 1724'te swing-high 1722.8 kirildi + 15m UP + OI -> +%4.96 ride.
    """
    none = ("", 0.0, "")
    if not bool(getattr(cfg, "V3_TREND_CONT_ENABLED", True)) or price <= 0:
        return none
    from engine.v3_common import bars_15m

    n = int(getattr(cfg, "V3_TREND_CONT_SWING_BARS", 12) or 12)
    bars = bars_15m(n + 3)
    if len(bars) < n + 1:
        return none
    win = bars[-(n + 1):-1]  # son n kapali bar
    sh = max(float(b.get("high", 0) or 0) for b in win)
    sl = min(float(b.get("low", 0) or 1e12) for b in win)

    # GUVENILIR kaynak: state.structure_15m ("UP"/"DOWN"). structure dict'inde
    # 'trend'/'fractal' YOK (get_structure_snapshot 1h/alignment/effective_bias
    # donduruyor) — onlari okumak no-op'tu, trend-devam hic tetiklenmiyordu.
    st15 = str(getattr(state, "structure_15m", "") or "").upper()
    up = st15 == "UP"
    dn = st15 == "DOWN"

    ok_oi, _ = oi_breakout_ok("LONG")  # OI dusuyorsa (squeeze) gecmez
    if not ok_oi:
        return none

    if price > sh and up:
        return ("LONG", sh, f"trend-devam: yeni tepe {sh:.1f} kirildi + UP + OI")
    if price < sl and dn:
        return ("SHORT", sl, f"trend-devam: yeni dip {sl:.1f} kirildi + DOWN + OI")
    return none


def _strong_flow_reversal(candidate: str, cvd: dict) -> bool:
    """
    Seviyede GUCLU akis-donusu (absorpsiyon): satis emilip alim devraldi (destekte)
    ya da tersi (direncte). Bu, donusun KANITI -> counter-trend vetolarini (trend
    filtresi, VR-trend, yapi-hizasi) asar. Veri: 1653 dibinde taker 0.70 / cvd
    +9929 ile alim dondu ama struct-align long'u bloklamisti.
    """
    if not bool(getattr(cfg, "V3_REVERSAL_OVERRIDE_ENABLED", True)):
        return False
    br = float(cvd.get("buy_ratio", 0.5) or 0.5)
    cum = float(cvd.get("cumulative", 0) or 0)
    direction = str(cvd.get("direction") or "").upper()
    rr = float(getattr(cfg, "V3_REVERSAL_FLOW_RATIO", 0.60) or 0.60)
    if candidate == "LONG":
        return br >= rr and (cum > 0 or direction == "BULL")
    return br <= (1.0 - rr) and (cum < 0 or direction == "BEAR")


def oi_breakout_ok(side: str) -> tuple[bool, str]:
    """
    OI/squeeze filtresi (saf hesap, indikator yok). Gercek breakout'ta yeni para
    girer -> OI artar. OI belirgin DUSUYORsa hareket pozisyon kapanisindan
    (squeeze/unwind) kaynaklidir -> sahte kirilim. Donus: (allow, note).
    """
    if not bool(getattr(cfg, "V3_OI_BREAKOUT_CONFIRM", True)):
        return True, "oi filtresi kapali"
    hist = list(getattr(state, "oi_history", None) or [])
    n = int(getattr(cfg, "OI_LOOKBACK", 3) or 3)
    if len(hist) < max(2, n):
        return True, "oi veri yetersiz"
    old = float(hist[-n].get("oi", 0) or 0)
    cur = float(hist[-1].get("oi", 0) or 0)
    if old <= 0:
        return True, "oi yok"
    chg = (cur - old) / old * 100.0
    drop = float(getattr(cfg, "V3_OI_SQUEEZE_DROP_PCT", 0.05) or 0.05)
    if chg <= -drop:
        return False, f"OI dusuyor (%{chg:.2f}) — squeeze/unwind, gercek breakout degil"
    return True, f"OI teyit (%{chg:+.2f})"


def cvd_breakout_supports(side: str, cvd: dict) -> tuple[bool, str]:
    """Breakout: CVD destekleyici veya notr; asiri ters veto."""
    side = side.upper()
    br = float(cvd.get("buy_ratio", 0.5) or 0.5)
    extreme = float(getattr(cfg, "V3_CVD_EXTREME_VETO_RATIO", 0.35) or 0.35)
    confirm = bool(getattr(cfg, "V3_FADE_FLOW_CONFIRM", True))
    conf_ratio = float(getattr(cfg, "V3_FADE_FLOW_RATIO", 0.50) or 0.50)
    if side == "LONG" and br < extreme:
        return False, "breakout LONG — CVD asiri satis"
    if side == "SHORT" and br > (1.0 - extreme):
        return False, "breakout SHORT — CVD asiri alis"
    # Breakout da akis teyidi ister: kirilim yonune akis eslik etmeli.
    if confirm and side == "LONG" and br < conf_ratio:
        return False, f"breakout LONG akis teyit yok (buy_ratio={br:.2f})"
    if confirm and side == "SHORT" and br > (1.0 - conf_ratio):
        return False, f"breakout SHORT akis teyit yok (buy_ratio={br:.2f})"
    return True, "CVD destekleyici/notr"


def compute_split_scores(
    *,
    levels: dict,
    structure: dict,
    scenario: dict,
    cvd: dict,
) -> dict[str, Any]:
    from engine.direction_score_v3 import (
        _base_score,
        _module_points,
        _structure_strengths,
        compute_probabilistic_decision,
    )

    ms = levels.get("market_state") or getattr(state, "v3_market_state", None) or {}
    px = float(levels.get("price") or effective_price() or 0)
    ref_s = float(levels.get("active_support") or 0)
    ref_r = float(levels.get("active_resistance") or 0)
    bear, bull = _structure_strengths(ms, structure)
    base = _base_score()

    long_mod = _module_points(
        "LONG",
        levels=levels,
        structure=structure,
        scenario=scenario,
        cvd=cvd,
        ms=ms,
        px=px,
        ref_s=ref_s,
        ref_r=ref_r,
        trend_mode=False,
        bear=bear,
        bull=bull,
    )
    short_mod = _module_points(
        "SHORT",
        levels=levels,
        structure=structure,
        scenario=scenario,
        cvd=cvd,
        ms=ms,
        px=px,
        ref_s=ref_s,
        ref_r=ref_r,
        trend_mode=False,
        bear=bear,
        bull=bull,
    )

    def _sum(keys: tuple[str, ...], mod: dict) -> float:
        return base + sum(float(mod.get(k, 0) or 0) for k in keys)

    entry_long = _sum(ENTRY_MODULES, long_mod)
    entry_short = _sum(ENTRY_MODULES, short_mod)
    struct_long = _sum(STRUCTURE_MODULES, long_mod)
    struct_short = _sum(STRUCTURE_MODULES, short_mod)

    legacy = compute_probabilistic_decision(
        levels=levels,
        structure=structure,
        scenario=scenario,
        cvd=cvd,
        entry=None,
    )
    return {
        **legacy,
        "entry_long_score": round(entry_long, 1),
        "entry_short_score": round(entry_short, 1),
        "structure_long_score": round(struct_long, 1),
        "structure_short_score": round(struct_short, 1),
        "entry_modules_long": {k: long_mod.get(k, 0) for k in ENTRY_MODULES},
        "entry_modules_short": {k: short_mod.get(k, 0) for k in ENTRY_MODULES},
        "structure_modules_long": {k: long_mod.get(k, 0) for k in STRUCTURE_MODULES},
        "structure_modules_short": {k: short_mod.get(k, 0) for k in STRUCTURE_MODULES},
        "structure_info": {
            "bear": round(bear, 2),
            "bull": round(bull, 2),
            "note": "bilgi — fade kararinda kullanilmaz",
        },
    }


def _score_passes(side: str, scores: dict, *, mode: str) -> tuple[bool, str]:
    side = side.upper()
    min_s = float(
        getattr(cfg, "V3_STRUCTURE_SCORE_MIN", 52.0)
        if mode == "breakout"
        else getattr(cfg, "V3_ENTRY_SCORE_MIN", 52.0)
    )
    if side == "LONG":
        val = float(
            scores.get("structure_long_score" if mode == "breakout" else "entry_long_score")
            or 0
        )
        opp = float(
            scores.get("structure_short_score" if mode == "breakout" else "entry_short_score")
            or 0
        )
    else:
        val = float(
            scores.get("structure_short_score" if mode == "breakout" else "entry_short_score")
            or 0
        )
        opp = float(
            scores.get("structure_long_score" if mode == "breakout" else "entry_long_score")
            or 0
        )
    if val < min_s:
        return False, f"{mode}_score {val:.0f} < {min_s:.0f}"
    if val <= opp:
        return False, f"{mode}_score edge yok ({val:.0f} vs {opp:.0f})"
    return True, f"{mode}_score OK ({val:.0f})"


def _build_entry(side: str, levels: dict, price: float, entry_type: str) -> dict:
    from engine.entry_v3 import _build_range_entry

    s = float(levels.get("active_support") or 0)
    r = float(levels.get("active_resistance") or 0)
    direction = "BUY" if side == "LONG" else "SELL"
    return _build_range_entry(
        direction,
        price,
        s,
        r,
        entry_type=entry_type,
        require_min_rr=True,
    )


def _scenario_name(path: str, side: str) -> str:
    if path == "breakout":
        return f"BREAKOUT_{'BUY' if side == 'LONG' else 'SELL'}"
    return f"RANGE_{'BUY' if side == 'LONG' else 'SELL'}"


def _details_from_entry(
    action: str,
    entry: dict,
    levels: dict,
    *,
    path: str,
    reasons: list[str],
    scores: dict,
) -> dict:
    s = float(levels.get("active_support") or 0)
    r = float(levels.get("active_resistance") or 0)
    scn = _scenario_name(path, action)
    return {
        "direction": action,
        "price": float(entry.get("price", 0) or 0),
        "signal_price": float(entry.get("price", 0) or 0),
        "sl": float(entry.get("sl", 0) or 0),
        "tp1": float(entry.get("tp1", 0) or 0),
        "tp2": float(entry.get("tp2", 0) or 0),
        "rr": float(entry.get("rr", 0) or 0),
        "entry_reason": " | ".join(reasons),
        "range_active_level": s if action == "LONG" else r,
        "break_level": r if action == "LONG" else s,
        "v3_mode": True,
        "v3_scenario": scn,
        "v3_entry_type": str(entry.get("entry_type") or path),
        "v3_support": s,
        "v3_resistance": r,
        "channel_authority": True,
        "channel_path": path,
        "score_decision": False,
        "active_support": s,
        "active_resistance": r,
        "sl_source": str(entry.get("sl_source") or ""),
        "sl_anchor": float(entry.get("sl_anchor", 0) or 0),
    }


_box_log_state: dict = {"sig": "", "ts": 0.0}


def _maybe_log_box(price: float, box: dict, s: float, r: float, zone: str, used: bool) -> None:
    """Kutu kararini DB'ye yaz — yalniz durum degisince (spam yok)."""
    import time as _t

    sig = f"{used}|{round(s,1)}|{round(r,1)}|{zone}"
    now = _t.time()
    if sig == _box_log_state["sig"] and (now - _box_log_state["ts"]) < 900:
        return
    _box_log_state["sig"] = sig
    _box_log_state["ts"] = now
    try:
        from botlog.db import log_box_decision

        log_box_decision({
            "ts": now, "price": price,
            "pine_s": float(box.get("pine_s") or 0),
            "pine_r": float(box.get("pine_r") or 0),
            "box_s": float(box.get("box_s") or 0),
            "box_r": float(box.get("box_r") or 0),
            "used": 1 if used else 0, "zone": zone,
            "reason": str(box.get("reason") or ""),
        })
        if used:
            log.info(f"[BOX] aktif kutu kullanildi S={s:.2f} R={r:.2f} zone={zone} px={price:.2f}")
    except Exception:
        pass


def decide_channel(
    *,
    levels: dict,
    structure: dict,
    cvd: dict,
    price: float,
) -> dict[str, Any]:
    """Tek karar ciktisi — FINAL_DECISION."""
    reasons: list[str] = []
    s = float(levels.get("active_support") or 0)
    r = float(levels.get("active_resistance") or 0)
    zone = str(levels.get("zone") or "MID_RANGE")

    # SABIT Pine referansi (kutudan ONCE): breakout/breakdown bunlardan tespit
    # edilir. Adaptif kutu fiyati kovaladigi icin trendde kirilim referansini
    # yiyordu (1680->1653 dususunde breakdown hic tetiklenmedi). Fade=kutu,
    # breakout=sabit Pine.
    pine_levels = levels
    pine_s, pine_r = s, r

    # Adaptif intraday kutu: Pine bandi genis + fiyat dar alt-aralikta konsolide
    # ise gercek kutuyu (son swing high/low) fade bandi yap. Iki kenar da
    # ulasilabilir hedeflerle oynanir (taban-LONG dahil). Veri: bot %32 alt-kenar
    # fırsatini kaciriyordu cunku destegi uzak Pine seviyesinde saniyordu.
    try:
        from engine.intraday_box import compute_intraday_box

        box = compute_intraday_box(price, s, r)
        if box.get("valid") and box["box_r"] > box["box_s"] > 0:
            from engine.v3_common import calculate_channel_zone

            s, r = box["box_s"], box["box_r"]
            zone = calculate_channel_zone(price, s, r)
            levels = {**levels, "active_support": s, "active_resistance": r,
                      "zone": zone}
            state.v3_box = {"used": True, "s": s, "r": r, "zone": zone}
            _maybe_log_box(price, box, s, r, zone, True)
        else:
            state.v3_box = {"used": False, "s": 0.0, "r": 0.0, "zone": zone}
            _maybe_log_box(price, box, s, r, zone, False)
    except Exception:
        pass

    if s <= 0 or r <= s:
        return {
            "final_decision": "WAIT",
            "reason": "Aktif destek/direnc yok",
            "reasons": ["kanal kurulamadi"],
            "path": "none",
            "zone": zone,
        }

    scores = compute_split_scores(
        levels=levels,
        structure=structure,
        scenario={"name": "CHANNEL"},
        cvd=cvd,
    )

    # Breakout/breakdown: SABIT Pine bandindan (kutu degil) — trendde kaymaz.
    breakout_side = detect_breakout_5m(pine_s, pine_r, price)
    tc_level = 0.0
    # Pine kirilim yoksa TREND-DEVAM (blue-sky/yeni-tepe): fiyat tum seviyelerin
    # ustunde/altinda, yeni swing kirar + yapi ayni yon + OI -> trend yonunde gir.
    if not breakout_side:
        tc_side, tc_lvl, tc_note = _trend_continuation(price, structure, cvd)
        if tc_side:
            breakout_side = tc_side
            tc_level = tc_lvl
            reasons.append(tc_note)
    path = "none"
    candidate = ""

    if breakout_side:
        path = "breakout"
        candidate = breakout_side
        reasons.append(f"breakout 5m/3m {breakout_side}")
        # TASARIM: TREND onayi = OI (pozisyonlama), taker DEGIL. Veri: yukselis
        # taker-satisla yukseliyordu (yaniltici) ama OI +%2.9 (gercek yeni-long).
        # Trend-devam (tc_level>0) icin OI zaten _trend_continuation'da dogrulandi;
        # taker cvd-onayini ATLA. Pine-breakout icin taker onayi kalir.
        if tc_level > 0:
            reasons.append("trend onayi=OI (taker atlandi)")
        else:
            ok_cvd, cvd_note = cvd_breakout_supports(candidate, cvd)
            if not ok_cvd:
                return {
                    "final_decision": "WAIT",
                    "reason": cvd_note,
                    "reasons": reasons + [cvd_note],
                    "path": path,
                    "zone": zone,
                    "direction_scores": scores,
                }
        # OI/squeeze filtresi: gercek breakout'ta yeni para girer (OI artar). OI
        # DUSUYORsa = pozisyon kapanisi (squeeze/unwind) -> sahte kirilim, girme.
        # (1698 spike: OI surekli dusuyordu = short-covering, gercek degil.)
        ok_oi, oi_note = oi_breakout_ok(candidate)
        reasons.append(oi_note)
        if not ok_oi:
            return {
                "final_decision": "WAIT",
                "reason": oi_note,
                "reasons": reasons,
                "path": path,
                "zone": zone,
                "direction_scores": scores,
            }
        ok_sc, sc_note = _score_passes(candidate, scores, mode="breakout")
        reasons.append(sc_note)
        if not ok_sc:
            return {
                "final_decision": "WAIT",
                "reason": sc_note,
                "reasons": reasons,
                "path": path,
                "zone": zone,
                "direction_scores": scores,
            }
    else:
        if zone == "MID_RANGE":
            return {
                "final_decision": "WAIT",
                "reason": "MID_RANGE — islem yok",
                "reasons": ["orta bölge"],
                "path": "fade",
                "zone": zone,
                "direction_scores": scores,
            }
        if zone == "NEAR_SUPPORT":
            candidate = "LONG"
            path = "fade"
            reasons.append("NEAR_SUPPORT fade LONG")
        elif zone == "NEAR_RESISTANCE":
            candidate = "SHORT"
            path = "fade"
            reasons.append("NEAR_RESISTANCE fade SHORT")
        else:
            return {
                "final_decision": "WAIT",
                "reason": f"zone={zone}",
                "reasons": reasons,
                "path": "fade",
                "zone": zone,
                "direction_scores": scores,
            }

        # DONUS-TEYIDI (yalniz SHORT, veriyle dogrulandi): direnc-fade'i, fiyat
        # YENI TEPE yapmayi BIRAKANA kadar bekle. Veri (25 gun): aninda direnc-short
        # -321bps (%46), donus-teyitli +83bps -> sahte-yukari-kirilimlari eler,
        # "yukselen direnci shortlama, reddedince shortla". LONG'a EKLENMEDI (long@destek
        # teyitle de kaybediyordu -544; dokunulmadi). Saf hesap (son K x15m tepe).
        if candidate == "SHORT" and bool(getattr(cfg, "V3_FADE_REJECT_CONFIRM", True)):
            try:
                from engine.v3_common import bars_15m

                k = int(getattr(cfg, "V3_FADE_REJECT_BARS", 2) or 2)
                bb = bars_15m(k + 3)
                hs = [float(b.get("high", 0) or 0) for b in bb if float(b.get("high", 0) or 0) > 0]
                if len(hs) >= k + 1 and hs[-1] >= max(hs[-(k + 1):-1]) - 1e-9:
                    msg = "donus-teyidi yok: direnc hala yeni tepe yapiyor — short bekle"
                    reasons.append(msg)
                    return {"final_decision": "WAIT", "reason": msg, "reasons": reasons,
                            "path": path, "zone": zone, "direction_scores": scores}
            except Exception:
                pass

        # BLUE-SKY vetosu (HARD, fiyat-bazli — etikete bagli degil): fiyat TUM gercek
        # seviyelerin belirgin USTUNDEyse short YOK (altindaysa long YOK). Sentetik
        # yakin-direnci fade etmeyi onler. #201: px 1813, en yuksek seviye 1730 ->
        # short absurd (475bps blue sky). En saglam counter-trend korumasi.
        try:
            # Gercek Pine seviyeleri chart_levels'te (L1-L6); all_levels bos olabilir.
            _merged = (pine_levels.get("chart_levels") or pine_levels.get("all_levels")
                       or levels.get("chart_levels") or levels.get("all_levels") or [])
            _allpx = [float(l.get("price", 0) or 0) for l in _merged
                      if float(l.get("price", 0) or 0) > 0]
            _marg = price * 0.0015
            if _allpx:
                if candidate == "SHORT" and price > max(_allpx) + _marg:
                    msg = f"blue-sky: fiyat tum seviyelerin ustunde (max {max(_allpx):.0f}) — short yok"
                    reasons.append(msg)
                    return {"final_decision": "WAIT", "reason": msg, "reasons": reasons,
                            "path": path, "zone": zone, "direction_scores": scores}
                if candidate == "LONG" and price < min(_allpx) - _marg:
                    msg = f"blue-sky: fiyat tum seviyelerin altinda (min {min(_allpx):.0f}) — long yok"
                    reasons.append(msg)
                    return {"final_decision": "WAIT", "reason": msg, "reasons": reasons,
                            "path": path, "zone": zone, "direction_scores": scores}
        except Exception:
            pass

        # CIFT-ZAMANLI trend vetosu (HARD): 15m VE 1h ayni yonde ise o yone fade YOK
        # — gap esiginden ve akis-donusundan bagimsiz. #200 (UP/UP'ta pullback-short,
        # gap 13.8<30 sizdi) gibi counter-trend felaketleri onler.
        # GUVENILIR kaynak: state.structure_15m/1h (snapshot'lar bunu UP/UP gosterdi).
        # structure.get('trend'/'dir_1h') bu dict'te YOK -> eski veto no-op'tu, #202/#203
        # gibi UP/UP counter-trend short'lar sizdi.
        t15 = str(getattr(state, "structure_15m", "") or "").upper()
        d1h = str(getattr(state, "structure_1h", "") or "").upper()
        both_up = t15 == "UP" and d1h == "UP"
        both_dn = t15 == "DOWN" and d1h == "DOWN"
        if (candidate == "SHORT" and both_up) or (candidate == "LONG" and both_dn):
            msg = f"cift-zamanli trend (15m={t15}/1h={d1h}) — ters fade {candidate} yok"
            reasons.append(msg)
            return {"final_decision": "WAIT", "reason": msg, "reasons": reasons,
                    "path": path, "zone": zone, "direction_scores": scores}

        # Guclu akis-donusu (absorpsiyon) -> counter-trend vetolari asar
        reversal = _strong_flow_reversal(candidate, cvd)
        if reversal:
            reasons.append("guclu akis-donusu (absorpsiyon) — counter-trend veto asildi")

        # Trend filtresi: GUCLU ters trendde fade yapma (direnci yukselen trendde
        # shortlamak = momentuma karsi, kayip kaynagi). 15m guc esigi.
        if bool(getattr(cfg, "V3_CHANNEL_FADE_TREND_FILTER", True)) and not reversal:
            tv = getattr(state, "trend_view", None) or {}
            tbias = str(tv.get("bias") or "").upper()
            tstr = float(tv.get("strength") or 0)
            thr = float(getattr(cfg, "V3_CHANNEL_FADE_TREND_STR", 80) or 80)
            blocked_up = candidate == "SHORT" and tbias == "UP" and tstr >= thr
            blocked_dn = candidate == "LONG" and tbias == "DOWN" and tstr >= thr
            if blocked_up or blocked_dn:
                msg = f"guclu {tbias} trend (guc={tstr:.0f}) — ters fade yok"
                reasons.append(msg)
                return {
                    "final_decision": "WAIT",
                    "reason": msg,
                    "reasons": reasons,
                    "path": path,
                    "zone": zone,
                    "direction_scores": scores,
                }

        # EDGE KAPISI (veriyle dogrulandi): son realized range hedefi (TP1)
        # karsilamiyorsa fade yok — ulasilmaz hedef, kar beklentisi yok. Ayrica
        # VR rejimi 'trend' ise fade yapma (trend'e karsi fade = kayip kaynagi).
        try:
            from engine.regime_vr import edge_gate, classify_regime

            # Hedef SABIT degil — KANAL genisliginden tureit (indikator yok, hesap).
            # Dar kanal -> kucuk ulasilabilir hedef; genis -> tavan. Kutu min-40bps
            # sarti olu chop'u zaten eler. Boylece sabit 60 kirilganligi biter.
            cap_tp1 = float(getattr(cfg, "V3_TP1_MAX_BPS", 60) or 60)
            band_bps = (r - s) / price * 1e4 if (r > s > 0 and price > 0) else cap_tp1
            frac = float(getattr(cfg, "V3_TP1_FRAC_OF_BAND", 0.6) or 0.6)
            ref_tp1 = max(20.0, min(cap_tp1, band_bps * frac))
            eg = edge_gate(price, ref_tp1)
            if not eg["allow"]:
                msg = (f"edge yok: son range {eg['range_bps']:.0f}bps < hedef "
                       f"{eg['need_bps']:.0f}bps (kanal={band_bps:.0f}bps, ulasilmaz)")
                reasons.append(msg)
                return {"final_decision": "WAIT", "reason": msg, "reasons": reasons,
                        "path": path, "zone": zone, "direction_scores": scores}
            reg = classify_regime()
            if reg.get("regime") == "trend" and not reversal:
                msg = f"VR trend rejimi (vr={reg['vr']}) — fade yok"
                reasons.append(msg)
                return {"final_decision": "WAIT", "reason": msg, "reasons": reasons,
                        "path": path, "zone": zone, "direction_scores": scores}
            # TREND-GUCU kapisi (ASIL ayrac, veriyle dogrulandi): yonsellik yuksekse
            # (trend) fade KATLEDILIR. Backtest 25 gun: fade'i yalniz trend-gucu <
            # esik iken ac -> 4 ceyrekten 3'u pozitif (VR tek basina yapamadi).
            if not reversal:
                from engine.regime_vr import trend_strength_15m

                ts_max = float(getattr(cfg, "V3_TREND_STR_MAX", 0.35) or 0.35)
                tstr = trend_strength_15m()
                if tstr >= ts_max:
                    msg = f"trend-gucu {tstr:.2f} >= {ts_max:.2f} (yonsel/trend) — fade yok"
                    reasons.append(msg)
                    return {"final_decision": "WAIT", "reason": msg, "reasons": reasons,
                            "path": path, "zone": zone, "direction_scores": scores}
        except Exception:
            pass

        # Yapi-hizasi kapisi: yapi sert TERS yondeyse o yone fade yapma.
        # Veri: 100 LONG %19 kazandi (ayi yapida destek-long = counter-trend bleed).
        # Trend filtresi sadece guc>=80'i yakaliyor; bu kapi skor farkini yakalar.
        # TUTARLILIK (16 Haz): bu kapi yalniz TREND'de gecerli. RANGE'de (trend-gucu
        # < esik) "karsi-trend" diye bir sey yoktur -> struct-align veto'su trend-gucu
        # kapisiyla CELISIR ve botu tek-yonlu yapar (9/9 short, destekte 438 kez long
        # bloklandi). Range'de iki kenar da fade edilmeli (LONG@destek +0.11, SHORT@
        # direnc +0.63 ikisi de kazancli). O yuzden veto yalniz trend-gucu >= esik iken.
        try:
            from engine.regime_vr import trend_strength_15m
            _tstr = trend_strength_15m()
        except Exception:
            _tstr = 1.0
        _is_trend = _tstr >= float(getattr(cfg, "V3_TREND_STR_MAX", 0.35) or 0.35)
        gap_min = float(getattr(cfg, "V3_FADE_STRUCT_ALIGN_GAP", 30) or 0)
        if gap_min > 0 and _is_trend:
            sL = float(scores.get("structure_long_score") or 0)
            sS = float(scores.get("structure_short_score") or 0)
            opp_lead = (sS - sL) if candidate == "LONG" else (sL - sS)
            # NOT: reversal override artik struct-align'i BYPASS ETMEZ. Cok-zamanli
            # yapi sert tersse (gap>=30) o yone fade YOK — anlik akis donusu bunu
            # asamaz. Yoksa yukselen trendde dirence "satis var" diye 8 ardisik
            # short aciliyor (06-15 1759->1825 felaketi). Override yalniz kisa-vade
            # momentum kapilarini (trend-filtre/VR) asar.
            if opp_lead >= gap_min:
                msg = f"yapi ters baskin (karsi-yon +{opp_lead:.0f}) — fade {candidate} yok"
                reasons.append(msg)
                return {
                    "final_decision": "WAIT",
                    "reason": msg,
                    "reasons": reasons,
                    "path": path,
                    "zone": zone,
                    "direction_scores": scores,
                }

        ok_cvd, cvd_note, penalty = cvd_fade_filter(candidate, cvd)
        reasons.append(cvd_note)
        if not ok_cvd:
            return {
                "final_decision": "WAIT",
                "reason": cvd_note,
                "reasons": reasons,
                "path": path,
                "zone": zone,
                "direction_scores": scores,
            }
        if penalty > 0:
            scores = dict(scores)
            if candidate == "LONG":
                scores["entry_long_score"] = float(scores.get("entry_long_score", 0)) - penalty
            else:
                scores["entry_short_score"] = float(scores.get("entry_short_score", 0)) - penalty
        # GERCEK-KENAR KANAL YOLU (ekleyici, OR): RANGE'de fiyat GERCEK kanal kenarinda
        # (Pine S/R, kutu degil) ise yon = kanal konumu; skor-kapilari ASKIYA alinir.
        # Cunku range'de skor (trend/yapi-bazli) kanala KARSI calisiyor (destekte bile
        # prob_long %35). Kullanici: "kanal ici islem". #208/#209 SOFT kenardaydi
        # (1805, gercek direnc 1820 degil) -> gercek-kenar sarti onlari yine eler.
        _band = (pine_r - pine_s) if (pine_r > pine_s > 0) else 0.0
        _ef = max(float(getattr(cfg, "V3_CHANNEL_EDGE_FRAC", 0.22) or 0.22), 0.05)
        _genuine_edge = False
        if _band > 0:
            if candidate == "LONG" and price <= pine_s + _ef * _band:
                _genuine_edge = True
            elif candidate == "SHORT" and price >= pine_r - _ef * _band:
                _genuine_edge = True
        _range_edge_ok = (not _is_trend) and _genuine_edge
        if _range_edge_ok:
            reasons.append(f"gercek-kenar kanal yolu (range, {candidate}@Pine kenar) "
                           f"— skor-kapilari askida")

        # TUTARLILIK kapisi (hesaba-dayali, eshik oynatmak DEGIL): "kapatacagin
        # islemi acma". Cikis (score_weak_exit) prob < V3_SCORE_EXIT_PROB ise kapatir;
        # o halde GIRIS de ayni esigi gerektirsin. Yoksa geometri (zone=dirence)
        # prob %40 short aciyor, cikis aninda kesiyor (#208/#209: bos ac-zararla kapa).
        # GERCEK-KENAR range fade bu kapidan MUAF (kanal konumu sinyalin kendisi).
        exit_th = float(getattr(cfg, "V3_SCORE_EXIT_PROB", 0.55) or 0.55)
        key = "prob_short_pct" if candidate == "SHORT" else "prob_long_pct"
        prob_side = float(scores.get(key, 0) or 0) / 100.0
        if prob_side > 0 and prob_side < exit_th and not _range_edge_ok:
            msg = (f"yon-skoru tutarsiz: {candidate} prob=%{prob_side*100:.0f} < cikis "
                   f"esigi %{exit_th*100:.0f} — geometri dirençte ama skor ters, acma")
            reasons.append(msg)
            return {"final_decision": "WAIT", "reason": msg, "reasons": reasons,
                    "path": path, "zone": zone, "direction_scores": scores}
        ok_sc, sc_note = _score_passes(candidate, scores, mode="fade")
        reasons.append(sc_note)
        if not ok_sc and not _range_edge_ok:
            return {
                "final_decision": "WAIT",
                "reason": sc_note,
                "reasons": reasons,
                "path": path,
                "zone": zone,
                "direction_scores": scores,
            }

    # Breakout: KIRILAN seviye SL'li ozel geometri (retest stop). Fade: kutu kenari.
    if path == "breakout":
        from engine.entry_v3 import _build_breakout_entry

        broken = tc_level if tc_level > 0 else (pine_s if candidate == "SHORT" else pine_r)
        entry = _build_breakout_entry(
            "SELL" if candidate == "SHORT" else "BUY",
            price, broken,
            entry_type=f"CHANNEL_BREAKOUT_{candidate}",
        )
    else:
        entry = _build_entry(
            candidate, levels, price, f"CHANNEL_FADE_{candidate}",
        )
    rr = float(entry.get("rr", 0) or 0)
    min_rr = float(getattr(cfg, "V3_MIN_RR_RATIO", 2.0) or 2.0)
    if not entry.get("valid") or rr < min_rr:
        msg = f"RR yetersiz: {rr:.2f} < {min_rr:.2f}"
        reasons.append(msg)
        return {
            "final_decision": "WAIT",
            "reason": msg,
            "reasons": reasons,
            "path": path,
            "zone": zone,
            "entry": entry,
            "direction_scores": scores,
        }

    reasons.append(f"RR={rr:.2f}")
    return {
        "final_decision": candidate,
        "reason": " | ".join(reasons),
        "reasons": reasons,
        "path": path,
        "zone": zone,
        "entry": entry,
        "direction_scores": scores,
    }


def update_decision_channel_authority(
    *,
    px: float,
    levels: dict,
    structure: dict,
    scenario: dict,
    cvd: dict,
    signal: dict,
    flow_tag: str = "",
    flow_force: bool = False,
) -> dict:
    from engine.decision_v3 import _commit_decision, _build_v3_thesis_details
    from engine.cvd_v3 import update_cvd_snapshot

    levels = apply_frozen_channel_levels(levels, px)
    zone_for_cvd = str(levels.get("zone") or "MID_RANGE")
    cvd = update_cvd_snapshot(zone=zone_for_cvd, breakout_side="")
    decision = decide_channel(
        levels=levels,
        structure=structure,
        cvd=cvd,
        price=px,
    )

    action = str(decision.get("final_decision") or "WAIT")
    path = str(decision.get("path") or "")
    zone = str(decision.get("zone") or "")
    scores = decision.get("direction_scores") or {}
    entry = decision.get("entry") or signal or {}
    scn_name = _scenario_name(path, action) if action in ("LONG", "SHORT") else "CHANNEL_WAIT"

    synth_scenario = {
        "name": scn_name,
        "detail": decision.get("reason", ""),
        "ref_support": float(levels.get("active_support") or 0),
        "ref_resistance": float(levels.get("active_resistance") or 0),
        "channel_path": path,
        "channel_zone": zone,
    }

    # Flip: kirilim ters pozisyon
    reverse_signal = False
    reverse_from = ""
    if state.in_position and action in ("LONG", "SHORT"):
        cur = str(state.pos_side or "").upper()
        if path == "breakout" and cur != action:
            reverse_signal = True
            reverse_from = cur
            reasons = list(decision.get("reasons") or [])
            reasons.insert(0, f"flip {cur}->{action}")
            decision["reason"] = " | ".join(reasons)
        elif cur == action:
            action = "WAIT"
            decision["reason"] = "pozisyon zaten acik (ayni yon)"
        elif path == "fade":
            action = "WAIT"
            decision["reason"] = f"pozisyon acik — fade {action} yok"

    log.info(
        f"[FINAL_DECISION] {action} | path={path} zone={zone} | "
        f"{decision.get('reason', '')}"
    )

    if action == "WAIT":
        reject_code = channel_reject_code(decision)
        ch_reason = str(decision.get("reason") or "WAIT")
        snap = {
            "action": "WAIT",
            "reason": ch_reason,
            "reject_reason": reject_code,
            "levels": levels,
            "structure": structure,
            "scenario": synth_scenario,
            "cvd": cvd,
            "entry": entry,
            "direction_scores": scores,
            "channel_decision": decision,
            "final_decision": action,
            "channel_authority": True,
        }
        return _commit_decision(snap, flow_tag=flow_tag, flow_force=flow_force)

    details = _build_v3_thesis_details(
        action=action,
        side_entry=entry,
        levels=levels,
        scenario=synth_scenario,
        px=px,
        rr=float(entry.get("rr", 0) or 0),
        scores=scores,
        range_locked=False,
    )
    details["channel_authority"] = True
    details["channel_path"] = path

    snap = {
        "action": action,
        "reason": details["entry_reason"],
        "details": details,
        "levels": levels,
        "structure": structure,
        "scenario": synth_scenario,
        "cvd": cvd,
        "entry": entry,
        "direction_scores": scores,
        "channel_decision": decision,
        "final_decision": action,
        "reverse_signal": reverse_signal,
        "reverse_from": reverse_from,
    }
    return _commit_decision(snap, flow_tag=flow_tag, flow_force=flow_force)
