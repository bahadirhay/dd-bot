"""
engine/liquidity_map_v3.py — Cok katmanli likidite haritasi (OHLC).

- Bolge (cizgi degil): zone_low / zone_high
- Esit tepe/dip kumeleri
- 1h duvar: 15m kirilim fake mi (HTF bari cok yakin)
"""
from __future__ import annotations

from core.config import cfg
from core.logger import get_logger
from core.state import state
from engine.v3_common import avg_body, bars_15m

log = get_logger("LiqMapV3")


def zone_half_width(price: float, bars: list[dict] | None = None) -> float:
    """Seviye merkezinden bolge yaricapi (fiyat birimi)."""
    px = float(price or 0)
    if px <= 0:
        return 0.0
    recent = bars if bars else bars_15m(20)
    body = avg_body(recent) if recent else 0.0
    pct = max(
        float(getattr(cfg, "V3_ZONE_HALF_PCT", 0.0025) or 0.0025),
        0.0005,
    )
    try:
        from engine.structure_thresholds import bar_noise_bps

        bps = bar_noise_bps(px)
        pct = max(pct, bps / 10000.0 * 0.35)
    except Exception:
        pass
    return max(body * 0.5, px * pct, 0.5)


def attach_zone_bands(level: dict, bars: list[dict] | None = None) -> dict:
    px = float(level.get("price", 0) or 0)
    if px <= 0:
        return level
    half = zone_half_width(px, bars)
    level["zone_low"] = round(px - half, 2)
    level["zone_high"] = round(px + half, 2)
    level["zone_half"] = round(half, 4)
    return level


def _cluster_tolerance(bars: list[dict], price: float = 0) -> float:
    recent = bars[-30:] if bars and len(bars) >= 30 else (bars or bars_15m(30))
    body = avg_body(recent) if recent else 0.0
    px = float(price or 0)
    return max(body * 0.6, px * 0.0012 if px > 0 else 0.01, 0.5)


def boost_equal_high_low_clusters(
    levels: list[dict], bars: list[dict] | None = None
) -> list[dict]:
    """Ayni bolgede >=2 swing tepe/dip -> cluster puan."""
    if not levels:
        return levels
    bars = bars or bars_15m(40)
    min_n = max(int(getattr(cfg, "V3_EQUAL_CLUSTER_MIN", 2) or 2), 2)
    bonus = max(int(getattr(cfg, "V3_EQUAL_CLUSTER_SCORE", 3) or 3), 1)
    tol_base = _cluster_tolerance(bars)

    for kind in ("support", "resistance"):
        swings = [
            l
            for l in levels
            if str(l.get("kind") or "") == kind and float(l.get("price", 0) or 0) > 0
        ]
        if len(swings) < min_n:
            continue
        used: set[int] = set()
        for i, la in enumerate(swings):
            if i in used:
                continue
            pa = float(la.get("price", 0) or 0)
            tol = max(tol_base, pa * 0.0008)
            cluster = [i]
            for j, lb in enumerate(swings):
                if j <= i or j in used:
                    continue
                pb = float(lb.get("price", 0) or 0)
                if abs(pb - pa) <= tol:
                    cluster.append(j)
            if len(cluster) < min_n:
                continue
            for idx in cluster:
                used.add(idx)
                lvl = swings[idx]
                lvl["equal_cluster"] = True
                lvl["cluster_size"] = len(cluster)
                old = int(lvl.get("score", 0) or 0)
                lvl["score"] = old + bonus
                if old + bonus >= cfg.V3_LEVEL_SCORE_STRONG:
                    lvl["strength"] = "STRONG"
                elif old + bonus >= cfg.V3_LEVEL_SCORE_MEDIUM:
                    lvl["strength"] = "MEDIUM"
                elif old + bonus >= cfg.V3_LEVEL_SCORE_WEAK:
                    lvl["strength"] = "WEAK"
    return levels


def score_to_100(level: dict) -> int:
    """Ham skor -> 0-100 (STRONG~85+, MEDIUM~65)."""
    raw = int(level.get("score", 0) or 0)
    if level.get("is_role_flip"):
        raw += 2
    if level.get("equal_cluster"):
        raw += 1
    if level.get("is_htf"):
        raw += 2
    # Mevcut skor bandi ~3-15
    return max(0, min(100, int(raw * 6.5 + 10)))


def _distance_bps(price: float, level_price: float) -> float:
    if price <= 0 or level_price <= 0:
        return 99999.0
    return abs(price - level_price) / level_price * 10000.0


def build_liquidity_map(
    levels: list[dict], price: float, bars: list[dict] | None = None
) -> list[dict]:
    """Skorlu bolge listesi (ust/alt mesafe dahil)."""
    px = float(price or 0)
    if not levels or px <= 0:
        return []
    bars = bars or bars_15m(40)

    zones: list[dict] = []
    for lvl in levels:
        p = float(lvl.get("price", 0) or 0)
        if p <= 0:
            continue
        kind = str(lvl.get("kind") or "")
        if kind not in ("support", "resistance"):
            continue
        attach_zone_bands(lvl, bars)
        z_low = float(lvl.get("zone_low", p) or p)
        z_high = float(lvl.get("zone_high", p) or p)
        sc = score_to_100(lvl)
        dist = _distance_bps(px, p)
        tags: list[str] = []
        if lvl.get("is_htf"):
            tags.append("1h")
        if lvl.get("equal_cluster"):
            tags.append(f"eq{int(lvl.get('cluster_size', 2) or 2)}")
        if lvl.get("is_role_flip"):
            tags.append("flip")
        if lvl.get("is_shelf"):
            tags.append("shelf")
        zones.append(
            {
                "price": p,
                "zone_low": z_low,
                "zone_high": z_high,
                "kind": kind,
                "score": sc,
                "raw_score": int(lvl.get("score", 0) or 0),
                "strength": str(lvl.get("strength") or ""),
                "timeframe": str(lvl.get("timeframe") or ""),
                "distance_bps": round(dist, 1),
                "touch_count": int(lvl.get("touch_count", 0) or 0),
                "failed_break_count": int(lvl.get("failed_break_count", 0) or 0),
                "tags": tags,
            }
        )

    zones.sort(key=lambda z: (-int(z["score"]), z["distance_bps"]))
    return zones


