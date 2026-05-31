"""
engine/decision_v3.py

Sade karar: seviye -> zone -> CVD -> giris -> RR
(1h yapi yalnizca bilgi; kanal teyidi ve 15m hizasi yok)
"""
from __future__ import annotations

from core.config import cfg
from core.state import state
from core.logger import get_logger
from engine.cvd_v3 import update_cvd_snapshot
from engine.entry_v3 import get_entry_snapshot, update_entry
from engine.levels_v3 import get_levels_snapshot, update_levels
from engine.scenario_v3 import get_scenario_snapshot, update_scenario
from engine.v3_flow_log import maybe_log_v3_flow
from engine.structure_v3 import get_structure_snapshot, update_structure

log = get_logger("V3Decision")
_last_diag_key = ""
_last_periodic_log_ts = 0.0


def format_decision_diag(snap: dict) -> str:
    action = str(snap.get("action") or "WAIT")
    reason = str(snap.get("reason") or "—")
    levels = snap.get("levels") or {}
    structure = snap.get("structure") or {}
    cvd = snap.get("cvd") or {}
    scenario = snap.get("scenario") or {}
    entry = snap.get("entry") or {}
    s1h = str(((structure.get("1h") or {}).get("direction")) or "?")
    scn = str(scenario.get("name") or "—")
    s_px = float(levels.get("active_support") or 0)
    r_px = float(levels.get("active_resistance") or 0)
    zone = str(levels.get("zone") or "?")
    lock = "kilit" if levels.get("active_locked") else "acik"
    cvd_dir = str(cvd.get("direction") or "?")
    cvd_ok = "evet" if cvd.get("confirmed") else "hayir"
    cvd_cum = float(cvd.get("cumulative", 0) or 0)
    buy_r = float(cvd.get("buy_ratio", 0.5) or 0.5)
    rr = float(entry.get("rr", 0) or 0)
    entry_ok = "evet" if entry.get("valid") else "hayir"
    sl_px = float(entry.get("sl", 0) or 0)
    tp_px = float(entry.get("tp2", 0) or 0)
    rr_tag = " onizleme" if entry.get("preview") and not entry.get("valid") else ""
    levels_line = (
        f"SL={sl_px:.2f} TP={tp_px:.2f} " if sl_px > 0 and tp_px > 0 else ""
    )
    return (
        f"karar={action} | neden: {reason} | "
        f"1h={s1h} (bilgi) | senaryo={scn} | band {s_px:.0f}/{r_px:.0f} zone={zone} {lock} | "
        f"CVD {cvd_dir} cum={cvd_cum:+.0f} alim={buy_r:.0%} teyit={cvd_ok} | "
        f"giris={entry_ok} {levels_line}RR={rr:.2f}{rr_tag}"
    )


def log_decision_diag(snap: dict, *, tag: str = "", force: bool = False) -> None:
    import time

    global _last_diag_key, _last_periodic_log_ts
    action = str(snap.get("action") or "WAIT")
    reason = str(snap.get("reason") or "")
    key = f"{action}|{reason}"
    now = time.time()
    interval = float(getattr(cfg, "V3_DECISION_LOG_SEC", 120) or 120)
    periodic = force or (now - _last_periodic_log_ts >= interval)
    if action not in ("LONG", "SHORT"):
        if key == _last_diag_key and not periodic:
            return
    _last_diag_key = key
    if periodic:
        _last_periodic_log_ts = now
    line = format_decision_diag(snap)
    suffix = f" ({tag})" if tag else ""
    log.info(f"[V3] {line}{suffix}")


def _commit_decision(snap: dict, *, flow_tag: str = "", flow_force: bool = False) -> dict:
    state.v3_decision = snap
    log_decision_diag(snap, tag=flow_tag, force=flow_force)
    maybe_log_v3_flow(snap, tag=flow_tag, force=flow_force)
    return snap


