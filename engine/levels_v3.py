"""
engine/levels_v3.py

Referans klasordeki seviye mantiginin mevcut runtime'a uyarlanmis hali.
Shelf + failed break + acceptance + swing + local trigger ile seviyeleri skorlar.
"""
from __future__ import annotations

import json
from pathlib import Path
from statistics import mean, stdev

from core.config import cfg
from core.logger import get_logger
from core.state import state, effective_price
from engine.v3_common import bars_15m, bars_1h, bars_1m, avg_body

log = get_logger("LevelsV3")

_PERSIST_FILE = Path(__file__).resolve().parent.parent / "data" / "v3_active_levels.json"
_last_verbose_diag_key = ""
_last_band_unlock_log_key = ""


def _find_swing_candidates(bars: list[dict], lookback: int) -> tuple[list[dict], list[dict]]:
    highs: list[dict] = []
    lows: list[dict] = []
    end = len(bars) - lookback
    for i in range(lookback, end):
        current_high = float(bars[i].get("high", 0) or 0)
        current_low = float(bars[i].get("low", 0) or 0)
        left_bars = bars[i - lookback : i]
        right_bars = bars[i + 1 : i + lookback + 1]
        if all(current_high > float(b.get("high", 0) or 0) for b in left_bars) and all(
            current_high > float(b.get("high", 0) or 0) for b in right_bars
        ):
            highs.append(
                {
                    "price": current_high,
                    "timestamp": float(bars[i].get("ts", 0) or 0),
                    "bar_index": i,
                }
            )
        if all(current_low < float(b.get("low", 0) or 0) for b in left_bars) and all(
            current_low < float(b.get("low", 0) or 0) for b in right_bars
        ):
            lows.append(
                {
                    "price": current_low,
                    "timestamp": float(bars[i].get("ts", 0) or 0),
                    "bar_index": i,
                }
            )
    return highs, lows


def _detect_shelf(bars: list[dict], level_price: float) -> dict:
    result = {"exists": False, "bar_count": 0, "price_top": level_price, "price_bot": level_price}
    touching_indices: list[int] = []
    for i, bar in enumerate(bars):
        body_top = max(float(bar.get("open", 0) or 0), float(bar.get("close", 0) or 0))
        body_bot = min(float(bar.get("open", 0) or 0), float(bar.get("close", 0) or 0))
        if body_bot <= level_price <= body_top:
            touching_indices.append(i)
    if not touching_indices:
        return result
    best_chain = 1
    current_chain = 1
    chain_top = float(bars[touching_indices[0]].get("high", level_price) or level_price)
    chain_bot = float(bars[touching_indices[0]].get("low", level_price) or level_price)
    for j in range(1, len(touching_indices)):
        prev_idx = touching_indices[j - 1]
        curr_idx = touching_indices[j]
        if curr_idx != prev_idx + 1:
            current_chain = 1
            chain_top = float(bars[curr_idx].get("high", level_price) or level_price)
            chain_bot = float(bars[curr_idx].get("low", level_price) or level_price)
            continue
        prev_bar = bars[prev_idx]
        curr_bar = bars[curr_idx]
        prev_close = float(prev_bar.get("close", 0) or 0)
        curr_close = float(curr_bar.get("close", 0) or 0)
        prev_close_in_current = float(curr_bar.get("low", 0) or 0) <= prev_close <= float(curr_bar.get("high", 0) or 0)
        curr_close_in_prev = float(prev_bar.get("low", 0) or 0) <= curr_close <= float(prev_bar.get("high", 0) or 0)
        if prev_close_in_current or curr_close_in_prev:
            current_chain += 1
            chain_top = max(chain_top, float(curr_bar.get("high", 0) or 0))
            chain_bot = min(chain_bot, float(curr_bar.get("low", 0) or 0))
            if current_chain > best_chain:
                best_chain = current_chain
                result["price_top"] = chain_top
                result["price_bot"] = chain_bot
        else:
            current_chain = 1
            chain_top = float(curr_bar.get("high", 0) or 0)
            chain_bot = float(curr_bar.get("low", 0) or 0)
    if best_chain >= cfg.V3_SHELF_MIN_BARS:
        result["exists"] = True
        result["bar_count"] = best_chain
    return result


def _detect_failed_break(bars: list[dict], level_price: float, kind: str, max_age: int = 20) -> dict:
    result = {"exists": False, "count": 0, "strong_count": 0, "last_bar_idx": -1}
    search_bars = bars[-max_age:] if len(bars) > max_age else bars
    for i, bar in enumerate(search_bars):
        high = float(bar.get("high", 0) or 0)
        low = float(bar.get("low", 0) or 0)
        open_ = float(bar.get("open", 0) or 0)
        close = float(bar.get("close", 0) or 0)
        failed = False
        strong = False
        if kind == "resistance" and high > level_price and close <= level_price:
            failed = True
            wick = high - max(open_, close)
            body = abs(close - open_)
            strong = body > 0 and wick >= body * 2.0
        elif kind == "support" and low < level_price and close >= level_price:
            failed = True
            wick = min(open_, close) - low
            body = abs(close - open_)
            strong = body > 0 and wick >= body * 2.0
        if failed:
            result["exists"] = True
            result["count"] += 1
            result["last_bar_idx"] = i
            if strong:
                result["strong_count"] += 1
    return result


def _detect_acceptance(bars: list[dict], level_price: float, max_age: int = 20) -> dict:
    result = {"exists": False, "bar_count": 0}
    search_bars = bars[-max_age:] if len(bars) > max_age else bars
    count = 0
    max_count = 0
    for bar in search_bars:
        body_top = max(float(bar.get("open", 0) or 0), float(bar.get("close", 0) or 0))
        body_bot = min(float(bar.get("open", 0) or 0), float(bar.get("close", 0) or 0))
        if body_bot <= level_price <= body_top:
            count += 1
            max_count = max(max_count, count)
        else:
            count = 0
    if max_count >= 2:
        result["exists"] = True
        result["bar_count"] = max_count
    return result


def _count_touches(bars: list[dict], level_price: float, kind: str) -> int:
    if not bars:
        return 0
    recent = bars[-10:]
    tolerance = avg_body(recent) * 0.5
    touches = 0
    for bar in bars:
        if kind == "resistance":
            if abs(float(bar.get("high", 0) or 0) - level_price) <= tolerance:
                touches += 1
        elif kind == "support":
            if abs(float(bar.get("low", 0) or 0) - level_price) <= tolerance:
                touches += 1
    return touches


def _check_local_trigger(level_price: float, bars: list[dict]) -> bool:
    if not bars:
        return False
    recent = bars[-5:]
    proximity = avg_body(recent) * 2.0
    for bar in recent:
        if abs(float(bar.get("high", 0) or 0) - level_price) <= proximity:
            return True
        if abs(float(bar.get("low", 0) or 0) - level_price) <= proximity:
            return True
    return False


def _score_level(level: dict) -> dict:
    score = 0
    if level.get("is_shelf"):
        if int(level.get("shelf_bars", 0)) >= 5:
            score += 4
        elif int(level.get("shelf_bars", 0)) >= 3:
            score += 3
    if level.get("is_failed_break"):
        if int(level.get("failed_break_count", 0)) >= 2 and level.get("failed_break_strong"):
            score += 4
        elif level.get("failed_break_strong"):
            score += 3
        else:
            score += 1
    if level.get("is_acceptance"):
        if int(level.get("acceptance_bars", 0)) >= 3:
            score += 2
        else:
            score += 1
    if level.get("is_swing"):
        score += 1
    touch_count = int(level.get("touch_count", 0))
    if touch_count >= 4:
        score += 2
    elif touch_count == 3:
        score += 1
    elif touch_count == 1:
        score -= 1
    if level.get("is_local_trigger"):
        score += 1
    if level.get("is_htf"):
        score += 2
    level["score"] = score
    if score >= cfg.V3_LEVEL_SCORE_STRONG:
        level["strength"] = "STRONG"
    elif score >= cfg.V3_LEVEL_SCORE_MEDIUM:
        level["strength"] = "MEDIUM"
    elif score >= cfg.V3_LEVEL_SCORE_WEAK:
        level["strength"] = "WEAK"
    else:
        level["strength"] = "IGNORE"
    return level


