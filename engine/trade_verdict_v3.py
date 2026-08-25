"""
engine/trade_verdict_v3.py — Tek yorum katmani (COLLAPSE + EXEC birlestirilir).

COLLAPSE: rejim + yon + senaryo izni
EXEC FILTER: zaman + tier (PASS sadece INSTANT/READY+)
Bu modul: ikisini TEK verdict'e indirger — cift beyin yok.
"""
from __future__ import annotations

from core.config import cfg
from engine.state_collapse_v3 import scenario_allowed


def _side_norm(side: str) -> str:
    s = str(side or "").upper()
    if s in ("BUY", "LONG"):
        return "LONG"
    if s in ("SELL", "SHORT"):
        return "SHORT"
    return ""


def synthesize_trade_verdict(
    collapse: dict,
    execution_filter: dict,
    events: dict | None = None,
) -> dict:
    """
    Piyasa durumunun tek ozeti — log ve dahili kullanim.
    scenario/side olmadan genel verdict.
    """
    events = events or {}
    ef = execution_filter or {}
    c = collapse or {}
    lt = ef.get("opportunity_lifetime") or {}
    urgency = ef.get("urgency") or {}
    tier = str(lt.get("tier") or ef.get("opportunity", {}).get("tier") or "NONE")

    collapse_allows = bool(c.get("allow_trade"))
    exec_pass = bool(ef.get("pass_gate"))
    exec_action = str(urgency.get("action") or "SKIP")
    observable = bool(ef.get("observable") or ef.get("event_monitor"))
    decayed = float(events.get("decayed_score", 0) or 0)

    regime = {
        "mode": c.get("mode"),
        "dominant_bias": c.get("dominant_bias"),
        "controller": c.get("controller"),
        "state_score": c.get("state_score"),
        "allow_trade": collapse_allows,
    }
    timing = {
        "tier": tier,
        "urgency": exec_action,
        "pass_gate": exec_pass,
        "setup_strength": lt.get("setup_strength", 0),
        "effective_lifetime": lt.get("effective_lifetime", 0),
        "trade_probability": ef.get("trade_probability", 0),
        "event_monitor": bool(ef.get("event_monitor")),
    }

    if not collapse_allows:
        verdict = "NO_TRADE"
        allow_entry = False
        source = "collapse"
        reason = f"Collapse rejim: {c.get('mode')} allow_trade=hayir — {c.get('detail', '')}"
    elif not exec_pass or exec_action in ("SKIP", "OBSERVE", "WATCH"):
        verdict = "OBSERVE" if observable else "TIMING_BLOCK"
        allow_entry = False
        source = "execution_timing"
        reason = urgency.get("reason") or lt.get("reason") or f"tier={tier} timing blok"
    elif exec_action == "NOW":
        verdict = "TRADE_NOW"
        allow_entry = True
        source = "unified"
        reason = f"Collapse OK + timing NOW ({tier}) setup={lt.get('setup_strength', 0):.0f}"
    elif exec_action == "LATER" and tier == "READY_PLUS":
        verdict = "TRADE_LATER"
        allow_entry = True
        source = "unified"
        reason = f"Collapse OK + timing LATER READY+ eff={lt.get('effective_lifetime', 0):.0f}"
    else:
        verdict = "TIMING_BLOCK"
        allow_entry = False
        source = "execution_timing"
        reason = urgency.get("reason", "timing uyumsuz")

    return {
        "verdict": verdict,
        "allow_entry": allow_entry,
        "source": source,
        "reason": reason,
        "regime": regime,
        "timing": timing,
        "event_strength": decayed,
        "aligned": collapse_allows == allow_entry or (not collapse_allows and not allow_entry),
    }


def trade_entry_allowed(
    market_state: dict | None,
    scenario_name: str,
    side: str,
) -> tuple[bool, str]:
    """
    TEK KAPI — decision, entry, scenario hepsi bunu kullanir.

    V3_SCORE_DECISION_ENABLED: veto kapali, skor karari kullanilir.
    """
    if getattr(cfg, "V3_SCORE_DECISION_ENABLED", True):
        return True, "prob_score_mode"

    ms = market_state or {}
    collapse = ms.get("collapse") or {}
    ef = ms.get("execution_filter") or {}
    events = ms.get("events") or {}

    unified = ms.get("trade_verdict") or synthesize_trade_verdict(collapse, ef, events)
    side_u = _side_norm(side)
    sn = str(scenario_name or "").upper()

    ok_scenario, scenario_msg = scenario_allowed(collapse, sn, side)
    if not ok_scenario:
        return False, scenario_msg

    if not unified.get("allow_entry"):
        return False, unified.get("reason", "trade verdict: giris yok")

    timing = unified.get("timing") or {}
    return True, (
        f"{unified.get('verdict')} | {collapse.get('mode')} "
        f"baskın={collapse.get('dominant_bias')} tier={timing.get('tier')}"
    )


def attach_trade_verdict(market_state: dict) -> dict:
    """market_state'e trade_verdict ekler; exec filter collapse ile hizalanir."""
    ms = dict(market_state)
    collapse = ms.get("collapse") or {}
    ef = dict(ms.get("execution_filter") or {})
    events = ms.get("events") or {}

    if not collapse.get("allow_trade"):
        ef = dict(ef)
        ef["pass_gate"] = False
        u = dict(ef.get("urgency") or {})
        u["action"] = "SKIP"
        u["reason"] = f"Collapse {collapse.get('mode')} — timing devre disi"
        ef["urgency"] = u
        lt = dict(ef.get("opportunity_lifetime") or {})
        lt["pass_gate"] = False
        lt["tier"] = "DEFERRED"
        lt["reason"] = u["reason"]
        ef["opportunity_lifetime"] = lt
        ms["execution_filter"] = ef

    ms["trade_verdict"] = synthesize_trade_verdict(
        collapse, ef, events
    )
    ms["authority"] = "unified_verdict"
    return ms


def verdict_log_line(v: dict | None) -> str:
    x = v or {}
    t = x.get("timing") or {}
    r = x.get("regime") or {}
    return (
        f"[VERDICT] {x.get('verdict')} entry={'evet' if x.get('allow_entry') else 'hayir'} "
        f"src={x.get('source')} | rejim={r.get('mode')} baskın={r.get('dominant_bias')} "
        f"tier={t.get('tier')} p={t.get('trade_probability', 0):.2f}"
    )