def update_decision(*, flow_tag: str = "", flow_force: bool = False) -> dict:
    from core.state import effective_price

    from engine.levels_v3 import update_levels
    from engine.scenario_v3 import update_scenario

    px = float(effective_price() or state.mark_price or state.price or 0)
    update_levels()
    update_scenario()
    signal = update_entry()
    levels = get_levels_snapshot(px)
    scenario = get_scenario_snapshot(px) or {}
    structure = get_structure_snapshot() or update_structure()
    zone = str(levels.get("zone") or "MID_RANGE")
    scn_name = str(scenario.get("name") or "WAIT")
    breakout_side = ""
    if scn_name.startswith("BREAKOUT_"):
        breakout_side = "BUY" if "BUY" in scn_name else "SELL"
    cvd = update_cvd_snapshot(zone=zone, breakout_side=breakout_side)

    if state.in_position:
        snap = {
            "action": "WAIT",
            "reason": "pozisyon acik",
            "levels": levels,
            "structure": structure,
            "scenario": scenario,
            "cvd": cvd,
            "entry": signal,
        }
        return _commit_decision(snap, flow_tag=flow_tag, flow_force=flow_force)

    reasons: list[str] = []
    support = levels.get("support") or {}
    resistance = levels.get("resistance") or {}
    support_ok = support and int(support.get("score", 0)) >= cfg.V3_LEVEL_SCORE_MEDIUM
    resistance_ok = resistance and int(resistance.get("score", 0)) >= cfg.V3_LEVEL_SCORE_MEDIUM

    is_breakout = scn_name.startswith("BREAKOUT_")
    is_range = scn_name in ("RANGE_BUY", "RANGE_SELL")
    ref_s = float(scenario.get("ref_support") or levels.get("active_support") or 0)
    ref_r = float(scenario.get("ref_resistance") or levels.get("active_resistance") or 0)

    if not is_breakout and not is_range:
        if not support_ok or not resistance_ok:
            reasons.append("Destek/direnc yok veya zayif.")
    if not is_breakout:
        if not levels.get("range_valid"):
            reasons.append("Range gecersiz.")
    if is_breakout:
        if scn_name == "BREAKOUT_SELL" and ref_s <= 0:
            reasons.append("Kirilim referans destegi yok.")
        elif scn_name == "BREAKOUT_BUY" and ref_r <= 0:
            reasons.append("Kirilim referans direnci yok.")

    zone_blocks = zone == "MID_RANGE" and not is_breakout
    if zone_blocks:
        reasons.append("Fiyat band ortasinda (zone) — destek/dirence yaklasinca senaryo acilir.")
    elif scn_name == "WAIT":
        detail = str(scenario.get("detail") or "")
        if detail:
            reasons.append(f"Senaryo: {detail}")
    if is_breakout and not cvd.get("confirmed"):
        reasons.append("CVD teyit etmiyor.")
    # Giris/RR yalnizca trade adayinda (MID_RANGE/WAIT'te RR=0 log gurultusu yok)
    trade_candidate = scn_name not in ("WAIT",) and not zone_blocks
    if trade_candidate:
        if not signal.get("valid"):
            reasons.append("Giris noktasi olusmadi.")
        elif float(signal.get("rr", 0) or 0) < cfg.V3_MIN_RR_RATIO:
            reasons.append(
                f"RR yetersiz: {float(signal.get('rr', 0) or 0):.2f} < {cfg.V3_MIN_RR_RATIO:.2f}"
            )

    if reasons:
        snap = {
            "action": "WAIT",
            "reason": " | ".join(reasons),
            "levels": levels,
            "structure": structure,
            "scenario": scenario,
            "cvd": cvd,
            "entry": signal,
        }
        return _commit_decision(snap, flow_tag=flow_tag, flow_force=flow_force)

    action = "LONG" if str(signal.get("direction") or "") == "BUY" else "SHORT"
    support_price = float((levels.get("support") or {}).get("price", 0) or 0)
    resistance_price = float((levels.get("resistance") or {}).get("price", 0) or 0)
    if is_breakout:
        if ref_s > 0:
            support_price = ref_s
        if ref_r > 0:
            resistance_price = ref_r
    break_level = 0.0
    if "BREAKOUT" in scn_name:
        break_level = resistance_price if action == "LONG" else support_price
    details = {
        "direction": action,
        "price": float(signal.get("price", 0) or 0),
        "signal_price": float(signal.get("price", 0) or 0),
        "sl": float(signal.get("sl", 0) or 0),
        "tp1": float(signal.get("tp1", 0) or 0),
        "tp2": float(signal.get("tp2", 0) or 0),
        "rr": float(signal.get("rr", 0) or 0),
        "entry_reason": f"v3 {scenario.get('name')} + {signal.get('entry_type')}",
        "range_active_level": support_price if action == "LONG" else resistance_price,
        "break_level": break_level,
        "v3_mode": True,
        "v3_scenario": scn_name,
        "v3_entry_type": str(signal.get("entry_type") or ""),
        "v3_support": support_price,
        "v3_resistance": resistance_price,
    }
    snap = {
        "action": action,
        "reason": details["entry_reason"],
        "details": details,
        "levels": levels,
        "structure": structure,
        "scenario": scenario,
        "cvd": cvd,
        "entry": signal,
    }
    return _commit_decision(snap, flow_tag=flow_tag, flow_force=flow_force)


def get_decision_snapshot() -> dict:
    snap = state.v3_decision or {}
    if not snap:
        snap = update_decision()
    return snap