def _merge_levels(levels: list[dict], bars: list[dict]) -> list[dict]:
    if not levels:
        return []
    recent = bars[-20:] if len(bars) >= 20 else bars
    base_tol = max(avg_body(recent) * 0.75, 0.01)

    def level_band(level: dict) -> tuple[float, float]:
        price = float(level.get("price", 0) or 0)
        band_tol = base_tol * (1.25 if level.get("is_htf") else 1.0)
        return price - band_tol, price + band_tol

    def reacting(level: dict) -> set[int]:
        out: set[int] = set()
        price = float(level.get("price", 0) or 0)
        kind = str(level.get("kind") or "")
        band_low, band_high = level_band(level)
        for idx, bar in enumerate(bars):
            high = float(bar.get("high", 0) or 0)
            low = float(bar.get("low", 0) or 0)
            open_ = float(bar.get("open", 0) or 0)
            close = float(bar.get("close", 0) or 0)
            body_top = max(open_, close)
            body_bot = min(open_, close)
            band_hit = low <= band_high and high >= band_low
            if not band_hit:
                continue
            if kind == "resistance":
                defended = body_top <= band_high or abs(high - price) <= (band_high - band_low)
                if defended:
                    out.add(idx)
            elif kind == "support":
                defended = body_bot >= band_low or abs(low - price) <= (band_high - band_low)
                if defended:
                    out.add(idx)
        return out

    merged: list[dict] = []
    used: set[int] = set()
    for i, lvl_a in enumerate(levels):
        if i in used:
            continue
        group = [lvl_a]
        reactions_a = reacting(lvl_a)
        band_a_low, band_a_high = level_band(lvl_a)
        for j, lvl_b in enumerate(levels):
            if j <= i or j in used:
                continue
            if str(lvl_a.get("kind")) != str(lvl_b.get("kind")):
                continue
            band_b_low, band_b_high = level_band(lvl_b)
            bands_overlap = not (band_a_high < band_b_low or band_b_high < band_a_low)
            if not bands_overlap:
                continue
            if len(reactions_a & reacting(lvl_b)) >= 2:
                group.append(lvl_b)
                used.add(j)
        if len(group) == 1:
            merged.append(lvl_a)
            continue
        base = max(group, key=lambda item: int(item.get("score", 0) or 0))
        max_score = int(base.get("score", 0) or 0)
        bonus = min(len(group) - 1, 3)
        base["score"] = max_score + bonus
        if int(base["score"]) >= cfg.V3_LEVEL_SCORE_STRONG:
            base["strength"] = "STRONG"
        elif int(base["score"]) >= cfg.V3_LEVEL_SCORE_MEDIUM:
            base["strength"] = "MEDIUM"
        else:
            base["strength"] = "WEAK"
        merged.append(base)
    return merged


def _empty_active() -> dict:
    return {
        "support": None,
        "resistance": None,
        "range_width": 0.0,
        "range_mid": 0.0,
        "range_position": 0.5,
        "zone": "MID_RANGE",
        "range_valid": False,
        "channel_confirmed": False,
        "locked": False,
        "extreme_fallback": False,
    }


def _finalize_active_pair(support: dict, resistance: dict, price: float) -> dict:
    result = _empty_active()
    s = float(support.get("price", 0) or 0)
    r = float(resistance.get("price", 0) or 0)
    if s <= 0 or r <= 0 or r <= s:
        return result

    result["support"] = support
    result["resistance"] = resistance
    result["range_width"] = r - s
    result["range_mid"] = (r + s) / 2.0
    recent_20 = bars_15m(20)
    if len(recent_20) >= 20:
        total_move = max(float(b.get("high", 0) or 0) for b in recent_20) - min(
            float(b.get("low", 0) or 0) for b in recent_20
        )
        result["range_valid"] = result["range_width"] >= total_move * 0.10
    else:
        result["range_valid"] = result["range_width"] > 0
    if result["range_width"] > 0:
        result["range_position"] = (price - s) / result["range_width"]
    result["zone"] = zone_for_price(s, r, price)
    return result


def _band_traversal_confirmed(
    bars: list[dict],
    support: float,
    resistance: float,
    support_tol: float,
    resistance_tol: float,
) -> bool:
    """
    Tek yonlu trend: fiyat band icinde dirence ve destege dokundu mu?
    (2070'ten 2041'e tek hamle dususte swing zinciri olmasa da True olabilir.)
    """
    lookback = max(int(getattr(cfg, "V3_CHANNEL_TRAVERSE_BARS", 48) or 48), 12)
    recent = bars[-lookback:] if len(bars) > lookback else bars
    if len(recent) < 12 or support <= 0 or resistance <= support:
        return False
    hit_support = False
    hit_resistance = False
    for bar in recent:
        low = float(bar.get("low", 0) or 0)
        high = float(bar.get("high", 0) or 0)
        if low > 0 and low <= support + support_tol:
            hit_support = True
        if high > 0 and high >= resistance - resistance_tol:
            hit_resistance = True
    return hit_support and hit_resistance


def _outer_level_gap(ref_price: float) -> float:
    pct = max(float(getattr(cfg, "V3_OUTER_LEVEL_GAP_PCT", 0.004) or 0.004), 0.0005)
    return max(float(ref_price) * pct, 2.0)


def _pick_outer_support(merged: list[dict], main_s: float) -> dict | None:
    if main_s <= 0:
        return None
    gap = _outer_level_gap(main_s)
    candidates = [
        level
        for level in merged
        if str(level.get("kind")) == "support"
        and str(level.get("strength")) in ("STRONG", "MEDIUM")
        and 0 < float(level.get("price", 0) or 0) < main_s - gap
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (int(item.get("score", 0) or 0), float(item.get("price", 0) or 0)),
    )


def _pick_outer_resistance(merged: list[dict], main_r: float) -> dict | None:
    if main_r <= 0:
        return None
    gap = _outer_level_gap(main_r)
    candidates = [
        level
        for level in merged
        if str(level.get("kind")) == "resistance"
        and str(level.get("strength")) in ("STRONG", "MEDIUM")
        and float(level.get("price", 0) or 0) > main_r + gap
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda item: (-int(item.get("score", 0) or 0), float(item.get("price", 0) or 0)),
    )


def _outer_price_from_active(active: dict, side: str) -> float:
    key = "outer_support" if side == "support" else "outer_resistance"
    price_key = f"{key}_price"
    direct = float(active.get(price_key, 0) or 0)
    if direct > 0:
        return direct
    level = active.get(key) or {}
    return float(level.get("price", 0) or 0)


def _dedupe_levels_by_price(levels: list[dict], ref_price: float) -> list[dict]:
    pct = max(float(getattr(cfg, "V3_CHANNEL_BAND_PCT", 0.003) or 0.003), 0.0005)
    tol = max(float(ref_price) * pct, 0.5)
    ranked = sorted(levels, key=lambda item: int(item.get("score", 0) or 0), reverse=True)
    picked: list[dict] = []
    for level in ranked:
        price = float(level.get("price", 0) or 0)
        if price <= 0:
            continue
        if any(abs(price - float(x.get("price", 0) or 0)) <= tol for x in picked):
            continue
        picked.append(level)
    return picked


def build_chart_levels(merged: list[dict], active: dict, price: float) -> list[dict]:
    """
    Dashboard ikincil seviyeleri kapali — V3 karar yalnizca aktif bant + pozisyon cizgileri.
    (Eski: merged STRONG/MEDIUM adaylari; grafikte gurultu yaratiyordu.)
    """
    return []


