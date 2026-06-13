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

    breakout_side = detect_breakout_5m(s, r, price)
    path = "none"
    candidate = ""

    if breakout_side:
        path = "breakout"
        candidate = breakout_side
        reasons.append(f"breakout 5m/3m {breakout_side}")
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

        # Trend filtresi: GUCLU ters trendde fade yapma (direnci yukselen trendde
        # shortlamak = momentuma karsi, kayip kaynagi). 15m guc esigi.
        if bool(getattr(cfg, "V3_CHANNEL_FADE_TREND_FILTER", True)):
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

        # Yapi-hizasi kapisi: yapi sert TERS yondeyse o yone fade yapma.
        # Veri: 100 LONG %19 kazandi (ayi yapida destek-long = counter-trend bleed).
        # Trend filtresi sadece guc>=80'i yakaliyor; bu kapi skor farkini yakalar.
        gap_min = float(getattr(cfg, "V3_FADE_STRUCT_ALIGN_GAP", 30) or 0)
        if gap_min > 0:
            sL = float(scores.get("structure_long_score") or 0)
            sS = float(scores.get("structure_short_score") or 0)
            opp_lead = (sS - sL) if candidate == "LONG" else (sL - sS)
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
        ok_sc, sc_note = _score_passes(candidate, scores, mode="fade")
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

    entry = _build_entry(
        candidate,
        levels,
        price,
        f"CHANNEL_{path.upper()}_{candidate}",
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
