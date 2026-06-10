"""
engine/trade_thesis_v3.py

Executable trade thesis layer.

Karar once destek/direnc haritasindan cikar:
- entry: mevcut fiyat
- invalidation: tez hangi seviyede bozulur
- target: bir sonraki anlamli S/R hedefi
- rr: target / invalidation mesafesi

Skor bu katmanda trade yaratmaz; sadece secilen/elenen tezi aciklar.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from core.config import cfg
from engine.structure_thresholds import sl_buffer_bps


@dataclass
class TradeThesis:
    direction: str
    state: str
    reason: str
    entry: dict
    entry_price: float
    invalidation: float
    target: float
    rr: float
    ref_support: float
    ref_resistance: float
    thesis_type: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _level_price(levels: dict, key: str) -> float:
    return float(levels.get(key) or 0)


def _swing_sl_anchor(side: str, px: float) -> float:
    """
    Pine SR olmadan SL anchor: son swing high (SHORT) veya swing low (LONG).
    market_state.structure.bounce_high / bounce_low = anlık yapısal referans.
    """
    from core.state import state as _s
    ms = getattr(_s, "v3_market_state", None) or {}
    struct = ms.get("structure") or {}
    if side == "SHORT":
        bh = float(struct.get("bounce_high") or 0)
        if bh > px:
            return bh
        # Fallback: en yakın swing high
        highs = sorted(
            [float(h.get("price", 0) or 0) for h in (_s.swing_highs_15m or []) if float(h.get("price", 0) or 0) > px],
        )
        return highs[0] if highs else 0.0
    else:
        bl = float(struct.get("bounce_low") or 0)
        if 0 < bl < px:
            return bl
        lows = sorted(
            [float(l.get("price", 0) or 0) for l in (_s.swing_lows_15m or []) if 0 < float(l.get("price", 0) or 0) < px],
            reverse=True,
        )
        return lows[0] if lows else 0.0


def _band_clamp_anchor(
    side: str, anchor: float, ref_r: float, ref_s: float, px: float, levels: dict
) -> float:
    """
    Dar trade-band'da SL çapasını aktif banda sabitle.

    Uzak swing high/low (ör. impulse tepesi 1713) bir RANGE içinde SL'i 40+ pt
    açıp RR'yi <1'e düşürüyor. trade_band aktifken SHORT için aktif direncin,
    LONG için aktif desteğin küçük buffer ötesini tavan/taban kabul et.
    """
    if anchor <= 0:
        return anchor
    if not bool(getattr(cfg, "V3_SL_BAND_CLAMP_ENABLED", True)):
        return anchor
    if not bool(levels.get("trade_band")):
        return anchor
    buf = float(getattr(cfg, "V3_SL_BAND_CLAMP_BUFFER_BPS", 30.0) or 30.0) / 10000.0
    side = (side or "").upper()
    if side == "SHORT" and ref_r > px:
        return min(anchor, ref_r * (1.0 + buf))
    if side == "LONG" and 0 < ref_s < px:
        return max(anchor, ref_s * (1.0 - buf))
    return anchor


def _range_tp_clamp(
    side: str, tp1: float, tp2: float, ref_s: float, ref_r: float, px: float
) -> tuple[float, float]:
    """
    RANGE tezinde TP'yi kanal sınırına sabitle.

    SHORT: runner hedefi (tp2) kanal desteğinin (ref_s) ALTINA inmesin —
    desteğin altı breakout bölgesi, ayrı tez. LONG: tp2 kanal direncinin
    (ref_r) ÜSTÜNE çıkmasın. tp1 (kısmi) kanal içinde kalır.
    """
    if not bool(getattr(cfg, "V3_RANGE_TP_BAND_CLAMP", True)):
        return tp1, tp2
    side = (side or "").upper()
    if side == "SHORT" and ref_s > 0:
        if tp2 > 0 and tp2 < ref_s:
            tp2 = ref_s                      # runner = kanal desteği
        if tp1 > 0 and tp1 < tp2:            # tp1 her zaman entry'ye daha yakın (yüksek)
            tp1 = (px + tp2) / 2.0
    elif side == "LONG" and ref_r > 0:
        if tp2 > 0 and tp2 > ref_r:
            tp2 = ref_r
        if tp1 > 0 and tp1 > tp2:
            tp1 = (px + tp2) / 2.0
    return tp1, tp2


def _layer_tp(side: str, px: float, levels: dict) -> float:
    """
    TP: market_state layer'larından — tarihsel değil, anlık yapısal bölge.
    SHORT → demand_weak alt kenarı | LONG → supply_mid alt kenarı
    """
    ms = levels.get("market_state") or {}
    layers = ms.get("layers") or {}
    if side == "SHORT":
        dw = layers.get("demand_weak") or {}
        dw_lo = float(dw.get("low") or 0)
        if 0 < dw_lo < px:
            return dw_lo
        dl = layers.get("demand_liq") or {}
        dl_lo = float(dl.get("low") or 0)
        if 0 < dl_lo < px:
            return dl_lo
        return 0.0
    else:
        sm = layers.get("supply_mid") or {}
        sm_lo = float(sm.get("low") or 0)
        if sm_lo > px:
            return sm_lo
        sj = layers.get("supply_major") or {}
        sj_lo = float(sj.get("low") or 0)
        if sj_lo > px:
            return sj_lo
        return 0.0


def _active_prices(levels: dict, scenario: dict) -> tuple[float, float]:
    support = float(
        scenario.get("ref_support")
        or levels.get("active_support")
        or (levels.get("support") or {}).get("price")
        or 0
    )
    resistance = float(
        scenario.get("ref_resistance")
        or levels.get("active_resistance")
        or (levels.get("resistance") or {}).get("price")
        or 0
    )
    return support, resistance


def _with_mode(levels: dict, mode: str) -> dict:
    out = dict(levels)
    if mode:
        out["decision_mode"] = mode
    return out


def _level_candidates(levels: dict) -> list[float]:
    vals: list[float] = []
    for src in (
        levels.get("all_levels") or [],
        levels.get("levels") or [],
        levels.get("chart_levels") or [],
    ):
        if isinstance(src, dict):
            src = [src]
        for lv in src or []:
            try:
                p = float(
                    lv.get("price")
                    or lv.get("level")
                    or lv.get("target")
                    or lv.get("center")
                    or 0
                )
            except Exception:
                p = 0.0
            if p > 0:
                vals.append(p)
    active = levels.get("active") or levels
    for key in ("support", "resistance"):
        try:
            p = float((active.get(key) or {}).get("price") or 0)
        except Exception:
            p = 0.0
        if p > 0:
            vals.append(p)
    for key in (
        "active_support",
        "active_resistance",
        "outer_support",
        "outer_resistance",
        "liquidity_support",
        "mid_supply_low",
        "mid_supply_high",
    ):
        try:
            p = float(levels.get(key) or 0)
        except Exception:
            p = 0.0
        if p > 0:
            vals.append(p)
    return sorted(set(round(v, 2) for v in vals if v > 0))


def _layer_candidates(levels: dict) -> list[tuple[float, float]]:
    layers = levels.get("zone_layers") or levels.get("layers") or {}
    out: list[tuple[float, float]] = []
    for val in layers.values() if isinstance(layers, dict) else []:
        if not isinstance(val, dict):
            continue
        lo = float(val.get("low") or 0)
        hi = float(val.get("high") or 0)
        if lo > 0 and hi >= lo:
            out.append((lo, hi))
    return out


def _layer_width_at(levels: dict, price: float) -> float:
    if price <= 0:
        return 0.0
    best = 0.0
    for lo, hi in _layer_candidates(levels):
        width = hi - lo
        if width <= 0:
            continue
        pad = max(width, 0.75)
        if lo - pad <= price <= hi + pad:
            if best <= 0 or width < best:
                best = width
    return best


def _local_shelf_buffer(levels: dict, px: float, anchor: float) -> float:
    full = px * sl_buffer_bps(px) / 10000.0 if px > 0 else 0.0
    if full <= 0:
        return 0.0
    width = _layer_width_at(levels, anchor)
    if width > 0:
        return max(0.75, min(full, width * 0.35))
    return max(0.75, full * 0.35)


def _nearest_above(levels: dict, px: float, *extra: float) -> float:
    vals = [v for v in _level_candidates(levels) + list(extra) if v > px]
    return min(vals) if vals else 0.0


def _zone_high_for_level(levels: dict, level: float) -> float:
    if level <= 0:
        return 0.0
    candidates: list[float] = []
    for src in (
        levels.get("support") or {},
        levels.get("resistance") or {},
        *(levels.get("all_levels") or []),
        *(levels.get("chart_levels") or []),
    ):
        if not isinstance(src, dict):
            continue
        price = float(src.get("price") or src.get("level") or 0)
        if price <= 0 or abs(price - level) > max(level * 0.0015, 0.75):
            continue
        hi = float(src.get("zone_high") or src.get("high") or src.get("upper") or 0)
        if hi > level:
            candidates.append(hi)
    return max(candidates) if candidates else 0.0


def _zone_low_for_level(levels: dict, level: float) -> float:
    if level <= 0:
        return 0.0
    candidates: list[float] = []
    for src in (
        levels.get("support") or {},
        levels.get("resistance") or {},
        *(levels.get("all_levels") or []),
        *(levels.get("chart_levels") or []),
    ):
        if not isinstance(src, dict):
            continue
        price = float(src.get("price") or src.get("level") or 0)
        if price <= 0 or abs(price - level) > max(level * 0.0015, 0.75):
            continue
        lo = float(src.get("zone_low") or src.get("low") or src.get("lower") or 0)
        if 0 < lo < level:
            candidates.append(lo)
    return min(candidates) if candidates else 0.0


def _recent_bars15(limit: int = 48) -> list[dict]:
    try:
        from engine.v3_common import bars_15m

        return list(bars_15m(limit) or [])
    except Exception:
        try:
            from engine.structure import get_bars_15m

            return list(get_bars_15m(limit) or [])
        except Exception:
            return []


def _bar_float(bar: dict, key: str) -> float:
    try:
        return float(bar.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _recent_breakdown_reclaim_high(levels: dict, px: float, support: float) -> float:
    """
    SHORT kirilim SL'i: cizgi ustu tek buffer degil, kirilim oncesi son dagilim/
    reclaim rafinin tepesi. Grafikte gozle gorulen 1780-1790 gibi alan buradan gelir.
    """
    if px <= 0 or support <= 0:
        return 0.0
    ref_r = float(levels.get("active_resistance") or (levels.get("resistance") or {}).get("price") or 0)
    bars = _recent_bars15(48)
    highs: list[float] = []
    for bar in bars:
        hi = _bar_float(bar, "high")
        close = _bar_float(bar, "close")
        if hi <= max(px, support):
            continue
        if close > 0 and close < support * 0.998 and hi < support * 1.002:
            continue
        if ref_r > support and hi >= ref_r * 0.985:
            continue
        highs.append(hi)
    return round(max(highs), 2) if highs else 0.0


def _recent_breakout_reclaim_low(levels: dict, px: float, resistance: float) -> float:
    """LONG kirilim SL'i: kirilim oncesi/sonrasi lokal reclaim rafinin dibi."""
    if px <= 0 or resistance <= 0:
        return 0.0
    ref_s = float(levels.get("active_support") or (levels.get("support") or {}).get("price") or 0)
    bars = _recent_bars15(48)
    lows: list[float] = []
    for bar in bars:
        lo = _bar_float(bar, "low")
        close = _bar_float(bar, "close")
        if lo <= 0 or lo >= min(px, resistance):
            continue
        if close > resistance * 1.002 and lo > resistance * 0.998:
            continue
        if ref_s > 0 and lo <= ref_s * 1.015:
            continue
        lows.append(lo)
    return round(min(lows), 2) if lows else 0.0


