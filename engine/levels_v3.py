"""
engine/levels_v3.py

Referans klasordeki seviye mantiginin mevcut runtime'a uyarlanmis hali.
Shelf + failed break + acceptance + swing + local trigger ile seviyeleri skorlar.
"""
from __future__ import annotations

import json
import time
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
_last_level_change_key = ""


def _swing_candidates_for_tf(
    bars: list[dict], lookback: int, *, is_htf: bool = False, price: float = 0
) -> tuple[list[dict], list[dict]]:
    """Pine SR pivot (varsayilan) veya legacy simetrik swing."""
    if getattr(cfg, "V3_SR_ENABLED", True):
        try:
            from engine.sr_levels_v3 import find_sr_swing_candidates

            return find_sr_swing_candidates(bars, is_htf=is_htf, current_price=price)
        except Exception as e:
            log.warning(f"[LEVELS] SR pivot kullanilamadi, legacy swing: {e}")
    return _find_swing_candidates(bars, lookback)


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
    touch_count = int(level.get("touch_count", 0) or level.get("sr_touches", 0) or 0)
    if touch_count >= 4:
        score += 2
    elif touch_count == 3:
        score += 1
    elif touch_count == 1:
        score -= 1
    sr_imp = int(level.get("sr_importance", 0) or 0)
    if sr_imp >= 4:
        score += 3
    elif sr_imp >= 3:
        score += 2
    elif sr_imp >= 2:
        score += 1
    if level.get("is_local_trigger"):
        score += 1
    if level.get("is_htf"):
        score += 2
    if level.get("sr_source"):
        score = max(score, int(level.get("sr_importance", 0) or 0))
        if int(level.get("sr_touches", 0) or 0) >= 2:
            score += 1
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


def _dedupe_sr_pivot_levels(levels: list[dict], price: float = 0) -> list[dict]:
    """Pivot S/R — grafikteki her cizgi ayri; genis merge yok."""
    px = float(price or 0)
    tol_pct = float(getattr(cfg, "V3_SR_MERGE_TOL_PCT", 0.0006) or 0.0006)
    out: list[dict] = []
    for kind in ("support", "resistance"):
        pool = sorted(
            [
                l
                for l in levels
                if l.get("sr_source") and str(l.get("kind") or "") == kind
            ],
            key=lambda x: float(x.get("price", 0) or 0),
        )
        for lvl in pool:
            p = float(lvl.get("price", 0) or 0)
            if p <= 0:
                continue
            tol = max(p * tol_pct, 0.8)
            if any(
                str(o.get("kind")) == kind and abs(float(o.get("price", 0) or 0) - p) <= tol
                for o in out
            ):
                continue
            copy = dict(lvl)
            copy["strength"] = copy.get("strength") or "MEDIUM"
            if int(copy.get("score", 0) or 0) < cfg.V3_LEVEL_SCORE_WEAK:
                copy["score"] = max(
                    int(copy.get("score", 0) or 0),
                    int(copy.get("sr_importance", 0) or 0) + 2,
                )
            out.append(copy)
    if px > 0:
        out.sort(
            key=lambda x: (
                0 if str(x.get("kind")) == "support" else 1,
                abs(float(x.get("price", 0) or 0) - px),
            ),
        )
    return out


def _merge_levels_for_snap(levels: list[dict], bars: list[dict], price: float = 0) -> list[dict]:
    if getattr(cfg, "V3_SR_ENABLED", True):
        sr = [l for l in levels if l.get("sr_source")]
        rest = [l for l in levels if not l.get("sr_source")]
        merged_sr = _dedupe_sr_pivot_levels(sr, price)
        merged_rest = _merge_levels(rest, bars) if rest else []
        return merged_sr + merged_rest
    return _merge_levels(levels, bars)


def _active_from_swing_structure(price: float) -> dict | None:
    """
    Real-time swing bant — indikatör yok, gecikme yok.

    state.swing_highs_15m / swing_lows_15m: structure.py tarafından her 15m
    kapanışında SWING_LB_15M=10 bar lookback ile güncellenir.
    Bu 2.5 saat veri — Pine SR'ın 17 saatlik gecikmesinden çok daha anlık.

    Ek kaynak: market_state.layers (demand_weak / supply_mid) likidite motorundan,
    tarihsel hesaplama değil anlık yapısal bilgi.
    """
    px = float(price or 0)
    if px <= 0 or not getattr(cfg, "V3_SWING_BAND_ENABLED", True):
        return None

    highs = list(state.swing_highs_15m or [])
    lows = list(state.swing_lows_15m or [])
    if not highs and not lows:
        return None

    # Fiyat üstündeki en yakın swing high → direnç
    above = sorted(
        [h for h in highs if float(h.get("price", 0) or 0) > px * 1.0002],
        key=lambda x: float(x.get("price", 0) or 0),
    )
    # Fiyat altındaki en yakın swing low → destek
    below = sorted(
        [l for l in lows if float(l.get("price", 0) or 0) < px * 0.9998],
        key=lambda x: -float(x.get("price", 0) or 0),
    )

    # Ek kaynak: market_state demand/supply layers
    ms = getattr(state, "v3_market_state", None) or {}
    layers = ms.get("layers") or getattr(state, "v3_zone_layers", None) or {}
    dw = layers.get("demand_weak") or {}
    sm = layers.get("supply_mid") or {}
    dw_lo = float(dw.get("low") or 0)
    sm_hi = float(sm.get("high") or 0)

    # En iyi direnç: en yakın swing high VEYA supply_mid üst kenarı
    r_candidates = []
    if above:
        r_candidates.append(float(above[0].get("price", 0) or 0))
    if sm_hi > px:
        r_candidates.append(sm_hi)
    r_px = min(r_candidates) if r_candidates else 0.0

    # En iyi destek: en yakın swing low VEYA demand_weak alt kenarı
    s_candidates = []
    if below:
        s_candidates.append(float(below[0].get("price", 0) or 0))
    if 0 < dw_lo < px:
        s_candidates.append(dw_lo)
    s_px = max(s_candidates) if s_candidates else 0.0

    if s_px <= 0 or r_px <= 0 or r_px <= s_px:
        return None

    min_width = max(px * 0.004, 4.0)  # min %0.4 band genişliği
    if r_px - s_px < min_width:
        return None

    s_lvl = {
        "price": round(s_px, 2), "kind": "support",
        "strength": "MEDIUM", "score": 8,
        "timeframe": "swing", "is_swing": True,
    }
    r_lvl = {
        "price": round(r_px, 2), "kind": "resistance",
        "strength": "MEDIUM", "score": 8,
        "timeframe": "swing", "is_swing": True,
    }
    active = _finalize_active_pair(s_lvl, r_lvl, px)
    active["swing_band"] = True
    active["locked"] = False
    log.info(
        f"[SWING_BAND] px={px:.2f} S={s_px:.2f} R={r_px:.2f} "
        f"(swing highs={len(highs)} lows={len(lows)})"
    )
    return active


def _active_from_sr_pivots(levels: list[dict], price: float) -> dict | None:
    """Aktif bant = calculate_sr_levels en yakin destek + direnc."""
    px = float(price or 0)
    if px <= 0:
        return None

    from engine.sr_calculator import level_to_dict
    from engine.sr_levels_v3 import nearest_sr_from_calculator
    from engine.v3_common import bars_15m

    bars = bars_15m(int(getattr(cfg, "V3_LEVEL_MAX_AGE_15M", 96) or 96))

    sup_sr, res_sr = nearest_sr_from_calculator(bars, px, is_htf=False)
    if sup_sr and res_sr and float(res_sr.price) > float(sup_sr.price):
        s_lvl = level_to_dict(sup_sr, "15m")
        r_lvl = level_to_dict(res_sr, "15m")
        active = _finalize_active_pair(s_lvl, r_lvl, px)
        active["sr_active_band"] = True
        active["locked"] = True
        active["layer_band"] = False
        return active

    if not levels:
        return None
    sups = [
        l
        for l in levels
        if l.get("sr_source")
        and str(l.get("kind") or "") == "support"
        and float(l.get("price", 0) or 0) < px
    ]
    ress = [
        l
        for l in levels
        if l.get("sr_source")
        and str(l.get("kind") or "") == "resistance"
        and float(l.get("price", 0) or 0) > px
    ]
    if not sups or not ress:
        return None
    s_lvl = max(sups, key=lambda x: float(x.get("price", 0) or 0))
    r_lvl = min(ress, key=lambda x: float(x.get("price", 0) or 0))
    active = _finalize_active_pair(s_lvl, r_lvl, px)
    active["sr_active_band"] = True
    active["locked"] = True
    active["layer_band"] = False
    return active


