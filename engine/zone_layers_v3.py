"""
engine/zone_layers_v3.py — Katmanli zone haritasi (supply major/mid, demand weak/liq).

Grafikteki swing tepeler/dipler: sabit fiyat araligi yok; son 96×15m yapı + zone skoru.
"""
from __future__ import annotations

from core.config import cfg
from engine.zone_engine_v3 import LC_ACTIVE, LC_BROKEN, LC_TRANSITION, get_zones
from engine.v3_common import bars_15m


def _band_from_zone(z: dict, label: str) -> dict:
    c = float(z.get("center", 0) or 0)
    lo = float(z.get("zone_low", 0) or 0)
    hi = float(z.get("zone_high", 0) or 0)
    if lo <= 0 or hi <= lo:
        half = c * 0.0025
        lo, hi = c - half, c + half
    return {
        "label": label,
        "center": c,
        "low": lo,
        "high": hi,
        "strength": int(z.get("strength", 0) or 0),
        "lifecycle": str(z.get("lifecycle") or ""),
        "zone_status": str(z.get("status") or z.get("role") or ""),
        "id": str(z.get("id") or ""),
    }


def _band_from_pool(pool: dict, label: str) -> dict:
    p = float(pool.get("price", 0) or 0)
    lo = float(pool.get("zone_low", 0) or p * 0.998)
    hi = float(pool.get("zone_high", 0) or p * 1.002)
    return {
        "label": label,
        "center": p,
        "low": lo,
        "high": hi,
        "strength": int(pool.get("score", 0) or 0),
        "lifecycle": "",
        "zone_status": str(pool.get("tag") or "liquidity"),
        "id": str(pool.get("tag") or label),
        "tested": bool(pool.get("tested")),
        "swept": bool(pool.get("swept")),
        "untouched": bool(pool.get("untouched")),
    }


def _bar_range(bars: list[dict], lookback: int = 96) -> tuple[float, float]:
    recent = bars[-lookback:] if len(bars) > lookback else bars
    if not recent:
        return 0.0, 0.0
    lows = [float(b.get("low", 0) or 0) for b in recent if float(b.get("low", 0) or 0) > 0]
    highs = [float(b.get("high", 0) or 0) for b in recent if float(b.get("high", 0) or 0) > 0]
    if not lows or not highs:
        return 0.0, 0.0
    return min(lows), max(highs)


def _zone_weight(z: dict) -> float:
    st = int(z.get("strength", 0) or 0)
    touches = int(z.get("touch_count", 0) or 0)
    return st + touches * 8


def _pick_resistance_major(
    zones: list[dict], px: float, bars: list[dict]
) -> dict | None:
    """
    Grafikteki ust tepe / supply major — en yakin mikro direnc degil.
    Son 96 bar tepesine yakin, fiyatin uzerindeki en guclu direnc zone.
    """
    bar_lo, bar_hi = _bar_range(bars, 96)
    if bar_hi <= px:
        bar_hi = px * 1.02
    floor = max(px * 1.008, bar_hi * 0.97)
    cands = [
        z
        for z in zones
        if str(z.get("role") or "") == "resistance"
        and float(z.get("center", 0) or 0) > px
        and float(z.get("center", 0) or 0) >= floor
    ]
    if not cands:
        cands = [
            z
            for z in zones
            if str(z.get("role") or "") == "resistance"
            and float(z.get("center", 0) or 0) > px
        ]
    if not cands:
        if bar_hi > px:
            return {
                "center": bar_hi,
                "zone_low": bar_hi * 0.998,
                "zone_high": bar_hi * 1.002,
                "strength": 65,
                "lifecycle": LC_ACTIVE,
                "status": "swing_high",
                "role": "resistance",
                "id": "bar_swing_high",
            }
        return None
    return max(
        cands,
        key=lambda z: (_zone_weight(z), float(z.get("center", 0) or 0)),
    )


def _pick_mid_supply(
    zones: list[dict], px: float, major_center: float
) -> dict | None:
    """Tepki / kirilim bolgesi — major ile fiyat arasindaki anlamli direnc veya kirilan raf."""
    mc = float(major_center or 0)
    if mc <= px:
        return None
    gap = max((mc - px) * 0.12, px * 0.003)
    cands = [
        z
        for z in zones
        if px + gap < float(z.get("center", 0) or 0) < mc - gap
        and (
            str(z.get("role") or "") == "resistance"
            or str(z.get("lifecycle") or "") in (LC_BROKEN, LC_TRANSITION)
        )
    ]
    if not cands:
        pivot = px + (mc - px) * 0.45
        near = [
            z
            for z in zones
            if abs(float(z.get("center", 0) or 0) - pivot) <= max(px * 0.006, 8.0)
        ]
        cands = near
    if not cands:
        return None
    return max(cands, key=lambda z: (_zone_weight(z), float(z.get("center", 0) or 0)))


def _pick_support_weak(zones: list[dict], px: float) -> dict | None:
    """Zayif toparlanma / demand — fiyat altindaki en yuksek anlamli destek."""
    cands = [
        z
        for z in zones
        if str(z.get("role") or "") == "support"
        and str(z.get("lifecycle") or "") in (LC_ACTIVE, LC_TRANSITION, LC_BROKEN)
    ]
    if not cands:
        return None
    below = [z for z in cands if float(z.get("center", 0) or 0) < px * 0.998]
    if below:
        return max(below, key=lambda z: (_zone_weight(z), float(z.get("center", 0) or 0)))
    above = [z for z in cands if float(z.get("center", 0) or 0) >= px]
    if above:
        return min(above, key=lambda z: float(z.get("center", 0) or 0))
    return None