def _broken_support_invalidation(levels: dict, px: float, support: float) -> float:
    inv, _, _ = _broken_support_invalidation_info(levels, px, support)
    return inv


def _broken_support_invalidation_info(
    levels: dict, px: float, support: float
) -> tuple[float, str, float]:
    if px <= 0 or support <= 0:
        return 0.0, "", 0.0
    zone_hi = _zone_high_for_level(levels, support)
    buf = px * sl_buffer_bps(px) / 10000.0
    shelf_hi = _recent_breakdown_reclaim_high(levels, px, support)
    shelf_buf = _local_shelf_buffer(levels, px, shelf_hi)
    # Kirilan destek artik direnc/reclaim bolgesi; SL cizgide degil, lokal rafin ustunde.
    candidates = [
        ("broken_support", support + buf, support),
        ("zone_high", zone_hi + buf if zone_hi > 0 else 0.0, zone_hi),
        ("local_reclaim_shelf", shelf_hi + shelf_buf if shelf_hi > 0 else 0.0, shelf_hi),
    ]
    source, inv, anchor = max(candidates, key=lambda item: item[1])
    return inv, source, anchor


def _broken_resistance_invalidation(levels: dict, px: float, resistance: float) -> float:
    inv, _, _ = _broken_resistance_invalidation_info(levels, px, resistance)
    return inv


def _broken_resistance_invalidation_info(
    levels: dict, px: float, resistance: float
) -> tuple[float, str, float]:
    if px <= 0 or resistance <= 0:
        return 0.0, "", 0.0
    zone_lo = _zone_low_for_level(levels, resistance)
    buf = px * sl_buffer_bps(px) / 10000.0
    shelf_lo = _recent_breakout_reclaim_low(levels, px, resistance)
    shelf_buf = _local_shelf_buffer(levels, px, shelf_lo)
    candidates = [("broken_resistance", resistance - buf, resistance)]
    if zone_lo > 0:
        candidates.append(("zone_low", zone_lo - buf, zone_lo))
    if shelf_lo > 0:
        candidates.append(("local_reclaim_shelf", shelf_lo - shelf_buf, shelf_lo))
    # Kirilan direnc artik destek/reclaim bolgesi; SL cizgide degil, lokal rafin altinda.
    source, inv, anchor = min(candidates, key=lambda item: item[1])
    return inv, source, anchor