def _attach_display_outer_levels(
    active: dict,
    merged: list[dict],
    price: float,
    prev_active: dict | None = None,
) -> None:
    """Grafik icin stabil dis S/R — kilitliyken onceki dis seviye korunur."""
    prev_active = prev_active or {}
    px = float(price or effective_price() or state.mark_price or state.price or 0)
    main_s = float((active.get("support") or {}).get("price", 0) or 0)
    main_r = float((active.get("resistance") or {}).get("price", 0) or 0)
    locked = bool(active.get("locked"))

    prev_os = _outer_price_from_active(prev_active, "support")
    prev_or = _outer_price_from_active(prev_active, "resistance")
    picked_os = _pick_outer_support(merged, main_s)
    picked_or = _pick_outer_resistance(merged, main_r)

    if locked and prev_os > 0 and main_s > 0 and prev_os < main_s - _outer_level_gap(main_s) * 0.5:
        active["outer_support"] = prev_active.get("outer_support") or {"price": prev_os}
        active["outer_support_price"] = prev_os
    elif picked_os:
        active["outer_support"] = picked_os
        active["outer_support_price"] = float(picked_os.get("price", 0) or 0)
    else:
        # Ana altinda aday yoksa: fiyat ile ana destek arasindaki en guclu destek
        inner = [
            level
            for level in merged
            if str(level.get("kind")) == "support"
            and str(level.get("strength")) in ("STRONG", "MEDIUM")
            and main_s < float(level.get("price", 0) or 0) < px
        ]
        if inner:
            best = max(
                inner,
                key=lambda item: (int(item.get("score", 0) or 0), float(item.get("price", 0) or 0)),
            )
            active["outer_support"] = best
            active["outer_support_price"] = float(best.get("price", 0) or 0)
        else:
            active.pop("outer_support", None)
            active["outer_support_price"] = 0.0

    if locked and prev_or > 0 and main_r > 0 and prev_or > main_r + _outer_level_gap(main_r) * 0.5:
        active["outer_resistance"] = prev_active.get("outer_resistance") or {"price": prev_or}
        active["outer_resistance_price"] = prev_or
    elif picked_or:
        active["outer_resistance"] = picked_or
        active["outer_resistance_price"] = float(picked_or.get("price", 0) or 0)
    else:
        inner_r = [
            level
            for level in merged
            if str(level.get("kind")) == "resistance"
            and str(level.get("strength")) in ("STRONG", "MEDIUM")
            and px < float(level.get("price", 0) or 0) < main_r
        ] if main_r > px > 0 else []
        if inner_r:
            best_r = min(
                inner_r,
                key=lambda item: (-int(item.get("score", 0) or 0), float(item.get("price", 0) or 0)),
            )
            active["outer_resistance"] = best_r
            active["outer_resistance_price"] = float(best_r.get("price", 0) or 0)
        else:
            active.pop("outer_resistance", None)
            active["outer_resistance_price"] = 0.0


def _channel_min_width(bars: list[dict]) -> float:
    recent = bars[-20:] if len(bars) >= 20 else bars
    if len(recent) >= 20:
        total_move = max(float(b.get("high", 0) or 0) for b in recent) - min(
            float(b.get("low", 0) or 0) for b in recent
        )
        return max(total_move * 0.10, 0.01)
    return 0.01


def _band_min_width(price: float, bars15: list[dict]) -> float:
    """Yapisal bant: kanal min + fiyat orani + USD taban."""
    pct = max(float(getattr(cfg, "V3_BAND_MIN_WIDTH_PCT", 0.004) or 0.004), 0.0005)
    abs_min = max(float(getattr(cfg, "V3_BAND_MIN_WIDTH_USD", 8.0) or 8.0), 1.0)
    structural = max(float(price) * pct, abs_min)
    return max(_channel_min_width(bars15), structural)


def _band_unlock_buffers(s: float, r: float) -> tuple[float, float]:
    pct = max(float(getattr(cfg, "V3_BAND_UNLOCK_BUFFER_PCT", 0.0015) or 0.0015), 0.0)
    return s * pct, r * pct


def _band_outside_with_hysteresis(price: float, s: float, r: float) -> bool:
    """True: fiyat bant disinda (S/R + buffer); kilit acilir / persist gecersiz."""
    if s <= 0 or r <= s or price <= 0:
        return True
    buf_s, buf_r = _band_unlock_buffers(s, r)
    return price > r + buf_r or price < s - buf_s


def _normalize_persisted_band(
    persisted: dict,
    price: float,
    bars15: list[dict],
    merged: list[dict] | None = None,
) -> dict:
    """
    Kayitli bant bozulmussa (dar primary, fiyat disinda) outer S/R ile toparla.
    Onceki oturumda resync kaymasi veya kirilim sonrasi dar kayit bu yolu kullanir.
    """
    if not persisted.get("support") or not persisted.get("resistance"):
        return persisted
    if _persist_band_usable(persisted, price, bars15, merged):
        return persisted

    o_ps = float(persisted.get("outer_support_price", 0) or 0)
    o_pr = float(persisted.get("outer_resistance_price", 0) or 0)
    if not (o_ps > 0 and o_pr > o_ps and o_ps < price < o_pr):
        return persisted
    width = o_pr - o_ps
    if width < _band_min_width(price, bars15):
        return persisted

    out = dict(persisted)
    outer_s = persisted.get("outer_support")
    outer_r = persisted.get("outer_resistance")
    if merged:
        outer_s = _lookup_merged_level(merged, "support", o_ps) or outer_s
        outer_r = _lookup_merged_level(merged, "resistance", o_pr) or outer_r
    if not outer_s:
        outer_s = {"price": o_ps, "kind": "support", "strength": "MEDIUM", "score": 0}
    if not outer_r:
        outer_r = {"price": o_pr, "kind": "resistance", "strength": "MEDIUM", "score": 0}
    out["support"] = outer_s
    out["resistance"] = outer_r
    log.info(
        f"[LEVELS] persist primary gecersiz — outer bant kullanildi "
        f"S={o_ps:.2f} R={o_pr:.2f} px={price:.2f}"
    )
    return out


def _persist_band_usable(
    persisted: dict,
    price: float,
    bars15: list[dict],
    merged: list[dict] | None = None,
) -> bool:
    support = persisted.get("support")
    resistance = persisted.get("resistance")
    if not support or not resistance:
        return False
    ps = float(support.get("price", 0) or 0)
    pr = float(resistance.get("price", 0) or 0)
    if not (ps < price < pr):
        return False
    width = pr - ps
    if width < _band_min_width(price, bars15):
        return False
    if merged and getattr(cfg, "V3_ACTIVE_BAND_HTF", False):
        macro_w = _htf_macro_band_width(merged, price, bars15)
        if macro_w > 0 and width < macro_w * 0.65:
            return False
    return True


def _persist_as_prev_active(persisted: dict) -> dict:
    out = {
        "support": persisted.get("support"),
        "resistance": persisted.get("resistance"),
        "locked": False,
        "from_persist": True,
        "extreme_fallback": bool(persisted.get("extreme_fallback")),
    }
    for key in (
        "outer_support",
        "outer_resistance",
        "outer_support_price",
        "outer_resistance_price",
    ):
        if key in persisted:
            out[key] = persisted[key]
    return out


def _persist_active(active: dict) -> None:
    support = active.get("support")
    resistance = active.get("resistance")
    if not support or not resistance:
        return
    try:
        _PERSIST_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "support": support,
            "resistance": resistance,
            "locked": bool(active.get("locked")),
            "extreme_fallback": bool(active.get("extreme_fallback")),
            "outer_support": active.get("outer_support"),
            "outer_resistance": active.get("outer_resistance"),
            "outer_support_price": float(active.get("outer_support_price", 0) or 0),
            "outer_resistance_price": float(active.get("outer_resistance_price", 0) or 0),
        }
        _PERSIST_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        log.warning(f"[LEVELS] aktif band kaydi yazilamadi: {e}")


def _restore_persisted_active() -> dict:
    try:
        if not _PERSIST_FILE.exists():
            return {}
        data = json.loads(_PERSIST_FILE.read_text(encoding="utf-8"))
        support = data.get("support")
        resistance = data.get("resistance")
        if not support or not resistance:
            return {}
        out = {
            "support": support,
            "resistance": resistance,
            "locked": bool(data.get("locked")),
            "extreme_fallback": bool(data.get("extreme_fallback")),
        }
        if data.get("outer_support"):
            out["outer_support"] = data.get("outer_support")
        if data.get("outer_resistance"):
            out["outer_resistance"] = data.get("outer_resistance")
        if float(data.get("outer_support_price", 0) or 0) > 0:
            out["outer_support_price"] = float(data.get("outer_support_price"))
        if float(data.get("outer_resistance_price", 0) or 0) > 0:
            out["outer_resistance_price"] = float(data.get("outer_resistance_price"))
        return out
    except Exception as e:
        log.warning(f"[LEVELS] aktif band kaydi okunamadi: {e}")
        return {}


