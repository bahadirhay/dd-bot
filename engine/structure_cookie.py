"""
engine/structure_cookie.py — Güncel çerez S/R (destek/direnç odaklı stratejinin harita katmanı).

Katmanlar:
- Makro (≈24h): işlem bandı kenarları — direnç kümesi, destek (çoklu impuls + küme)
- Meso (≈8h): giriş rafı ince ayarı
- 1m: mikro teyit

Spec: docs/STRUCTURE_COOKIE.md
"""
from __future__ import annotations

from core.config import cfg
from core.state import state


def _market_regime() -> str:
    s15 = (state.structure_15m or "").upper()
    tv = state.trend_view or {}
    phase = (tv.get("phase") or "").lower()
    strength = float(tv.get("strength") or 0)

    if s15 in ("UP", "DOWN") and strength >= 55:
        return "trend"
    if s15 == "UNCLEAR" or phase in ("range",):
        return "range"
    if phase in ("drop", "rise", "downtrend", "uptrend"):
        return "trend"
    return "range"


def cookie_bar_count(layer: str = "meso", regime: str | None = None) -> int:
    """layer: macro | meso | trend_meso"""
    regime = regime or _market_regime()
    if layer == "macro":
        return int(getattr(cfg, "STRUCTURE_COOKIE_BARS_MACRO", 96) or 96)
    if layer == "trend_meso" or (layer == "meso" and regime == "trend"):
        return int(getattr(cfg, "STRUCTURE_COOKIE_BARS_TREND", 16) or 16)
    return int(getattr(cfg, "STRUCTURE_COOKIE_BARS", 32) or 32)


def _bars_15m_n(n: int) -> list[dict]:
    try:
        from engine.structure import get_bars_15m

        return get_bars_15m(n) or []
    except Exception:
        return []


def cookie_bars_15m(regime: str | None = None) -> list[dict]:
    """Geriye uyumluluk — meso pencere."""
    return _bars_15m_n(cookie_bar_count("meso", regime))


def _cluster_bps(px: float) -> float:
    from engine.structure_thresholds import bar_noise_bps

    mul = float(getattr(cfg, "STRUCTURE_COOKIE_CLUSTER_MUL", 2.5) or 2.5)
    return max(bar_noise_bps(px) * mul, 12.0)


def _cluster_prices(prices: list[float], bps: float) -> list[list[float]]:
    if not prices or bps <= 0:
        return []
    sorted_p = sorted(prices)
    clusters: list[list[float]] = []
    cur = [sorted_p[0]]
    for p in sorted_p[1:]:
        ref = cur[-1]
        if ref > 0 and abs(p - ref) / ref * 10000.0 <= bps:
            cur.append(p)
        else:
            clusters.append(cur)
            cur = [p]
    clusters.append(cur)
    return clusters


def _median(vals: list[float]) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2.0