def _first_supply_above(levels: dict, px: float) -> float:
    layers = levels.get("zone_layers") or levels.get("layers") or {}
    for key in ("supply_mid", "supply_major"):
        layer = layers.get(key) if isinstance(layers, dict) else {}
        if not isinstance(layer, dict):
            continue
        lo = float(layer.get("low") or 0)
        hi = float(layer.get("high") or 0)
        center = float(layer.get("center") or 0)
        for candidate in (lo, center, hi):
            if candidate > px:
                return candidate
    return 0.0


def _first_demand_below(levels: dict, px: float) -> float:
    layers = levels.get("zone_layers") or levels.get("layers") or {}
    for key in ("demand_weak", "demand_liq"):
        layer = layers.get(key) if isinstance(layers, dict) else {}
        if not isinstance(layer, dict):
            continue
        hi = float(layer.get("high") or 0)
        center = float(layer.get("center") or 0)
        lo = float(layer.get("low") or 0)
        for candidate in (hi, center, lo):
            if 0 < candidate < px:
                return candidate
    return 0.0


def _first_supply_below(levels: dict, px: float) -> float:
    layers = levels.get("zone_layers") or levels.get("layers") or {}
    for key in ("supply_mid", "supply_major"):
        layer = layers.get(key) if isinstance(layers, dict) else {}
        if not isinstance(layer, dict):
            continue
        hi = float(layer.get("high") or 0)
        center = float(layer.get("center") or 0)
        lo = float(layer.get("low") or 0)
        for candidate in (hi, center, lo):
            if 0 < candidate < px:
                return candidate
    return 0.0


def _range_short_tp_ladder(levels: dict, px: float, ref_s: float) -> tuple[float, float]:
    """Kanal icinde TP1 (yakin) + demand/band destek (TP2/runner)."""
    from core.state import state
    from engine.v3_common import range_channel_tp_ladder

    ref_r = float(
        levels.get("active_resistance")
        or (levels.get("resistance") or {}).get("price")
        or 0
    )
    tp1, tp2 = range_channel_tp_ladder(
        "SHORT",
        px,
        ref_s,
        ref_r,
        levels=levels,
        swing_lows=state.swing_lows_15m or [],
    )
    if tp1 > 0 and tp2 > 0 and tp1 < px:
        return tp1, tp2
    return _first_supply_below(levels, px) or ref_s, ref_s


def _range_long_tp_ladder(levels: dict, px: float, ref_r: float) -> tuple[float, float]:
    from core.state import state
    from engine.v3_common import range_channel_tp_ladder

    ref_s = float(
        levels.get("active_support")
        or (levels.get("support") or {}).get("price")
        or 0
    )
    tp1, tp2 = range_channel_tp_ladder(
        "LONG",
        px,
        ref_s,
        ref_r,
        levels=levels,
        swing_highs=state.swing_highs_15m or [],
    )
    if tp1 > px and tp2 > tp1:
        return tp1, tp2
    return _first_supply_above(levels, px) or ref_r, ref_r


def _layer_band(levels: dict, key: str) -> dict:
    layers = levels.get("zone_layers") or levels.get("layers") or {}
    layer = layers.get(key) if isinstance(layers, dict) else {}
    if not isinstance(layer, dict):
        return {}
    lo = float(layer.get("low") or 0)
    hi = float(layer.get("high") or 0)
    if lo <= 0 or hi < lo:
        return {}
    center = float(layer.get("center") or 0) or ((lo + hi) / 2.0)
    return {
        "key": key,
        "low": lo,
        "high": hi,
        "center": center,
        "label": str(layer.get("label") or key).upper(),
    }


def _price_in_layer(layer: dict, px: float) -> bool:
    if px <= 0 or not layer:
        return False
    return float(layer.get("low") or 0) <= px <= float(layer.get("high") or 0)


def _local_layer_buffer(levels: dict, px: float, layer: dict) -> float:
    center = float(layer.get("center") or 0)
    width = float(layer.get("high") or 0) - float(layer.get("low") or 0)
    if width > 0:
        return max(0.75, min(width * 0.35, px * sl_buffer_bps(px) / 10000.0))
    return _local_shelf_buffer(levels, px, center)


def _local_layer_levels(
    levels: dict,
    *,
    side: str,
    px: float,
    layer: dict,
    ref_s: float,
    ref_r: float,
) -> dict:
    out = dict(levels)
    if side == "SHORT":
        support = _first_demand_below(levels, px) or ref_s
        resistance = float(layer.get("high") or 0)
        zone = "NEAR_RESISTANCE"
    else:
        support = float(layer.get("low") or 0)
        resistance = _first_supply_above(levels, px) or ref_r
        zone = "NEAR_SUPPORT"
    if support <= 0 or resistance <= support or not (support < px < resistance):
        return {}
    out.update(
        {
            "active_support": support,
            "active_resistance": resistance,
            "range_valid": True,
            "zone": zone,
            "local_trade_band": True,
            "local_layer_key": layer.get("key"),
            "local_layer_low": float(layer.get("low") or 0),
            "local_layer_high": float(layer.get("high") or 0),
        }
    )
    active = dict(out.get("active") or {})
    active["support"] = {"price": support, "kind": "local_support"}
    active["resistance"] = {"price": resistance, "kind": "local_resistance"}
    active["zone"] = zone
    active["local_trade_band"] = True
    out["active"] = active
    return out


def _nearest_below(levels: dict, px: float, *extra: float) -> float:
    vals = [v for v in _level_candidates(levels) + list(extra) if 0 < v < px]
    for lo, hi in _layer_candidates(levels):
        if hi < px:
            vals.append(hi)
        elif lo < px:
            vals.append(lo)
    return max(vals) if vals else 0.0


def _entry_from_geometry(
    direction: str,
    *,
    px: float,
    invalidation: float,
    target: float,
    entry_type: str,
) -> dict:
    if px <= 0 or invalidation <= 0 or target <= 0:
        return {
            "valid": False,
            "direction": "SELL" if direction == "SHORT" else "BUY",
            "entry_type": entry_type,
            "price": px,
            "sl": invalidation,
            "tp1": target,
            "tp2": target,
            "rr": 0.0,
            "preview": False,
        }
    if direction == "SHORT":
        risk = invalidation - px
        reward = px - target
        order_dir = "SELL"
    else:
        risk = px - invalidation
        reward = target - px
        order_dir = "BUY"
    rr = reward / risk if risk > 0 and reward > 0 else 0.0
    return {
        "valid": rr >= float(getattr(cfg, "V3_MIN_RR_RATIO", 2.0) or 2.0),
        "direction": order_dir,
        "entry_type": entry_type,
        "price": px,
        "sl": invalidation,
        "tp1": target,
        "tp2": target,
        "rr": rr,
        "preview": False,
    }


