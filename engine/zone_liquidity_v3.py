"""
engine/zone_liquidity_v3.py — Likidite havuzu tespiti ve zone hedef skoru.

equal highs/lows, haftalik tepe/dip (1h), yakin swing kumeleri.
"""
from __future__ import annotations

from statistics import mean

from core.config import cfg
from engine.liquidity_map_v3 import _cluster_tolerance
from engine.v3_common import avg_body, bars_15m, bars_1h

from core.logger import get_logger

log = get_logger("ZoneLiqV3")


def _bar_volume(bar: dict) -> float:
    return float(bar.get("volume", 0) or bar.get("v", 0) or 0)


def _pool_score(base: int, *, dist_bps: float, tag: str) -> int:
    sc = base
    if tag in ("weekly_high", "weekly_low"):
        sc += 15
    if tag.startswith("eq_"):
        sc += 12
    if dist_bps < 80:
        sc += 8
    elif dist_bps < 150:
        sc += 4
    return max(0, min(100, sc))


def detect_liquidity_pools(
    price: float,
    bars15: list[dict] | None = None,
    bars1h: list[dict] | None = None,
) -> list[dict]:
    """Ust/alt likidite hedefleri (0-100 skor)."""
    px = float(price or 0)
    if px <= 0:
        return []
    bars15 = list(bars15 or bars_15m(96))
    bars1h = list(bars1h or bars_1h(168))
    pools: list[dict] = []

    def add(price_level: float, side: str, base: int, tag: str) -> None:
        if price_level <= 0:
            return
        dist_bps = abs(px - price_level) / px * 10000.0
        sc = _pool_score(base, dist_bps=dist_bps, tag=tag)
        pools.append(
            {
                "price": round(price_level, 2),
                "side": side,
                "score": sc,
                "tag": tag,
                "distance_bps": round(dist_bps, 1),
            }
        )

    week_n = max(int(getattr(cfg, "V3_LIQ_WEEKLY_BARS", 168) or 168), 24)
    if bars1h:
        recent = bars1h[-week_n:]
        highs = [float(b.get("high", 0) or 0) for b in recent]
        lows = [float(b.get("low", 0) or 0) for b in recent]
        if highs:
            wh = max(highs)
            if wh > px:
                add(wh, "above", 78, "weekly_high")
            else:
                add(wh, "below", 55, "weekly_high")
        if lows:
            wl = min(lows)
            if wl < px:
                add(wl, "below", 78, "weekly_low")
            else:
                add(wl, "above", 55, "weekly_low")

    if len(bars15) >= 12:
        tol = _cluster_tolerance(bars15, px)
        look = bars15[-48:]
        highs = [float(b.get("high", 0) or 0) for b in look if float(b.get("high", 0) or 0) > 0]
        lows = [float(b.get("low", 0) or 0) for b in look if float(b.get("low", 0) or 0) > 0]

        def cluster_levels(values: list[float], side: str, tag: str) -> None:
            if len(values) < 2:
                return
            values = sorted(values)
            i = 0
            while i < len(values):
                cluster = [values[i]]
                j = i + 1
                while j < len(values) and abs(values[j] - values[i]) <= tol:
                    cluster.append(values[j])
                    j += 1
                if len(cluster) >= max(int(getattr(cfg, "V3_EQUAL_CLUSTER_MIN", 2) or 2), 2):
                    lvl = mean(cluster)
                    add(lvl, side, 70, tag)
                i = j if j > i + 1 else i + 1

        cluster_levels(highs, "above", "eq_high")
        cluster_levels(lows, "below", "eq_low")

        sh = max(highs) if highs else 0
        sl = min(lows) if lows else 0
        if sh > px:
            add(sh, "above", 62, "swing_high")
        if sl < px:
            add(sl, "below", 62, "swing_low")

    vacuums = detect_liquidity_vacuums(px, bars15)
    for v in vacuums:
        pools.append(v)

    pools.sort(key=lambda p: (-int(p["score"]), p["distance_bps"]))
    return pools