def _pick_demand_liq(
    pools: list[dict], bars: list[dict], px: float
) -> dict | None:
    """Likidite / sweep dibi — grafikteki alt fitil (or. 1954-1960)."""
    liq_pool = None
    for p in pools:
        tag = str(p.get("tag") or "")
        if tag.startswith("eq_low") or tag == "swing_low":
            if liq_pool is None or float(p.get("price", 0) or 0) < float(
                liq_pool.get("price", 0) or 0
            ):
                liq_pool = p
    lookback = max(int(getattr(cfg, "V3_EXTREME_FALLBACK_BARS", 24) or 24), 20)
    recent = bars[-lookback:] if len(bars) > lookback else bars
    wick = 0.0
    if recent:
        wick = min(
            float(b.get("low", 0) or 0)
            for b in recent
            if float(b.get("low", 0) or 0) > 0
        )
    bar_lo, _ = _bar_range(bars, 96)
    candidates: list[tuple[float, dict]] = []
    if liq_pool:
        candidates.append((float(liq_pool.get("price", 0) or 0), liq_pool))
    if wick > 0 and wick < px:
        candidates.append(
            (
                wick,
                {
                    "price": wick,
                    "zone_low": wick * 0.998,
                    "zone_high": wick * 1.002,
                    "tag": "wick_low",
                    "score": 70,
                },
            )
        )
    if bar_lo > 0 and bar_lo < px:
        candidates.append(
            (
                bar_lo,
                {
                    "price": bar_lo,
                    "zone_low": bar_lo * 0.998,
                    "zone_high": bar_lo * 1.002,
                    "tag": "bar_swing_low",
                    "score": 68,
                },
            )
        )
    if not candidates:
        return None
    price, pool = min(candidates, key=lambda x: x[0])
    return pool


def build_zone_layers(
    zones: list[dict] | None = None,
    bars15: list[dict] | None = None,
    price: float = 0,
    pools: list[dict] | None = None,
) -> dict:
    px = float(price or 0)
    zones = list(zones or get_zones())
    bars = list(bars15 or bars_15m(96))
    pools = list(pools or [])

    major_z = _pick_resistance_major(zones, px, bars)
    major_c = float(major_z.get("center", 0) or 0) if major_z else 0.0
    mid_z = _pick_mid_supply(zones, px, major_c) if major_c > px else None
    weak_z = _pick_support_weak(zones, px)
    liq_pool = _pick_demand_liq(pools, bars, px)

    layers: dict = {}
    if major_z:
        layers["supply_major"] = _band_from_zone(major_z, "SUPPLY_MAJOR")
    if mid_z:
        layers["supply_mid"] = _band_from_zone(mid_z, "SUPPLY_MID")
    if weak_z:
        layers["demand_weak"] = _band_from_zone(weak_z, "DEMAND_WEAK")
    if liq_pool:
        layers["demand_liq"] = _band_from_pool(liq_pool, "DEMAND_LIQ")

    return layers


def layer_to_level_dict(layer: dict, kind: str) -> dict:
    c = float(layer.get("center", 0) or 0)
    return {
        "price": c,
        "zone_low": float(layer.get("low", 0) or 0),
        "zone_high": float(layer.get("high", 0) or 0),
        "kind": kind,
        "zone_status": str(layer.get("zone_status") or layer.get("label") or "").lower(),
        "lifecycle": str(layer.get("lifecycle") or "ACTIVE"),
        "layer": str(layer.get("label") or ""),
        "strength": "STRONG" if int(layer.get("strength", 0) or 0) >= 60 else "MEDIUM",
        "lifecycle_strength": int(layer.get("strength", 0) or 0),
        "score": max(6, int(layer.get("strength", 0) or 0) // 6),
    }


def layers_log_line(layers: dict | None) -> str:
    L = layers or {}

    def _fmt(key: str) -> str:
        b = L.get(key) or {}
        if not b:
            return f"{key}=—"
        return f"{key}={b.get('low', 0):.0f}-{b.get('high', 0):.0f}"

    return "[LAYERS] " + " | ".join(
        [_fmt("supply_major"), _fmt("supply_mid"), _fmt("demand_weak"), _fmt("demand_liq")]
    )


def build_trade_map(
    story: dict,
    layers: dict,
    price: float,
) -> dict:
    px = float(price or 0)
    bias = str(story.get("bias") or "NEUTRAL")
    sm = layers.get("supply_major") or {}
    mid = layers.get("supply_mid") or {}
    dw = layers.get("demand_weak") or {}
    dl = layers.get("demand_liq") or {}

    def _in_band(layer: dict) -> bool:
        if not layer or px <= 0:
            return False
        return float(layer.get("low", 0) or 0) <= px <= float(layer.get("high", 0) or 0)

    ideas: list[str] = []
    if bias == "BEAR":
        if sm:
            ideas.append(f"major supply {sm.get('low', 0):.0f}-{sm.get('high', 0):.0f}")
        if mid:
            ideas.append(f"mid supply retest {mid.get('low', 0):.0f}-{mid.get('high', 0):.0f}")
        if dw:
            ideas.append(f"weak demand {dw.get('low', 0):.0f}-{dw.get('high', 0):.0f}")
    if dl:
        ideas.append(f"liq sweep {dl.get('low', 0):.0f}-{dl.get('high', 0):.0f}")

    return {
        "bias": bias,
        "ideas": ideas,
        "in_supply_major": _in_band(sm),
        "in_supply_mid": _in_band(mid),
        "in_demand_weak": _in_band(dw),
        "in_demand_liq": _in_band(dl),
        "message": ideas[0] if ideas else "",
    }
