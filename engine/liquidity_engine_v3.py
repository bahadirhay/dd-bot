"""
engine/liquidity_engine_v3.py — Multi-scale likidite (20–32 micro + 48–96 macro) → tek harita.

structure.trend ile filtrelenir.
"""
from __future__ import annotations

from core.config import cfg
from engine.zone_liquidity_v3 import (
    annotate_liquidity_pools,
    detect_liquidity_pools,
    detect_liquidity_vacuums,
    global_liquidity_bias,
)
from engine.liquidity_quality_v3 import enrich_pools_quality
from engine.v3_common import bars_15m


def _micro_bars(bars: list[dict]) -> list[dict]:
    n = int(getattr(cfg, "V3_LIQ_MICRO_BARS", 24) or 24)
    n = max(16, min(n, 32))
    return list(bars[-n:])


def _macro_bars(bars: list[dict]) -> list[dict]:
    n = int(getattr(cfg, "V3_LIQ_MACRO_BARS", 72) or 72)
    n = max(48, min(n, 96))
    return list(bars[-n:])


def _pool_to_level(p: dict, scale: str) -> dict:
    return {
        "price": float(p.get("price", 0) or 0),
        "low": float(p.get("zone_low", p.get("price", 0)) or 0),
        "high": float(p.get("zone_high", p.get("price", 0)) or 0),
        "score": int(p.get("score", 0) or 0),
        "tag": str(p.get("tag") or ""),
        "side": str(p.get("side") or ""),
        "tested": bool(p.get("tested")),
        "swept": bool(p.get("swept")),
        "untouched": bool(p.get("untouched")),
        "scale": scale,
    }


def _merge_pools(micro: list[dict], macro: list[dict], px: float) -> list[dict]:
    by_price: dict[float, dict] = {}
    for p in micro + macro:
        pr = round(float(p.get("price", 0) or 0), 2)
        if pr <= 0:
            continue
        prev = by_price.get(pr)
        if not prev or int(p.get("score", 0) or 0) > int(prev.get("score", 0) or 0):
            merged = dict(p)
            merged["scales"] = sorted(
                set(list((prev or {}).get("scales", [])) + [str(p.get("scale", "macro"))])
            )
            by_price[pr] = merged
        elif prev:
            prev["scales"] = sorted(
                set(list(prev.get("scales", [])) + [str(p.get("scale", "macro"))])
            )
    out = list(by_price.values())
    out.sort(key=lambda x: abs(float(x.get("price", 0) or 0) - px))
    return out


def _filter_by_structure(pools: list[dict], structure: dict, px: float) -> list[dict]:
    trend = str(structure.get("trend") or "range")
    out = []
    for p in pools:
        pr = float(p.get("price", 0) or 0)
        if pr <= 0:
            continue
        if trend == "bearish" and p.get("untouched") and pr > px:
            p = dict(p)
            p["score"] = min(100, int(p.get("score", 0) or 0) + 5)
        if trend == "bullish" and p.get("untouched") and pr < px:
            p = dict(p)
            p["score"] = min(100, int(p.get("score", 0) or 0) + 5)
        out.append(p)
    return out


def compute_liquidity(
    price: float,
    structure: dict,
    bars15: list[dict] | None = None,
) -> dict:
    px = float(price or 0)
    bars = list(bars15 or bars_15m(96))
    micro = _micro_bars(bars)
    macro = _macro_bars(bars)

    raw_micro = detect_liquidity_pools(px, micro)
    raw_macro = detect_liquidity_pools(px, macro)
    for p in raw_micro:
        p["scale"] = "micro"
    for p in raw_macro:
        p["scale"] = "macro"

    merged = _merge_pools(
        annotate_liquidity_pools(px, raw_micro, micro),
        annotate_liquidity_pools(px, raw_macro, macro),
        px,
    )
    merged = _filter_by_structure(merged, structure, px)
    merged = enrich_pools_quality(merged, bars, px)

    highs = [
        _pool_to_level(p, ",".join(p.get("scales", ["macro"])))
        for p in merged
        if float(p.get("price", 0) or 0) > px
    ]
    lows = [
        _pool_to_level(p, ",".join(p.get("scales", ["macro"])))
        for p in merged
        if float(p.get("price", 0) or 0) < px
    ]
    highs.sort(key=lambda x: x["price"])
    lows.sort(key=lambda x: -x["price"])

    sweep_zones: list[dict] = []
    for p in merged:
        if p.get("swept"):
            sweep_zones.append(_pool_to_level(p, ",".join(p.get("scales", []))))

    vacuums = detect_liquidity_vacuums(px, macro)
    bias = global_liquidity_bias(merged, px)

    quality_scores = [int(p.get("quality_score", 0) or 0) for p in merged if p.get("quality_score")]
    avg_quality = int(sum(quality_scores) / len(quality_scores)) if quality_scores else 0
    low_quality = sum(1 for p in merged if str(p.get("quality", "")) == "LOW")

    return {
        "highs": highs,
        "lows": lows,
        "sweep_zones": sweep_zones,
        "pools": merged,
        "vacuums": vacuums[:5],
        "bias": bias,
        "avg_quality": avg_quality,
        "low_quality_pools": low_quality,
        "micro_bars": len(micro),
        "macro_bars": len(macro),
        "window": "liquidity",
    }


def liquidity_log_line(liq: dict | None) -> str:
    L = liq or {}
    h = L.get("highs") or []
    lo = L.get("lows") or []
    sw = L.get("sweep_zones") or []
    top_h = h[0] if h else {}
    top_l = lo[0] if lo else {}
    bh = str((L.get("bias") or {}).get("bias") or "NEUTRAL")
    aq = int(L.get("avg_quality", 0) or 0)
    return (
        f"[LIQUIDITY] bias={bh} qual={aq} highs={len(h)} lows={len(lo)} sweeps={len(sw)} | "
        f"ust={top_h.get('price', 0):.0f}"
        f"{' untouched' if top_h.get('untouched') else ''} "
        f"alt={top_l.get('price', 0):.0f}"
        f"{' swept' if top_l.get('swept') else ''}"
    )