def _resolve_htf_macro_band(
    levels: list[dict], price: float, bars15: list[dict]
) -> dict:
    """
    1h swing destek + 1h swing direnc = makro aktif bant.
    WEAK 1h seviyeleri dahil (2029 gibi uzak direnc skoru dusuk olabilir).
    """
    if not getattr(cfg, "V3_ACTIVE_BAND_HTF", False):
        return _empty_active()

    htf = [
        level
        for level in levels
        if level.get("is_htf")
        and str(level.get("strength")) in ("STRONG", "MEDIUM", "WEAK")
    ]
    supports = [
        l
        for l in htf
        if str(l.get("kind")) == "support"
        and 0 < float(l.get("price", 0) or 0) < price
    ]
    resistances = [
        l
        for l in htf
        if str(l.get("kind")) == "resistance"
        and float(l.get("price", 0) or 0) > price
    ]
    if not supports or not resistances:
        return _empty_active()

    support = max(
        supports,
        key=lambda item: (
            int(item.get("score", 0) or 0),
            float(item.get("price", 0) or 0),
        ),
    )
    resistance = min(
        resistances,
        key=lambda item: (
            float(item.get("price", 0) or 0),
            -int(item.get("score", 0) or 0),
        ),
    )
    s = float(support.get("price", 0) or 0)
    r = float(resistance.get("price", 0) or 0)
    if not (s < price < r):
        return _empty_active()
    if (r - s) < _band_min_width(price, bars15):
        return _empty_active()

    out = _finalize_active_pair(support, resistance, price)
    out["macro_htf"] = True
    return out


def _htf_macro_band_width(levels: list[dict], price: float, bars15: list[dict]) -> float:
    macro = _resolve_htf_macro_band(levels, price, bars15)
    if not macro.get("support") or not macro.get("resistance"):
        return 0.0
    s = float((macro.get("support") or {}).get("price", 0) or 0)
    r = float((macro.get("resistance") or {}).get("price", 0) or 0)
    return max(r - s, 0.0)


def _resolve_active_levels(levels: list[dict], price: float, bars15: list[dict]) -> dict:
    macro = _resolve_htf_macro_band(levels, price, bars15)
    if macro.get("support") and macro.get("resistance"):
        s = float((macro.get("support") or {}).get("price", 0) or 0)
        r = float((macro.get("resistance") or {}).get("price", 0) or 0)
        combined = _pair_combined_score(macro.get("support"), macro.get("resistance"))
        log.info(
            f"[LEVELS] 1h makro bant S={s:.2f} R={r:.2f} "
            f"width={r - s:.2f} combined={combined} px={price:.2f}"
        )
        return macro

    valid = [level for level in levels if str(level.get("strength")) in ("STRONG", "MEDIUM")]
    if not valid:
        return _empty_active()
    supports = [
        l
        for l in valid
        if 0 < float(l.get("price", 0) or 0) < price and str(l.get("kind")) == "support"
    ]
    resistances = [
        l
        for l in valid
        if float(l.get("price", 0) or 0) > price and str(l.get("kind")) == "resistance"
    ]
    if not supports or not resistances:
        return _empty_active()

    min_width = _band_min_width(price, bars15)
    candidates: list[tuple[int, float, dict, dict]] = []
    for support in supports:
        s_price = float(support.get("price", 0) or 0)
        for resistance in resistances:
            r_price = float(resistance.get("price", 0) or 0)
            if r_price <= s_price:
                continue
            if not (s_price < price < r_price):
                continue
            width = r_price - s_price
            if width < min_width:
                continue
            combined = int(support.get("score", 0) or 0) + int(
                resistance.get("score", 0) or 0
            )
            candidates.append((combined, width, support, resistance))

    if candidates:
        max_combined = max(c[0] for c in candidates)
        frac = max(float(getattr(cfg, "V3_BAND_SCORE_FRAC", 0.88) or 0.88), 0.5)
        threshold = max_combined * frac
        eligible = [c for c in candidates if c[0] >= threshold]
        combined, width, best_s, best_r = max(eligible, key=lambda c: (c[0], c[1]))
        log.debug(
            f"[LEVELS] bant adaylari={len(candidates)} min_w={min_width:.2f} "
            f"secilen combined={combined} width={width:.2f}"
        )
        return _finalize_active_pair(best_s, best_r, price)

    # Dar kanal filtresi disinda kalan tek aday varsa geri don.
    resistance = min(resistances, key=lambda item: float(item.get("price", 0) or 0))
    support = max(supports, key=lambda item: int(item.get("score", 0) or 0))
    if float(resistance.get("price", 0) or 0) > float(support.get("price", 0) or 0):
        return _finalize_active_pair(support, resistance, price)
    return _empty_active()


def _recent_bar_low_high(bars15: list[dict], lookback: int = 24) -> tuple[float, float]:
    recent = bars15[-lookback:] if len(bars15) > lookback else bars15
    if not recent:
        return 0.0, 0.0
    lows = [float(b.get("low", 0) or 0) for b in recent if float(b.get("low", 0) or 0) > 0]
    highs = [float(b.get("high", 0) or 0) for b in recent if float(b.get("high", 0) or 0) > 0]
    if not lows or not highs:
        return 0.0, 0.0
    return min(lows), max(highs)


def _resolve_band_outside_channel(
    levels: list[dict], price: float, bars15: list[dict]
) -> dict:
    """
    Fiyat dar kanal disinda:
    - Normal: en yakin direnc + en guclu destek (fiyatin altinda)
    - Tum desteklerin alti: en yakin direnc + son 15m dip low
    - Tum direnclerin ustu: en guclu destek + son 15m tepe high
    """
    valid = [level for level in levels if str(level.get("strength")) in ("STRONG", "MEDIUM")]
    resistances = [
        l
        for l in valid
        if float(l.get("price", 0) or 0) > price and str(l.get("kind")) == "resistance"
    ]
    supports_below = [
        l
        for l in valid
        if 0 < float(l.get("price", 0) or 0) < price and str(l.get("kind")) == "support"
    ]

    if resistances and supports_below:
        resistance = min(resistances, key=lambda item: float(item.get("price", 0) or 0))
        support = max(supports_below, key=lambda item: int(item.get("score", 0) or 0))
        s_p = float(support.get("price", 0) or 0)
        r_p = float(resistance.get("price", 0) or 0)
        if r_p > s_p:
            active = _finalize_active_pair(support, resistance, price)
            active["outside_channel"] = True
            return active

    bar_low, bar_high = _recent_bar_low_high(bars15)

    if resistances and not supports_below:
        resistance = min(resistances, key=lambda item: float(item.get("price", 0) or 0))
        r_p = float(resistance.get("price", 0) or 0)
        s_price = bar_low if 0 < bar_low < price else max(price - max(r_p - price, 1.0) * 0.15, price * 0.995)
        support = _make_extreme_fallback_level(s_price, "support")
        if r_p > s_price:
            active = _finalize_active_pair(support, resistance, price)
            active["outside_channel"] = True
            active["below_all_supports"] = True
            log.info(
                f"[LEVELS] tum desteklerin alti px={price:.2f} "
                f"S={s_price:.2f} R={r_p:.2f} (dip low + en yakin direnc)"
            )
            return active

    if supports_below and not resistances:
        support = max(supports_below, key=lambda item: int(item.get("score", 0) or 0))
        s_p = float(support.get("price", 0) or 0)
        r_price = bar_high if bar_high > price else min(price + max(price - s_p, 1.0) * 0.15, price * 1.005)
        resistance = _make_extreme_fallback_level(r_price, "resistance")
        if r_price > s_p:
            active = _finalize_active_pair(support, resistance, price)
            active["outside_channel"] = True
            active["above_all_resistances"] = True
            log.info(
                f"[LEVELS] tum direnclerin ustu px={price:.2f} "
                f"S={s_p:.2f} R={r_price:.2f} (tepe high + en yakin destek)"
            )
            return active

    return _empty_active()