def _entry_from_breakout_geometry(
    direction: str,
    *,
    px: float,
    invalidation: float,
    target: float,
    entry_type: str,
) -> dict:
    """
    Trend kirilimi: yakin hedef TP1 olabilir ama giris karari TP1 mesafesine
    takilmaz. TP2 runner projeksiyonudur; TP2 emri kapaliysa kalan pozisyonu
    15m trailing SL takip eder.
    """
    min_rr = float(getattr(cfg, "V3_MIN_RR_RATIO", 2.0) or 2.0)
    tp1_min_rr = max(float(getattr(cfg, "V3_BREAKOUT_TP1_MIN_RR", 1.0) or 1.0), 1.0)
    if px <= 0 or invalidation <= 0:
        return _entry_from_geometry(
            direction,
            px=px,
            invalidation=invalidation,
            target=target,
            entry_type=entry_type,
        )
    if direction == "SHORT":
        risk = invalidation - px
        if risk <= 0:
            return _entry_from_geometry(direction, px=px, invalidation=invalidation, target=target, entry_type=entry_type)
        natural = target if 0 < target < px else 0.0
        min_tp1 = px - risk * tp1_min_rr
        # TP1 ilk lokal demand/dip bolgesidir; min R:R hedefi runner/TP2 tarafinda kalir.
        tp1 = natural if natural > 0 else min_tp1
        tp2 = min(natural if natural > 0 else px, px - risk * min_rr)
        reward = px - tp2
        order_dir = "SELL"
    else:
        risk = px - invalidation
        if risk <= 0:
            return _entry_from_geometry(direction, px=px, invalidation=invalidation, target=target, entry_type=entry_type)
        natural = target if target > px else 0.0
        min_tp1 = px + risk * tp1_min_rr
        # TP1 ilk lokal supply/tepe bolgesidir; min R:R hedefi runner/TP2 tarafinda kalir.
        tp1 = natural if natural > 0 else min_tp1
        tp2 = max(natural if natural > 0 else px, px + risk * min_rr)
        reward = tp2 - px
        order_dir = "BUY"
    rr = reward / risk if risk > 0 and reward > 0 else 0.0
    return {
        "valid": rr >= min_rr,
        "direction": order_dir,
        "entry_type": entry_type,
        "price": px,
        "sl": invalidation,
        "tp1": tp1,
        "tp2": tp2,
        "rr": rr,
        "preview": False,
        "runner_projection": True,
        "tp1_min_rr": tp1_min_rr,
    }


def _entry_from_range_ladder_geometry(
    direction: str,
    *,
    px: float,
    invalidation: float,
    tp1: float,
    tp2: float,
    entry_type: str,
) -> dict:
    """
    Range tez: global katman hedefleri + swing SL.
    RR karari runner (tp2) uzerinden; TP1 kismi cikis.
    """
    min_rr = float(getattr(cfg, "V3_MIN_RR_RATIO", 2.0) or 2.0)
    if px <= 0 or invalidation <= 0:
        return _entry_from_geometry(
            direction,
            px=px,
            invalidation=invalidation,
            target=tp2 or tp1,
            entry_type=entry_type,
        )
    if direction == "SHORT":
        risk = invalidation - px
        if risk <= 0:
            return _entry_from_geometry(
                direction, px=px, invalidation=invalidation, target=tp2, entry_type=entry_type
            )
        runner = tp2 if tp2 > 0 and tp2 < px else (tp1 if 0 < tp1 < px else px - risk * min_rr)
        partial = tp1 if 0 < tp1 < px else runner
        reward = px - runner
        order_dir = "SELL"
    else:
        risk = px - invalidation
        if risk <= 0:
            return _entry_from_geometry(
                direction, px=px, invalidation=invalidation, target=tp2, entry_type=entry_type
            )
        runner = tp2 if tp2 > px else (tp1 if tp1 > px else px + risk * min_rr)
        partial = tp1 if tp1 > px else runner
        reward = runner - px
        order_dir = "BUY"
    rr = reward / risk if risk > 0 and reward > 0 else 0.0
    return {
        "valid": rr >= min_rr,
        "direction": order_dir,
        "entry_type": entry_type,
        "price": px,
        "sl": invalidation,
        "tp1": partial,
        "tp2": runner,
        "rr": rr,
        "preview": False,
        "range_ladder": True,
    }