def _trade_band_resistance_candidates(
    px: float,
    merged: list[dict],
    *,
    layers: dict | None = None,
    zones: list[dict] | None = None,
) -> list[tuple[float, dict]]:
    """Fiyatin ustundeki en yakin trade direnc adaylari (price, level_dict)."""
    out: list[tuple[float, dict]] = []
    seen: set[float] = set()

    def _add(price: float, lvl: dict) -> None:
        p = round(float(price), 2)
        if p <= px or p in seen:
            return
        seen.add(p)
        out.append((p, dict(lvl)))

    for lvl in merged or []:
        p = float(lvl.get("price", 0) or 0)
        if p <= px:
            continue
        kind = str(lvl.get("kind") or "")
        # Hem "resistance" hem de "support" olarak etiketlenen ama fiyat üstündeki
        # seviyeler direnç adayı — flip mekanizması buraya yansır
        if kind in ("resistance", "support"):
            flipped = dict(lvl)
            flipped["kind"] = "resistance"  # fiyat üstünde → direnç
            _add(p, flipped)

    for key in ("supply_mid", "supply_major"):
        layer = (layers or {}).get(key) or {}
        if not layer:
            continue
        hi = float(layer.get("high") or 0)
        lo = float(layer.get("low") or 0)
        if hi > px or lo > px:
            from engine.zone_layers_v3 import layer_to_level_dict

            pick = hi if hi > px else lo
            _add(pick, layer_to_level_dict(layer, "resistance"))

    for z in zones or []:
        lc = str(z.get("lifecycle") or "").upper()
        if lc not in ("ACTIVE", "ROLE_REVERSAL"):
            continue
        if str(z.get("role") or "").lower() != "resistance":
            continue
        hi = float(z.get("zone_high") or z.get("center") or 0)
        if hi <= px:
            continue
        p = round(float(z.get("center") or hi), 2)
        _add(
            hi,
            {
                "price": p,
                "kind": "resistance",
                "zone_status": str(z.get("status") or "resistance"),
                "lifecycle": lc,
                "sr_source": "zone_lifecycle",
                "strength": "STRONG"
                if int(z.get("strength", 0) or 0) >= 30
                else "MEDIUM",
                "score": max(6, int(z.get("strength", 0) or 0) // 5),
            },
        )

    out.sort(key=lambda item: item[0])
    return out


def _pick_trade_resistance_candidate(
    candidates: list[tuple[float, dict]],
    merged: list[dict],
    px: float,
    *,
    sr_active: dict | None = None,
) -> tuple[float, dict] | None:
    """
    Trade direnci: yakin cluster icinde Pine/SR pivot, eski zone adayinin onune gecer.
    """
    if not candidates:
        return None
    best_price, best_lvl = candidates[0]
    cluster_tol = max(px * float(getattr(cfg, "V3_TRADE_BAND_CLUSTER_PCT", 0.012) or 0.012), 10.0)

    sr_r = 0.0
    sr_lvl: dict = {}
    if isinstance(sr_active, dict) and sr_active.get("sr_active_band"):
        sr_lvl = dict(sr_active.get("resistance") or {})
        sr_r = float(sr_lvl.get("price", 0) or 0)
    if sr_r <= px:
        sr_r = 0.0
        sr_lvl = {}

    if not sr_r:
        pivot_ress = [
            (float(l.get("price", 0) or 0), dict(l))
            for l in (merged or [])
            if str(l.get("kind") or "") == "resistance"
            and l.get("sr_source") == "pivot"
            and float(l.get("price", 0) or 0) > px
        ]
        if pivot_ress:
            pivot_ress.sort(key=lambda x: x[0])
            sr_r, sr_lvl = pivot_ress[0]

    if sr_r > px and sr_lvl:
        if best_lvl.get("sr_source") == "zone_lifecycle" and sr_r > best_price:
            if (sr_r - best_price) <= cluster_tol:
                return sr_r, sr_lvl
        for price, lvl in candidates:
            if abs(price - sr_r) <= 0.05:
                return price, lvl
        if sr_r > best_price:
            return sr_r, sr_lvl

    return best_price, best_lvl


def _persist_support_too_stale(support_px: float, price: float) -> bool:
    """Persist destegi fiyattan cok uzaksa (or. 1601 @ px 1621) makro kirli sayilir."""
    ps = float(support_px or 0)
    px = float(price or 0)
    if ps <= 0 or px <= 0 or ps >= px:
        return False
    max_gap = float(getattr(cfg, "V3_PERSIST_MAX_SUPPORT_GAP_PCT", 0.012) or 0.012)
    return (px - ps) / px > max_gap


def _ladder_macro_band_candidate(price: float) -> dict:
    """Tarihsel merdivenden stabil/anlamli makro kanal — kirli persist yerine."""
    px = float(price or 0)
    if px <= 0 or not bool(getattr(cfg, "V3_LADDER_ENABLED", True)):
        return {}
    try:
        from engine.level_ladder_v3 import (
            build_level_ladder,
            meaningful_band,
            stable_macro_channel,
        )
    except Exception:
        return {}
    ladder = build_level_ladder(px)
    if not ladder:
        return {}
    ch = stable_macro_channel(px, ladder)
    ms = float(ch.get("support", 0) or 0)
    mr = float(ch.get("resistance", 0) or 0)
    if ms > 0 and mr > ms and ms < px < mr:
        out = _finalize_active_pair(
            _ladder_level_dict(ms, "support"),
            _ladder_level_dict(mr, "resistance"),
            px,
        )
        out["ladder_macro"] = True
        return out
    mb = meaningful_band(px, ladder)
    if not mb:
        return {}
    ns = float(mb.get("support", 0) or 0)
    nr = float(mb.get("resistance", 0) or 0)
    if not (0 < ns < px < nr):
        return {}
    out = _finalize_active_pair(
        _ladder_level_dict(ns, "support"),
        _ladder_level_dict(nr, "resistance"),
        px,
    )
    out["ladder_macro"] = True
    return out


def _deepen_support_from_meaningful_band(
    active: dict,
    price: float,
) -> dict:
    """
    Dar pivot bandi (or. 1612/1621) icin destegi merdiven/demand_liq tabanina
    indir (or. 1606); direnc ayni kalir — gercek islem kanali.
    """
    px = float(price or 0)
    if px <= 0 or not active:
        return active
    s_lvl = dict(active.get("support") or {})
    r_lvl = dict(active.get("resistance") or {})
    s = float(s_lvl.get("price", 0) or 0)
    r = float(r_lvl.get("price", 0) or 0)
    if not (0 < s < px < r):
        return active
    min_w = px * float(getattr(cfg, "V3_BAND_MIN_WIDTH_PCT", 0.008) or 0.008)
    min_sep = max(px * float(getattr(cfg, "V3_SR_ACTIVE_MIN_SEP_PCT", 0.0025) or 0.0025), 4.0)
    if (r - s) >= max(min_w, min_sep):
        return active
    if not bool(getattr(cfg, "V3_LADDER_ENABLED", True)):
        return active

    ns = 0.0
    try:
        from engine.level_ladder_v3 import meaningful_band

        mb = meaningful_band(px)
        if mb:
            ns = float(mb.get("support", 0) or 0)
    except Exception:
        ns = 0.0

    layers = getattr(state, "v3_zone_layers", None) or {}
    dl = layers.get("demand_liq") or {}
    dl_px = float(dl.get("center") or dl.get("low") or 0)
    if dl_px > 0 and dl_px < px and (ns <= 0 or abs(dl_px - px) < abs(ns - px)):
        if dl_px < s or ns <= 0:
            ns = dl_px

    if not (0 < ns < s and ns < px):
        return active
    if (r - ns) < max(min_w, min_sep) * 0.5:
        return active

    new_s = _ladder_level_dict(ns, "support")
    out = _finalize_active_pair(new_s, r_lvl, px)
    for key in (
        "sr_active_band",
        "locked",
        "layer_band",
        "trade_band",
        "macro_support",
        "macro_resistance",
        "macro_range_valid",
        "min_width_expanded",
        "entry_band_frozen",
        "channel_traversed",
        "zone",
    ):
        if key in active:
            out[key] = active[key]
    out["ladder_fill"] = True
    out["support_deepened"] = True
    log.info(
        f"[LEVELS] destek derinlestirildi {s:.2f} -> {ns:.2f} "
        f"R={r:.2f} px={px:.2f} (anlamli kanal)"
    )
    return out


def _support_from_ladder_fallback(price: float) -> dict | None:
    """Tarihsel merdivenden fiyatin altindaki en yakin destek (impulse sonrasi macro yerine)."""
    if price <= 0:
        return None
    try:
        from engine.level_ladder_v3 import build_level_ladder, nearest_below
    except Exception:
        return None
    ladder = build_level_ladder(price)
    if not ladder:
        return None
    nb = nearest_below(price, ladder)
    if not nb:
        return None
    px = float(nb.get("price", 0) or 0)
    if not (0 < px < price):
        return None
    return _ladder_level_dict(px, "support")


def _prefer_ladder_support(
    s_lvl: dict,
    price: float,
    macro_sup: dict | None,
) -> dict:
    """Macro zemin (or. 1601) yerine anlamli merdiven/demand_liq destegini tercih et."""
    px = float(price or 0)
    cur_px = float(s_lvl.get("price", 0) or 0)
    macro_px = float((macro_sup or {}).get("price", 0) or 0)

    meaningful_px = 0.0
    if bool(getattr(cfg, "V3_LADDER_ENABLED", True)):
        try:
            from engine.level_ladder_v3 import meaningful_band

            mb = meaningful_band(px)
            if mb:
                meaningful_px = float(mb.get("support", 0) or 0)
        except Exception:
            meaningful_px = 0.0

    layers = getattr(state, "v3_zone_layers", None) or {}
    dl = layers.get("demand_liq") or {}
    dl_px = float(dl.get("center") or dl.get("low") or 0)
    if dl_px > 0 and dl_px < px:
        if meaningful_px <= 0 or abs(dl_px - px) <= abs(meaningful_px - px) + 2.0:
            meaningful_px = dl_px

    pick_px = meaningful_px
    if pick_px <= 0 or pick_px >= px:
        ladder_sup = _support_from_ladder_fallback(px)
        if ladder_sup:
            pick_px = float(ladder_sup.get("price", 0) or 0)
        else:
            return s_lvl
    if pick_px <= 0 or pick_px >= px:
        return s_lvl

    # Macro taban veya dar pivot ustunde anlamli taban varsa onu kullan
    if macro_px > 0 and pick_px > macro_px + 0.5:
        out = _ladder_level_dict(pick_px, "support")
        out["ladder_fill"] = True
        return out
    if cur_px <= 0 or macro_px == cur_px:
        if pick_px > macro_px + 0.5 or macro_px <= 0:
            out = _ladder_level_dict(pick_px, "support")
            out["ladder_fill"] = True
            return out
    # Dar bant: en yakin pivot yerine daha derin anlamli destek
    if 0 < cur_px < px and pick_px < cur_px - 0.5:
        out = _ladder_level_dict(pick_px, "support")
        out["ladder_fill"] = True
        out["support_deepened"] = True
        return out
    return s_lvl


def _resolve_trade_band(
    macro_active: dict,
    merged: list[dict],
    price: float,
    *,
    bars15: list[dict],
    layers: dict | None = None,
    zones: list[dict] | None = None,
    sr_active: dict | None = None,
) -> dict | None:
    """
    Dar islem bandi: makro destek + fiyatin ustundeki en yakin anlamli direnc.
    Makro direnc (or. 2006) trade R olmaz; yalnizca baglam/persist icin kalir.
    """
    px = float(price or 0)
    if px <= 0:
        return None
    macro_sup = macro_active.get("support") or {}
    macro_res = macro_active.get("resistance") or {}
    macro_s = float(macro_sup.get("price", 0) or 0)
    macro_r = float(macro_res.get("price", 0) or 0)
    if macro_s <= 0 or macro_r <= macro_s:
        return None

    # En yakın destek: önce merged içindeki SR desteklerini dene, yoksa makro
    sups_all = sorted(
        [
            l for l in (merged or [])
            if str(l.get("kind") or "") == "support"
            and 0 < float(l.get("price", 0) or 0) < px
        ],
        key=lambda x: -float(x.get("price", 0) or 0),
    )
    if sups_all:
        # En yakın SR desteği — makro çok uzaktaysa bunu tercih et
        nearest_sr_s = sups_all[0]
        nearest_sr_s_px = float(nearest_sr_s.get("price", 0) or 0)
        # Fiyatın hemen altındaki gerçek SR desteği makro desteğin ÜZERİNDEyse,
        # trade bandının tabanı odur (grafik "aktif destek" etiketi + zone bunu
        # kullanır). Aksi halde 7-10 pt aşağıdaki makro destek "aktif" görünür ve
        # fiyat aslında desteğin üstündeyken band ortada sanılır.
        if macro_s < nearest_sr_s_px < px:
            s_lvl = nearest_sr_s
        elif macro_s >= px:
            s_lvl = nearest_sr_s
        else:
            s_lvl = dict(macro_sup)
    elif macro_s >= px:
        sups = [
            l for l in (merged or [])
            if str(l.get("kind") or "") == "support"
            and 0 < float(l.get("price", 0) or 0) < px
        ]
        if sups:
            s_lvl = max(sups, key=lambda x: float(x.get("price", 0) or 0))
        else:
            ladder_only = _support_from_ladder_fallback(px)
            if ladder_only:
                s_lvl = ladder_only
                s_lvl["ladder_fill"] = True
            else:
                return None
    else:
        s_lvl = dict(macro_sup)
    s_lvl = _prefer_ladder_support(s_lvl, px, macro_sup)
    trade_s = float(s_lvl.get("price", 0) or 0)

    candidates = _trade_band_resistance_candidates(
        px, merged, layers=layers, zones=zones
    )
    if not candidates:
        return None

    picked = _pick_trade_resistance_candidate(
        candidates, merged, px, sr_active=sr_active
    )
    if not picked:
        return None

    min_sep = max(px * float(getattr(cfg, "V3_SR_ACTIVE_MIN_SEP_PCT", 0.0025) or 0.0025), 4.0)
    trade_r_price, r_lvl = picked
    if trade_r_price - trade_s < max(_band_min_width(px, bars15), min_sep):
        return None
    if not (trade_s < px < trade_r_price):
        return None

    active = _finalize_active_pair(s_lvl, r_lvl, px)
    active["trade_band"] = True
    active["locked"] = False
    active["macro_support"] = macro_s
    active["macro_resistance"] = macro_r
    active["macro_range_valid"] = bool(macro_active.get("range_valid"))
    return active


def _level_dict_near_price(
    merged: list[dict] | None, kind: str, price: float
) -> dict:
    px = float(price or 0)
    if px <= 0:
        return {}
    for lvl in merged or []:
        if str(lvl.get("kind") or "") != kind:
            continue
        if abs(float(lvl.get("price", 0) or 0) - px) <= 0.05:
            return dict(lvl)
    return {"price": px, "kind": kind, "strength": "MEDIUM", "score": 6}


def _macro_band_from_prices(
    support_lvl: dict,
    macro_s: float,
    macro_r: float,
    price: float,
    *,
    merged: list[dict] | None = None,
) -> dict:
    ms = float(macro_s or 0)
    mr = float(macro_r or 0)
    if ms <= 0 or mr <= ms:
        return {}
    sup = dict(support_lvl or {})
    if float(sup.get("price", 0) or 0) != ms:
        sup = _level_dict_near_price(merged, "support", ms) or {
            **sup,
            "price": ms,
            "kind": "support",
        }
    res = _level_dict_near_price(merged, "resistance", mr)
    if not res:
        res = {"price": mr, "kind": "resistance", "strength": "MEDIUM", "score": 6}
    out = _finalize_active_pair(sup, res, price)
    out.pop("trade_band", None)
    return out


def _active_macro_prices(active: dict) -> tuple[float, float]:
    ms = float(active.get("macro_support") or 0)
    mr = float(active.get("macro_resistance") or 0)
    if ms > 0 and mr > ms:
        return ms, mr
    s = float((active.get("support") or {}).get("price", 0) or 0)
    r = float((active.get("resistance") or {}).get("price", 0) or 0)
    return s, r


def _active_is_narrow_trade_band(active: dict) -> bool:
    if not active.get("support") or not active.get("resistance"):
        return False
    if active.get("trade_band"):
        return True
    ms, mr = _active_macro_prices(active)
    r = float((active.get("resistance") or {}).get("price", 0) or 0)
    return mr > r + 1.0 and ms > 0 and r > ms


def _macro_band_candidate_valid(
    candidate: dict | None, price: float, *, min_resistance: float = 0.0
) -> bool:
    if not isinstance(candidate, dict):
        return False
    if not candidate.get("support") or not candidate.get("resistance"):
        return False
    s = float((candidate.get("support") or {}).get("price", 0) or 0)
    r = float((candidate.get("resistance") or {}).get("price", 0) or 0)
    if s <= 0 or r <= s or price <= s:
        return False
    if min_resistance > 0 and r < min_resistance - 0.05:
        return False
    return True


def _widen_persisted_macro_from_layers(
    persisted: dict, price: float, bars15: list[dict], merged: list[dict] | None
) -> dict:
    """Dar trade bandi makro olarak kaydedilmisse supply_major ile genislet."""
    if not persisted.get("support") or not persisted.get("resistance"):
        return persisted
    ps = float(persisted["support"].get("price", 0) or 0)
    pr = float(persisted["resistance"].get("price", 0) or 0)
    if ps <= 0 or pr <= ps:
        return persisted
    layers = getattr(state, "v3_zone_layers", None) or {}
    sup_major = layers.get("supply_major") or {}
    if not sup_major:
        return persisted
    hi = float(sup_major.get("high") or 0)
    lo = float(sup_major.get("low") or hi)
    macro_r = (hi + lo) / 2.0 if hi > 0 else 0.0
    if macro_r <= pr * 1.08 or macro_r <= pr:
        return persisted
    if not (ps < price < macro_r):
        return persisted
    from engine.zone_layers_v3 import layer_to_level_dict

    out = dict(persisted)
    out["resistance"] = layer_to_level_dict(sup_major, "resistance")
    out["resistance"]["price"] = round(macro_r, 2)
    log.info(
        f"[LEVELS] persist makro genisletildi S={ps:.2f} R={pr:.2f} -> {macro_r:.2f} "
        f"(supply_major) px={price:.2f}"
    )
    return out


def _resolve_macro_band_for_overlay(
    snap: dict,
    price: float,
    bars15: list[dict],
    *,
    merged: list[dict] | None = None,
) -> dict:
    """
    Trade band overlay oncesi makro bant — onceki tick'in dar trade S/R'si kullanilmaz.
    """
    px = float(price or 0)
    if px <= 0:
        return {}
    active = snap.get("active") or {}
    trade_r = float((active.get("resistance") or {}).get("price", 0) or 0)

    for candidate in (
        snap.get("macro_band"),
        (state.v3_levels or {}).get("macro_band"),
    ):
        if not isinstance(candidate, dict):
            continue
        if _macro_band_candidate_valid(candidate, px, min_resistance=trade_r):
            return dict(candidate)

    for src in (active, (state.v3_levels or {}).get("active") or {}):
        if not isinstance(src, dict):
            continue
        ms, mr = _active_macro_prices(src)
        if ms > 0 and mr > ms and mr > trade_r + 0.5:
            macro = _macro_band_from_prices(
                src.get("support") or {},
                ms,
                mr,
                px,
                merged=merged,
            )
            if macro:
                return macro

    layers = snap.get("zone_layers") or getattr(state, "v3_zone_layers", None) or {}
    sup_major = layers.get("supply_major") or {}
    if sup_major and active.get("support"):
        hi = float(sup_major.get("high") or 0)
        lo = float(sup_major.get("low") or hi)
        macro_r = (hi + lo) / 2.0 if hi > 0 else 0.0
        macro_s = float((active.get("support") or {}).get("price", 0) or 0)
        if macro_r > trade_r + 0.5 and macro_s > 0 and macro_s < px < macro_r:
            from engine.zone_layers_v3 import layer_to_level_dict

            return _finalize_active_pair(
                active["support"],
                layer_to_level_dict(sup_major, "resistance"),
                px,
            )

    ladder_macro = _ladder_macro_band_candidate(px)
    if ladder_macro:
        lms = float((ladder_macro.get("support") or {}).get("price", 0) or 0)
        lmr = float((ladder_macro.get("resistance") or {}).get("price", 0) or 0)
        if lms > 0 and lmr > lms and (trade_r <= 0 or lmr > trade_r + 0.5):
            return ladder_macro

    persisted = _widen_persisted_macro_from_layers(
        _restore_persisted_active(), px, bars15, merged
    )
    ps = float((persisted.get("support") or {}).get("price", 0) or 0)
    pr = float((persisted.get("resistance") or {}).get("price", 0) or 0)
    if (
        ps > 0
        and pr > ps
        and pr > trade_r + 0.5
        and ps < px < pr
        and not _persist_support_too_stale(ps, px)
    ):
        return _finalize_active_pair(
            persisted["support"], persisted["resistance"], px
        )

    if active.get("support") and active.get("resistance") and not _active_is_narrow_trade_band(
        active
    ):
        act_r = float((active.get("resistance") or {}).get("price", 0) or 0)
        hi = float(sup_major.get("high") or 0) if sup_major else 0.0
        if hi <= 0 or act_r >= hi * 0.92:
            return dict(active)

    if active.get("support") and active.get("resistance"):
        return dict(active)
    return {}


def _apply_trade_band_overlay(snap: dict, price: float, bars15: list[dict]) -> dict:
    if not getattr(cfg, "V3_TRADE_BAND_ENABLED", True):
        return snap

    # Açık pozisyon için giriş bandı dondurulmuşsa overlay bunu ezmesin
    cur_active = snap.get("active") or {}
    if cur_active.get("entry_band_frozen") and cur_active.get("locked"):
        # Sadece macro_band bilgisini ekle, aktif bandı değiştirme
        macro_info = _resolve_macro_band_for_overlay(
            snap, price, bars15, merged=snap.get("levels") or []
        )
        if macro_info.get("support") and macro_info.get("resistance"):
            snap["macro_band"] = dict(macro_info)
        return snap

    macro = _resolve_macro_band_for_overlay(
        snap, price, bars15, merged=snap.get("levels") or []
    )
    if not macro.get("support") or not macro.get("resistance"):
        return snap
    snap["macro_band"] = dict(macro)
    layers = snap.get("zone_layers") or getattr(state, "v3_zone_layers", None) or {}
    zones = snap.get("zones") or []
    trade = _resolve_trade_band(
        macro,
        snap.get("levels") or [],
        price,
        bars15=bars15,
        layers=layers,
        zones=zones,
        sr_active=snap.get("active"),
    )
    if trade:
        cur_s = float((cur_active.get("support") or {}).get("price", 0) or 0)
        trade_s_new = float((trade.get("support") or {}).get("price", 0) or 0)
        if cur_active.get("ladder_fill") and cur_s > 0 and trade_s_new > 0:
            if trade_s_new < cur_s - 0.5 and cur_s < price:
                trade["support"] = cur_active.get("support")
                trade["ladder_fill"] = True
            elif cur_s > trade_s_new and cur_s < price:
                trade["support"] = cur_active.get("support")
                trade["ladder_fill"] = True
        snap["active"] = trade
        ts = float((trade.get("support") or {}).get("price", 0) or 0)
        tr = float((trade.get("resistance") or {}).get("price", 0) or 0)
        ms = float(trade.get("macro_support") or 0)
        mr = float(trade.get("macro_resistance") or 0)
        prev_active = (state.v3_levels or {}).get("active") or {}
        prev_res = prev_active.get("resistance") or {}
        prev_tr = float(prev_res.get("price", 0) or 0)
        if prev_tr > 0 and abs(tr - prev_tr) > 0.5:
            log.info(
                f"[LEVELS] trade R guncellendi {prev_tr:.2f} -> {tr:.2f} px={price:.2f}"
            )
        traverse = _trade_channel_traversed(bars15, ts, tr)
        state.v3_trade_channel_traversed = traverse
        trade["channel_traversed"] = traverse
        log.info(
            f"[LEVELS] trade bant px={price:.2f} S={ts:.2f} R={tr:.2f} "
            f"zone={trade.get('zone')} | macro S={ms:.2f} R={mr:.2f}"
            f"{' | traverse=evet' if traverse else ''}"
        )
    return snap


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
        has_htf = any(item.get("is_htf") for item in group)
        has_ltf = any(not item.get("is_htf") for item in group)
        bonus = min(len(group) - 1, 3)
        if has_htf and has_ltf:
            mult = max(int(getattr(cfg, "V3_HTF_CONFLUENCE_MULT", 2) or 2), 1)
            bonus *= mult
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
    result["edge_zone"] = zone_for_price(s, r, price, bars15=recent_20)
    result["zone"] = price_zone_for_band(s, r, price)
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
    """Dashboard: tum pivot S/R cizgileri (Pine pivot_high/low — fiyatin ust/alti degil)."""
    if not getattr(cfg, "V3_SR_ENABLED", True):
        return []
    if not merged:
        return []
    max_lines = int(
        getattr(cfg, "V3_SR_CHART_MAX_LINES", 0)
        or getattr(cfg, "V3_SR_NUM_LEVELS", 6)
        or 6
    )
    px = float(price or effective_price() or state.mark_price or 0)
    main_s = float((active.get("support") or {}).get("price", 0) or 0)
    main_r = float((active.get("resistance") or {}).get("price", 0) or 0)
    out: list[dict] = []
    for lvl in merged:
        if getattr(cfg, "V3_SR_ONLY", True):
            pass
        elif not lvl.get("sr_source"):
            continue
        p = float(lvl.get("price", 0) or 0)
        kind = str(lvl.get("kind") or "")
        if p <= 0 or kind not in ("support", "resistance"):
            continue
        is_main = (kind == "support" and abs(p - main_s) < 0.05) or (
            kind == "resistance" and abs(p - main_r) < 0.05
        )
        out.append(
            {
                "price": p,
                "kind": kind,
                "importance": int(lvl.get("sr_importance", 0) or 0),
                "source": str(lvl.get("sr_source") or "pivot"),
                "primary": is_main,
            }
        )
    out.sort(
        key=lambda x: (
            0 if x["kind"] == "resistance" else 1,
            -x["price"] if x["kind"] == "support" else x["price"],
        )
    )
    if px > 0:
        out.sort(
            key=lambda x: (
                0 if x["kind"] == "resistance" else 1,
                abs(x["price"] - px),
            )
        )
    return out[:max_lines]


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
    persisted = _widen_persisted_macro_from_layers(
        persisted, price, bars15, merged
    )
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
    if bool(persisted.get("extreme_fallback")) or bool(
        support.get("is_extreme_fallback")
    ):
        return False
    ps = float(support.get("price", 0) or 0)
    pr = float(resistance.get("price", 0) or 0)
    if not (ps < price < pr):
        return False
    width = pr - ps
    if width < _band_min_width(price, bars15):
        return False
    if _persist_support_too_stale(ps, price):
        ladder_macro = _ladder_macro_band_candidate(price)
        lms = float((ladder_macro.get("support") or {}).get("price", 0) or 0)
        if lms > ps + 0.5:
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


def _persist_band_payload(active: dict) -> dict:
    support = active.get("support")
    resistance = active.get("resistance")
    if not support or not resistance:
        return {}
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
    return payload


def _persist_active(active: dict) -> None:
    payload = _persist_band_payload(active)
    if not payload:
        return
    if active.get("trade_band"):
        return
    try:
        _PERSIST_FILE.parent.mkdir(parents=True, exist_ok=True)
        envelope = {"macro": payload}
        _PERSIST_FILE.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        log.warning(f"[LEVELS] aktif band kaydi yazilamadi: {e}")


def _restore_persisted_active() -> dict:
    try:
        if not _PERSIST_FILE.exists():
            return {}
        data = json.loads(_PERSIST_FILE.read_text(encoding="utf-8"))
        macro = data.get("macro") if isinstance(data.get("macro"), dict) else data
        support = macro.get("support")
        resistance = macro.get("resistance")
        if not support or not resistance:
            return {}
        out = {
            "support": support,
            "resistance": resistance,
            "locked": bool(macro.get("locked")),
            "extreme_fallback": bool(macro.get("extreme_fallback")),
        }
        if macro.get("outer_support"):
            out["outer_support"] = macro.get("outer_support")
        if macro.get("outer_resistance"):
            out["outer_resistance"] = macro.get("outer_resistance")
        if float(macro.get("outer_support_price", 0) or 0) > 0:
            out["outer_support_price"] = float(macro.get("outer_support_price"))
        if float(macro.get("outer_resistance_price", 0) or 0) > 0:
            out["outer_resistance_price"] = float(macro.get("outer_resistance_price"))
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


def _ladder_level_dict(price_level: float, kind: str) -> dict:
    """Merdiven seviyesinden sade level dict (extreme-fallback flag'i olmadan)."""
    return {
        "price": round(float(price_level), 2),
        "kind": kind,
        "strength": "MEDIUM",
        "score": 6,
        "source": "ladder",
    }


def _enforce_min_band_width(active: dict, price: float) -> dict:
    """
    Aktif band dejenere derecede darsa (ör. 6pt) tarihsel merdivenden anlamlı
    banda genişlet. Yalnız band min genişliğin altındaysa devreye girer.
    """
    if price <= 0 or not bool(getattr(cfg, "V3_LADDER_ENABLED", True)):
        return active
    s = float((active.get("support") or {}).get("price", 0) or 0)
    r = float((active.get("resistance") or {}).get("price", 0) or 0)
    if not (0 < s < price < r):
        return active
    min_w = price * float(getattr(cfg, "V3_BAND_MIN_WIDTH_PCT", 0.008) or 0.008)
    if (r - s) >= min_w:
        return active
    try:
        from engine.level_ladder_v3 import meaningful_band

        mb = meaningful_band(price)
    except Exception:
        mb = None
    if not mb:
        return active
    ns, nr = float(mb["support"]), float(mb["resistance"])
    if not (0 < ns < price < nr) or (nr - ns) <= (r - s):
        return active
    out = _finalize_active_pair(
        _ladder_level_dict(ns, "support"), _ladder_level_dict(nr, "resistance"), price
    )
    for k in ("macro_support", "macro_resistance", "macro_range_valid", "trade_band"):
        if k in active:
            out[k] = active[k]
    out["min_width_expanded"] = True
    log.info(
        f"[LEVELS] dar bant genisletildi {r - s:.1f}pt -> {nr - ns:.1f}pt "
        f"S={ns:.2f} R={nr:.2f} (tarihsel merdiven)"
    )
    return out


def _validate_band_against_ladder(active: dict, price: float) -> dict:
    """
    Aktif band kenarları touch-validated (≥V3_BAND_MIN_TOUCHES dokunuş) GERÇEK
    seviyeler mi? Değilse merdivenden anlamlı banda (meaningful_band) snap'le.
    Tek-wick / gürültü pivotu / likidite-katmanı kenarlarını eler — "1606.11
    destek değil" sorununun çözümü. HER döngüde çalışır (sadece fallback değil).
    """
    if price <= 0 or not bool(getattr(cfg, "V3_LADDER_ENABLED", True)):
        return active
    try:
        from engine.level_ladder_v3 import build_level_ladder, meaningful_band
    except Exception:
        return active
    ladder = build_level_ladder(price)
    if not ladder:
        return active
    min_touch = int(getattr(cfg, "V3_BAND_MIN_TOUCHES", 2) or 2)
    validated = [l for l in ladder if l["touches"] >= min_touch]
    if not validated:
        return active
    tol = price * float(getattr(cfg, "V3_LADDER_MERGE_PCT", 0.0015) or 0.0015)
    min_w = price * float(getattr(cfg, "V3_BAND_MIN_WIDTH_PCT", 0.008) or 0.008)
    s = float((active.get("support") or {}).get("price", 0) or 0)
    r = float((active.get("resistance") or {}).get("price", 0) or 0)

    def _is_validated(px0: float) -> bool:
        return any(abs(px0 - l["price"]) <= tol for l in validated)

    band_ok = (
        0 < s < price < r
        and _is_validated(s)
        and _is_validated(r)
        and (r - s) >= min_w * 0.6
    )
    if band_ok:
        return active  # kenarlar zaten gerçek seviye

    mb = meaningful_band(price, ladder)
    if not mb:
        return active
    ns, nr = float(mb["support"]), float(mb["resistance"])
    if not (0 < ns < price < nr):
        return active
    out = _finalize_active_pair(
        _ladder_level_dict(ns, "support"), _ladder_level_dict(nr, "resistance"), price
    )
    for k in ("macro_support", "macro_resistance", "macro_range_valid", "trade_band"):
        if k in active:
            out[k] = active[k]
    out["ladder_validated"] = True
    log.info(
        f"[LEVELS] S/R touch-validated snap: S {s:.2f}->{ns:.2f} R {r:.2f}->{nr:.2f} "
        f"(>={min_touch} dokunus, tarihsel merdiven)"
    )
    return out


def _fill_band_from_ladder(active: dict, price: float) -> dict | None:
    """
    Band eksikse (fiyat tüm pivotların dışında) tarihsel merdivenden en yakın
    GERÇEK seviyeyle doldur. Ayrıca stabil makro kanalı günceller.
    """
    if price <= 0:
        return None
    try:
        from engine.level_ladder_v3 import (
            build_level_ladder,
            meaningful_band,
            nearest_below,
            nearest_above,
            stable_macro_channel,
        )
    except Exception:
        return None

    ladder = build_level_ladder(price)
    if not ladder:
        return None

    # Önce anlamlı band (min genişlik + çok-dokunuş); yoksa en yakına düş.
    mb = meaningful_band(price, ladder)
    if mb:
        sup = _ladder_level_dict(mb["support"], "support")
        res = _ladder_level_dict(mb["resistance"], "resistance")
    else:
        sup = dict(active.get("support") or {})
        res = dict(active.get("resistance") or {})
        if not (0 < float(sup.get("price", 0) or 0) < price):
            nb = nearest_below(price, ladder)
            if nb:
                sup = _ladder_level_dict(nb["price"], "support")
        if not (float(res.get("price", 0) or 0) > price):
            na = nearest_above(price, ladder)
            if na:
                res = _ladder_level_dict(na["price"], "resistance")

    s_px = float((sup or {}).get("price", 0) or 0)
    r_px = float((res or {}).get("price", 0) or 0)
    if not (0 < s_px < price < r_px):
        return None

    out = _finalize_active_pair(sup, res, price)
    out["ladder_fill"] = True
    ch = stable_macro_channel(price, ladder)
    if ch.get("support") and ch.get("resistance"):
        out["macro_support"] = float(ch["support"])
        out["macro_resistance"] = float(ch["resistance"])
        out["macro_range_valid"] = True
    log.info(
        f"[LEVELS] ladder-fill px={price:.2f} S={s_px:.2f} R={r_px:.2f} "
        f"makro={ch.get('support')}/{ch.get('resistance')} (tarihsel merdiven)"
    )
    return out


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
    if new_active.get("lifecycle_band") and not new_active.get("layer_band"):
        out = dict(new_active)
        out["locked"] = True
        out.pop("from_persist", None)
        out.pop("extreme_fallback", None)
        return out

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


def register_v3_level_flip(
    price: float,
    from_kind: str,
    to_kind: str,
    direction: str = "",
) -> None:
    """Kirilan direnc->destek / destek->direnc — V3 merge adaylarina eklenir."""
    px = round(float(price or 0), 2)
    if px <= 0:
        return
    to_k = str(to_kind or "").lower()
    if to_k not in ("support", "resistance"):
        return
    flips: list[dict] = list(getattr(state, "v3_flipped_levels", None) or [])
    flips = [
        f
        for f in flips
        if abs(float(f.get("price", 0) or 0) - px) > 0.05
        or str(f.get("kind") or "") != to_k
    ]
    flips.append(
        {
            "price": px,
            "kind": to_k,
            "from_kind": str(from_kind or ""),
            "direction": str(direction or "").upper(),
            "ts": time.time(),
        }
    )
    state.v3_flipped_levels = flips[-16:]


def _prune_flipped_levels() -> list[dict]:
    now = time.time()
    max_age = max(int(getattr(cfg, "V3_FLIP_MAX_AGE_SEC", 48 * 3600) or 0), 3600)
    kept = [
        f
        for f in (getattr(state, "v3_flipped_levels", None) or [])
        if now - float(f.get("ts", 0) or 0) <= max_age
    ]
    state.v3_flipped_levels = kept
    return kept


def _flipped_levels_as_candidates() -> list[dict]:
    out: list[dict] = []
    for rec in _prune_flipped_levels():
        px = float(rec.get("price", 0) or 0)
        kind = str(rec.get("kind") or "")
        if px <= 0 or kind not in ("support", "resistance"):
            continue
        level = {
            "price": px,
            "kind": kind,
            "timeframe": "flip",
            "bar_index": -2,
            "is_swing": True,
            "is_htf": False,
            "is_shelf": False,
            "shelf_bars": 0,
            "is_failed_break": True,
            "failed_break_count": 2,
            "failed_break_strong": True,
            "is_acceptance": False,
            "acceptance_bars": 0,
            "touch_count": 3,
            "is_local_trigger": False,
            "is_role_flip": True,
        }
        out.append(_score_level(level))
    return out


def _register_flips_from_15m_close(
    bars15: list[dict], merged: list[dict] | None = None
) -> None:
    """Son kapanmis 15m mumu: yapisal kirilimda rol degisimi kaydi."""
    if len(bars15) < 3:
        return
    try:
        from engine.structure_thresholds import close_broke_above, close_broke_below
    except Exception:
        return
    prev_close = float(bars15[-3].get("close", 0) or 0)
    close = float(bars15[-2].get("close", 0) or 0)
    if close <= 0:
        return
    levels = list(merged) if merged is not None else (state.v3_levels or {}).get("levels") or []
    for lvl in levels:
        p = float(lvl.get("price", 0) or 0)
        if p <= 0:
            continue
        kind = str(lvl.get("kind") or "")
        if kind == "resistance":
            if close_broke_above(close, p, close) and not (
                prev_close > 0 and close_broke_above(prev_close, p, prev_close)
            ):
                register_v3_level_flip(p, "resistance", "support", "LONG")
        elif kind == "support":
            if close_broke_below(close, p, close) and not (
                prev_close > 0 and close_broke_below(prev_close, p, prev_close)
            ):
                register_v3_level_flip(p, "support", "resistance", "SHORT")


def _in_v3_open_position() -> bool:
    if not state.in_position:
        return False
    pb = state.position_breakout or {}
    return str(pb.get("strategy") or pb.get("entry_mode") or "") == "v3"


def _pick_merged_level_near(
    merged: list[dict], price: float, kind: str, *, tol_pct: float = 0.002
) -> dict | None:
    if price <= 0:
        return None
    tol = max(price * tol_pct, 0.5)
    same = [
        l
        for l in merged
        if str(l.get("kind") or "") == kind
        and abs(float(l.get("price", 0) or 0) - price) <= tol
    ]
    if not same:
        return None
    return max(same, key=lambda item: int(item.get("score", 0) or 0))


def _band_level_from_entry_price(
    price: float, kind: str, merged: list[dict]
) -> dict:
    found = _pick_merged_level_near(merged, price, kind)
    if found:
        return dict(found)
    lvl = _make_extreme_fallback_level(price, kind)
    lvl["is_extreme_fallback"] = False
    lvl["strength"] = "STRONG"
    lvl["score"] = int(cfg.V3_LEVEL_SCORE_STRONG)
    lvl["entry_anchor"] = True
    return lvl


def _apply_position_entry_band_lock(
    active: dict, price: float, merged: list[dict]
) -> dict:
    """Acik V3 pozisyon: aktif bant = giris S/R (canli yeniden secim yok)."""
    if not _in_v3_open_position():
        return active
    pb = state.position_breakout or {}
    es = float(pb.get("entry_support") or 0)
    er = float(pb.get("entry_resistance") or 0)
    if es <= 0 or er <= es:
        return active
    support = _band_level_from_entry_price(es, "support", merged)
    resistance = _band_level_from_entry_price(er, "resistance", merged)
    frozen = _finalize_active_pair(support, resistance, price)
    frozen["locked"] = True
    frozen["entry_band_frozen"] = True
    return frozen


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


def _level_price_tag(lvl: dict | None) -> float:
    return round(float((lvl or {}).get("price", 0) or 0), 2)


def _level_strength_tag(
    lvl: dict | None, merged: list[dict] | None = None, *, kind: str = ""
) -> str:
    if not lvl:
        return "—"
    p = _level_price_tag(lvl)
    st = str(lvl.get("strength") or "")
    if not st and merged and kind and p > 0:
        st = _level_strength_at(merged, kind, p)
    sc = int(lvl.get("score", 0) or 0)
    imp = int(lvl.get("sr_importance", 0) or 0)
    parts = [st or "?"]
    if sc:
        parts.append(f"score={sc}")
    if imp:
        parts.append(f"imp={imp}")
    return " ".join(parts)


def _infer_level_change_reason(
    side: str,
    old_p: float,
    new_p: float,
    prev_active: dict,
    new_active: dict,
    merged: list[dict],
    *,
    band_unlocked: bool,
    cold_start: bool,
) -> str:
    reasons: list[str] = []
    if cold_start and old_p <= 0:
        reasons.append("cold_start")
    if band_unlocked:
        reasons.append("band_unlock")
    if new_active.get("sr_active_band"):
        reasons.append(
            "sr_nearest_below_px" if side == "support" else "sr_nearest_above_px"
        )
    lvl = (new_active.get(side) or {}) if side in ("support", "resistance") else {}
    if str(lvl.get("sr_tag") or "") == "quick_prev_support":
        reasons.append("quick_prev_was_active")
    elif int(lvl.get("sr_slot", 0) or 0) == 1 and side == "support":
        reasons.append("pine_L1_quick_support")
    elif int(lvl.get("sr_slot", 0) or 0) == 2 and side == "resistance":
        reasons.append("pine_L2_quick_resistance")
    if new_active.get("lifecycle_band"):
        reasons.append("lifecycle_band")
    if new_active.get("layer_band"):
        reasons.append("layer_band")
    if new_active.get("from_session"):
        reasons.append("session_band")
    if new_active.get("extreme_fallback"):
        reasons.append("extreme_fallback")
    if new_active.get("macro_htf"):
        reasons.append("macro_htf")
    if prev_active.get("locked") and not new_active.get("locked"):
        reasons.append("lock_released")
    elif not prev_active.get("locked") and new_active.get("locked"):
        reasons.append("band_locked")
    if merged and old_p > 0 and new_p > 0:
        old_st = _level_strength_at(merged, side, old_p)
        new_st = _level_strength_at(merged, side, new_p)
        if old_st != new_st:
            reasons.append(f"strength_{old_st}_to_{new_st}")
    if side == "support":
        if new_p > 0 and old_p > new_p:
            reasons.append("lower_support_selected")
        elif new_p > old_p > 0:
            reasons.append("support_raised")
    elif new_p > old_p > 0:
        reasons.append("resistance_raised")
    elif old_p > new_p > 0:
        reasons.append("resistance_lowered")
    if not reasons:
        reasons.append("active_reselect")
    return "+".join(dict.fromkeys(reasons))


def _maybe_log_level_change(
    prev_active: dict,
    new_active: dict,
    price: float,
    merged: list[dict],
    *,
    band_unlocked: bool,
    cold_start: bool,
) -> None:
    global _last_level_change_key
    if not getattr(cfg, "V3_LEVEL_CHANGE_LOG", True):
        return
    old_s = _level_price_tag(prev_active.get("support"))
    old_r = _level_price_tag(prev_active.get("resistance"))
    new_s = _level_price_tag(new_active.get("support"))
    new_r = _level_price_tag(new_active.get("resistance"))
    tol = 0.05
    s_changed = (old_s <= 0 and new_s > 0) or (new_s > 0 and abs(old_s - new_s) > tol)
    r_changed = (old_r <= 0 and new_r > 0) or (new_r > 0 and abs(old_r - new_r) > tol)
    if not s_changed and not r_changed:
        return
    key = f"{old_s}|{new_s}|{old_r}|{new_r}"
    if key == _last_level_change_key:
        return
    _last_level_change_key = key

    parts = ["[LEVEL_CHANGE]"]
    if s_changed:
        parts.extend(
            [
                f"old_support={old_s:.2f}" if old_s > 0 else "old_support=—",
                f"new_support={new_s:.2f}",
                f"reason={_infer_level_change_reason('support', old_s, new_s, prev_active, new_active, merged, band_unlocked=band_unlocked, cold_start=cold_start)}",
                f"strength_old={_level_strength_tag(prev_active.get('support'), merged, kind='support')}",
                f"strength_new={_level_strength_tag(new_active.get('support'), merged, kind='support')}",
            ]
        )
    if r_changed:
        if s_changed:
            parts.append("")
        parts.extend(
            [
                f"old_resistance={old_r:.2f}" if old_r > 0 else "old_resistance=—",
                f"new_resistance={new_r:.2f}",
                f"reason={_infer_level_change_reason('resistance', old_r, new_r, prev_active, new_active, merged, band_unlocked=band_unlocked, cold_start=cold_start)}",
                f"strength_old={_level_strength_tag(prev_active.get('resistance'), merged, kind='resistance')}",
                f"strength_new={_level_strength_tag(new_active.get('resistance'), merged, kind='resistance')}",
            ]
        )
    parts.append(f"px={price:.2f}")
    log.info("\n".join(parts))


def _active_level_label(price: float, level: float, nominal: str) -> str:
    px = float(price or 0)
    lv = float(level or 0)
    tol = max(abs(px) * 0.0001, 0.05) if px > 0 else 0.0
    if nominal == "support" and px > 0 and lv > px + tol:
        return "broken_support"
    if nominal == "resistance" and px > 0 and lv < px - tol:
        return "broken_resistance"
    return "support" if nominal == "support" else "resistance"


def _log_level_diagnostics(price: float, all_levels: list[dict], merged: list[dict], active: dict) -> None:
    global _last_verbose_diag_key
    active_support = float(((active.get("support") or {}).get("price", 0) or 0))
    active_resistance = float(((active.get("resistance") or {}).get("price", 0) or 0))
    range_valid = bool(active.get("range_valid"))
    locked = bool(active.get("locked"))
    zone = str(active.get("zone") or "?")
    s_label = _active_level_label(price, active_support, "support")
    r_label = _active_level_label(price, active_resistance, "resistance")
    log.info(
        f"[LEVELS] px={price:.2f} aday={len(all_levels)} merged={len(merged)} "
        f"{s_label}={active_support:.2f} {r_label}={active_resistance:.2f} "
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
    bars15 = bars_15m(int(getattr(cfg, "V3_LEVEL_MAX_AGE_15M", 500) or 500))
    bars1h = bars_1h(int(getattr(cfg, "V3_LEVEL_MAX_AGE_1H", 150) or 150))
    bars1 = bars_1m(200)
    if not bars15 or price <= 0:
        snap = {"levels": [], "active": _empty_active(), "price": price}
        state.v3_levels = snap
        log.warning(f"[LEVELS] Veri yetersiz: bars15={len(bars15)} price={price:.2f}")
        return snap

    all_levels: list[dict] = []
    sr_only = getattr(cfg, "V3_SR_ENABLED", True) and getattr(cfg, "V3_SR_ONLY", True)
    from engine.liquidity_map_v3 import update_liquidity_map

    sr15_pkg: dict | None = None
    if sr_only:
        from engine.sr_levels_v3 import run_sr_analysis

        sr15_pkg = run_sr_analysis(bars15, price, is_htf=False)
        merged = list((sr15_pkg or {}).get("trade_levels") or [])
        all_levels = list(merged)
        sup_p = sorted(
            [float(l["price"]) for l in merged if l.get("kind") == "support"],
            reverse=True,
        )
        res_p = sorted([float(l["price"]) for l in merged if l.get("kind") == "resistance"])
        log.info(
            f"[SR] calculate_sr_levels merged={len(merged)} | destek={[round(x, 2) for x in sup_p]} "
            f"direnc={[round(x, 2) for x in res_p]}"
        )
    else:

        def process_timeframe(bars: list[dict], tf: str, max_age: int, is_htf: bool) -> None:
            lookback = (
                cfg.V3_SWING_LOOKBACK_1H if is_htf else cfg.V3_SWING_LOOKBACK
            )
            highs, lows = _swing_candidates_for_tf(
                bars, lookback, is_htf=is_htf, price=price
            )
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
                    "sr_source": swing.get("sr_source"),
                    "sr_importance": swing.get("sr_importance"),
                    "sr_touches": swing.get("sr_touches"),
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
                sr_t = int(swing.get("sr_touches", 0) or 0)
                level["touch_count"] = max(sr_t, _count_touches(bars, level_price, kind))
                level["is_local_trigger"] = _check_local_trigger(level_price, bars1)
                level = _score_level(level)
                if str(level.get("strength")) != "IGNORE":
                    from engine.liquidity_map_v3 import attach_zone_bands

                    attach_zone_bands(level, bars)
                    all_levels.append(level)

        process_timeframe(bars15, "15m", cfg.V3_LEVEL_MAX_AGE_15M, False)
        if bars1h:
            process_timeframe(bars1h, "1h", cfg.V3_LEVEL_MAX_AGE_1H, True)
        all_levels.extend(_flipped_levels_as_candidates())
        from engine.liquidity_map_v3 import boost_equal_high_low_clusters

        all_levels = boost_equal_high_low_clusters(all_levels, bars15)
        merged = _merge_levels_for_snap(all_levels, bars15, price)
    _register_flips_from_15m_close(bars15, merged)
    zone_lifecycle: list[dict] = []
    if getattr(cfg, "V3_ZONE_LIFECYCLE", True):
        from engine.zone_engine_v3 import filter_tradeable_levels, tick_zone_lifecycle

        zone_lifecycle = tick_zone_lifecycle(merged, price, bars15)
        if sr_only:
            merged = list(merged)
        else:
            merged = filter_tradeable_levels(merged, zone_lifecycle)
    prev_active = (state.v3_levels or {}).get("active") or {}
    prev_active_snapshot = {
        "support": dict(prev_active.get("support") or {}),
        "resistance": dict(prev_active.get("resistance") or {}),
        "locked": bool(prev_active.get("locked")),
    }
    cold_start = not prev_active.get("support") or not prev_active.get("resistance")
    persist_loaded = False
    band_unlocked = False

    skip_persist = getattr(cfg, "V3_SR_ENABLED", True) and getattr(
        cfg, "V3_SR_SKIP_PERSIST", True
    )
    if cold_start and skip_persist:
        prev_active = {}
        persist_loaded = False
        log.info("[LEVELS] cold start — SR/katman modu, eski persist yuklenmedi")
    elif cold_start:
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
            if _in_v3_open_position():
                pass
            else:
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
                band_unlocked = True

    active = _resolve_active_levels(merged, price, bars15)
    if sr_only:
        sr_band = _active_from_sr_pivots(merged, price)
        if sr_band:
            active = sr_band
    # Aktif S/R cifti: katman bandi (supply_major / demand) — lifecycle mikro bandi ezmez
    use_lifecycle_active = getattr(cfg, "V3_LIFECYCLE_ACTIVE_BAND", False)
    if use_lifecycle_active and getattr(cfg, "V3_ZONE_LIFECYCLE", True) and zone_lifecycle:
        from engine.zone_engine_v3 import (
            _lifecycle_band_price_valid,
            pick_active_from_zones,
        )

        z_sup, z_res = pick_active_from_zones(price, zone_lifecycle, bars15)
        if z_sup and z_res:
            zs = float(z_sup.get("price", 0) or 0)
            zr = float(z_res.get("price", 0) or 0)
            if zs > 0 and zr > zs and _lifecycle_band_price_valid(price, z_sup, z_res):
                active = _finalize_active_pair(z_sup, z_res, price)
                active["lifecycle_band"] = True
                log.info(
                    f"[LEVELS] lifecycle bant S={zs:.2f}({z_sup.get('zone_status')}) "
                    f"R={zr:.2f}({z_res.get('zone_status')}) "
                    f"str={z_sup.get('lifecycle_strength')}/{z_res.get('lifecycle_strength')}"
                )
    if cold_start and active.get("support") and active.get("resistance"):
        fs = float((active.get("support") or {}).get("price", 0) or 0)
        fr = float((active.get("resistance") or {}).get("price", 0) or 0)
        fw = fr - fs
        fc = _pair_combined_score(active.get("support"), active.get("resistance"))
        if active.get("lifecycle_band"):
            prev_active = {}
            persist_loaded = False
            log.info(
                f"[LEVELS] lifecycle bant aktif — persist yok sayildi "
                f"S={fs:.2f} R={fr:.2f} px={price:.2f}"
            )
        elif persist_loaded and active.get("macro_htf"):
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
        elif persist_loaded:
            prev_resync = _resync_band_levels(prev_active, merged)
            pc = _pair_combined_score(
                prev_resync.get("support"), prev_resync.get("resistance")
            )
            ps = float((prev_resync.get("support") or {}).get("price", 0) or 0)
            pr = float((prev_resync.get("resistance") or {}).get("price", 0) or 0)
            prev_active = prev_resync
            upgraded, did_upgrade = _upgrade_prev_active_trade_r(
                prev_resync, active, price
            )
            if did_upgrade:
                prev_active = upgraded
                pr = float((prev_active.get("resistance") or {}).get("price", 0) or 0)
                log.info(
                    f"[LEVELS] cold start trade R guncellendi persist {ps:.2f}/{pr:.2f} "
                    f"<- fresh S={fs:.2f} R={fr:.2f} px={price:.2f}"
                )
            else:
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
    lock_prev = prev_active
    if active.get("lifecycle_band"):
        lock_prev = {}
    elif (
        getattr(cfg, "V3_TRADE_BAND_ENABLED", True)
        and getattr(cfg, "V3_SR_ENABLED", True)
        and sr_only
    ):
        # Trade band her tick SR pivot + overlay ile hesaplanir; eski persist kilidi ezmesin
        lock_prev = {}

    use_layer_band = getattr(cfg, "V3_LAYER_BAND_ACTIVE", True) and not sr_only
    if not active.get("support") or not active.get("resistance"):
        if sr_only:
            sr_band = _active_from_sr_pivots(merged, price)
            if sr_band:
                active = sr_band
        elif not use_layer_band:
            session_active = _active_from_prev_band(lock_prev or prev_active, price)
            if session_active:
                active = session_active
                s = float((active.get("support") or {}).get("price", 0) or 0)
                r = float((active.get("resistance") or {}).get("price", 0) or 0)
                log.info(
                    f"[LEVELS] oturum bandi aktif px={price:.2f} S={s:.2f} R={r:.2f} "
                    f"(dar kanal yok, onceki bant)"
                )
            elif not sr_only:
                active = _resolve_band_outside_channel(merged, price, bars15)
        elif not sr_only:
            active = _resolve_band_outside_channel(merged, price, bars15)
    if not active.get("support") or not active.get("resistance"):
        # Önce tarihsel merdiveni tara — geçmişten en yakın GERÇEK seviye
        # (kaba 24-bar sentetik band yerine). Fiyat tüm pivotların altına/üstüne
        # çıksa bile bot kör kalmaz.
        if bool(getattr(cfg, "V3_LADDER_ENABLED", True)):
            filled = _fill_band_from_ladder(active, price)
            if filled is not None:
                active = filled
        use_extreme = (
            (not active.get("support") or not active.get("resistance"))
            and (
                getattr(cfg, "V3_EXTREME_FALLBACK_ENABLED", False)
                or not getattr(cfg, "V3_ZONE_LIFECYCLE", True)
            )
        )
        if use_extreme:
            extreme = _apply_extreme_bar_fallback(bars15, price)
            if extreme:
                active = extreme
                s = float((active.get("support") or {}).get("price", 0) or 0)
                r = float((active.get("resistance") or {}).get("price", 0) or 0)
                log.info(
                    f"[LEVELS] extreme fallback px={price:.2f} S={s:.2f} R={r:.2f} "
                    f"(son {getattr(cfg, 'V3_EXTREME_FALLBACK_BARS', 24)}x15m)"
                )
        elif getattr(cfg, "V3_ZONE_LIFECYCLE", True):
            log.warning(
                f"[LEVELS] lifecycle bant yok px={price:.2f} — extreme fallback kapali"
            )
    if not use_layer_band:
        active = _apply_active_level_lock(active, merged, price, lock_prev, bars15)
        active = _resync_band_levels(active, merged)
        active = _ensure_active_covers_price(active, merged, price, bars15)
        # Aktif band kenarlarını touch-validated gerçek seviyelere snap'le
        # (gürültü/tek-wick kenar elenir), sonra dar bandı genişlet.
        active = _validate_band_against_ladder(active, price)
        active = _enforce_min_band_width(active, price)
    active = _apply_position_entry_band_lock(active, price, merged)
    _attach_display_outer_levels(active, merged, price, prev_active)
    liq_map = update_liquidity_map(merged, price, bars15)
    from engine.zone_engine_v3 import zones_snapshot

    if sr_only and sr15_pkg:
        from engine.sr_calculator import snapshot_chart_level_dicts

        act_s_px = float((active.get("support") or {}).get("price", 0) or 0)
        act_r_px = float((active.get("resistance") or {}).get("price", 0) or 0)
        chart_levels = snapshot_chart_level_dicts(
            sr15_pkg["snapshot"],
            active_support=act_s_px,
            active_resistance=act_r_px,
        )
    elif sr_only:
        chart_levels = []
    else:
        chart_src = merged
        chart_levels = build_chart_levels(chart_src, active, price)
    snap = {
        "price": price,
        "levels": merged,
        "sr_pine": bool(sr_only),
        "active": active,
        "chart_levels": chart_levels,
        "liquidity_map": liq_map,
        "zone_lifecycle": zones_snapshot(price),
        "zones": zone_lifecycle,
    }
    from engine.market_reading_v3 import apply_market_reading_to_snap

    snap = apply_market_reading_to_snap(snap, price, bars15)
    active = snap.get("active") or active
    if getattr(cfg, "V3_SR_ENABLED", False) and getattr(cfg, "V3_SR_ACTIVE_BAND", True):
        # Pine SR: V3_SR_ENABLED=false olduğu için artık çalışmaz
        sr_active = _active_from_sr_pivots(merged, price)
        if sr_active:
            active = sr_active
            snap["active"] = active
            s = float((active.get("support") or {}).get("price", 0) or 0)
            r = float((active.get("resistance") or {}).get("price", 0) or 0)
            n_sr = sum(1 for l in merged if l.get("sr_source"))
            active = _deepen_support_from_meaningful_band(active, price)
            active = _enforce_min_band_width(active, price)
            snap["active"] = active
            s = float((active.get("support") or {}).get("price", 0) or 0)
            r = float((active.get("resistance") or {}).get("price", 0) or 0)
            log.info(
                f"[LEVELS] SR pivot bant px={price:.2f} S={s:.2f} R={r:.2f} "
                f"({n_sr} pivot cizgi)"
            )
    elif getattr(cfg, "V3_SWING_BAND_ENABLED", True):
        # Real-time swing band — Pine SR'ın yerini alıyor
        swing_active = _active_from_swing_structure(price)
        if swing_active:
            active = swing_active
            snap["active"] = active
    elif active.get("layer_band"):
        active["locked"] = True
        s = float((active.get("support") or {}).get("price", 0) or 0)
        r = float((active.get("resistance") or {}).get("price", 0) or 0)
        log.info(
            f"[LEVELS] katman bandi aktif px={price:.2f} S={s:.2f} R={r:.2f} "
            f"(supply_major / demand)"
        )
    elif use_layer_band:
        active = _apply_active_level_lock(active, merged, price, lock_prev, bars15)
        active = _resync_band_levels(active, merged)
        active = _ensure_active_covers_price(active, merged, price, bars15)
        snap["active"] = active
    snap = _apply_trade_band_overlay(snap, price, bars15)
    active = snap.get("active") or active
    active = _deepen_support_from_meaningful_band(active, price)
    active = _enforce_min_band_width(active, price)
    snap["active"] = active
    state.v3_levels = snap
    macro_persist = snap.get("macro_band") or {}
    if not macro_persist.get("support"):
        macro_persist = _resolve_macro_band_for_overlay(
            snap, price, bars15, merged=merged
        )
    trade_r = float((active.get("resistance") or {}).get("price", 0) or 0)
    macro_r = float((macro_persist.get("resistance") or {}).get("price", 0) or 0)
    if macro_persist.get("support") and macro_persist.get("resistance"):
        if macro_r > trade_r + 0.5 or not active.get("trade_band"):
            if not macro_persist.get("extreme_fallback") or macro_persist.get(
                "lifecycle_band"
            ):
                _persist_active(macro_persist)
    if getattr(cfg, "V3_SR_ENABLED", True) and merged:
        chart = snap.get("chart_levels") or []
        trade_s = sorted(
            [float(l["price"]) for l in merged if l.get("kind") == "support"],
            reverse=True,
        )
        trade_r = sorted([float(l["price"]) for l in merged if l.get("kind") == "resistance"])
        pine_slots = [
            f"L{int(c.get('sr_slot') or 0)}={float(c.get('price', 0) or 0):.2f}"
            f"({'S' if c.get('kind') == 'support' else 'R'})"
            for c in chart
            if int(c.get("sr_slot") or 0) > 0
        ]
        log.info(
            f"[SR] trade S/R: destek={[round(x, 2) for x in trade_s]} "
            f"direnc={[round(x, 2) for x in trade_r]}"
        )
        log.info(
            f"[SR] grafik Pine n={len(chart)}: "
            + (" ".join(pine_slots) if pine_slots else "—")
        )

    _maybe_log_level_change(
        prev_active_snapshot,
        active,
        price,
        merged,
        band_unlocked=band_unlocked,
        cold_start=cold_start,
    )
    _log_level_diagnostics(price, all_levels, merged, active)
    return snap


def _level_touch_tolerance(bars15: list[dict], level: float) -> float:
    recent = bars15[-12:] if len(bars15) >= 12 else bars15
    body = avg_body(recent) if recent else 0.0
    return max(body * 0.5, level * 0.00015, 1e-9)


def _dynamic_level_window(band_width_pct: float) -> int:
    return max(3, min(8, int(band_width_pct * 500)))


def _reliability_lookback(band_width_pct: float, available: int) -> int:
    want = max(20, min(80, int(band_width_pct * 8000) + 20))
    return min(want, available)


def _reliability_hist(
    bars15: list[dict], support: float, resistance: float
) -> list[dict]:
    band_width_pct = 0.01
    if resistance > support > 0:
        band_width_pct = (resistance - support) / resistance
    lookback = _reliability_lookback(band_width_pct, len(bars15))
    return bars15[-lookback:] if lookback > 0 else []


def _support_rejection_on_bar(bar: dict, support: float, tol: float) -> bool:
    low = float(bar.get("low", 0) or 0)
    close = float(bar.get("close", 0) or 0)
    if low <= 0 or close <= 0 or low > support + tol:
        return False
    return close >= support - tol * 0.25


def _resistance_rejection_on_bar(bar: dict, resistance: float, tol: float) -> bool:
    high = float(bar.get("high", 0) or 0)
    close = float(bar.get("close", 0) or 0)
    if high <= 0 or close <= 0 or high < resistance - tol:
        return False
    return close <= resistance + tol * 0.25


def _bar_closed_green(bar: dict) -> bool:
    o = float(bar.get("open") or 0)
    c = float(bar.get("close") or 0)
    return o > 0 and c > o


def _bar_closed_red(bar: dict) -> bool:
    o = float(bar.get("open") or 0)
    c = float(bar.get("close") or 0)
    return o > 0 and c < o


def _sweep_high_on_level(bar: dict, level: float, tol: float) -> bool:
    """event_engine SWEEP_HIGH ile ayni geometri: fitil ustte, kapanis seviye altinda."""
    hi = float(bar.get("high") or 0)
    close = float(bar.get("close") or 0)
    if level <= 0 or hi <= 0 or close <= 0:
        return False
    return hi > level + tol * 0.2 and close < level


def _sweep_low_on_level(bar: dict, level: float, tol: float) -> bool:
    lo = float(bar.get("low") or 0)
    close = float(bar.get("close") or 0)
    if level <= 0 or lo <= 0 or close <= 0:
        return False
    return lo < level - tol * 0.2 and close > level


def _decayed_sweep_event_active(events: dict | None, side: str) -> bool:
    if not isinstance(events, dict):
        return False
    flags = events.get("flags")
    if not isinstance(flags, dict):
        flags = events
    if str(side or "").upper() in ("SHORT", "SELL"):
        return bool(flags.get("sweep_high"))
    return bool(flags.get("sweep_low"))


def range_sweep_sequence_ready(
    bars15: list[dict],
    levels: dict,
    side: str,
    *,
    events: dict | None = None,
) -> tuple[bool, str]:
    """
    Sweep sonrasi yapisal adim sirasi (indikatör yok):
      SHORT: sweep_high → yesil 15m kapanis → 1+ bar direnc ret
      LONG:  sweep_low  → kirmizi 15m kapanis → 1+ bar destek ret

    Sweep baglami yoksa (True, ...) — normal range hazirligi; ek filtre degil.
    """
    side_u = str(side or "").upper()
    if side_u in ("BUY", "LONG"):
        order = "LONG"
        level = float(levels.get("active_support") or 0)
        sweep_fn = _sweep_low_on_level
        confirm_fn = _bar_closed_red
        reject_fn = _support_rejection_on_bar
        confirm_label = "kirmizi"
        reject_label = "destek"
    elif side_u in ("SELL", "SHORT"):
        order = "SHORT"
        level = float(levels.get("active_resistance") or 0)
        sweep_fn = _sweep_high_on_level
        confirm_fn = _bar_closed_green
        reject_fn = _resistance_rejection_on_bar
        confirm_label = "yesil"
        reject_label = "direnc"
    else:
        return False, "Gecersiz yon."

    if level <= 0 or not bars15:
        return True, "seviye_yok"

    closed = bars15[:-1] if len(bars15) > 1 else list(bars15)
    if not closed:
        return True, "mum_yok"

    tol = _level_touch_tolerance(bars15, level)
    win = closed[-12:]
    sweep_pos = -1
    for i in range(len(win) - 1, -1, -1):
        if sweep_fn(win[i], level, tol):
            sweep_pos = i
            break

    event_active = _decayed_sweep_event_active(events, order)
    if sweep_pos < 0 and not event_active:
        return True, "sweep_baglami_yok"

    start = sweep_pos if sweep_pos >= 0 else max(0, len(win) - 4)
    green_idx = -1
    for i in range(start, len(win)):
        if confirm_fn(win[i]):
            green_idx = i
            break

    if green_idx < 0:
        return False, (
            f"Adim 2/3: sweep@{level:.0f} sonrasi {confirm_label} 15m kapanis bekleniyor."
        )

    rej = 0
    for i in range(green_idx + 1, len(win)):
        if reject_fn(win[i], level, tol):
            rej += 1

    if rej < 1:
        return False, (
            f"Adim 3/3: sweep+{confirm_label} sonrasi {reject_label} ret bekleniyor "
            f"({rej}/1 bar)."
        )

    return True, (
        f"sweep_adim_tamam ret={rej} {confirm_label}_ofset=+{green_idx - start}"
    )


def _zone_from_last_rejection(
    bars15: list[dict], support: float, resistance: float
) -> str | None:
    """
    Son ret hangi seviyede (level_reliability ile ayni ret tanimi).
    Fiyat konumuna bakilmaz.
    """
    s = float(support or 0)
    r = float(resistance or 0)
    if s <= 0 or r <= s:
        return None
    hist = _reliability_hist(bars15, s, r)
    if not hist:
        return None
    tol_s = _level_touch_tolerance(bars15, s)
    tol_r = _level_touch_tolerance(bars15, r)
    last_s = -1
    last_r = -1
    for i, bar in enumerate(hist):
        if _support_rejection_on_bar(bar, s, tol_s):
            last_s = i
        if _resistance_rejection_on_bar(bar, r, tol_r):
            last_r = i
    if last_s < 0 and last_r < 0:
        return None
    if last_r > last_s:
        return "NEAR_RESISTANCE"
    if last_s > last_r:
        return "NEAR_SUPPORT"
    return "NEAR_RESISTANCE"


def _zone_edge_distance_px(price: float) -> float:
    px = float(price or 0)
    pct = float(getattr(cfg, "V3_ZONE_EDGE_DIST_PCT", 0.012) or 0.012)
    return max(px * pct, 8.0)


def _trade_channel_traversed(
    bars15: list[dict], support: float, resistance: float
) -> bool:
    """Son N 15m'de hem destek hem dirence dokunma = kanal traverse."""
    s = float(support or 0)
    r = float(resistance or 0)
    if s <= 0 or r <= s or not bars15:
        return False
    sup_tol = _level_touch_tolerance(bars15, s)
    res_tol = _level_touch_tolerance(bars15, r)
    return _band_traversal_confirmed(bars15, s, r, sup_tol, res_tol)


def _upgrade_prev_active_trade_r(
    prev_active: dict, fresh_active: dict, price: float
) -> tuple[dict, bool]:
    """Persist trade R eskiyse Pine/SR taze direnc ile guncelle."""
    if not getattr(cfg, "V3_TRADE_BAND_FRESH_SR", True):
        return prev_active, False
    prev_r = float((prev_active.get("resistance") or {}).get("price", 0) or 0)
    fresh_r = float((fresh_active.get("resistance") or {}).get("price", 0) or 0)
    fresh_s = float((fresh_active.get("support") or {}).get("price", 0) or 0)
    prev_s = float((prev_active.get("support") or {}).get("price", 0) or 0)
    px = float(price or 0)
    if fresh_r <= 0 or prev_r <= 0 or fresh_s <= 0:
        return prev_active, False
    stale = max(px * float(getattr(cfg, "V3_PERSIST_TRADE_R_STALE_PCT", 0.008) or 0.008), 2.0)
    if fresh_r <= prev_r + stale:
        return prev_active, False
    if abs(fresh_s - prev_s) > max(stale * 2, 12.0):
        return prev_active, False
    out = dict(prev_active)
    out["support"] = dict(fresh_active.get("support") or prev_active.get("support") or {})
    out["resistance"] = dict(
        fresh_active.get("resistance") or prev_active.get("resistance") or {}
    )
    out["locked"] = False
    out.pop("from_persist", None)
    return out, True


def price_zone_for_band(support: float, resistance: float, price: float) -> str:
    """
    Fiyat konumu etiketi. Trade uygunlugu degildir.

    Once dirence/destege mesafe (%1.2 veya $8), sonra band yuzdesi (varsayilan %20).
    """
    s = float(support or 0)
    r = float(resistance or 0)
    p = float(price or 0)
    if s <= 0 or r <= s or p <= 0:
        return "MID_RANGE"
    if p <= s:
        return "NEAR_SUPPORT"
    if p >= r:
        return "NEAR_RESISTANCE"
    near = _zone_edge_distance_px(p)
    if (r - p) <= near:
        return "NEAR_RESISTANCE"
    if (p - s) <= near:
        return "NEAR_SUPPORT"
    edge_frac = float(getattr(cfg, "V3_ZONE_EDGE_FRAC", 0.20) or 0.20)
    pos = (p - s) / (r - s)
    if pos <= edge_frac:
        return "NEAR_SUPPORT"
    if pos >= (1.0 - edge_frac):
        return "NEAR_RESISTANCE"
    return "MID_RANGE"


def zone_for_price(
    support: float,
    resistance: float,
    price: float,
    bars15: list[dict] | None = None,
) -> str:
    """
    Zone = kanitli kenar (min ret + min dokunus), anlik konum degil.

    Iki kenar da kanitli: son ret hangi seviyedeydi (15m mum sirasi), fiyat degil.
    """
    s = float(support or 0)
    r = float(resistance or 0)
    if s <= 0 or r <= s or float(price or 0) <= 0:
        return "MID_RANGE"

    bars = list(bars15) if bars15 else bars_15m(40)
    if not bars:
        return "MID_RANGE"

    resistance_active = level_edge_proven(
        bars, r, "SELL", support=s, resistance=r
    )
    support_active = level_edge_proven(bars, s, "BUY", support=s, resistance=r)

    act = (state.v3_levels or {}).get("active") or {}
    sd = act.get("support") or {}
    rd = act.get("resistance") or {}
    if abs(float(sd.get("price", 0) or 0) - s) < 0.6:
        zst = str(sd.get("zone_status") or sd.get("lifecycle") or "support")
        lc = str(sd.get("lifecycle") or "")
        if zst not in ("support",) or lc in ("TRANSITION", "BROKEN", "DECAY"):
            support_active = False
    if abs(float(rd.get("price", 0) or 0) - r) < 0.6:
        zst = str(rd.get("zone_status") or rd.get("lifecycle") or "resistance")
        lc = str(rd.get("lifecycle") or "")
        if zst not in ("resistance",) or lc in ("TRANSITION", "BROKEN", "DECAY"):
            resistance_active = False

    if resistance_active and support_active:
        last_zone = _zone_from_last_rejection(bars, s, r)
        return last_zone if last_zone else "MID_RANGE"
    if resistance_active:
        return "NEAR_RESISTANCE"
    if support_active:
        return "NEAR_SUPPORT"
    return "MID_RANGE"


def level_reliability_counts(
    bars15: list[dict],
    level: float,
    direction: str,
    *,
    support: float = 0.0,
    resistance: float = 0.0,
) -> tuple[int, int]:
    """(dokunus, ret) — level_reliability ile ayni tanim."""
    if not bars15 or level <= 0:
        return 0, 0
    side = str(direction or "").upper()
    if side in ("BUY", "LONG"):
        kind = "support"
    elif side in ("SELL", "SHORT"):
        kind = "resistance"
    else:
        return 0, 0
    tol = _level_touch_tolerance(bars15, level)
    hist = _reliability_hist(
        bars15, float(support or 0), float(resistance or 0)
    )
    touches = 0
    rejections = 0
    for bar in hist:
        if kind == "support":
            if float(bar.get("low", 0) or 0) > level + tol:
                continue
            touches += 1
            if _support_rejection_on_bar(bar, level, tol):
                rejections += 1
        else:
            if float(bar.get("high", 0) or 0) < level - tol:
                continue
            touches += 1
            if _resistance_rejection_on_bar(bar, level, tol):
                rejections += 1
    return touches, rejections


def level_edge_proven(
    bars15: list[dict],
    level: float,
    direction: str,
    *,
    support: float = 0.0,
    resistance: float = 0.0,
) -> bool:
    touches, rejections = level_reliability_counts(
        bars15, level, direction, support=support, resistance=resistance
    )
    min_t = max(int(getattr(cfg, "V3_MIN_RELIABILITY_TOUCHES", 2) or 2), 1)
    min_r = max(int(getattr(cfg, "V3_MIN_RELIABILITY_REJECTIONS", 2) or 2), 1)
    return touches >= min_t and rejections >= min_r


def level_reliability(
    bars15: list[dict],
    level: float,
    direction: str,
    *,
    support: float = 0.0,
    resistance: float = 0.0,
) -> float:
    """
    Gecmis ret orani; kanit esigi saglanmazsa 0 (tek ret ile zone/giris yok).
    """
    touches, rejections = level_reliability_counts(
        bars15, level, direction, support=support, resistance=resistance
    )
    if touches <= 0 or not level_edge_proven(
        bars15, level, direction, support=support, resistance=resistance
    ):
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


def trade_band_first_leg_long_ok(levels: dict | None, *, zone: str = "") -> bool:
    """
    Pinli trade band: destek kenarinda ilk yukari bacak.
    Direnc (TP) henuz test edilmemis olabilir; destek kaniti yeterli.
    """
    if not getattr(cfg, "V3_TRADE_BAND_FIRST_LEG_LONG", True):
        return False
    if not getattr(cfg, "V3_TRADE_BAND_ENABLED", True):
        return False
    lv = levels or {}
    if not bool(lv.get("trade_band")):
        return False
    z = str(zone or lv.get("zone") or "").upper()
    return z == "NEAR_SUPPORT"


def trade_band_first_leg_short_ok(levels: dict | None, *, zone: str = "") -> bool:
    """
    Pinli trade band: direnc kenarinda ilk asagi bacak.
    Destek (TP) henuz test edilmemis olabilir; direnc kaniti yeterli.
    """
    if not getattr(cfg, "V3_TRADE_BAND_FIRST_LEG_SHORT", True):
        return False
    if not getattr(cfg, "V3_TRADE_BAND_ENABLED", True):
        return False
    lv = levels or {}
    if not bool(lv.get("trade_band")):
        return False
    z = str(zone or lv.get("zone") or "").upper()
    return z == "NEAR_RESISTANCE"


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
    RANGE icin kosullar:

    Normal mod (range_locked=False):
      1) giris seviyesinde gecmis ret var
      2) son mumlar seviyeye/banda saygi gosteriyor (>50%)
      3) CVD karsi degil
      4) TP tarafi (karsi band kenari) de en az bir kez test edilmis

    Range locked mod (range_locked=True — fiyat dar kanalda):
      1) Atlanir — kanal zaten teyit edilmis, gecmis ret aranmaz
      2) Kosul 2 devam eder (saygi kontrolu)
      3) Kosul 3 devam eder (CVD kontrolu)
      4) Atlanir — kanal tek tarafli olabilir, bu normal
    """
    side = str(side or "").upper()
    support = float(levels.get("active_support") or 0)
    resistance = float(levels.get("active_resistance") or 0)

    # range_locked state'den oku
    range_locked = getattr(state, "v3_range_locked", False)

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

    lvl_obj = (levels.get("support") if side in ("BUY", "LONG") else levels.get("resistance")) or {}
    if isinstance(lvl_obj, dict):
        zst = str(lvl_obj.get("zone_status") or lvl_obj.get("lifecycle") or "").lower()
        if zst in ("broken", "transition", "decay"):
            return False, (
                f"{label} lifecycle={zst} — kanitli destek/direnc degil "
                f"(acc_sc={lvl_obj.get('acceptance_score', 0)})."
            )

    if getattr(cfg, "V3_ZONE_LIFECYCLE", True):
        from engine.zone_engine_v3 import liquidity_blocks_chase

        chase_side = "SHORT" if side in ("SELL", "SHORT") else "LONG"
        blocked, reason = liquidity_blocks_chase(chase_side, float(levels.get("price", 0) or 0))
        if blocked:
            return False, reason

    # Koşul 1: geçmiş test — range_locked'da atla
    if not range_locked:
        historical = level_reliability(
            bars15, level, direction, support=support, resistance=resistance
        )
        if historical <= 0.0:
            t, r = level_reliability_counts(
                bars15, level, direction, support=support, resistance=resistance
            )
            min_t = int(getattr(cfg, "V3_MIN_RELIABILITY_TOUCHES", 2) or 2)
            min_r = int(getattr(cfg, "V3_MIN_RELIABILITY_REJECTIONS", 2) or 2)
            return (
                False,
                f"Kosul 1: {label} kanit yetersiz "
                f"(ret={r}/{t}, min {min_r} ret + {min_t} dokunus).",
            )
    else:
        historical = 1.0  # range_locked'da varsayılan: kanal teyitli

    # Koşul 2: güncel saygı
    recent = level_respect_now(
        bars15, level, direction, support=support, resistance=resistance
    )
    if recent <= 0.5:
        return False, (
            f"Kosul 2: {label} tutmuyor (saygi={recent:.0%}, esik>50%)."
        )

    # Koşul 3: CVD
    momentum = cvd_supports_level(cvd, direction)
    if momentum <= 0.0:
        cvd_dir = str((cvd or {}).get("direction") or "?")
        return False, f"Kosul 3: CVD karsi ({cvd_dir})."

    # Koşul 5: sweep baglami varsa yapisal adim sirasi (dogru adim; ek filtre degil)
    ms = levels.get("market_state") or getattr(state, "v3_market_state", None) or {}
    events = ms.get("events") if isinstance(ms, dict) else {}
    seq_ok, seq_detail = range_sweep_sequence_ready(
        bars15, levels, direction, events=events
    )
    if not seq_ok:
        return False, seq_detail

    # Koşul 4: TP tarafı testi — range_locked veya kanal traverse'da atla
    traverse_ok = _trade_channel_traversed(bars15, support, resistance)
    state.v3_trade_channel_traversed = traverse_ok
    first_leg_used = False
    if not range_locked:
        tp_ret = level_tp_reliability(bars15, support, resistance, side)
        if tp_ret <= 0.0:
            if traverse_ok and getattr(cfg, "V3_CHANNEL_TRAVERSE_TP_OK", True):
                tp_ret = 1.0
            elif (
                side in ("BUY", "LONG")
                and trade_band_first_leg_long_ok(levels)
                and historical > 0.0
            ):
                tp_ret = 1.0
                first_leg_used = True
            elif (
                side in ("SELL", "SHORT")
                and trade_band_first_leg_short_ok(levels)
                and historical > 0.0
            ):
                tp_ret = 1.0
                first_leg_used = True
            else:
                return False, (
                    f"Kosul 4: {tp_label} test edilmemis (tp_ret=0%) — kanal tek tarafli."
                )
    else:
        tp_ret = 1.0  # range_locked'da atlanır

    locked_tag = " [range_locked]" if range_locked else ""
    traverse_tag = " traverse=evet" if traverse_ok else ""
    first_leg_tag = " first_leg=evet" if first_leg_used else ""
    seq_tag = "" if seq_detail == "sweep_baglami_yok" else f" {seq_detail}"
    return True, (
        f"ret={historical:.0%} saygi={recent:.0%} tp_ret={tp_ret:.0%}"
        f"{locked_tag}{traverse_tag}{first_leg_tag}{seq_tag}"
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
    Kirilim referansi: guncel aktif trade bandi (oncelik).
    Persist yalnizca aktif bant yoksa ve fiyat icinde gecerliyse kullanilir;
    makro persist (or. R=1688) dar trade bandini ezmemeli.
    """
    px = float(price or effective_price() or state.mark_price or state.price or 0)
    snap = state.v3_levels or {}
    active = snap.get("active") or {}
    s = float((active.get("support") or {}).get("price", 0) or 0)
    r = float((active.get("resistance") or {}).get("price", 0) or 0)

    persisted = _restore_persisted_active()
    ps = float((persisted.get("support") or {}).get("price", 0) or 0)
    pr = float((persisted.get("resistance") or {}).get("price", 0) or 0)

    ref_s, ref_r = s, r
    source = "active"
    active_ok = s > 0 and r > s and (px <= 0 or s < px < r)

    if active_ok:
        ref_s, ref_r = s, r
        source = "active"
    elif (
        ps > 0
        and pr > ps
        and (px <= 0 or ps < px < pr)
        and not _persist_support_too_stale(ps, px)
        and (pr - ps) < max(px * 0.06, 80.0)
    ):
        ref_s, ref_r = ps, pr
        source = "session"
    elif s > 0 and r > s:
        ref_s, ref_r = s, r
        source = "active"
    elif ps > 0 and pr > ps:
        ref_s, ref_r = ps, pr
        source = "session"

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
    macro_active = snap.get("macro_band") or active
    if not snap:
        snap = update_levels()
        active = snap.get("active") or {}
        macro_active = snap.get("macro_band") or active
    support = active.get("support") or {}
    resistance = active.get("resistance") or {}
    macro_support = macro_active.get("support") or {}
    macro_resistance = macro_active.get("resistance") or {}
    px = float(price or snap.get("price", 0) or effective_price() or state.mark_price or 0)
    s_px = float(support.get("price", 0) or 0)
    r_px = float(resistance.get("price", 0) or 0)
    macro_s_px = float(macro_support.get("price", 0) or active.get("macro_support", 0) or 0)
    macro_r_px = float(macro_resistance.get("price", 0) or active.get("macro_resistance", 0) or 0)
    rw = max(r_px - s_px, 0.0)
    rpos = ((px - s_px) / rw) if rw > 0 and px > 0 else float(active.get("range_position", 0.5) or 0.5)
    zone = str(active.get("zone") or "") or price_zone_for_band(s_px, r_px, px)
    edge_zone = str(active.get("edge_zone") or "") or zone_for_price(
        s_px, r_px, px, bars15=bars_15m(20)
    )
    liq = snap.get("liquidity_map") or state.v3_liquidity_map or []
    liq_bias = dict(getattr(state, "v3_liquidity_bias", None) or {})
    if not liq_bias and liq:
        from engine.liquidity_map_v3 import liquidity_target_bias

        liq_bias = liquidity_target_bias(px, liq)
    return {
        "price": px,
        "all_levels": snap.get("levels") or [],
        "liquidity_map": liq[:12],
        "liquidity_bias": liq_bias,
        "active_support": s_px,
        "active_resistance": r_px,
        "macro_support": macro_s_px,
        "macro_resistance": macro_r_px,
        "support": support,
        "resistance": resistance,
        "macro_band": macro_active,
        "trade_band": bool(active.get("trade_band")),
        "range_width": rw if rw > 0 else float(active.get("range_width", 0) or 0),
        "range_mid": (s_px + r_px) / 2.0 if s_px > 0 and r_px > s_px else float(active.get("range_mid", 0) or 0),
        "range_position": rpos,
        "zone": zone,
        "edge_zone": edge_zone,
        "range_valid": bool(active.get("range_valid")),
        "macro_range_valid": bool(
            macro_active.get("range_valid") or active.get("macro_range_valid")
        ),
        "trade_range_valid": bool(active.get("trade_band") and active.get("range_valid")),
        "channel_traversed": bool(
            active.get("channel_traversed")
            or getattr(state, "v3_trade_channel_traversed", False)
        ),
        "channel_confirmed": bool(active.get("channel_confirmed")),
        "channel_mode": str(active.get("channel_mode") or ""),
        "active_locked": bool(active.get("locked")),
        "extreme_fallback": bool(active.get("extreme_fallback")),
        "outer_support": float(active.get("outer_support_price", 0) or 0),
        "outer_resistance": float(active.get("outer_resistance_price", 0) or 0),
        "chart_levels": (
            (snap.get("chart_levels") or [])
            if (snap.get("sr_pine") or getattr(cfg, "V3_SR_ONLY", True))
            else build_chart_levels(snap.get("levels") or [], active, px)
        ),
        "sr_pine": bool(snap.get("sr_pine")),
        "zone_lifecycle": snap.get("zone_lifecycle") or {},
        "zones": snap.get("zones") or state.v3_zones or [],
        "market_story": snap.get("market_story") or getattr(state, "v3_market_story", None) or {},
        "zone_layers": snap.get("zone_layers") or getattr(state, "v3_zone_layers", None) or {},
        "trade_map": snap.get("trade_map") or getattr(state, "v3_trade_map", None) or {},
        "liquidity_pools": snap.get("liquidity_pools") or state.v3_liquidity_pools or [],
        "market_state": snap.get("market_state") or getattr(state, "v3_market_state", None) or {},
        "liquidity_support": float(
            (active.get("liquidity_support_price") or 0) if isinstance(active, dict) else 0
        ),
        "mid_supply_low": float((active.get("mid_supply_low") or 0) if isinstance(active, dict) else 0),
        "mid_supply_high": float((active.get("mid_supply_high") or 0) if isinstance(active, dict) else 0),
    }