def _make_extreme_fallback_level(price_level: float, kind: str) -> dict:
    return {
        "price": round(float(price_level), 2),
        "kind": kind,
        "timeframe": "15m",
        "bar_index": -1,
        "is_swing": False,
        "is_htf": False,
        "is_shelf": False,
        "shelf_bars": 0,
        "is_failed_break": False,
        "failed_break_count": 0,
        "failed_break_strong": False,
        "is_acceptance": False,
        "acceptance_bars": 0,
        "touch_count": 0,
        "is_local_trigger": False,
        "is_extreme_fallback": True,
        "score": int(cfg.V3_LEVEL_SCORE_MEDIUM),
        "strength": "MEDIUM",
    }


def _apply_extreme_bar_fallback(bars: list[dict], price: float) -> dict | None:
    """Güçlü seviye yoksa son N×15m mumun en düşük low / en yüksek high geçici S/R."""
    lookback = max(int(getattr(cfg, "V3_EXTREME_FALLBACK_BARS", 24) or 24), 12)
    recent = bars[-lookback:] if len(bars) > lookback else bars
    if len(recent) < 12 or price <= 0:
        return None
    s_price = min(float(b.get("low", 0) or 0) for b in recent)
    r_price = max(float(b.get("high", 0) or 0) for b in recent)
    if s_price <= 0 or r_price <= s_price:
        return None
    support = _make_extreme_fallback_level(s_price, "support")
    resistance = _make_extreme_fallback_level(r_price, "resistance")
    active = _finalize_active_pair(support, resistance, price)
    active["extreme_fallback"] = True
    return active


def _lookup_merged_level(merged: list[dict], kind: str, price: float) -> dict | None:
    """
    Persist/aktif band S/R kaydini merged ile eslestir.
    Dar tolerans + en yakin fiyat: genis merge bandi (or. 6 USD) ile
    direnci 2019 -> 2015 gibi kaydirmaz.
    """
    if price <= 0:
        return None
    pct = max(float(getattr(cfg, "V3_CHANNEL_BAND_PCT", 0.003) or 0.003), 0.0005)
    wide_tol = max(float(price) * pct, 0.01)
    resync_tol = min(wide_tol, max(float(price) * 0.0003, 0.35))
    matches = [
        level
        for level in merged
        if str(level.get("kind")) == kind
        and abs(float(level.get("price", 0) or 0) - float(price)) <= resync_tol
    ]
    if not matches:
        return None
    return min(
        matches,
        key=lambda item: (
            abs(float(item.get("price", 0) or 0) - float(price)),
            -int(item.get("score", 0) or 0),
        ),
    )


def _resync_band_levels(active: dict, merged: list[dict]) -> dict:
    """Band S/R kayitlarini guncel merged skorlarina bagla (persist sisfirmesi)."""
    out = dict(active)
    for key, kind in (("support", "support"), ("resistance", "resistance")):
        lv = active.get(key)
        if not lv:
            continue
        price = float(lv.get("price", 0) or 0)
        fresh = _lookup_merged_level(merged, kind, price)
        if fresh:
            out[key] = fresh
    return out


def _ensure_active_covers_price(
    active: dict, merged: list[dict], price: float, bars15: list[dict]
) -> dict:
    """Fiyat aktif bant + buffer disindaysa kanal disi / genis bant sec."""
    if not active.get("support") or not active.get("resistance"):
        return active
    s = float((active.get("support") or {}).get("price", 0) or 0)
    r = float((active.get("resistance") or {}).get("price", 0) or 0)
    if not _band_outside_with_hysteresis(price, s, r):
        return active
    outside = _resolve_band_outside_channel(merged, price, bars15)
    if not outside.get("support") or not outside.get("resistance"):
        return active
    os = float((outside.get("support") or {}).get("price", 0) or 0)
    or_ = float((outside.get("resistance") or {}).get("price", 0) or 0)
    if os <= 0 or or_ <= os:
        return active
    log.info(
        f"[LEVELS] fiyat bant disinda px={price:.2f} eski S={s:.2f} R={r:.2f} "
        f"→ yeni S={os:.2f} R={or_:.2f}"
    )
    global _last_band_unlock_log_key
    _last_band_unlock_log_key = ""
    return outside


def _band_selection_key(support: dict | None, resistance: dict | None) -> tuple[int, float]:
    if not support or not resistance:
        return (0, 0.0)
    s_p = float(support.get("price", 0) or 0)
    r_p = float(resistance.get("price", 0) or 0)
    combined = int(support.get("score", 0) or 0) + int(resistance.get("score", 0) or 0)
    return (combined, max(r_p - s_p, 0.0))


def _level_strength_at(merged: list[dict], kind: str, price: float) -> str:
    pct = max(float(getattr(cfg, "V3_CHANNEL_BAND_PCT", 0.003) or 0.003), 0.0005)
    tol = max(float(price) * pct, 0.01)
    matches = [
        level
        for level in merged
        if str(level.get("kind")) == kind
        and abs(float(level.get("price", 0) or 0) - float(price)) <= tol
    ]
    if not matches:
        return "MISSING"
    best = max(matches, key=lambda item: int(item.get("score", 0) or 0))
    return str(best.get("strength") or "IGNORE")


def _break_confirm_bars() -> int:
    return max(int(getattr(cfg, "V3_LEVEL_BREAK_CONFIRM_BARS", 3) or 3), 1)


def _consecutive_15m_closes_outside(
    bars15: list[dict], level: float, side: str
) -> int:
    """Son ardışık 15m kapanışlar seviyenin altında/üstünde (en yeniden geriye)."""
    if not bars15 or level <= 0:
        return 0
    count = 0
    for bar in reversed(bars15):
        close = float(bar.get("close", 0) or 0)
        if close <= 0:
            break
        if side == "below" and close < level:
            count += 1
        elif side == "above" and close > level:
            count += 1
        else:
            break
    return count


def _should_refresh_active_levels(
    prev_active: dict,
    price: float,
    merged: list[dict],
) -> bool:
    """Gecersiz bant veya IGNORE disinda kilidi acma — MISSING tek basina yetmez."""
    prev_support = prev_active.get("support") or {}
    prev_resistance = prev_active.get("resistance") or {}
    s = float(prev_support.get("price", 0) or 0)
    r = float(prev_resistance.get("price", 0) or 0)
    if s <= 0 or r <= 0 or r <= s:
        return True
    # Fiyat bant + buffer icindeyse merged'de birebir eslesme olmasa da kilitle.
    if not _band_outside_with_hysteresis(price, s, r):
        s_strength = _level_strength_at(merged, "support", s)
        r_strength = _level_strength_at(merged, "resistance", r)
        if s_strength == "IGNORE" or r_strength == "IGNORE":
            return True
        return False
    s_strength = _level_strength_at(merged, "support", s)
    r_strength = _level_strength_at(merged, "resistance", r)
    if s_strength in ("IGNORE", "MISSING"):
        return True
    if r_strength in ("IGNORE", "MISSING"):
        return True
    return False


def _pair_combined_score(support: dict | None, resistance: dict | None) -> int:
    if not support or not resistance:
        return 0
    return int(support.get("score", 0) or 0) + int(resistance.get("score", 0) or 0)


def _active_from_prev_band(prev_active: dict, price: float) -> dict | None:
    """Oturum bandi: fiyat icindeyse merged kanal bulunamasa bile onceki S/R kullan."""
    prev_support = prev_active.get("support")
    prev_resistance = prev_active.get("resistance")
    if not prev_support or not prev_resistance:
        return None
    s = float(prev_support.get("price", 0) or 0)
    r = float(prev_resistance.get("price", 0) or 0)
    if _band_outside_with_hysteresis(price, s, r):
        return None
    active = _finalize_active_pair(prev_support, prev_resistance, price)
    active["from_session"] = True
    return active