def _entry_thesis(
    direction: str,
    *,
    levels: dict,
    scenario: dict,
    px: float,
    thesis_type: str,
    reason: str,
) -> TradeThesis:
    ref_s, ref_r = _active_prices(levels, scenario)
    sl_source = ""
    sl_anchor = 0.0
    target = 0.0          # default — BROKEN_* dalları aşağıda doldurur
    invalidation = 0.0
    if direction == "SHORT":
        if thesis_type == "BROKEN_SUPPORT":
            invalidation, sl_source, sl_anchor = _broken_support_invalidation_info(
                levels, px, ref_s
            )
            target = _first_demand_below(levels, px) or _nearest_below(levels, px)
        elif thesis_type == "LOCAL_SUPPLY_REJECTION":
            layer = _layer_band(levels, str(levels.get("local_layer_key") or "supply_mid"))
            layer_hi = float(layer.get("high") or ref_r)
            buf = _local_layer_buffer(levels, px, layer) if layer else _local_shelf_buffer(levels, px, ref_r)
            invalidation = layer_hi + buf
            sl_source = "local_supply_layer"
            sl_anchor = layer_hi
            target = _first_demand_below(levels, px) or _nearest_below(levels, px)
        elif thesis_type == "RESISTANCE_REJECTION":
            from engine.entry_v3 import _structural_sl_short

            fallback = max(ref_r * 1.001, px * 1.0002)
            invalidation = _structural_sl_short(px, ref_r, fallback)
            # Dar RANGE: SL'i uzak swing high (supply_major) yerine aktif direnç
            # bandına clamp'le — executed SL de bunu kullansın.
            invalidation = _band_clamp_anchor("SHORT", invalidation, ref_r, ref_s, px, levels)
            sl_source = "swing_high_15m"
            sl_anchor = ref_r
            tp1, tp2 = _range_short_tp_ladder(levels, px, ref_s)
            tp1, tp2 = _range_tp_clamp("SHORT", tp1, tp2, ref_s, ref_r, px)
            target = tp2
            entry = _entry_from_range_ladder_geometry(
                direction,
                px=px,
                invalidation=invalidation,
                tp1=tp1,
                tp2=tp2,
                entry_type=thesis_type,
            )
        elif thesis_type == "SWING_HIGH_REJECTION":
            # bounce_high = recent swing high passed via levels["_swing_high_ref"]
            swing_hi = float(levels.get("_swing_high_ref") or ref_r)
            # Dar RANGE: uzak swing high yerine aktif direnç tavanına sabitle.
            swing_hi = _band_clamp_anchor("SHORT", swing_hi, ref_r, ref_s, px, levels)
            buf = max(swing_hi * 0.003, 1.0)
            invalidation = swing_hi + buf
            invalidation = _band_clamp_anchor("SHORT", invalidation, ref_r, ref_s, px, levels)
            sl_source = "swing_high_15m"
            sl_anchor = swing_hi
            tp1, tp2 = _range_short_tp_ladder(levels, px, ref_s)
            tp1, tp2 = _range_tp_clamp("SHORT", tp1, tp2, ref_s, ref_r, px)
            target = tp2
            entry = _entry_from_range_ladder_geometry(
                direction,
                px=px,
                invalidation=invalidation,
                tp1=tp1,
                tp2=tp2,
                entry_type=thesis_type,
            )
        else:
            invalidation = _nearest_above(levels, px, ref_s, ref_r)
            target = _first_demand_below(levels, px) or _nearest_below(levels, px)
    else:
        if thesis_type == "BROKEN_RESISTANCE":
            invalidation, sl_source, sl_anchor = _broken_resistance_invalidation_info(
                levels, px, ref_r
            )
            target = _first_supply_above(levels, px) or _nearest_above(levels, px, ref_r)
        elif thesis_type == "LOCAL_DEMAND_HOLD":
            layer_key = str(levels.get("local_layer_key") or "demand_weak")
            layer = _layer_band(levels, layer_key)
            layer_lo = float(layer.get("low") or ref_s)
            buf = _local_layer_buffer(levels, px, layer) if layer else _local_shelf_buffer(levels, px, ref_s)
            invalidation = layer_lo - buf
            sl_source = "local_demand_layer"
            sl_anchor = layer_lo
            target = _first_supply_above(levels, px) or _nearest_above(levels, px, ref_r)
        elif thesis_type == "SUPPORT_HOLD":
            from engine.entry_v3 import _structural_sl_long

            fallback = min(ref_s * 0.999, px * 0.9998)
            invalidation = _structural_sl_long(px, ref_s, fallback)
            invalidation = _band_clamp_anchor("LONG", invalidation, ref_r, ref_s, px, levels)
            sl_source = "swing_low_15m"
            sl_anchor = ref_s
            tp1, tp2 = _range_long_tp_ladder(levels, px, ref_r)
            tp1, tp2 = _range_tp_clamp("LONG", tp1, tp2, ref_s, ref_r, px)
            target = tp2
            entry = _entry_from_range_ladder_geometry(
                direction,
                px=px,
                invalidation=invalidation,
                tp1=tp1,
                tp2=tp2,
                entry_type=thesis_type,
            )
        else:
            invalidation = _nearest_below(levels, px, ref_s)
            target = _first_supply_above(levels, px) or _nearest_above(levels, px, ref_r)
    if thesis_type in ("BROKEN_SUPPORT", "BROKEN_RESISTANCE"):
        entry = _entry_from_breakout_geometry(
            direction,
            px=px,
            invalidation=invalidation,
            target=target,
            entry_type=thesis_type,
        )
    elif thesis_type not in ("RESISTANCE_REJECTION", "SUPPORT_HOLD"):
        # Breakout geometrisi kullan: tp1 (partial) ve tp2 (runner) ayrı hesaplanır.
        # _entry_from_geometry tp1=tp2 setlediğinden RR hesabı ve runner yönetimi bozulur.
        entry = _entry_from_breakout_geometry(
            direction,
            px=px,
            invalidation=invalidation,
            target=target,
            entry_type=thesis_type,
        )
    if sl_source:
        entry["sl_source"] = sl_source
        entry["sl_anchor"] = round(sl_anchor, 2) if sl_anchor > 0 else 0.0
    rr = float(entry.get("rr", 0) or 0)
    inv = float(entry.get("sl", 0) or 0)
    target = float(entry.get("tp2", 0) or entry.get("tp1", 0) or 0)
    min_rr = float(getattr(cfg, "V3_MIN_RR_RATIO", 2.0) or 2.0)
    valid_geometry = px > 0 and inv > 0 and target > 0
    if direction == "SHORT":
        valid_geometry = valid_geometry and inv > px and target < px
    else:
        valid_geometry = valid_geometry and inv < px and target > px
    # --- Min SL mesafe tabanı (gürültü-stop önlemi) ---
    # SL girişe çok yakınsa (ör. %0.06) RR kâğıt üstünde iyi görünse de
    # piyasa gürültüsü anında stop tetikliyor (#56/#57 tipi). 0.25%'ten dar
    # SL'leri geçersiz say → bu girişleri tamamen ele.
    entry_px = float(entry.get("price", 0) or px)
    min_sl_pct = float(getattr(cfg, "V3_MIN_SL_DIST_PCT", 0.25) or 0.0)
    sl_too_tight = False
    if min_sl_pct > 0 and entry_px > 0 and inv > 0:
        sl_dist_pct = abs(inv - entry_px) / entry_px * 100.0
        if sl_dist_pct < min_sl_pct:
            valid_geometry = False
            sl_too_tight = True
    state = "VALID" if valid_geometry and rr >= min_rr else "WEAK"
    if not valid_geometry:
        state = "INVALID"
    if sl_too_tight:
        reason = f"{reason}; SL cok dar ({abs(inv - entry_px) / entry_px * 100.0:.2f}%<{min_sl_pct:.2f}%) — gurultu-stop riski"
    return TradeThesis(
        direction=direction,
        state=state,
        reason=reason if state == "VALID" else f"{reason}; rr/giris yetersiz",
        entry=entry,
        entry_price=float(entry.get("price", 0) or px),
        invalidation=inv,
        target=target,
        rr=rr,
        ref_support=ref_s,
        ref_resistance=ref_r,
        thesis_type=thesis_type,
    )


def _scenario_text(scenario: dict) -> str:
    parts: list[str] = []
    for key in ("name", "reason", "detail", "block_reason", "message"):
        val = scenario.get(key)
        if val:
            parts.append(str(val))
    return " | ".join(parts).lower()


def _scenario_gate_text(scenario: dict) -> str:
    scn_name = str(scenario.get("name") or "").upper()
    if scn_name == "WAIT":
        return str(scenario.get("block_reason") or "").lower()
    return _scenario_text(scenario)


def _weak_countertrend_support_hold(levels: dict, scenario: dict, cvd_dir: str) -> bool:
    if levels.get("channel_traversed") and cvd_dir == "BULL":
        return False
    if getattr(cfg, "V3_TRADE_BAND_FIRST_LEG_LONG", True) and levels.get("trade_band"):
        if str(levels.get("zone") or "").upper() == "NEAR_SUPPORT" and cvd_dir != "BEAR":
            return False
    ms = levels.get("market_state") or {}
    collapse = ms.get("collapse") or {}
    struct_u = ms.get("structure") or {}
    text = _scenario_gate_text(scenario)

    dominant = str(collapse.get("dominant_bias") or "").lower()
    mode = str(collapse.get("mode") or "").upper()
    pattern = str(struct_u.get("pattern") or "").upper()
    score = float(collapse.get("state_score") or 0)
    bearish_control = (
        dominant in ("bear", "bearish")
        and (
            score >= 60
            or collapse.get("rejection_watch")
            or collapse.get("counter_trend_only")
            or mode in ("ACTIVE_BIAS", "STRUCTURE_CONTROLLED", "TRANSITION")
            or "IMPULSE_DOWN" in pattern
        )
    )
    scenario_blocks_long = any(
        token in text
        for token in (
            "long kovalama blok",
            "zayif talep",
            "kirilim bekleniyor",
            "kırılım bekleniyor",
            "direnc (tp) test edilmemis",
            "direnç (tp) test edilmemiş",
            "kanal tek tarafli",
            "kanal tek taraflı",
        )
    )
    cvd_not_supportive = cvd_dir in ("BEAR", "NEUTRAL", "")
    return bearish_control and (scenario_blocks_long or cvd_not_supportive)