def detect_liquidity_vacuums(
    price: float,
    bars15: list[dict] | None = None,
) -> list[dict]:
    """
    Hizli tek yonlu hareket, ara bolgede reaksiyon yok -> vacuum_score.

    Ornek: 1965 -> 1935 arasi bosluk; kirilim sonrasi hizli kosu riski.
    """
    px = float(price or 0)
    bars = list(bars15 or bars_15m(64))
    if px <= 0 or len(bars) < 8:
        return []

    body = avg_body(bars[-24:]) or avg_body(bars) or 1.0
    min_span = body * float(getattr(cfg, "V3_VACUUM_MIN_SPAN_BODY", 3.0) or 3.0)
    max_reaction = float(getattr(cfg, "V3_VACUUM_MAX_REACTION_RATIO", 0.28) or 0.28)
    look = bars[-32:]
    vacuums: list[dict] = []

    for i in range(len(look)):
        for j in range(i + 3, min(i + 18, len(look))):
            block = look[i : j + 1]
            hi = max(float(b.get("high", 0) or 0) for b in block)
            lo = min(float(b.get("low", 0) or 0) for b in block)
            span = hi - lo
            if span < min_span:
                continue
            reactions = 0
            for b in block[1:-1]:
                rng = float(b.get("high", 0) or 0) - float(b.get("low", 0) or 0)
                if rng >= span * 0.22:
                    reactions += 1
            ratio = reactions / max(len(block) - 2, 1)
            if ratio > max_reaction:
                continue
            mid = (hi + lo) / 2.0
            vac_score = min(100, int(span / max(body, 0.01) * 6))
            if lo < px < hi:
                side = "through"
            elif hi <= px:
                side = "below"
            else:
                side = "above"
            tag = "vacuum_down" if float(block[-1].get("close", 0) or 0) < float(
                block[0].get("close", 0) or 0
            ) else "vacuum_up"
            vacuums.append(
                {
                    "price": round(mid, 2),
                    "zone_low": round(lo, 2),
                    "zone_high": round(hi, 2),
                    "side": "below" if tag == "vacuum_down" else "above",
                    "score": vac_score,
                    "tag": tag,
                    "distance_bps": round(abs(px - mid) / px * 10000.0, 1),
                    "vacuum_score": vac_score,
                    "span": round(span, 2),
                }
            )

    vacuums.sort(key=lambda v: -int(v.get("score", 0) or 0))
    deduped: list[dict] = []
    for v in vacuums:
        if deduped and abs(float(v["price"]) - float(deduped[-1]["price"])) < body * 2:
            if int(v["score"]) > int(deduped[-1]["score"]):
                deduped[-1] = v
            continue
        deduped.append(v)
    return deduped[:5]


def global_vacuum_score(
    price: float, bars15: list[dict] | None = None
) -> int:
    """0-100: asagi/yukari bosluk cekim gucu (fiyata en yakin vacuum)."""
    px = float(price or 0)
    vacs = detect_liquidity_vacuums(px, bars15)
    if not vacs:
        return 0
    best = max(vacs, key=lambda v: int(v.get("score", 0) or 0))
    dist = float(best.get("distance_bps", 999) or 999)
    sc = int(best.get("score", 0) or 0)
    if dist < 120:
        return sc
    return max(0, int(sc * (120 / max(dist, 1))))


def score_zone_liquidity_targets(
    zone_center: float,
    pools: list[dict],
    price: float,
) -> tuple[int, int, int]:
    """
    (liquidity_target_above, liquidity_target_below, liquidity_score).

    Zone merkezine gore ust/alt cekim; en guclu hedef zone skoruna yansir.
    """
    c = float(zone_center or 0)
    px = float(price or 0)
    if c <= 0 or px <= 0 or not pools:
        return 0, 0, 0

    above = 0
    below = 0
    for p in pools:
        ppx = float(p.get("price", 0) or 0)
        sc = int(p.get("score", 0) or 0)
        dist = abs(ppx - c)
        weight = sc / max(dist, c * 0.0003, 0.5)
        if ppx >= c:
            above = max(above, int(sc * (1 + min(weight / 200, 0.35))))
        else:
            below = max(below, int(sc * (1 + min(weight / 200, 0.35))))

    liq_score = max(above, below)
    return min(100, above), min(100, below), min(100, liq_score)