def _locked_from_prev(
    prev_active: dict,
    prev_support: dict,
    prev_resistance: dict,
    price: float,
    *,
    break_pending: str = "",
    break_confirm: str = "",
) -> dict:
    locked = _finalize_active_pair(prev_support, prev_resistance, price)
    locked["locked"] = True
    locked["extreme_fallback"] = bool(
        prev_active.get("extreme_fallback")
        or prev_support.get("is_extreme_fallback")
        or prev_resistance.get("is_extreme_fallback")
    )
    if break_pending:
        locked["break_pending"] = break_pending
        locked["break_confirm"] = break_confirm
    for key in (
        "outer_support",
        "outer_resistance",
        "outer_support_price",
        "outer_resistance_price",
    ):
        if key in prev_active:
            locked[key] = prev_active[key]
    return locked


def _apply_active_level_lock(
    new_active: dict,
    merged: list[dict],
    price: float,
    prev_active: dict,
    bars15: list[dict],
) -> dict:
    """Bant icinde (+ buffer): onceki S/R korunur; disinda yeni hesap."""
    prev_support = prev_active.get("support")
    prev_resistance = prev_active.get("resistance")
    if not prev_support or not prev_resistance:
        return new_active

    s = float(prev_support.get("price", 0) or 0)
    r = float(prev_resistance.get("price", 0) or 0)
    if s <= 0 or r <= 0 or r <= s:
        return new_active

    if _band_outside_with_hysteresis(price, s, r):
        return new_active

    if bool(prev_active.get("locked")):
        return _locked_from_prev(prev_active, prev_support, prev_resistance, price)

    new_support = new_active.get("support") or {}
    new_resistance = new_active.get("resistance") or {}
    ns = float(new_support.get("price", 0) or 0)
    nr = float(new_resistance.get("price", 0) or 0)
    if (
        ns > 0
        and nr > ns
        and new_active.get("macro_htf")
        and (nr - ns) > (r - s) * 1.15
    ):
        return new_active

    if _should_refresh_active_levels(prev_active, price, merged):
        if new_active.get("support") and new_active.get("resistance"):
            return new_active

    kept = _finalize_active_pair(prev_support, prev_resistance, price)
    kept["locked"] = not bool(prev_active.get("from_persist"))
    return kept


def _level_debug_line(level: dict) -> str:
    return (
        f"{str(level.get('kind') or '?')} {float(level.get('price', 0) or 0):.2f} "
        f"skor={int(level.get('score', 0) or 0)} "
        f"guc={str(level.get('strength') or '?')} "
        f"tf={str(level.get('timeframe') or '?')} "
        f"shelf={int(level.get('shelf_bars', 0) or 0)} "
        f"fb={int(level.get('failed_break_count', 0) or 0)} "
        f"acc={int(level.get('acceptance_bars', 0) or 0)} "
        f"touch={int(level.get('touch_count', 0) or 0)} "
        f"local={'Y' if level.get('is_local_trigger') else 'N'} "
        f"htf={'Y' if level.get('is_htf') else 'N'}"
    )


def _log_level_diagnostics(price: float, all_levels: list[dict], merged: list[dict], active: dict) -> None:
    global _last_verbose_diag_key
    active_support = float(((active.get("support") or {}).get("price", 0) or 0))
    active_resistance = float(((active.get("resistance") or {}).get("price", 0) or 0))
    range_valid = bool(active.get("range_valid"))
    locked = bool(active.get("locked"))
    zone = str(active.get("zone") or "?")
    log.info(
        f"[LEVELS] px={price:.2f} aday={len(all_levels)} merged={len(merged)} "
        f"aktif={active_support:.2f}/{active_resistance:.2f} "
        f"range_valid={range_valid} zone={zone} locked={locked}"
    )
    if active_support > 0 and active_resistance > 0 and range_valid:
        _last_verbose_diag_key = ""
        return

    diag_key = f"{price:.0f}|{active_support:.0f}|{active_resistance:.0f}|{zone}"
    if diag_key == _last_verbose_diag_key:
        return
    _last_verbose_diag_key = diag_key

    if all_levels:
        log.info(f"[LEVELS] Toplam aday: {len(all_levels)}")
        ranked_all = sorted(
            all_levels,
            key=lambda item: (
                int(item.get("score", 0) or 0),
                float(item.get("price", 0) or 0),
            ),
            reverse=True,
        )
        for level in ranked_all:
            log.info("  aday  " + _level_debug_line(level))
    else:
        log.info("[LEVELS] Ham aday yok")

    if merged:
        log.info(f"[LEVELS] Merge sonrasi: {len(merged)}")
        ranked_merged = sorted(
            merged,
            key=lambda item: (
                int(item.get("score", 0) or 0),
                float(item.get("price", 0) or 0),
            ),
            reverse=True,
        )
        for level in ranked_merged:
            log.info("  merge " + _level_debug_line(level))
    else:
        log.info("[LEVELS] Merge sonrasi seviye yok")