def _weak_countertrend_resistance_rejection(
    levels: dict, scenario: dict, cvd_dir: str
) -> bool:
    if levels.get("channel_traversed") and cvd_dir == "BEAR":
        return False
    if getattr(cfg, "V3_TRADE_BAND_FIRST_LEG_SHORT", True) and levels.get("trade_band"):
        if str(levels.get("zone") or "").upper() == "NEAR_RESISTANCE" and cvd_dir != "BULL":
            return False
    ms = levels.get("market_state") or {}
    collapse = ms.get("collapse") or {}
    struct_u = ms.get("structure") or {}
    text = _scenario_gate_text(scenario)

    dominant = str(collapse.get("dominant_bias") or "").lower()
    mode = str(collapse.get("mode") or "").upper()
    pattern = str(struct_u.get("pattern") or "").upper()
    score = float(collapse.get("state_score") or 0)
    bullish_control = (
        dominant in ("bull", "bullish")
        and (
            score >= 60
            or collapse.get("rejection_watch")
            or collapse.get("counter_trend_only")
            or mode in ("ACTIVE_BIAS", "STRUCTURE_CONTROLLED", "TRANSITION")
            or "IMPULSE_UP" in pattern
        )
    )
    scenario_blocks_short = any(
        token in text
        for token in (
            "short kovalama blok",
            "short kovalama yok",
            "zayif arz",
            "zayıf arz",
            "kirilim bekleniyor",
            "kırılım bekleniyor",
            "destek (tp) test edilmemis",
            "destek (tp) test edilmemiş",
            "kanal tek tarafli",
            "kanal tek taraflı",
        )
    )
    cvd_not_supportive = cvd_dir in ("BULL", "NEUTRAL", "")
    return bullish_control and (scenario_blocks_short or cvd_not_supportive)


def _local_layer_thesis(
    direction: str,
    *,
    levels: dict,
    scenario: dict,
    px: float,
    cvd: dict | None,
    ref_s: float,
    ref_r: float,
) -> TradeThesis | None:
    if direction == "SHORT":
        layer = _layer_band(levels, "supply_mid") or _layer_band(levels, "supply_major")
        if not _price_in_layer(layer, px):
            return None
        local_levels = _local_layer_levels(
            levels,
            side="SHORT",
            px=px,
            layer=layer,
            ref_s=ref_s,
            ref_r=ref_r,
        )
        thesis_type = "LOCAL_SUPPLY_REJECTION"
        reason = (
            f"Lokal arz bolgesinde short tezi: "
            f"{layer.get('low', 0):.2f}-{layer.get('high', 0):.2f}"
        )
    else:
        layer = _layer_band(levels, "demand_weak") or _layer_band(levels, "demand_liq")
        if not _price_in_layer(layer, px):
            return None
        local_levels = _local_layer_levels(
            levels,
            side="LONG",
            px=px,
            layer=layer,
            ref_s=ref_s,
            ref_r=ref_r,
        )
        thesis_type = "LOCAL_DEMAND_HOLD"
        reason = (
            f"Lokal talep bolgesinde long tezi: "
            f"{layer.get('low', 0):.2f}-{layer.get('high', 0):.2f}"
        )
    if not local_levels:
        return None

    from engine.range_validation_v3 import clean_range_scenario, validate_range_trade

    range_check = validate_range_trade(
        direction,
        levels=local_levels,
        scenario=clean_range_scenario(scenario, direction),
        cvd=cvd,
        px=px,
    )
    thesis = _entry_thesis(
        direction,
        levels=local_levels,
        scenario={
            "name": f"THESIS_{direction}",
            "ref_support": local_levels.get("active_support"),
            "ref_resistance": local_levels.get("active_resistance"),
        },
        px=px,
        thesis_type=thesis_type,
        reason=reason,
    )
    if not range_check.get("valid"):
        thesis.state = "WEAK"
        thesis.entry["valid"] = False
        thesis.reason = (
            f"{thesis.reason}; lokal range kapisi {direction} blok: "
            f"{range_check.get('reason') or 'teyit yetersiz'}"
        )
    return thesis


def _swing_high_fallback_short_thesis(
    *,
    levels: dict,
    scenario: dict,
    px: float,
    cvd: dict | None,
    ref_s: float,
    ref_r: float,
) -> "TradeThesis | None":
    """
    Swing-yapı bazlı SHORT tezi — indikatörsüz, anlık price action.

    Pine SR bulunamazsa veya devre dışıysa bu tez çalışır.
    SL: son swing high (bounce_high), TP: demand layer.
    Bearish dominant koşul aranmaz — swing + CVD yeterli.
    """
    ms = levels.get("market_state") or {}
    collapse = ms.get("collapse") or {}
    struct_u = ms.get("structure") or {}

    # SL anchor: son swing high
    swing_hi = _swing_sl_anchor("SHORT", px)
    if swing_hi <= 0:
        # bounce_high yoksa struct'tan almayı dene
        swing_hi = float(struct_u.get("bounce_high") or 0)
    if swing_hi <= 0 or swing_hi <= px:
        return None
    if swing_hi > px * 1.03:  # SL çok uzaksa kullanma
        return None

    # TP: demand layer veya ref_s
    tp = _layer_tp("SHORT", px, levels)
    if tp <= 0:
        tp = ref_s if ref_s > 0 and ref_s < px else 0.0
    if tp <= 0 or tp >= px:
        return None

    # Yön teyidi: bearish collapse VEYA CVD bearish
    dominant = str(collapse.get("dominant_bias") or "").lower()
    cvd_dir = str((cvd or {}).get("direction") or "").upper()
    mode = str(collapse.get("mode") or "").upper()

    bearish_ok = (
        dominant in ("bear", "bearish")
        or cvd_dir == "BEAR"
        or "IMPULSE_DOWN" in str(struct_u.get("pattern") or "")
    )
    if not bearish_ok:
        return None

    is_lower_high = bool(struct_u.get("is_lower_high"))
    bounce_high = float(struct_u.get("bounce_high") or 0)

    if not is_lower_high or bounce_high <= 0:
        return None

    # Swing high yakınında mı? (fiyatın max %2.5 üzerinde olmalı)
    if bounce_high > px * 1.025:
        return None
    if bounce_high <= px:
        # Zaten altında, geçersiz
        return None

    mode = str(collapse.get("mode") or "").upper()
    bearish_modes = ("ACTIVE_BIAS", "STRUCTURE_CONTROLLED", "TRANSITION")
    if not any(m in mode for m in bearish_modes):
        return None

    # Thesis'i swing high bazlı oluştur
    patched_levels = dict(levels)
    patched_levels["_swing_high_ref"] = bounce_high
    thesis = _entry_thesis(
        "SHORT",
        levels=patched_levels,
        scenario={"name": "THESIS_SHORT_SWING_HIGH", "ref_support": ref_s, "ref_resistance": ref_r},
        px=px,
        thesis_type="SWING_HIGH_REJECTION",
        reason=f"Swing high rejection (bounce={bounce_high:.2f}, bearish={mode})",
    )
    return thesis