def _percentile(vals: list[float], q: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    q = max(0.0, min(1.0, q))
    idx = q * (len(s) - 1)
    lo = int(idx)
    hi = min(len(s) - 1, lo + 1)
    frac = idx - lo
    return s[lo] * (1.0 - frac) + s[hi] * frac


def _collect_high_touches(bars: list[dict]) -> list[float]:
    out: list[float] = []
    for i, b in enumerate(bars):
        h = float(b.get("high", 0) or 0)
        if h <= 0:
            continue
        out.append(h)
        if 0 < i < len(bars) - 1:
            h_prev = float(bars[i - 1].get("high", 0) or 0)
            h_next = float(bars[i + 1].get("high", 0) or 0)
            if h >= h_prev and h >= h_next:
                out.append(h)
    return out


def _collect_low_touches(bars: list[dict]) -> list[float]:
    out: list[float] = []
    for i, b in enumerate(bars):
        lo = float(b.get("low", 0) or 0)
        if lo <= 0:
            continue
        out.append(lo)
        if 0 < i < len(bars) - 1:
            lo_prev = float(bars[i - 1].get("low", 0) or 0)
            lo_next = float(bars[i + 1].get("low", 0) or 0)
            if lo <= lo_prev and lo <= lo_next:
                out.append(lo)
    return out


def _impulse_moves(bars: list[dict], direction: str) -> list[tuple[int, float]]:
    """(index, move_size) — down = satış impulsu, up = alım impulsu."""
    if len(bars) < 2:
        return []
    out: list[tuple[int, float]] = []
    for i in range(1, len(bars)):
        prev_c = float(bars[i - 1].get("close", 0) or bars[i - 1].get("open", 0) or 0)
        hi = float(bars[i].get("high", 0) or 0)
        lo = float(bars[i].get("low", 0) or 0)
        if direction == "down":
            move = prev_c - lo
        else:
            move = hi - prev_c
        if move > 0:
            out.append((i, move))
    out.sort(key=lambda t: -t[1])
    return out


def _shelf_lows_after_index(
    bars: list[dict], idx: int, px: float, inv: float
) -> list[float]:
    seg = bars[idx:] if idx < len(bars) else bars
    cap = px * 0.9998
    return sorted(
        float(b.get("low", 0) or 0)
        for b in seg
        if float(b.get("low", 0) or 0) < cap
        and (inv <= 0 or float(b.get("low", 0) or 0) > inv * 1.001)
    )


def _body_support_candidates(bars: list[dict], px: float, inv: float) -> list[float]:
    """
    Destek = yalnızca wick değil, kapanış/body kabul bölgesi.
    Blue line gibi tekrar tekrar üstünde kapanılan rafları yakalar.
    """
    if px <= 0:
        return []
    cap = px * 0.9998
    out: list[float] = []
    for b in bars:
        o = float(b.get("open", 0) or 0)
        c = float(b.get("close", 0) or 0)
        lo = float(b.get("low", 0) or 0)
        if c <= 0:
            continue
        body_lo = min(o, c) if o > 0 else c
        vals = [c, body_lo]
        for v in vals:
            if 0 < v < cap and (inv <= 0 or v > inv * 1.001):
                # close/body adaylarını iki kez ekle: wick yerine acceptance öne çıksın
                out.extend([v, v])
        if 0 < lo < cap and abs(c - lo) / max(c, 1e-9) < 0.0025:
            out.append(lo)
    return out


def _body_resistance_candidates(bars: list[dict], px: float) -> list[float]:
    """
    Direnç = yalnızca wick high değil, kapanış/body acceptance tavanı.
    Üstte tekrar tekrar reddedilen rafları yakalar.
    """
    if px <= 0:
        return []
    floor = px * 1.0002
    out: list[float] = []
    for b in bars:
        o = float(b.get("open", 0) or 0)
        c = float(b.get("close", 0) or 0)
        hi = float(b.get("high", 0) or 0)
        if c <= 0:
            continue
        body_hi = max(o, c) if o > 0 else c
        vals = [c, body_hi]
        for v in vals:
            if v > floor:
                # close/body adaylarını iki kez ekle: acceptance wick'e göre ağır bassın
                out.extend([v, v])
        if hi > floor and abs(hi - c) / max(c, 1e-9) < 0.0025:
            out.append(hi)
    return out


def _support_cluster_stats(
    bars: list[dict], level: float, px: float, inv: float
) -> tuple[int, int, int]:
    tol_bps = max(_cluster_bps(px) * 0.55, 8.0)
    band_lo = level * (1.0 - tol_bps / 10000.0)
    band_hi = level * (1.0 + tol_bps / 10000.0)
    dwell = rejects = closes_below = 0
    for b in bars:
        o = float(b.get("open", 0) or 0)
        c = float(b.get("close", 0) or 0)
        lo = float(b.get("low", 0) or 0)
        if c <= 0:
            continue
        body_lo = min(o, c) if o > 0 else c
        body_hi = max(o, c) if o > 0 else c
        if (
            band_lo <= c <= band_hi
            or band_lo <= body_lo <= band_hi
            or band_lo <= body_hi <= band_hi
        ):
            dwell += 1
        if (
            lo < band_lo
            and c > level
            and (inv <= 0 or lo > inv * 0.999)
        ):
            rejects += 1
        if c < band_lo:
            closes_below += 1
    return dwell, rejects, closes_below


def _resistance_cluster_stats(
    bars: list[dict], level: float, px: float
) -> tuple[int, int, int]:
    tol_bps = max(_cluster_bps(px) * 0.55, 8.0)
    band_lo = level * (1.0 - tol_bps / 10000.0)
    band_hi = level * (1.0 + tol_bps / 10000.0)
    dwell = rejects = closes_above = 0
    for b in bars:
        o = float(b.get("open", 0) or 0)
        c = float(b.get("close", 0) or 0)
        hi = float(b.get("high", 0) or 0)
        if c <= 0:
            continue
        body_lo = min(o, c) if o > 0 else c
        body_hi = max(o, c) if o > 0 else c
        if (
            band_lo <= c <= band_hi
            or band_lo <= body_lo <= band_hi
            or band_lo <= body_hi <= band_hi
        ):
            dwell += 1
        if hi > band_hi and c < level:
            rejects += 1
        if c > band_hi:
            closes_above += 1
    return dwell, rejects, closes_above


def _support_quality(
    touches: int,
    failed_breaks: int,
    score: int,
    min_touches: int,
) -> str:
    min_fb = int(getattr(cfg, "STRUCTURE_COOKIE_MIN_FAILED_BREAKS", 2) or 2)
    if touches >= min_touches and (failed_breaks >= min_fb or score >= 12):
        return "ok"
    if touches >= 2 and (failed_breaks >= 1 or score >= 6):
        return "kısmi"
    return "zayıf_küme"


def _support_acceptance_layers(
    bars: list[dict],
    px: float,
    inv: float,
    min_touches: int,
    anchor_bars: list[dict] | None = None,
) -> tuple[dict, dict, dict]:
    """
    Üç katman:
    - micro: fiyata en yakın acceptance shelf
    - range: ana taban shelf (üstteki micro cluster hariç alt acceptance katmanı)
    - deep: range'in altındaki ikinci shelf / deep floor
    """
    anchors = anchor_bars or []
    candidates = _body_support_candidates(bars, px, inv)
    if anchors:
        anchor_cands = _body_support_candidates(anchors, px, inv)
        candidates.extend(anchor_cands)
        candidates.extend(anchor_cands)
    if not candidates:
        return {}, {}, {}

    clusters = _cluster_prices(candidates, _cluster_bps(px))
    rows: list[dict] = []
    bars_all = bars + anchors
    for cl in clusters:
        med = round(_median(cl), 2)
        if med <= 0 or med >= px * 0.9998:
            continue
        dwell, rejects, closes_below = _support_cluster_stats(bars_all, med, px, inv)
        fb = _failed_breaks_below(bars_all, med)
        n = len(cl)
        score = dwell * 4 + rejects * 5 + fb * 3 - min(3, closes_below) * 3
        rows.append(
            {
                "level": med,
                "touches": n,
                "failed_breaks": fb,
                "dwell": dwell,
                "rejects": rejects,
                "closes_below": closes_below,
                "score": score,
                "quality": _support_quality(n, fb, score, min_touches),
            }
        )
    if not rows:
        return {}, {}, {}

    rows.sort(key=lambda x: x["level"])
    micro = max(rows, key=lambda x: (x["level"], x["score"], x["touches"]))

    levels = [r["level"] for r in rows]
    # Ana support micro'ya en yakın üst cluster değil; alt acceptance katmanı olmalı.
    range_ceiling = _percentile(levels, 0.66)
    range_pool = [r for r in rows if r["level"] <= range_ceiling]
    if not range_pool and len(rows) >= 2:
        range_pool = rows[:-1]
    if not range_pool:
        range_pool = rows
    range_support = max(
        range_pool,
        key=lambda x: (x["score"], x["touches"], x["failed_breaks"], x["level"]),
    )

    deep_pool = [r for r in rows if r["level"] < range_support["level"] * 0.998]
    if deep_pool:
        deep = max(
            deep_pool,
            key=lambda x: (x["score"], x["touches"], x["failed_breaks"], x["level"]),
        )
    else:
        deep = {}

    return micro, range_support, deep


def _resistance_acceptance_layers(
    bars: list[dict],
    px: float,
    min_touches: int,
    anchor_bars: list[dict] | None = None,
) -> tuple[dict, dict, dict]:
    """
    Üç katman:
    - micro: fiyata en yakın acceptance tavanı
    - range: ana üst cap (alttaki micro cluster hariç üst acceptance katmanı)
    - deep: range'in üstündeki ikinci cap / overshoot tavanı
    """
    anchors = anchor_bars or []
    candidates = _body_resistance_candidates(bars, px)
    if anchors:
        anchor_cands = _body_resistance_candidates(anchors, px)
        candidates.extend(anchor_cands)
        candidates.extend(anchor_cands)
    if not candidates:
        return {}, {}, {}

    clusters = _cluster_prices(candidates, _cluster_bps(px))
    rows: list[dict] = []
    bars_all = bars + anchors
    for cl in clusters:
        med = round(_median(cl), 2)
        if med <= px * 1.0002:
            continue
        dwell, rejects, closes_above = _resistance_cluster_stats(bars_all, med, px)
        fb = _failed_breaks_above(bars_all, med)
        n = len(cl)
        score = dwell * 4 + rejects * 5 + fb * 3 - min(3, closes_above) * 3
        rows.append(
            {
                "level": med,
                "touches": n,
                "failed_breaks": fb,
                "dwell": dwell,
                "rejects": rejects,
                "closes_above": closes_above,
                "score": score,
                "quality": _support_quality(n, fb, score, min_touches),
            }
        )
    if not rows:
        return {}, {}, {}

    rows.sort(key=lambda x: x["level"])
    micro = min(rows, key=lambda x: (x["level"], -x["score"], -x["touches"]))

    levels = [r["level"] for r in rows]
    # Ana resistance micro'ya en yakın alt cluster değil; üst acceptance cap olmalı.
    range_floor = _percentile(levels, 0.34)
    range_pool = [r for r in rows if r["level"] >= range_floor]
    if not range_pool and len(rows) >= 2:
        range_pool = rows[1:]
    if not range_pool:
        range_pool = rows
    range_resistance = min(
        range_pool,
        key=lambda x: (-x["score"], -x["touches"], -x["failed_breaks"], x["level"]),
    )

    deep_pool = [r for r in rows if r["level"] > range_resistance["level"] * 1.002]
    if deep_pool:
        deep = min(
            deep_pool,
            key=lambda x: (-x["score"], -x["touches"], -x["failed_breaks"], x["level"]),
        )
    else:
        deep = {}

    return micro, range_resistance, deep


def _support_from_acceptance(
    bars: list[dict],
    px: float,
    inv: float,
    min_touches: int,
    anchor_bars: list[dict] | None = None,
) -> tuple[float, int, int, str]:
    range_layer = _support_acceptance_layers(
        bars, px, inv, min_touches, anchor_bars=anchor_bars
    )[1]
    if not range_layer:
        return 0.0, 0, 0, "yok"
    return (
        float(range_layer["level"]),
        int(range_layer["touches"]),
        int(range_layer["failed_breaks"]),
        str(range_layer["quality"]),
    )


def _resistance_from_acceptance(
    bars: list[dict],
    px: float,
    min_touches: int,
    anchor_bars: list[dict] | None = None,
) -> tuple[float, int, int, str]:
    range_layer = _resistance_acceptance_layers(
        bars, px, min_touches, anchor_bars=anchor_bars
    )[1]
    if not range_layer:
        return 0.0, 0, 0, "yok"
    return (
        float(range_layer["level"]),
        int(range_layer["touches"]),
        int(range_layer["failed_breaks"]),
        str(range_layer["quality"]),
    )


def _support_from_impulses(
    bars: list[dict], px: float, inv: float, min_touches: int
) -> tuple[float, int, int, str]:
    """
    Çoklu impuls → raf low'ları → küme (direnç simetrisi).
    Dönüş: (seviye, küme_boyutu, failed_breaks, kalite_etiketi)
    """
    top_n = int(getattr(cfg, "STRUCTURE_COOKIE_IMPULSE_TOP_N", 3) or 3)
    impulses = _impulse_moves(bars, "down")[:top_n]
    shelf_lows: list[float] = []
    for idx, _ in impulses:
        shelf_lows.extend(_shelf_lows_after_index(bars, idx, px, inv))

    if not shelf_lows:
        touches_l = _collect_low_touches(bars)
        clusters = _cluster_prices(touches_l, _cluster_bps(px))
        below = [c for c in clusters if _median(c) < px * 0.9998]
        if not below:
            return 0.0, 0, 0, "yok"
        pick = max(below, key=lambda c: (len(c), _median(c)))
        med = round(_median(pick), 2)
        fb = _failed_breaks_below(bars, med)
        q = "ok" if len(pick) >= min_touches else "kısmi"
        return med, len(pick), fb, q

    clusters = _cluster_prices(shelf_lows, _cluster_bps(px))
    below = [c for c in clusters if _median(c) < px * 0.9998]
    if not below:
        return 0.0, 0, 0, "yok"
    pick = max(below, key=lambda c: (len(c), _median(c)))
    med = round(_median(pick), 2)
    fb = _failed_breaks_below(bars, med)
    n = len(pick)
    if n >= min_touches and fb >= int(getattr(cfg, "STRUCTURE_COOKIE_MIN_FAILED_BREAKS", 2) or 2):
        return med, n, fb, "ok"
    if n >= 2 or fb >= 1:
        return med, n, fb, "kısmi"
    return med, n, fb, "zayıf_küme"


def _failed_breaks_below(bars: list[dict], level: float) -> int:
    """Seviye altına inip kapanış üstünde biten 15m sayısı."""
    if level <= 0 or len(bars) < 2:
        return 0
    from engine.structure_thresholds import break_threshold_price

    thresh = break_threshold_price(level, "SHORT")
    count = 0
    for b in bars:
        lo = float(b.get("low", 0) or 0)
        close = float(b.get("close", 0) or 0)
        if lo < thresh and close > level:
            count += 1
    return count


def _failed_breaks_above(bars: list[dict], level: float) -> int:
    thresh = level
    try:
        from engine.structure_thresholds import break_threshold_price

        thresh = break_threshold_price(level, "LONG")
    except Exception:
        pass
    count = 0
    for b in bars:
        hi = float(b.get("high", 0) or 0)
        close = float(b.get("close", 0) or 0)
        if hi > thresh and close < level:
            count += 1
    return count


def _pick_resistance_cluster(
    touches: list[float], px: float, min_touches: int, bars: list[dict]
) -> tuple[float, int, int, str]:
    if not touches or px <= 0:
        return 0.0, 0, 0, "yok"
    clusters = _cluster_prices(touches, _cluster_bps(px))
    if not clusters:
        return 0.0, 0, 0, "yok"

    scored = [(len(c), _median(c), c) for c in clusters]
    scored.sort(key=lambda t: (-t[0], -t[1]))

    above = [t for t in scored if t[1] > px * 1.0005]
    if not above:
        return 0.0, 0, 0, "yok"
    pick = above[0]
    count, med, _ = pick

    med = round(med, 2)
    fb = _failed_breaks_above(bars, med)
    if count < 2:
        return med, count, fb, "zayıf_küme"
    q = "ok" if count >= min_touches else "kısmi"
    return med, count, fb, q


def _edge_confidence(
    touches: int,
    failed_breaks: int,
    quality: str,
    min_touches: int,
) -> float:
    min_fb = int(getattr(cfg, "STRUCTURE_COOKIE_MIN_FAILED_BREAKS", 2) or 2)
    t_part = min(1.0, touches / max(min_touches, 1))
    fb_part = min(1.0, failed_breaks / max(min_fb, 1))
    q_part = 1.0 if quality == "ok" else (0.72 if quality == "kısmi" else 0.4)
    return round(min(1.0, (t_part * 0.55 + fb_part * 0.35) * q_part), 3)


def _1m_refine_support(s_15m: float, px: float, inv: float) -> float:
    if not getattr(cfg, "STRUCTURE_COOKIE_1M_ENABLED", True):
        return s_15m
    if s_15m <= 0 or px <= 0:
        return s_15m
    look = int(getattr(cfg, "STRUCTURE_COOKIE_1M_LOOKBACK", 20) or 20)
    try:
        from engine.bars_1m import get_bars_1m

        bars = get_bars_1m(look)
    except Exception:
        return s_15m
    if len(bars) < 3:
        return s_15m

    tol = max(px * 0.0025, abs(px - s_15m) * 0.5)
    band_lo = s_15m - tol
    band_hi = min(px * 0.9998, s_15m + tol * 2)
    lows = [
        float(b["low"])
        for b in bars
        if band_lo <= float(b["low"]) <= band_hi
        and float(b["low"]) < px * 0.9998
        and (inv <= 0 or float(b["low"]) > inv * 1.001)
    ]
    if lows:
        return round(max(lows), 2)
    return s_15m


def _1m_refine_resistance(r_15m: float, px: float) -> float:
    if not getattr(cfg, "STRUCTURE_COOKIE_1M_ENABLED", True):
        return r_15m
    if r_15m <= 0 or px <= 0:
        return r_15m
    look = int(getattr(cfg, "STRUCTURE_COOKIE_1M_LOOKBACK", 20) or 20)
    try:
        from engine.bars_1m import get_bars_1m

        bars = get_bars_1m(look)
    except Exception:
        return r_15m
    if len(bars) < 3:
        return r_15m

    tol = max(px * 0.0025, abs(r_15m - px) * 0.5)
    band_lo = max(px * 1.0002, r_15m - tol * 2)
    band_hi = r_15m + tol
    highs = [
        float(b["high"])
        for b in bars
        if band_lo <= float(b["high"]) <= band_hi
        and float(b["high"]) > px * 1.0002
    ]
    if highs:
        return round(min(highs), 2)
    return r_15m


def _cookie_min_width_bps(px: float) -> float:
    floor = float(getattr(cfg, "STRUCTURE_COOKIE_MIN_WIDTH_BPS", 90) or 90)
    cap = float(getattr(cfg, "STRUCTURE_COOKIE_MAX_MIN_WIDTH_BPS", 140) or 140)
    try:
        from engine.structure_thresholds import range_min_width_bps

        structural = range_min_width_bps(px)
    except Exception:
        structural = floor
    return max(floor, min(structural, cap))


def _channel_quality(
    s: float,
    r: float,
    px: float,
    r_conf: float,
    s_conf: float,
) -> str:
    if s <= 0 or r <= 0 or px <= 0 or r <= s:
        return "geçersiz"
    w_bps = (r - s) / px * 10000.0
    min_w = _cookie_min_width_bps(px)
    min_conf = float(getattr(cfg, "STRUCTURE_COOKIE_MIN_EDGE_CONF", 0.5) or 0.5)

    if w_bps < min_w * 0.85:
        return "dar"
    if r_conf < min_conf * 0.85:
        return "zayıf_direnç"
    if s_conf < min_conf * 0.85:
        return "zayıf_destek"
    if r_conf >= min_conf and s_conf >= min_conf and w_bps >= min_w * 0.85:
        return "ok"
    if r_conf >= min_conf * 0.7 and s_conf >= min_conf * 0.7:
        return "kısmi"
    return "zayıf_destek"


def _compute_edges(
    bars: list[dict],
    px: float,
    inv: float,
    min_t: int,
    *,
    refine_1m: bool = True,
) -> tuple[float, float, dict]:
    anchor_1h: list[dict] = []
    try:
        from engine.structure import get_bars_1h

        anchor_1h = get_bars_1h(48) or []
    except Exception:
        anchor_1h = []

    touches_h = _collect_high_touches(bars)
    r_touch, r_n_touch, r_fb_touch, r_q_touch = _pick_resistance_cluster(
        touches_h, px, min_t, bars
    )
    s_imp, s_imp_n, s_imp_fb, s_imp_q = _support_from_impulses(bars, px, inv, min_t)
    micro_res_layer, range_res_layer, deep_res_layer = _resistance_acceptance_layers(
        bars, px, min_t, anchor_bars=anchor_1h
    )
    micro_layer, range_layer, deep_layer = _support_acceptance_layers(
        bars, px, inv, min_t, anchor_bars=anchor_1h
    )
    r_acc = float(range_res_layer.get("level", 0) or 0)
    r_acc_n = int(range_res_layer.get("touches", 0) or 0)
    r_acc_fb = int(range_res_layer.get("failed_breaks", 0) or 0)
    r_acc_q = str(range_res_layer.get("quality", "yok") or "yok")
    s_acc = float(range_layer.get("level", 0) or 0)
    s_acc_n = int(range_layer.get("touches", 0) or 0)
    s_acc_fb = int(range_layer.get("failed_breaks", 0) or 0)
    s_acc_q = str(range_layer.get("quality", "yok") or "yok")

    if r_acc > 0 and r_acc_q in ("ok", "kısmi"):
        r, r_n, r_fb, r_q = r_acc, r_acc_n, r_acc_fb, r_acc_q
    else:
        r, r_n, r_fb, r_q = r_touch, r_n_touch, r_fb_touch, r_q_touch

    # Aktif destek = savunulan acceptance shelf; impuls dibi invalidasyon mantığında kalır.
    if s_acc > 0 and s_acc_q in ("ok", "kısmi"):
        s, s_n, s_fb, s_q = s_acc, s_acc_n, s_acc_fb, s_acc_q
    else:
        s, s_n, s_fb, s_q = s_imp, s_imp_n, s_imp_fb, s_imp_q

    r_conf = _edge_confidence(r_n, r_fb, r_q, min_t) if r > 0 else 0.0
    s_conf = _edge_confidence(s_n, s_fb, s_q, min_t) if s > 0 else 0.0

    if refine_1m and r > 0:
        r = _1m_refine_resistance(r, px)
    if refine_1m and s > 0:
        s = _1m_refine_support(s, px, inv)

    detail = {
        "resistance": r,
        "support": s,
        "resistance_touches": r_n,
        "support_touches": s_n,
        "resistance_failed_breaks": r_fb,
        "support_failed_breaks": s_fb,
        "resistance_quality": r_q,
        "support_quality": s_q,
        "resistance_confidence": r_conf,
        "support_confidence": s_conf,
        "resistance_acceptance": r_acc,
        "resistance_touch_cluster": r_touch,
        "micro_resistance": float(micro_res_layer.get("level", 0) or 0),
        "range_resistance": r_acc,
        "deep_resistance": float(deep_res_layer.get("level", 0) or r_touch or 0),
        "support_acceptance": s_acc,
        "micro_support": float(micro_layer.get("level", 0) or 0),
        "range_support": s_acc,
        "deep_support": float(deep_layer.get("level", 0) or s_imp or 0),
        "support_impulse": s_imp,
        "resistance_source": "acceptance"
        if r > 0 and r == r_acc and r_acc_q in ("ok", "kısmi")
        else "touch_cluster",
        "support_source": "acceptance"
        if s > 0 and s == s_acc and s_acc_q in ("ok", "kısmi")
        else "impulse",
        "resistance_reason": (
            f"range cap dwell={range_res_layer.get('dwell', 0)} "
            f"reject={range_res_layer.get('rejects', 0)} "
            f"fb={range_res_layer.get('failed_breaks', 0)}"
            if r > 0 and r == r_acc and range_res_layer
            else f"touch cluster fb={r_fb_touch}"
        ),
        "support_reason": (
            f"range shelf dwell={range_layer.get('dwell', 0)} "
            f"reject={range_layer.get('rejects', 0)} "
            f"fb={range_layer.get('failed_breaks', 0)}"
            if s > 0 and s == s_acc and range_layer
            else f"impulse shelf fb={s_imp_fb}"
        ),
        "anchor_1h_bars": len(anchor_1h),
        "refine_1m": refine_1m,
    }
    return r, s, detail


def cookie_channel(
    px: float,
    inv: float = 0.0,
    side: str = "",
) -> tuple[float, float, dict]:
    """
    (destek, direnç, meta).
    Makro kenarlar + meso raf ince ayarı (destek).
    """
    regime = _market_regime()
    macro_n = cookie_bar_count("macro", regime)
    meso_n = cookie_bar_count("meso", regime)
    min_t = int(getattr(cfg, "STRUCTURE_COOKIE_MIN_TOUCHES", 3) or 3)

    meta: dict = {
        "cookie_bars": meso_n,
        "cookie_bars_macro": macro_n,
        "cookie_hours": round(macro_n * 15 / 60, 1),
        "regime": regime,
        "source": "structure_cookie",
        "layer": "macro+meso",
        "quality": "yok",
    }

    if px <= 0:
        return 0.0, 0.0, meta

    bars_macro = _bars_15m_n(macro_n)
    if not bars_macro:
        return 0.0, 0.0, meta

    r, s, detail = _compute_edges(bars_macro, px, inv, min_t)

    bars_meso = _bars_15m_n(meso_n)
    if bars_meso and r > 0:
        r_meso, r_mn, r_mfb, r_mq = _resistance_from_acceptance(bars_meso, px, min_t)
        if r_meso <= 0:
            r_meso, r_mn, r_mfb, r_mq = _pick_resistance_cluster(
                _collect_high_touches(bars_meso), px, min_t, bars_meso
            )
        if r_meso > px * 1.0002:
            r_meso = _1m_refine_resistance(r_meso, px)
            meta["meso_resistance"] = r_meso
            if abs(r_meso - r) / px * 10000.0 < _cluster_bps(px) * 1.5:
                r = r_meso
                detail["resistance"] = r
                detail["meso_refined_resistance"] = True
    if bars_meso and s > 0:
        s_meso, s_mn, s_mfb, s_mq = _support_from_acceptance(
            bars_meso, px, inv, min_t
        )
        if s_meso <= 0:
            s_meso, s_mn, s_mfb, s_mq = _support_from_impulses(
                bars_meso, px, inv, min_t
            )
        if s_meso > 0 and s_meso < px * 0.9998:
            s_meso = _1m_refine_support(s_meso, px, inv)
            if s_meso > inv * 1.001 if inv > 0 else True:
                meta["meso_support"] = s_meso
                if abs(s_meso - s) / px * 10000.0 < _cluster_bps(px) * 1.5:
                    s = s_meso
                    detail["support"] = s
                    detail["meso_refined"] = True

    quality = _channel_quality(
        s,
        r,
        px,
        float(detail.get("resistance_confidence", 0)),
        float(detail.get("support_confidence", 0)),
    )
    min_conf = min(
        float(detail.get("resistance_confidence", 0)),
        float(detail.get("support_confidence", 0)),
    )
    meta.update(detail)
    meta["quality"] = quality
    meta["tradeable"] = quality in ("ok", "kısmi")
    meta["min_edge_confidence"] = round(min_conf, 3)
    if r > 0 and s > 0:
        meta["width_bps"] = round((r - s) / px * 10000.0, 1)

    if quality not in ("ok", "kısmi", "dar"):
        return 0.0, 0.0, meta
    if r > 0 and s > 0 and r > s * 1.0003:
        return s, r, meta
    if r > 0 and s <= 0:
        return 0.0, r, meta
    if s > 0 and r <= 0:
        return s, 0.0, meta
    return 0.0, 0.0, meta


def cookie_structural_channel(
    px: float,
    inv: float = 0.0,
) -> tuple[float, float, dict]:
    """
    Sticky yapısal band:
    - yalnızca macro pencere
    - meso/1m execution ince ayarı yok
    """
    regime = _market_regime()
    macro_n = cookie_bar_count("macro", regime)
    min_t = int(getattr(cfg, "STRUCTURE_COOKIE_MIN_TOUCHES", 3) or 3)

    meta: dict = {
        "cookie_bars": macro_n,
        "cookie_bars_macro": macro_n,
        "cookie_hours": round(macro_n * 15 / 60, 1),
        "regime": regime,
        "source": "structure_cookie",
        "layer": "macro",
        "quality": "yok",
    }

    if px <= 0:
        return 0.0, 0.0, meta

    bars_macro = _bars_15m_n(macro_n)
    if not bars_macro:
        return 0.0, 0.0, meta

    r, s, detail = _compute_edges(bars_macro, px, inv, min_t, refine_1m=False)
    quality = _channel_quality(
        s,
        r,
        px,
        float(detail.get("resistance_confidence", 0)),
        float(detail.get("support_confidence", 0)),
    )
    min_conf = min(
        float(detail.get("resistance_confidence", 0)),
        float(detail.get("support_confidence", 0)),
    )
    meta.update(detail)
    meta["quality"] = quality
    meta["tradeable"] = quality in ("ok", "kısmi")
    meta["min_edge_confidence"] = round(min_conf, 3)
    if r > 0 and s > 0:
        meta["width_bps"] = round((r - s) / px * 10000.0, 1)

    if quality not in ("ok", "kısmi", "dar"):
        return 0.0, 0.0, meta
    if r > 0 and s > 0 and r > s * 1.0003:
        return s, r, meta
    if r > 0 and s <= 0:
        return 0.0, r, meta
    if s > 0 and r <= 0:
        return s, 0.0, meta
    return 0.0, 0.0, meta


def cookie_resistance(px: float) -> float:
    _, r, meta = cookie_channel(px, 0.0)
    return r if meta.get("quality") in ("ok", "kısmi", "dar") else 0.0


def cookie_support(px: float, inv: float = 0.0) -> float:
    s, _, meta = cookie_channel(px, inv)
    return s if meta.get("quality") in ("ok", "kısmi", "dar") else 0.0


def cookie_meta() -> dict:
    px = float(state.mark_price or state.price or 0)
    _, _, meta = cookie_channel(px, 0.0)
    return meta