def update_levels() -> dict:
    price = float(effective_price() or state.mark_price or state.price or 0)
    bars15 = bars_15m(200)
    bars1h = bars_1h(100)
    bars1 = bars_1m(200)
    if not bars15 or price <= 0:
        snap = {"levels": [], "active": _empty_active(), "price": price}
        state.v3_levels = snap
        log.warning(f"[LEVELS] Veri yetersiz: bars15={len(bars15)} price={price:.2f}")
        return snap

    all_levels: list[dict] = []

    def process_timeframe(bars: list[dict], tf: str, max_age: int, is_htf: bool) -> None:
        highs, lows = _find_swing_candidates(bars, cfg.V3_SWING_LOOKBACK)
        min_bar_index = max(0, len(bars) - max_age)
        for swing, kind in [(s, "resistance") for s in highs] + [(s, "support") for s in lows]:
            if int(swing.get("bar_index", 0)) < min_bar_index:
                continue
            level_price = float(swing.get("price", 0) or 0)
            level = {
                "price": round(level_price, 2),
                "kind": kind,
                "timeframe": tf,
                "bar_index": int(swing.get("bar_index", 0)),
                "is_swing": True,
                "is_htf": is_htf,
            }
            shelf = _detect_shelf(bars, level_price)
            level["is_shelf"] = shelf["exists"]
            level["shelf_bars"] = int(shelf["bar_count"])
            fb = _detect_failed_break(bars, level_price, kind)
            level["is_failed_break"] = fb["exists"]
            level["failed_break_count"] = int(fb["count"])
            level["failed_break_strong"] = int(fb["strong_count"]) > 0
            acc = _detect_acceptance(bars, level_price)
            level["is_acceptance"] = acc["exists"]
            level["acceptance_bars"] = int(acc["bar_count"])
            level["touch_count"] = _count_touches(bars, level_price, kind)
            level["is_local_trigger"] = _check_local_trigger(level_price, bars1)
            level = _score_level(level)
            if str(level.get("strength")) != "IGNORE":
                all_levels.append(level)

    process_timeframe(bars15, "15m", cfg.V3_LEVEL_MAX_AGE_15M, False)
    if bars1h:
        process_timeframe(bars1h, "1h", cfg.V3_LEVEL_MAX_AGE_1H, True)
    merged = _merge_levels(all_levels, bars15)
    prev_active = (state.v3_levels or {}).get("active") or {}
    cold_start = not prev_active.get("support") or not prev_active.get("resistance")
    persist_loaded = False

    if cold_start:
        persisted = _normalize_persisted_band(
            _restore_persisted_active(), price, bars15, merged
        )
        if _persist_band_usable(persisted, price, bars15, merged):
            ps = float(persisted["support"].get("price", 0) or 0)
            pr = float(persisted["resistance"].get("price", 0) or 0)
            prev_active = _persist_as_prev_active(persisted)
            persist_loaded = True
            log.info(
                f"[LEVELS] cold start — persist yuklendi locked=False "
                f"S={ps:.2f} R={pr:.2f} px={price:.2f}"
            )
        elif persisted.get("support") and persisted.get("resistance"):
            ps = float(persisted["support"].get("price", 0) or 0)
            pr = float(persisted["resistance"].get("price", 0) or 0)
            log.info(
                f"[LEVELS] cold start — persist gecersiz "
                f"(px={price:.2f} S={ps:.2f} R={pr:.2f}"
                f"{'; 1h makro banda gore dar' if getattr(cfg, 'V3_ACTIVE_BAND_HTF', False) else ''}"
                f") — yeni bant"
            )
            prev_active = {}
        else:
            prev_active = {}
    else:
        ps = float((prev_active.get("support") or {}).get("price", 0) or 0)
        pr = float((prev_active.get("resistance") or {}).get("price", 0) or 0)
        if ps > 0 and pr > ps and _band_outside_with_hysteresis(price, ps, pr):
            global _last_band_unlock_log_key
            _, buf_r = _band_unlock_buffers(ps, pr)
            unlock_key = f"{ps:.2f}|{pr:.2f}"
            if unlock_key != _last_band_unlock_log_key:
                _last_band_unlock_log_key = unlock_key
                log.info(
                    f"[LEVELS] aktif bant disinda px={price:.2f} "
                    f"S={ps:.2f} R={pr:.2f} (buffer={buf_r:.2f}) — kilit acildi, yeni bant"
                )
            prev_active = {}

    active = _resolve_active_levels(merged, price, bars15)
    if cold_start and active.get("support") and active.get("resistance"):
        fs = float((active.get("support") or {}).get("price", 0) or 0)
        fr = float((active.get("resistance") or {}).get("price", 0) or 0)
        fw = fr - fs
        fc = _pair_combined_score(active.get("support"), active.get("resistance"))
        if persist_loaded and active.get("macro_htf"):
            prev_resync = _resync_band_levels(prev_active, merged)
            ps = float((prev_resync.get("support") or {}).get("price", 0) or 0)
            pr = float((prev_resync.get("resistance") or {}).get("price", 0) or 0)
            if fw > (pr - ps) * 1.15:
                log.info(
                    f"[LEVELS] cold start 1h makro bant S={fs:.2f} R={fr:.2f} "
                    f"width={fw:.2f} — dar persist ({ps:.2f}/{pr:.2f}) yok sayildi"
                )
                prev_active = {}
                persist_loaded = False
        if persist_loaded:
            prev_resync = _resync_band_levels(prev_active, merged)
            pc = _pair_combined_score(
                prev_resync.get("support"), prev_resync.get("resistance")
            )
            ps = float((prev_resync.get("support") or {}).get("price", 0) or 0)
            pr = float((prev_resync.get("resistance") or {}).get("price", 0) or 0)
            prev_active = prev_resync
            log.info(
                f"[LEVELS] cold start persist korunuyor S={ps:.2f} R={pr:.2f} "
                f"combined={pc} width={pr - ps:.2f} | fresh S={fs:.2f} R={fr:.2f} "
                f"combined={fc} width={fw:.2f}"
            )
        else:
            log.info(
                f"[LEVELS] yeni bant secildi S={fs:.2f} R={fr:.2f} "
                f"combined={fc} width={fw:.2f} px={price:.2f}"
            )
    if not active.get("support") or not active.get("resistance"):
        session_active = _active_from_prev_band(prev_active, price)
        if session_active:
            active = session_active
            s = float((active.get("support") or {}).get("price", 0) or 0)
            r = float((active.get("resistance") or {}).get("price", 0) or 0)
            log.info(
                f"[LEVELS] oturum bandi aktif px={price:.2f} S={s:.2f} R={r:.2f} "
                f"(dar kanal yok, onceki bant)"
            )
        else:
            active = _resolve_band_outside_channel(merged, price, bars15)
    if not active.get("support") or not active.get("resistance"):
        extreme = _apply_extreme_bar_fallback(bars15, price)
        if extreme:
            active = extreme
            s = float((active.get("support") or {}).get("price", 0) or 0)
            r = float((active.get("resistance") or {}).get("price", 0) or 0)
            log.info(
                f"[LEVELS] extreme fallback px={price:.2f} S={s:.2f} R={r:.2f} "
                f"(son {getattr(cfg, 'V3_EXTREME_FALLBACK_BARS', 24)}x15m)"
            )
    active = _apply_active_level_lock(active, merged, price, prev_active, bars15)
    active = _resync_band_levels(active, merged)
    active = _ensure_active_covers_price(active, merged, price, bars15)
    _attach_display_outer_levels(active, merged, price, prev_active)
    snap = {
        "price": price,
        "levels": merged,
        "active": active,
    }
    state.v3_levels = snap
    if active.get("support") and active.get("resistance"):
        _persist_active(active)
    _log_level_diagnostics(price, all_levels, merged, active)
    return snap


def zone_for_price(support: float, resistance: float, price: float) -> str:
    """
    Anlik fiyata gore zone (kilitli S/R sabit).
    esik = (direnc - destek) * V3_ZONE_RATIO
    px <= destek + esik -> NEAR_SUPPORT
    px >= direnc - esik -> NEAR_RESISTANCE
    """
    s = float(support or 0)
    r = float(resistance or 0)
    px = float(price or 0)
    band = r - s
    if s <= 0 or band <= 0 or px <= 0:
        return "MID_RANGE"
    threshold = band * float(cfg.V3_ZONE_RATIO)
    if px <= s + threshold:
        return "NEAR_SUPPORT"
    if px >= r - threshold:
        return "NEAR_RESISTANCE"
    return "MID_RANGE"


def _level_touch_tolerance(bars15: list[dict], level: float) -> float:
    recent = bars15[-12:] if len(bars15) >= 12 else bars15
    body = avg_body(recent) if recent else 0.0
    return max(body * 0.5, level * 0.00015, 1e-9)


def _dynamic_level_window(band_width_pct: float) -> int:
    return max(3, min(8, int(band_width_pct * 500)))


def _reliability_lookback(band_width_pct: float, available: int) -> int:
    want = max(20, min(80, int(band_width_pct * 8000) + 20))
    return min(want, available)


def level_reliability(
    bars15: list[dict],
    level: float,
    direction: str,
    *,
    support: float = 0.0,
    resistance: float = 0.0,
) -> float:
    """
    Gecmis ret orani: fiyat seviyeye geldi ve geri dondu mu?
    0.0 = hic test yok veya ret yok.
    """
    if not bars15 or level <= 0:
        return 0.0

    side = str(direction or "").upper()
    if side in ("BUY", "LONG"):
        kind = "support"
    elif side in ("SELL", "SHORT"):
        kind = "resistance"
    else:
        return 0.0

    band_width_pct = 0.01
    if resistance > support > 0:
        band_width_pct = (resistance - support) / resistance
    tol = _level_touch_tolerance(bars15, level)
    lookback = _reliability_lookback(band_width_pct, len(bars15))
    hist = bars15[-lookback:]

    touches = 0
    rejections = 0
    for bar in hist:
        high = float(bar.get("high", 0) or 0)
        low = float(bar.get("low", 0) or 0)
        close = float(bar.get("close", 0) or 0)
        if kind == "support":
            touched = low <= level + tol
            if not touched:
                continue
            touches += 1
            if close >= level - tol * 0.25:
                rejections += 1
        else:
            touched = high >= level - tol
            if not touched:
                continue
            touches += 1
            if close <= level + tol * 0.25:
                rejections += 1

    if touches == 0:
        return 0.0
    return rejections / touches


def level_respect_now(
    bars15: list[dict],
    level: float,
    direction: str,
    *,
    support: float = 0.0,
    resistance: float = 0.0,
) -> float:
    """Son N mumda seviye/bant tarafina saygi orani (0-1)."""
    if not bars15 or level <= 0:
        return 0.0

    band_width_pct = 0.01
    if resistance > support > 0:
        band_width_pct = (resistance - support) / resistance
    n = _dynamic_level_window(band_width_pct)
    recent = bars15[-n:]
    if not recent:
        return 0.0

    ok = 0
    for bar in recent:
        close = float(bar.get("close", 0) or 0)
        if support > 0 and resistance > support:
            if support < close < resistance:
                ok += 1
        elif str(direction or "").upper() in ("BUY", "LONG"):
            if close >= level:
                ok += 1
        else:
            if close <= level:
                ok += 1
    return ok / len(recent)