def _swing_low_fallback_long_thesis(
    *,
    levels: dict,
    scenario: dict,
    px: float,
    cvd: dict | None,
    ref_s: float,
    ref_r: float,
) -> "TradeThesis | None":
    """
    Swing-yapı bazlı LONG tezi — indikatörsüz, anlık price action.
    SL: son swing low, TP: supply layer.
    """
    ms = levels.get("market_state") or {}
    collapse = ms.get("collapse") or {}
    struct_u = ms.get("structure") or {}

    swing_lo = _swing_sl_anchor("LONG", px)
    if swing_lo <= 0 or swing_lo >= px:
        return None
    if swing_lo < px * 0.97:  # SL çok uzaksa kullanma
        return None

    tp = _layer_tp("LONG", px, levels)
    if tp <= 0:
        tp = ref_r if ref_r > px else 0.0
    if tp <= 0 or tp <= px:
        return None

    dominant = str(collapse.get("dominant_bias") or "").lower()
    cvd_dir = str((cvd or {}).get("direction") or "").upper()
    bullish_ok = (
        dominant in ("bull", "bullish")
        or cvd_dir == "BULL"
        or "IMPULSE_UP" in str(struct_u.get("pattern") or "")
    )
    if not bullish_ok:
        return None

    patched = dict(levels)
    patched["_swing_low_ref"] = swing_lo
    patched["active_support"] = swing_lo
    patched["active_resistance"] = tp
    thesis = _entry_thesis(
        "LONG",
        levels=patched,
        scenario={"name": "THESIS_LONG_SWING", "ref_support": swing_lo, "ref_resistance": tp},
        px=px,
        thesis_type="BROKEN_RESISTANCE",
        reason=f"Swing low hold (low={swing_lo:.2f}, tp={tp:.2f})",
    )
    return thesis


def _effective_bias_dir(levels: dict) -> str:
    """Etkin yön: 'SHORT' (bearish) | 'LONG' (bullish) | '' (belirsiz)."""
    ms = levels.get("market_state") or {}
    col = ms.get("collapse") or {}
    dom = str(col.get("dominant_bias") or "").lower()
    if "bear" in dom:
        return "SHORT"
    if "bull" in dom:
        return "LONG"
    st = ms.get("structure") or {}
    d = str(st.get("direction") or st.get("trend") or "").lower()
    if "down" in d or "bear" in d:
        return "SHORT"
    if "up" in d or "bull" in d:
        return "LONG"
    return ""


