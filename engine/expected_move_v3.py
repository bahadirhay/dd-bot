"""
engine/expected_move_v3.py — Beklenen hareket / R-R oncelik skoru.

Yakin S/R risk, sonraki likidite odul -> trade_priority
"""
from __future__ import annotations

from core.config import cfg
from core.state import state
from engine.zone_liquidity_v3 import detect_liquidity_pools


def compute_expected_move(
    side: str,
    price: float,
    *,
    support: float = 0.0,
    resistance: float = 0.0,
    pools: list[dict] | None = None,
) -> dict:
    """
    Ornek LONG: risk = px - support, reward = next liq above veya resistance
    trade_priority 0-100 (RR>=2 -> +20 boost mantigi)
    """
    side = str(side or "").upper()
    px = float(price or 0)
    s = float(support or 0)
    r = float(resistance or 0)
    if px <= 0:
        return {"valid": False, "trade_priority": 0, "rr": 0.0}

    pools = list(pools or getattr(state, "v3_liquidity_pools", None) or [])
    if not pools and px > 0:
        pools = detect_liquidity_pools(px)

    min_rr = float(getattr(cfg, "V3_MIN_RR_RATIO", 2.0) or 2.0)
    boost_at_rr = float(getattr(cfg, "V3_EXPECTED_MOVE_BOOST_RR", 2.0) or 2.0)
    priority_boost = int(getattr(cfg, "V3_EXPECTED_MOVE_PRIORITY_BOOST", 20) or 20)

    risk = 0.0
    reward = 0.0
    next_liq = 0.0

    if side in ("LONG", "BUY"):
        if s <= 0 or s >= px:
            return {"valid": False, "trade_priority": 0, "rr": 0.0, "reason": "destek yok"}
        risk = px - s
        targets_above = sorted(
            [float(p.get("price", 0) or 0) for p in pools if float(p.get("price", 0) or 0) > px],
        )
        if targets_above:
            next_liq = targets_above[0]
            reward = next_liq - px
        elif r > px:
            next_liq = r
            reward = r - px
        else:
            reward = risk * min_rr
    elif side in ("SHORT", "SELL"):
        if r <= 0 or r <= px:
            return {"valid": False, "trade_priority": 0, "rr": 0.0, "reason": "direnc yok"}
        risk = r - px
        targets_below = sorted(
            [float(p.get("price", 0) or 0) for p in pools if float(p.get("price", 0) or 0) < px],
            reverse=True,
        )
        if targets_below:
            next_liq = targets_below[0]
            reward = px - next_liq
        elif s < px:
            next_liq = s
            reward = px - s
        else:
            reward = risk * min_rr
    else:
        return {"valid": False, "trade_priority": 0, "rr": 0.0}

    if risk <= 0:
        return {"valid": False, "trade_priority": 0, "rr": 0.0}

    rr = reward / risk
    priority = max(0, min(100, int(rr * 15)))
    if rr >= boost_at_rr:
        priority = min(100, priority + priority_boost)

    vac = float(getattr(state, "v3_vacuum_score", 0) or 0)
    if vac >= 60 and side in ("SHORT", "SELL") and next_liq < px:
        priority = min(100, priority + int(vac * 0.15))

    return {
        "valid": True,
        "side": side,
        "risk_usd": round(risk, 2),
        "reward_usd": round(reward, 2),
        "next_liquidity": round(next_liq, 2),
        "rr": round(rr, 2),
        "trade_priority": priority,
        "meets_min_rr": rr >= min_rr,
    }


def expected_move_blocks(side: str, price: float, **kwargs) -> tuple[bool, str]:
    """RR cok dusukse islem onceligini dusur / blok."""
    em = compute_expected_move(side, price, **kwargs)
    if not em.get("valid"):
        return False, ""
    min_rr = float(getattr(cfg, "V3_MIN_RR_RATIO", 2.0) or 2.0)
    min_pri = int(getattr(cfg, "V3_EXPECTED_MOVE_MIN_PRIORITY", 25) or 25)
    if em.get("meets_min_rr"):
        return False, ""
    if int(em.get("trade_priority", 0) or 0) < min_pri:
        return (
            True,
            f"Expected move zayif: RR={em.get('rr')} risk={em.get('risk_usd')} "
            f"odul={em.get('reward_usd')} oncelik={em.get('trade_priority')} "
            f"(min RR {min_rr}).",
        )
    return False, ""