def _htf_levels(levels: list[dict], kind: str) -> list[dict]:
    return [
        l
        for l in levels
        if l.get("is_htf")
        and str(l.get("kind") or "") == kind
        and float(l.get("price", 0) or 0) > 0
        and str(l.get("strength") or "") in ("STRONG", "MEDIUM", "WEAK")
    ]


def check_htf_wall_veto(
    side: str,
    local_level: float,
    price: float,
    levels: list[dict] | None = None,
) -> tuple[bool, str]:
    """
    15m kirilim ama 1h duvari cok yakin -> BREAKOUT sayma (fake / HTF'ye kosu).

    LONG: local R kirildi; ustteki 1h R, local'dan max_bps icinde ve fiyat altinda.
    SHORT: simetrik destek.
    """
    side = str(side or "").upper()
    local = float(local_level or 0)
    px = float(price or 0)
    if local <= 0 or px <= 0:
        return False, ""

    levels = list(levels or [])
    if not levels:
        snap = state.v3_levels or {}
        levels = list(snap.get("levels") or [])

    max_bps = max(float(getattr(cfg, "V3_HTF_WALL_MAX_BPS", 65) or 65), 15.0)
    min_sep_bps = max(float(getattr(cfg, "V3_HTF_WALL_MIN_SEP_BPS", 8) or 8), 1.0)

    if side in ("LONG", "BUY"):
        htf = _htf_levels(levels, "resistance")
        if not htf:
            return False, ""
        above = [l for l in htf if float(l["price"]) > local + local * min_sep_bps / 10000.0]
        if not above:
            return False, ""
        nearest = min(above, key=lambda l: float(l["price"]))
        r_htf = float(nearest["price"])
        gap_bps = (r_htf - local) / local * 10000.0
        if gap_bps > max_bps:
            return False, ""
        if px >= r_htf:
            return False, ""
        return (
            True,
            f"1h direnc duvari: R_15m={local:.2f} R_1h={r_htf:.2f} "
            f"mesafe={gap_bps:.0f}bps (<{max_bps:.0f}) — kirilim HTF'ye kosu, breakout degil.",
        )

    if side in ("SHORT", "SELL"):
        htf = _htf_levels(levels, "support")
        if not htf:
            return False, ""
        below = [l for l in htf if float(l["price"]) < local - local * min_sep_bps / 10000.0]
        if not below:
            return False, ""
        nearest = max(below, key=lambda l: float(l["price"]))
        s_htf = float(nearest["price"])
        gap_bps = (local - s_htf) / local * 10000.0
        if gap_bps > max_bps:
            return False, ""
        if px <= s_htf:
            return False, ""
        return (
            True,
            f"1h destek duvari: S_15m={local:.2f} S_1h={s_htf:.2f} "
            f"mesafe={gap_bps:.0f}bps (<{max_bps:.0f}) — kirilim HTF'ye kosu, breakout degil.",
        )

    return False, ""


def liquidity_target_bias(
    price: float, zones: list[dict] | None = None
) -> dict:
    """Ust/alt skorlu bolgeye mesafe — yon agirligi (bilgi)."""
    px = float(price or 0)
    zones = list(zones or state.v3_liquidity_map or [])
    if px <= 0 or not zones:
        return {"bias": "NEUTRAL", "up_score": 0.0, "down_score": 0.0}

    up = 0.0
    down = 0.0
    for z in zones:
        p = float(z.get("price", 0) or 0)
        sc = float(z.get("score", 0) or 0)
        if p <= px:
            dist = max(px - p, 0.01)
            down += sc / dist
        else:
            dist = max(p - px, 0.01)
            up += sc / dist

    if up > down * 1.25:
        bias = "UP"
    elif down > up * 1.25:
        bias = "DOWN"
    else:
        bias = "NEUTRAL"
    return {"bias": bias, "up_score": round(up, 2), "down_score": round(down, 2)}


def update_liquidity_map(
    levels: list[dict], price: float, bars: list[dict] | None = None
) -> list[dict]:
    zones = build_liquidity_map(levels, price, bars)
    state.v3_liquidity_map = zones
    if zones:
        top = zones[0]
        log.debug(
            f"[LIQMAP] {len(zones)} bolge px={price:.2f} "
            f"en guclu={top.get('kind')} {top.get('price'):.2f} score={top.get('score')}"
        )
    return zones


def top_zones_near(
    price: float, kind: str, *, limit: int = 3
) -> list[dict]:
    zones = list(state.v3_liquidity_map or [])
    out = [z for z in zones if str(z.get("kind") or "") == kind]
    if kind == "resistance":
        out = [z for z in out if float(z.get("price", 0) or 0) >= price]
        out.sort(key=lambda z: float(z.get("price", 0)))
    else:
        out = [z for z in out if float(z.get("price", 0) or 0) <= price]
        out.sort(key=lambda z: -float(z.get("price", 0)))
    return out[:limit]
