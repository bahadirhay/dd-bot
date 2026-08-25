"""
engine/regime_physics_v3.py — Tek otorite: COLLAPSE rejim fizigi.

Inertia, liquidity trap, continuation — execution brain DEGIL.
"""
from __future__ import annotations

from core.config import cfg


def compute_state_inertia(structure: dict, collapse: dict) -> dict:
    trend = str(structure.get("trend") or "range")
    strength = int(structure.get("strength", 0) or 0)
    pattern = str(structure.get("pattern") or "")
    weak_bounce = bool(structure.get("weak_bounce"))
    lh = bool(structure.get("is_lower_high"))
    comp = bool(structure.get("compression"))
    fractal = structure.get("fractal") or {}

    score = 0
    side = "neutral"

    if trend == "bearish":
        side = "bearish"
        score = 40 + strength // 3
        if weak_bounce:
            score += 18
        if lh:
            score += 14
        if comp:
            score += 10
        if "IMPULSE_DOWN" in pattern:
            score += 12
        if fractal.get("aligned") and fractal.get("alignment") == "bearish":
            score += 10
    elif trend == "bullish":
        side = "bullish"
        score = 40 + strength // 3
        if "UP" in pattern:
            score += 12
        if fractal.get("aligned") and fractal.get("alignment") == "bullish":
            score += 10

    dom = str(collapse.get("dominant_bias") or "neutral")
    if dom == side:
        score = min(100, score + 8)
    elif dom != "neutral" and dom != side:
        score = max(0, score - 15)

    min_sc = int(getattr(cfg, "V3_INERTIA_MIN_CONTINUATION", 58) or 58)
    continuation = score >= min_sc and side in ("bearish", "bullish")

    return {
        "side": side,
        "score": max(0, min(100, int(score))),
        "continuation": continuation,
        "weak_bounce": weak_bounce,
        "pattern": pattern,
    }


def detect_liquidity_trap(
    events: dict,
    liquidity: dict,
    structure: dict,
) -> dict:
    flags = events.get("flags") or {}
    pools = liquidity.get("pools") or []
    min_reaction = float(getattr(cfg, "V3_TRAP_MIN_REACTION", 0.45) or 0.45)

    trap_long = False
    trap_short = False
    detail = ""

    for p in pools:
        q = str(p.get("quality") or "")
        reaction = float(p.get("reaction_strength", 0) or 0)
        swept = bool(p.get("swept"))
        pr = float(p.get("price", 0) or 0)
        if not swept or reaction < min_reaction:
            continue
        if flags.get("sweep_low") and pr > 0 and q == "LOW":
            trap_long = True
            detail = f"trap_long reaction={reaction:.2f} @{pr:.0f}"
        if flags.get("sweep_high") and pr > 0 and q == "LOW":
            trap_short = True
            detail = f"trap_short reaction={reaction:.2f} @{pr:.0f}"

    return {
        "reversal_long": trap_long,
        "reversal_short": trap_short,
        "active": trap_long or trap_short,
        "detail": detail,
    }


def apply_regime_physics_to_collapse(
    collapse: dict,
    structure: dict,
    liquidity: dict,
    events: dict,
) -> dict:
    """COLLAPSE tek karar otoritesi — inertia/trap burada uygulanir."""
    c = dict(collapse)
    inertia = compute_state_inertia(structure, c)
    trap = detect_liquidity_trap(events, liquidity, structure)

    c["inertia"] = inertia
    c["liquidity_trap"] = trap

    mode = str(c.get("mode") or "")
    dom = str(c.get("dominant_bias") or "")

    if inertia.get("continuation") and mode == "STRUCTURE_CONTROLLED":
        if dom == "bearish" and inertia.get("side") == "bearish":
            c["inertia_continuation_short"] = True
            c["event_confirms_short"] = c.get("event_confirms_short") or True
            c["allow_trade"] = True
        elif dom == "bullish" and inertia.get("side") == "bullish":
            c["inertia_continuation_long"] = True
            c["event_confirms_long"] = c.get("event_confirms_long") or True
            c["allow_trade"] = True

    if trap.get("reversal_long"):
        c["trap_reversal_long"] = True
        c["event_confirms_long"] = True
        if mode == "STRUCTURE_CONTROLLED" and c.get("override_structure"):
            c["allow_trade"] = True
    if trap.get("reversal_short"):
        c["trap_reversal_short"] = True
        c["event_confirms_short"] = True

    parts = [str(c.get("detail") or "")]
    if inertia.get("continuation"):
        parts.append(f"inertia={inertia.get('side')} {inertia.get('score')}")
    if trap.get("active"):
        parts.append(trap.get("detail", "trap"))
    c["detail"] = " | ".join(p for p in parts if p)
    return c