def build_trade_theses(
    *,
    levels: dict,
    scenario: dict,
    px: float,
    cvd: dict | None = None,
) -> dict[str, Any]:
    """
    LONG/SHORT tezlerini destek/direnc konumuna gore uretir.

    Gecerli temel durumlar:
    - SHORT: fiyat aktif destek altinda veya direncten satis bolgesinde
    - LONG: fiyat aktif direnc ustunde veya destekten alis bolgesinde
    """
    ref_s, ref_r = _active_prices(levels, scenario)
    zone = str(levels.get("zone") or "").upper()
    cvd_dir = str((cvd or {}).get("direction") or "").upper()
    market_state = levels.get("market_state") or {}
    verdict = str(
        market_state.get("verdict")
        or market_state.get("trade_verdict")
        or market_state.get("entry_verdict")
        or ""
    ).upper()
    out: dict[str, Any] = {
        "long": None,
        "short": None,
        "selected": None,
        "reason": "",
    }

    if px <= 0 or ref_s <= 0 or ref_r <= 0 or ref_r <= ref_s:
        out["reason"] = "Aktif destek/direnc tezi kurulamadı."
        return out

    short_levels = levels
    long_levels = levels

    if px < ref_s:
        short_levels = _with_mode(levels, "TREND_CONTINUATION")
        out["short"] = _entry_thesis(
            "SHORT",
            levels=short_levels,
            scenario={"name": "THESIS_SHORT", "ref_support": ref_s, "ref_resistance": ref_r},
            px=px,
            thesis_type="BROKEN_SUPPORT",
            reason=f"Fiyat destek altinda: px={px:.2f} < S={ref_s:.2f}",
        )
        cvd_confirmed = bool((cvd or {}).get("confirmed"))
        if zone == "NEAR_SUPPORT" and cvd_dir == "BULL" and cvd_confirmed:
            out["short"].state = "WEAK"
            out["short"].entry["valid"] = False
            out["short"].reason = (
                f"{out['short'].reason}; destek reclaim riski "
                f"(zone={zone} cvd={cvd_dir or 'NA'} verdict={verdict or 'NA'})"
            )
    elif zone == "NEAR_RESISTANCE" and px < ref_r:
        from engine.range_validation_v3 import clean_range_scenario, validate_range_trade

        range_check = validate_range_trade(
            "SHORT",
            levels=levels,
            scenario=clean_range_scenario(scenario, "SHORT"),
            cvd=cvd,
            px=px,
        )
        out["short"] = _entry_thesis(
            "SHORT",
            levels=levels,
            scenario={"name": "THESIS_SHORT", "ref_support": ref_s, "ref_resistance": ref_r},
            px=px,
            thesis_type="RESISTANCE_REJECTION",
            reason=f"Fiyat direnc bolgesinde: R={ref_r:.2f}",
        )
        if (
            not range_check.get("valid")
            or _weak_countertrend_resistance_rejection(levels, scenario, cvd_dir)
        ):
            out["short"].state = "WEAK"
            out["short"].entry["valid"] = False
            out["short"].reason = (
                f"{out['short'].reason}; range kapisi SHORT blok: "
                f"{range_check.get('reason') or 'tek basina direnc alti yeterli degil'}"
            )

    if px > ref_r:
        long_levels = _with_mode(levels, "TREND_CONTINUATION")
        out["long"] = _entry_thesis(
            "LONG",
            levels=long_levels,
            scenario={"name": "THESIS_LONG", "ref_support": ref_s, "ref_resistance": ref_r},
            px=px,
            thesis_type="BROKEN_RESISTANCE",
            reason=f"Fiyat direnc ustunde: px={px:.2f} > R={ref_r:.2f}",
        )
    elif zone == "NEAR_SUPPORT" and px > ref_s:
        from engine.range_validation_v3 import clean_range_scenario, validate_range_trade

        range_check = validate_range_trade(
            "LONG",
            levels=levels,
            scenario=clean_range_scenario(scenario, "LONG"),
            cvd=cvd,
            px=px,
        )
        out["long"] = _entry_thesis(
            "LONG",
            levels=levels,
            scenario={"name": "THESIS_LONG", "ref_support": ref_s, "ref_resistance": ref_r},
            px=px,
            thesis_type="SUPPORT_HOLD",
            reason=f"Fiyat destek ustunde: S={ref_s:.2f}",
        )
        if (
            not range_check.get("valid")
            or _weak_countertrend_support_hold(levels, scenario, cvd_dir)
        ):
            out["long"].state = "WEAK"
            out["long"].entry["valid"] = False
            out["long"].reason = (
                f"{out['long'].reason}; range kapisi LONG blok: "
                f"{range_check.get('reason') or 'tek basina destek ustu yeterli degil'}"
            )

    if out["short"] is None:
        out["short"] = _local_layer_thesis(
            "SHORT",
            levels=levels,
            scenario=scenario,
            px=px,
            cvd=cvd,
            ref_s=ref_s,
            ref_r=ref_r,
        )

    if out["short"] is None:
        out["short"] = _swing_high_fallback_short_thesis(
            levels=levels,
            scenario=scenario,
            px=px,
            cvd=cvd,
            ref_s=ref_s,
            ref_r=ref_r,
        )
    if out["long"] is None:
        out["long"] = _local_layer_thesis(
            "LONG",
            levels=levels,
            scenario=scenario,
            px=px,
            cvd=cvd,
            ref_s=ref_s,
            ref_r=ref_r,
        )

    # Swing-yapı LONG fallback — Pine SR olmasa bile çalışır
    if out["long"] is None:
        out["long"] = _swing_low_fallback_long_thesis(
            levels=levels,
            scenario=scenario,
            px=px,
            cvd=cvd,
            ref_s=ref_s,
            ref_r=ref_r,
        )

    # ── Ekstrem CVD ters-akış vetosu (#53 -3.39% squeeze önlemi) ──────────────
    # Güçlü alım akışında (ratio>0.70) SHORT, güçlü satışta (<0.30) LONG bloke.
    # confirmed şartı yok — ekstrem oran tek başına yeterli (cum +4103 gibi).
    _cvd_ratio = float((cvd or {}).get("buy_ratio", 0.5) or 0.5)
    _veto_hi = float(getattr(cfg, "V3_CVD_COUNTERFLOW_SHORT", 0.70) or 0.70)
    _veto_lo = float(getattr(cfg, "V3_CVD_COUNTERFLOW_LONG", 0.30) or 0.30)
    for _side_key, _t in (("short", out.get("short")), ("long", out.get("long"))):
        if not isinstance(_t, TradeThesis) or _t.state != "VALID":
            continue
        _against = (_side_key == "short" and _cvd_ratio >= _veto_hi) or (
            _side_key == "long" and _cvd_ratio <= _veto_lo
        )
        if _against:
            _t.state = "WEAK"
            if isinstance(_t.entry, dict):
                _t.entry["valid"] = False
            _t.reason = (
                f"{_t.reason}; CVD ekstrem ters-akış veto "
                f"(ratio={_cvd_ratio:.2f}, {_side_key} engellendi)"
            )

    # ── Trend-hizalı + iyi RR override (DB kalibrasyonu) ─────────────────────
    # Backtest (48h, n=7595): edge = RR + trend-hizası, prob DEĞİL. range_check
    # trend-hizalı RR≥2 setupları gereksiz WEAK'liyordu. Bunları geri VALID yap —
    # AMA CVD ters-akış vetosu ve ters-trend koruması korunur.
    if bool(getattr(cfg, "V3_TREND_ALIGNED_OVERRIDE", True)):
        _bias = _effective_bias_dir(levels)
        _amin = float(getattr(cfg, "V3_ALIGNED_MIN_RR", 2.0) or 2.0)
        for _sk, _t in (("short", out.get("short")), ("long", out.get("long"))):
            if not isinstance(_t, TradeThesis) or _t.state != "WEAK":
                continue
            _dir = _sk.upper()
            if _dir != _bias:
                continue  # yalnız trend-hizalı
            if float(getattr(_t, "rr", 0) or 0) < _amin:
                continue
            if _dir == "SHORT" and _cvd_ratio >= _veto_hi:
                continue  # akış alımda → short'u geri alma
            if _dir == "LONG" and _cvd_ratio <= _veto_lo:
                continue
            if "ters-akış veto" in str(_t.reason or ""):
                continue  # CVD vetosunu ezme
            ent = _t.entry if isinstance(_t.entry, dict) else {}
            has_geo = float(ent.get("sl", 0) or 0) > 0 and (
                float(ent.get("tp1", 0) or 0) > 0 or float(ent.get("tp2", 0) or 0) > 0
            )
            if not has_geo:
                continue
            _t.state = "VALID"
            ent["valid"] = True
            _t.reason = (
                f"{_t.reason}; trend-hizali RR>={_amin:.1f} override "
                f"(kalibrasyon: prob degil RR+hiza)"
            )

    candidates = [
        t for t in (out.get("short"), out.get("long"))
        if isinstance(t, TradeThesis) and t.state == "VALID"
    ]
    if not candidates:
        weak = [
            t for t in (out.get("short"), out.get("long"))
            if isinstance(t, TradeThesis) and t.state == "WEAK"
        ]
        if weak:
            best_weak = max(weak, key=lambda t: t.rr)
            out["reason"] = f"Tez var ama executable degil: {best_weak.direction} RR={best_weak.rr:.2f}"
        elif zone == "MID_RANGE" and ref_r > ref_s:
            near = max(
                px * float(getattr(cfg, "V3_ZONE_EDGE_DIST_PCT", 0.012) or 0.012),
                8.0,
            )
            edge_frac = float(getattr(cfg, "V3_ZONE_EDGE_FRAC", 0.20) or 0.20)
            band_w = ref_r - ref_s
            r_thr = max(ref_r - near, ref_s + band_w * (1.0 - edge_frac))
            s_thr = min(ref_s + near, ref_s + band_w * edge_frac)
            traverse = "evet" if levels.get("channel_traversed") else "hayir"
            out["reason"] = (
                f"Band ortasi: dirence {ref_r - px:.0f}$, destege {px - ref_s:.0f}$ "
                f"(SHORT ~{r_thr:.0f}+ LONG ~{s_thr:.0f}-) traverse={traverse}"
            )
        else:
            out["reason"] = "Executable destek/direnc tezi yok."
        return out

    # Iki tez birden gecerliyse en yuksek RR'li olan secilir; RR piyasanin kendi
    # invalidation/target geometrisidir, sabit skor carpani degildir.
    selected = max(candidates, key=lambda t: t.rr)
    out["selected"] = selected
    out["reason"] = selected.reason
    return out


def thesis_snapshot(theses: dict[str, Any]) -> dict[str, Any]:
    snap = dict(theses)
    for key in ("long", "short", "selected"):
        val = snap.get(key)
        if isinstance(val, TradeThesis):
            snap[key] = val.to_dict()
    return snap