def cvd_supports_level(cvd: dict | None, direction: str) -> float:
    """CVD en az notr/uyumlu ise >0, karsi yonde <=0."""
    cvd = cvd or {}
    cvd_dir = str(cvd.get("direction") or "NEUTRAL").upper()
    side = str(direction or "").upper()
    if side in ("BUY", "LONG"):
        return -1.0 if cvd_dir == "BEAR" else 1.0
    if side in ("SELL", "SHORT"):
        return -1.0 if cvd_dir == "BULL" else 1.0
    return 0.0


def level_tp_reliability(
    bars15: list[dict],
    support: float,
    resistance: float,
    side: str,
) -> float:
    """RANGE giris yonune gore TP tarafi (karsi band kenari) ret orani."""
    side = str(side or "").upper()
    if side in ("BUY", "LONG"):
        return level_reliability(
            bars15, resistance, "SELL", support=support, resistance=resistance
        )
    if side in ("SELL", "SHORT"):
        return level_reliability(
            bars15, support, "BUY", support=support, resistance=resistance
        )
    return 0.0


def level_trade_ready(
    bars15: list[dict],
    levels: dict,
    side: str,
    *,
    cvd: dict | None = None,
) -> tuple[bool, str]:
    """
    RANGE icin 4 kosul — hepsi gecmeli:
      1) giris seviyesinde gecmis ret var
      2) son mumlar seviyeye/banda saygi gosteriyor (>50%)
      3) CVD karsi degil
      4) TP tarafi (karsi band kenari) de en az bir kez test edilmis — cift tarafli kanal
    """
    side = str(side or "").upper()
    support = float(levels.get("active_support") or 0)
    resistance = float(levels.get("active_resistance") or 0)
    if side in ("BUY", "LONG"):
        level = support
        direction = "BUY"
        label = "Destek"
        tp_label = "Direnc (TP)"
    elif side in ("SELL", "SHORT"):
        level = resistance
        direction = "SELL"
        label = "Direnc"
        tp_label = "Destek (TP)"
    else:
        return False, "Gecersiz yon."

    if level <= 0:
        return False, f"{label} seviyesi yok."

    historical = level_reliability(
        bars15, level, direction, support=support, resistance=resistance
    )
    if historical <= 0.0:
        return False, f"Kosul 1: {label} test edilmemis (ret=0%)."

    recent = level_respect_now(
        bars15, level, direction, support=support, resistance=resistance
    )
    if recent <= 0.5:
        return False, (
            f"Kosul 2: {label} tutmuyor (saygi={recent:.0%}, esik>50%)."
        )

    momentum = cvd_supports_level(cvd, direction)
    if momentum <= 0.0:
        cvd_dir = str((cvd or {}).get("direction") or "?")
        return False, f"Kosul 3: CVD karsi ({cvd_dir})."

    tp_ret = level_tp_reliability(bars15, support, resistance, side)
    if tp_ret <= 0.0:
        return False, (
            f"Kosul 4: {tp_label} test edilmemis (tp_ret=0%) — kanal tek tarafli."
        )

    return True, (
        f"ret={historical:.0%} saygi={recent:.0%} tp_ret={tp_ret:.0%}"
    )


def band_is_stable(
    bars15: list[dict],
    support: float,
    resistance: float,
) -> tuple[bool, str]:
    """
    Band stabilitesi — indikatör yok, yalnizca guncel band + ayni penceredeki mumlar:
      1) band icinde kapanis orani
      2) son bar/adim hareketi / band genisligi (anlik gurultu)
      3) kapanis yogunlasmasi (std / genislik)
    3 metrikten en az 2'si gecerse stabil.
    """
    if not bars15 or resistance <= support:
        return False, "band gecersiz"

    band_width = resistance - support
    band_width_pct = band_width / resistance if resistance > 0 else 0.0

    n = max(3, min(8, int(band_width_pct * 500)))
    recent = bars15[-n:]
    if not recent:
        return False, "yetersiz mum"

    closes = [float(b.get("close", 0) or 0) for b in recent]

    inside = sum(1 for c in closes if support < c < resistance)
    inside_ratio = inside / len(recent)

    last_bar = recent[-1]
    last_range = float(last_bar.get("high", 0) or 0) - float(last_bar.get("low", 0) or 0)
    last_step = abs(closes[-1] - closes[-2]) if len(closes) >= 2 else last_range
    move_px = max(last_range, last_step)
    move_ratio = move_px / band_width if band_width > 0 else 1.0

    if len(closes) > 1:
        concentration = stdev(closes) / band_width if band_width > 0 else 1.0
    else:
        concentration = 1.0

    score = 0
    if inside_ratio >= 0.60:
        score += 1
    if move_ratio < 0.40:
        score += 1
    if concentration < 0.25:
        score += 1

    stable = score >= 2
    reason = (
        f"inside={inside_ratio:.0%} "
        f"move={move_ratio:.2f} "
        f"conc={concentration:.2f} "
        f"score={score}/3 N={n}"
    )
    return stable, reason


def get_breakout_reference_levels(price: float = 0.0) -> dict:
    """
    Kirilim referansi: son bilinen oturum bandi (persist) veya guncel aktif S/R.
    range_valid gerekmez — destek alti kapanis buradaki ref_s ile olculur.
    """
    px = float(price or effective_price() or state.mark_price or state.price or 0)
    snap = state.v3_levels or {}
    active = snap.get("active") or {}
    s = float((active.get("support") or {}).get("price", 0) or 0)
    r = float((active.get("resistance") or {}).get("price", 0) or 0)

    persisted = _restore_persisted_active()
    ps = float((persisted.get("support") or {}).get("price", 0) or 0)
    pr = float((persisted.get("resistance") or {}).get("price", 0) or 0)

    ref_s = ps if ps > 0 else s
    ref_r = pr if pr > 0 else r
    source = "session" if ps > 0 else "active"
    if ref_r <= ref_s and r > s:
        ref_s, ref_r = s, r
        source = "active"

    return {
        "support": ref_s,
        "resistance": ref_r,
        "session_support": ps,
        "session_resistance": pr,
        "active_support": s,
        "active_resistance": r,
        "source": source,
        "price": px,
    }


def get_levels_snapshot(price: float = 0.0) -> dict:
    snap = state.v3_levels or {}
    active = snap.get("active") or {}
    if not snap:
        snap = update_levels()
        active = snap.get("active") or {}
    support = active.get("support") or {}
    resistance = active.get("resistance") or {}
    px = float(price or snap.get("price", 0) or effective_price() or state.mark_price or 0)
    s_px = float(support.get("price", 0) or 0)
    r_px = float(resistance.get("price", 0) or 0)
    rw = max(r_px - s_px, 0.0)
    rpos = ((px - s_px) / rw) if rw > 0 and px > 0 else float(active.get("range_position", 0.5) or 0.5)
    zone = zone_for_price(s_px, r_px, px)
    return {
        "price": px,
        "all_levels": snap.get("levels") or [],
        "active_support": s_px,
        "active_resistance": r_px,
        "support": support,
        "resistance": resistance,
        "range_width": rw if rw > 0 else float(active.get("range_width", 0) or 0),
        "range_mid": (s_px + r_px) / 2.0 if s_px > 0 and r_px > s_px else float(active.get("range_mid", 0) or 0),
        "range_position": rpos,
        "zone": zone,
        "range_valid": bool(active.get("range_valid")),
        "channel_confirmed": bool(active.get("channel_confirmed")),
        "channel_mode": str(active.get("channel_mode") or ""),
        "active_locked": bool(active.get("locked")),
        "extreme_fallback": bool(active.get("extreme_fallback")),
        "outer_support": float(active.get("outer_support_price", 0) or 0),
        "outer_resistance": float(active.get("outer_resistance_price", 0) or 0),
        "chart_levels": build_chart_levels(snap.get("levels") or [], active, px),
    }