def annotate_liquidity_pools(
    price: float,
    pools: list[dict] | None,
    bars15: list[dict] | None = None,
    *,
    lookback: int = 48,
) -> list[dict]:
    """Her havuz: tested / swept / untouched."""
    px = float(price or 0)
    bars = list(bars15 or bars_15m(96))[-lookback:]
    out: list[dict] = []
    for p in list(pools or []):
        pool = dict(p)
        lvl = float(pool.get("price", 0) or 0)
        if lvl <= 0:
            out.append(pool)
            continue
        tol = max(lvl * 0.0015, 0.5)
        touched = False
        swept = False
        for b in bars:
            hi = float(b.get("high", 0) or 0)
            lo = float(b.get("low", 0) or 0)
            close = float(b.get("close", 0) or 0)
            if abs(hi - lvl) <= tol or abs(lo - lvl) <= tol or (lo <= lvl <= hi):
                touched = True
            if pool.get("side") == "below" or str(pool.get("tag", "")).endswith("low"):
                if lo < lvl - tol * 0.3 and close > lvl:
                    swept = True
            if pool.get("side") == "above" or str(pool.get("tag", "")).endswith("high"):
                if hi > lvl + tol * 0.3 and close < lvl:
                    swept = True
        pool["tested"] = touched
        pool["swept"] = swept
        pool["untouched"] = not touched and not swept
        if pool["untouched"] and lvl > px:
            pool["score"] = min(100, int(pool.get("score", 0) or 0) + 8)
        out.append(pool)
    return out


def global_liquidity_bias(
    pools: list[dict], price: float
) -> dict:
    """Piyasanin gitmek istedigi yon (ust/alt skor)."""
    px = float(price or 0)
    if px <= 0 or not pools:
        return {
            "bias": "NEUTRAL",
            "up_score": 0,
            "down_score": 0,
            "top_above": None,
            "top_below": None,
        }
    up = 0.0
    down = 0.0
    top_a = None
    top_b = None
    for p in pools:
        ppx = float(p.get("price", 0) or 0)
        sc = float(p.get("score", 0) or 0)
        if ppx > px:
            dist = max(ppx - px, 0.01)
            w = sc / dist
            up += w
            if top_a is None or w > top_a[1]:
                top_a = (p, w)
        elif ppx < px:
            dist = max(px - ppx, 0.01)
            w = sc / dist
            down += w
            if top_b is None or w > top_b[1]:
                top_b = (p, w)

    ratio = float(getattr(cfg, "V3_LIQ_CHASE_RATIO", 1.3) or 1.3)
    if up > down * ratio:
        bias = "UP"
    elif down > up * ratio:
        bias = "DOWN"
    else:
        bias = "NEUTRAL"
    return {
        "bias": bias,
        "up_score": round(up, 2),
        "down_score": round(down, 2),
        "top_above": top_a[0] if top_a else None,
        "top_below": top_b[0] if top_b else None,
    }


def liquidity_chase_blocks(side: str, price: float, pools: list[dict] | None = None) -> tuple[bool, str]:
    """
    Likidite hedefi ters yonde ise kovalama blok.

    Ornek: destek kirildi ama ust likidite 95 -> SHORT kovalama yok.
    """
    side = str(side or "").upper()
    px = float(price or 0)
    if px <= 0:
        return False, ""
    pools = list(pools or detect_liquidity_pools(px))
    if not pools:
        return False, ""
    g = global_liquidity_bias(pools, px)
    up = float(g.get("up_score", 0) or 0)
    down = float(g.get("down_score", 0) or 0)
    min_sc = max(int(getattr(cfg, "V3_LIQ_CHASE_MIN_SCORE", 70) or 70), 40)
    ratio = float(getattr(cfg, "V3_LIQ_CHASE_RATIO", 1.3) or 1.3)

    if side in ("SHORT", "SELL"):
        if up >= min_sc and up > down * ratio:
            top = g.get("top_above") or {}
            return (
                True,
                f"Likidite hedefi yukari (up={up:.0f} down={down:.0f}) "
                f"tag={top.get('tag')} @{top.get('price')} — short kovalama blok.",
            )
    if side in ("LONG", "BUY"):
        if down >= min_sc and down > up * ratio:
            top = g.get("top_below") or {}
            return (
                True,
                f"Likidite hedefi asagi (down={down:.0f} up={up:.0f}) "
                f"tag={top.get('tag')} @{top.get('price')} — long kovalama blok.",
            )
    return False, ""
