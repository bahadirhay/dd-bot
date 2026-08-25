"""
engine/liquidity_quality_v3.py — Likidite kalitesi (miktar degil).

equal cluster sweep = HIGH | rastgele wick = LOW
"""
from __future__ import annotations

from statistics import mean

from core.config import cfg
from engine.v3_common import avg_body


def _bar_volume(b: dict) -> float:
    return float(b.get("volume", 0) or b.get("v", 0) or 0)


def _reaction_strength(bars: list[dict], level: float, side: str) -> float:
    """Seviyeye dokunus sonrasi red/reversal gucu 0-1."""
    if not bars or level <= 0:
        return 0.0
    tol = max(level * 0.0015, 0.5)
    touches = 0
    rejections = 0
    for b in bars:
        hi = float(b.get("high", 0) or 0)
        lo = float(b.get("low", 0) or 0)
        close = float(b.get("close", 0) or 0)
        if side == "below":
            if lo <= level + tol:
                touches += 1
                if close > level + tol * 0.5:
                    rejections += 1
        else:
            if hi >= level - tol:
                touches += 1
                if close < level - tol * 0.5:
                    rejections += 1
    if touches <= 0:
        return 0.0
    return min(1.0, rejections / touches)


def score_pool_quality(
    pool: dict,
    bars15: list[dict],
    price: float,
) -> dict:
    """volume_behind + stop_density + reaction → quality_score 0-100."""
    px = float(price or 0)
    lvl = float(pool.get("price", 0) or 0)
    tag = str(pool.get("tag") or "")
    side = "below" if lvl < px else "above"

    cluster = int(pool.get("cluster_size", 0) or 0)
    if cluster <= 0 and tag.startswith("eq_"):
        cluster = 2

    stop_density = min(1.0, cluster / 4.0) if cluster else 0.3
    if tag.startswith("eq_"):
        stop_density = min(1.0, stop_density + 0.35)
    if tag == "weekly_high" or tag == "weekly_low":
        stop_density = min(1.0, stop_density + 0.25)

    look = bars15[-24:] if len(bars15) >= 24 else bars15
    vols = [_bar_volume(b) for b in look if _bar_volume(b) > 0]
    avg_vol = mean(vols) if vols else 1.0
    vol_at = 0.0
    for b in look:
        if side == "below" and float(b.get("low", 0) or 0) <= lvl * 1.002:
            vol_at += _bar_volume(b)
        elif side == "above" and float(b.get("high", 0) or 0) >= lvl * 0.998:
            vol_at += _bar_volume(b)
    volume_factor = min(1.0, vol_at / max(avg_vol * 3.0, 1e-9))

    reaction = _reaction_strength(look, lvl, side)
    swept = bool(pool.get("swept"))
    if swept and tag.startswith("eq_"):
        reaction = min(1.0, reaction + 0.35)

    quality_score = int(
        stop_density * 40 + volume_factor * 30 + reaction * 30
    )
    if tag.startswith("eq_") and swept:
        quality_score = min(100, quality_score + 15)
    if tag in ("swing_low", "swing_high") and not tag.startswith("eq_"):
        quality_score = max(0, quality_score - 20)

    if quality_score >= 65:
        label = "HIGH"
    elif quality_score >= 40:
        label = "MEDIUM"
    else:
        label = "LOW"

    return {
        "quality_score": max(0, min(100, quality_score)),
        "quality": label,
        "stop_density": round(stop_density, 2),
        "volume_factor": round(volume_factor, 2),
        "reaction_strength": round(reaction, 2),
    }


def enrich_pools_quality(
    pools: list[dict],
    bars15: list[dict],
    price: float,
) -> list[dict]:
    out = []
    for p in pools:
        q = score_pool_quality(p, bars15, price)
        merged = dict(p)
        merged.update(q)
        base = int(merged.get("score", 0) or 0)
        qmult = {"HIGH": 1.15, "MEDIUM": 1.0, "LOW": 0.72}.get(q["quality"], 1.0)
        merged["score"] = min(100, int(base * qmult))
        merged["quality_adjusted_score"] = merged["score"]
        out.append(merged)
    return out
