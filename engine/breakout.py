"""
engine/breakout.py — Swing seviye kırılımı + anlık flow (CVD 5m, taker).

15m: seviye haritası. Giriş: bookTicker. Pozisyon: yapısal çıkış + seviye çevirme.
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from core.config import cfg
from core.state import state, trade_is_fresh
from core.logger import get_logger

log = get_logger("Breakout")


def _narrative_trade_entry_allowed(
    direction: str, level: float, price: float
) -> tuple[bool, str]:
    from engine.market_narrative import trade_entry_allowed

    return trade_entry_allowed(direction, level, price)


def _narrative_update_tick(price: float, resistance: float, support: float) -> None:
    from engine.market_narrative import update_tick

    update_tick(price, resistance, support)


def _narrative_display_hint(direction: str, level: float) -> str:
    from engine.market_narrative import get_display_hint

    return get_display_hint(direction, level)


_hold_start: dict[str, float] = {"LONG": 0.0, "SHORT": 0.0}
_exit_hold_start: dict[str, float] = {"LONG": 0.0, "SHORT": 0.0}
_last_touch: str = ""
_last_status_log: float = 0.0
_levels: dict = {}
_level_tests: dict[str, dict] = {}
_level_cooldown: dict[str, float] = {}  # seviye key → son giriş ts
_struct_closing: bool = False
_breakout_lock: asyncio.Lock | None = None


def _lock() -> asyncio.Lock:
    global _breakout_lock
    if _breakout_lock is None:
        _breakout_lock = asyncio.Lock()
    return _breakout_lock


def _level_on_cooldown(level: float) -> bool:
    k = _level_key(level)
    last = _level_cooldown.get(k, 0.0)
    return (time.time() - last) < float(getattr(cfg, "ENTRY_COOLDOWN_SEC", 300))


def _mark_level_entered(level: float) -> None:
    _level_cooldown[_level_key(level)] = time.time()


def _level_key(price: float) -> str:
    return f"{round(price, 2):.2f}"


def _extension_bps(price: float, level: float, direction: str) -> float:
    if level <= 0 or price <= 0:
        return 0.0
    if direction == "LONG":
        return max(0.0, (price - level) / level * 10000.0)
    return max(0.0, (level - price) / level * 10000.0)


def _swing_high_prices() -> list[float]:
    from engine.structure_levels import _swing_prices

    return sorted(_swing_prices(state.swing_highs_15m or []))


def _swing_low_prices() -> list[float]:
    from engine.structure_levels import _swing_prices

    return sorted(_swing_prices(state.swing_lows_15m or []))


def _last_broken_swing_high(px: float) -> float:
    """Fiyatın gerçekten kırdığı en yüksek swing tepe (ara dirençler dahil)."""
    if px <= 0:
        return 0.0
    broken = [
        p for p in _swing_high_prices() if px > _break_threshold(p, "LONG")
    ]
    return max(broken) if broken else 0.0


def _last_broken_swing_low(px: float) -> float:
    if px <= 0:
        return 0.0
    broken = [
        p for p in _swing_low_prices() if px < _break_threshold(p, "SHORT")
    ]
    return min(broken) if broken else 0.0


def _nearest_swing_high_above(px: float) -> float:
    above = [p for p in _swing_high_prices() if p > px * 1.0005]
    return min(above) if above else 0.0


def _major_swing_high_above(px: float, floor: float = 0) -> float:
    """Yapısal üst direnç — destek üstündeki en yüksek swing (lokal 2126 değil)."""
    highs = _swing_high_prices()
    if not highs:
        return 0.0
    base = max(px, floor) if floor > 0 else px
    above = [h for h in highs if h > base * 1.0002]
    if not above:
        return max(highs) if highs else 0.0
    if len(above) == 1:
        return above[0]
    span_bps = (max(above) - min(above)) / base * 10000.0 if base > 0 else 0.0
    if span_bps > 80:
        return max(above)
    return min(above)


def _major_swing_low_below(px: float, ceiling: float = 0) -> float:
    lows = _swing_low_prices()
    if not lows:
        return 0.0
    cap = min(px, ceiling) if ceiling > 0 else px
    below = [p for p in lows if p < cap * 0.9998]
    if not below:
        return min(lows) if lows else 0.0
    if len(below) == 1:
        return below[0]
    span_bps = (max(below) - min(below)) / cap * 10000.0 if cap > 0 else 0.0
    if span_bps > 80:
        return min(below)
    return max(below)


def _swing_channel_extents() -> tuple[float, float]:
    """Grafik soluk kanal: tüm swing tepeleri/dipleri (konsolidasyon daraltması yok)."""
    highs = _swing_high_prices()
    lows = _swing_low_prices()
    if highs and lows:
        return max(highs), min(lows)
    return 0.0, 0.0


def _infer_break_level_long(entry: float, sl: float) -> float:
    """Restart: SL altındaki kırılan direnç (swing tepe ≈ break)."""
    if sl <= 0:
        return 0.0
    highs = sorted(_swing_high_prices())
    above_sl = [h for h in highs if h > sl * 1.0003 and h <= entry * 1.015]
    if above_sl:
        return above_sl[0]
    if highs:
        for h in reversed(highs):
            if h <= entry * 1.01 and h > sl:
                return h
    return round(sl * 1.0008, 2)


def _infer_break_level_short(entry: float, sl: float) -> float:
    if sl <= 0:
        return 0.0
    lows = sorted(_swing_low_prices(), reverse=True)
    below_sl = [p for p in lows if p < sl * 0.9997 and p >= entry * 0.985]
    if below_sl:
        return below_sl[0]
    if lows:
        for p in lows:
            if p >= entry * 0.99 and p < sl:
                return p
    return round(sl * 0.9992, 2)


def _nearest_swing_low_below(px: float) -> float:
    below = [p for p in _swing_low_prices() if p < px * 0.9995]
    return max(below) if below else 0.0


def _nearest_swing_low_below_level(level: float) -> float:
    if level <= 0:
        return 0.0
    below = [p for p in _swing_low_prices() if p < level * 0.9998]
    return max(below) if below else 0.0


def _support_from_break_swing(
    break_lvl: float, px: float = 0.0, fallback: float = 0.0
) -> float:
    """
    Tek destek kaynağı:
    - varsa kırılım çizgisinin altındaki en yakın swing dip
    - yoksa fiyatın altındaki en yakın swing dip
    - o da yoksa fallback
    """
    ref = break_lvl if break_lvl > 0 else px
    s = _nearest_swing_low_below_level(ref) if ref > 0 else 0.0
    if s > 0:
        return round(s, 2)
    s = _nearest_swing_low_below(px) if px > 0 else 0.0
    if s > 0:
        return round(s, 2)
    return round(fallback, 2) if fallback > 0 else 0.0


def _channel_swing_bounds(px: float = 0) -> tuple[float, float]:
    """
    Ham 15m kanalı: fiyatı çevreleyen swing tepe/dip.
    highs[-1]/lows[-1] konsolidasyonda 66bps dar kanal üretir — kullanılmaz.
    """
    px = float(px or state.mark_price or state.price or 0)
    highs = _swing_high_prices()
    lows = _swing_low_prices()
    sh = state.swing_highs_15m or []
    sl = state.swing_lows_15m or []

    if px > 0 and highs and lows:
        above = [p for p in highs if p > px * 1.0002]
        below = [p for p in lows if p < px * 0.9998]
        resist = min(above) if above else max(highs)
        support = max(below) if below else min(lows)
        if resist > support:
            return resist, support

    resist = float(sh[-1]["price"]) if sh else 0.0
    support = float(sl[-1]["price"]) if sl else 0.0
    if resist > 0 and support > 0 and resist <= support:
        hi, lo = max(resist, support), min(resist, support)
        if hi > lo:
            return hi, lo
    return resist, support


def _fixed_swing_trade_band(
    px: float = 0.0, base: dict | None = None
) -> tuple[float, float, str]:
    """
    Sabit trade bandı:
    - ana tercih: refresh anında kilitlenen lokal/taktik bant
    - fallback: 15m swing kutusu / çevreleyen swing tepe-dip
    Bu bant yalnızca 15m refresh ile güncellenir; tick bazında zıplamaz.
    """
    px = float(px or state.mark_price or state.price or 0)
    base = dict(base or _levels)

    stored_r = float(base.get("fixed_trade_resistance") or 0)
    stored_s = float(base.get("fixed_trade_support") or 0)
    if stored_r > stored_s > 0:
        return (
            round(stored_r, 2),
            round(stored_s, 2),
            str(base.get("fixed_trade_source") or "15m_swing_box"),
        )

    ext_r, ext_s = _swing_channel_extents()
    if ext_r > ext_s > 0:
        return round(ext_r, 2), round(ext_s, 2), "15m_swing_box"

    swing_r = float(base.get("swing_resistance") or 0)
    swing_s = float(base.get("swing_support") or 0)
    if swing_r > swing_s > 0:
        return round(swing_r, 2), round(swing_s, 2), "15m_surround_swings"

    ch_r, ch_s = _channel_swing_bounds(px)
    if ch_r > ch_s > 0:
        return round(ch_r, 2), round(ch_s, 2), "15m_surround_swings"

    return 0.0, 0.0, ""


def _fixed_active_major_band(
    px: float = 0.0, base: dict | None = None
) -> tuple[float, float]:
    """
    Aktif majör artık ayrı iç kutu üretmez.
    Sabit trade bandının kendisini kullanır; sadece 15m refresh ile değişir.
    """
    px = float(px or state.mark_price or state.price or 0)
    base = dict(base or _levels)

    stored_r = float(base.get("fixed_active_major_resistance") or 0)
    stored_s = float(base.get("fixed_active_major_support") or 0)
    if stored_r > stored_s > 0:
        return round(stored_r, 2), round(stored_s, 2)

    fixed_r = float(base.get("fixed_trade_resistance") or 0)
    fixed_s = float(base.get("fixed_trade_support") or 0)
    if fixed_r > fixed_s > 0:
        return round(fixed_r, 2), round(fixed_s, 2)

    return 0.0, 0.0


def _compute_refresh_tactical_band(
    px: float,
    swing_r: float,
    swing_s: float,
    *,
    break_lvl: float = 0.0,
    trade_r: float = 0.0,
    trade_s: float = 0.0,
    flipped: bool = False,
) -> tuple[float, float, dict]:
    """
    Lokal/taktik işlem bandını refresh anında hesapla ve dondur.
    Tick bazında yeniden üretme; yeni 15m yapıda güncellensin.
    """
    eff_r, eff_s = 0.0, 0.0
    layer_meta: dict = {}

    try:
        from engine.structure_cookie import cookie_channel

        ck_s, ck_r, ck_m = cookie_channel(px, break_lvl)
        layer_meta = dict(ck_m or {})
        min_conf = float(ck_m.get("min_edge_confidence", 0) or 0)
        floor_conf = float(
            getattr(cfg, "STRUCTURE_COOKIE_MIN_EDGE_CONF", 0.5) or 0.5
        )
        if ck_m.get("quality") in ("ok", "kısmi", "dar") and ck_s > 0:
            if ck_m.get("quality") in ("ok", "dar") or min_conf >= floor_conf * 0.85:
                eff_s = ck_s
                hybrid_r, hybrid_r_src = _hybrid_local_trigger_resistance(
                    px, eff_s, swing_r, ck_m
                )
                hybrid_s, hybrid_s_src = _hybrid_local_trigger_support(
                    px, ck_r if ck_r > ck_s else swing_r, swing_s, ck_m
                )
                if hybrid_r > eff_s:
                    eff_r = hybrid_r
                    layer_meta["resistance"] = hybrid_r
                    layer_meta["resistance_source"] = hybrid_r_src or "hybrid_local"
                    layer_meta["resistance_reason"] = (
                        f"hibrit yakın cap {hybrid_r:.2f}"
                    )
                elif ck_r > ck_s and ck_r > px * 1.0002:
                    eff_r = ck_r
                else:
                    eff_r = _resolved_long_resistance(
                        eff_s, swing_r, swing_s, px, trade_r
                    )
                if hybrid_s > 0:
                    layer_meta["support"] = hybrid_s
                    layer_meta["support_source"] = hybrid_s_src or "hybrid_local"
                    layer_meta["support_reason"] = (
                        f"hibrit yakın floor {hybrid_s:.2f}"
                    )
    except Exception:
        pass

    if eff_r <= 0 or eff_s <= 0:
        eff_r, eff_s = _apply_flat_level_map(
            px, swing_r, swing_s, break_lvl, trade_r, trade_s, flipped
        )
        if eff_r > 0:
            layer_meta.setdefault("resistance_source", "15m_swing")
            layer_meta.setdefault("resistance_reason", f"15m swing cap {eff_r:.2f}")
        if eff_s > 0:
            layer_meta.setdefault("support_source", "15m_swing")
            layer_meta.setdefault("support_reason", f"15m swing floor {eff_s:.2f}")

    published_s = _support_from_break_swing(break_lvl, px, eff_s or swing_s)
    if published_s <= 0:
        published_s = eff_s

    tactical_r = eff_r
    tactical_s = published_s if published_s > 0 else eff_s
    if tactical_r > 0 and tactical_s > 0 and tactical_r <= tactical_s:
        tactical_r = _resolved_long_resistance(
            tactical_s, swing_r, swing_s, px, trade_r
        )

    layer_meta["support"] = tactical_s
    layer_meta["resistance"] = tactical_r
    layer_meta["range_support"] = tactical_s
    layer_meta["range_resistance"] = tactical_r
    layer_meta["quality"] = str(layer_meta.get("quality", "") or "ok")
    layer_meta["tradeable"] = True
    layer_meta["support_confidence"] = max(
        0.75, float(layer_meta.get("support_confidence", 0) or 0)
    )
    layer_meta["resistance_confidence"] = max(
        0.75, float(layer_meta.get("resistance_confidence", 0) or 0)
    )
    layer_meta["min_edge_confidence"] = max(
        0.75, float(layer_meta.get("min_edge_confidence", 0) or 0)
    )
    return round(tactical_r, 2), round(tactical_s, 2), layer_meta


def _swings_within_band(
    support: float, resistance: float, *, px: float = 0.0
) -> tuple[float, float]:
    """
    Ana destek/direnç bandının içinde kalan swing noktaları.
    Dışarıdaki dip/tepe artık ana swing etiketi sayılmaz.
    """
    if resistance <= support or support <= 0:
        return 0.0, 0.0
    highs = [p for p in _swing_high_prices() if support < p < resistance]
    lows = [p for p in _swing_low_prices() if support < p < resistance]
    sw_r = min(highs) if highs else 0.0
    sw_s = max(lows) if lows else 0.0
    if sw_r <= 0:
        sw_r = resistance
    if sw_s <= 0:
        sw_s = support
    if sw_r <= sw_s:
        sw_r, sw_s = resistance, support
    return sw_r, sw_s


def _clustered_bar_channel_levels(
    px: float, support: float, resistance: float, lookback: int = 48
) -> tuple[float, float, str]:
    """
    15m mumlardan yatay raf/kume bul:
    - destek: alt yaridaki tekrar eden dip bandi
    - direnc: ust yaridaki tekrar eden tepe bandi
    Trader'in cizdigi yatay kanal raflarini wick ekstreminden ayirir.
    """
    if px <= 0 or resistance <= support or support <= 0:
        return 0.0, 0.0, ""

    try:
        from engine.structure import get_bars_15m

        bars = get_bars_15m(lookback) or []
    except Exception:
        return 0.0, 0.0, ""

    if len(bars) < 8:
        return 0.0, 0.0, ""

    width = resistance - support
    bucket = max(width * 0.03, px * 0.0006, 0.35)
    lower_cap = support + width * 0.45
    upper_floor = resistance - width * 0.45

    def _cluster(values: list[float], *, choose_highest: bool) -> float:
        if len(values) < 3:
            return 0.0
        groups: dict[float, list[float]] = {}
        for v in values:
            key = round(round(v / bucket) * bucket, 2)
            groups.setdefault(key, []).append(float(v))
        ranked = sorted(
            groups.items(),
            key=lambda item: (
                len(item[1]),
                item[0] if choose_highest else -item[0],
            ),
            reverse=True,
        )
        best_key, best_vals = ranked[0]
        if len(best_vals) < 2:
            return 0.0
        val = sum(best_vals) / len(best_vals)
        return round(val or best_key, 2)

    low_vals = [
        float(b["low"])
        for b in bars
        if support * 1.0002 < float(b["low"]) < lower_cap
    ]
    high_vals = [
        float(b["high"])
        for b in bars
        if upper_floor < float(b["high"]) < resistance * 0.9998
    ]

    shelf_s = _cluster(low_vals, choose_highest=True)
    shelf_r = _cluster(high_vals, choose_highest=False)
    if shelf_r > shelf_s > 0:
        return shelf_r, shelf_s, "15m_shelf"
    return 0.0, 0.0, ""


def _inner_channel_levels(
    px: float, support: float, resistance: float
) -> tuple[float, float, str]:
    """
    Ana bandın içindeki aktif kanal.
    - önce 15m iç swing rafları
    - sonra son 1m mumlardan daha sıkı yerel raf
    """
    if px <= 0 or resistance <= support or support <= 0:
        return 0.0, 0.0, ""

    width = resistance - support
    channel_r = 0.0
    channel_s = 0.0
    sources: list[str] = []

    shelf_r, shelf_s, shelf_src = _clustered_bar_channel_levels(
        px, support, resistance
    )
    if 0 < shelf_r < resistance * 0.9998:
        channel_r = shelf_r
        sources.append(shelf_src)
    if support * 1.0002 < shelf_s < resistance:
        channel_s = shelf_s
        sources.append(shelf_src)

    inner_r, inner_s = _swings_within_band(support, resistance, px=px)
    if 0 < inner_r < resistance * 0.9998:
        channel_r = min(channel_r, inner_r) if channel_r > 0 else inner_r
        sources.append("15m_inner")
    if support * 1.0002 < inner_s < resistance:
        channel_s = max(channel_s, inner_s) if channel_s > 0 else inner_s
        sources.append("15m_inner")

    try:
        from engine.bars_1m import get_bars_1m

        look = max(8, int(getattr(cfg, "RANGE_LOCAL_LOOKBACK", 12) or 12))
        bars = get_bars_1m(look)
        tol = width * 0.0025
        valid = [
            b
            for b in bars
            if b["high"] <= resistance + tol and b["low"] >= support - tol
        ]
        if len(valid) >= 4:
            local_r = min(max(b["high"] for b in valid), resistance)
            local_s = max(min(b["low"] for b in valid), support)
            if support * 1.0002 < local_s < resistance * 0.9998 and local_r > local_s:
                if channel_r <= 0 or local_r < channel_r:
                    channel_r = local_r
                if channel_s <= 0 or local_s > channel_s:
                    channel_s = local_s
                sources.append("1m_local")
    except Exception:
        pass

    min_span = max(width * 0.08, px * 0.0008)
    if (
        channel_r <= 0
        or channel_s <= 0
        or channel_r <= channel_s
        or channel_r >= resistance * 0.9998
        or channel_s <= support * 1.0002
        or (channel_r - channel_s) < min_span
    ):
        return 0.0, 0.0, ""

    return round(channel_r, 2), round(channel_s, 2), "+".join(dict.fromkeys(sources))


def _apply_flat_level_map(
    px: float,
    swing_r: float,
    swing_s: float,
    break_lvl: float,
    trade_r: float,
    trade_s: float,
    flipped: bool,
) -> tuple[float, float]:
    """Pozisyon yokken: kırılmış swing + fiyat bağlamı ile S/R haritası."""
    eff_r, eff_s = swing_r, swing_s
    if px <= 0 or swing_r <= swing_s:
        return eff_r, eff_s

    broken_hi = _last_broken_swing_high(px)
    broken_lo = _last_broken_swing_low(px)

    if broken_hi > 0 and (broken_lo == 0 or px >= broken_hi):
        eff_s = max(broken_hi, break_lvl) if break_lvl > 0 else broken_hi
        eff_r = _resolved_long_resistance(eff_s, swing_r, swing_s, px, trade_r)
    elif broken_lo > 0 and (broken_hi == 0 or px <= broken_lo):
        eff_r = min(broken_lo, break_lvl) if break_lvl > 0 else broken_lo
        nxt = _nearest_swing_low_below(px)
        eff_s = (
            nxt
            if nxt > 0
            else (
                trade_s
                if trade_s > 0 and trade_s < eff_r
                else _post_break_target_below(eff_r, swing_r, swing_s)
            )
        )
    elif flipped and break_lvl > 0:
        if px > break_lvl:
            eff_s = max(break_lvl, broken_hi or 0.0)
            eff_r = _resolved_long_resistance(eff_s, swing_r, swing_s, px, trade_r)
        elif px < break_lvl:
            eff_r = min(break_lvl, broken_lo or break_lvl)
            nxt = _nearest_swing_low_below(px)
            eff_s = (
                nxt
                if nxt > 0
                else _post_break_target_below(eff_r, swing_r, swing_s)
            )

    if eff_r > 0 and px > eff_r * 1.0002:
        eff_s = max(eff_s, broken_hi or 0.0, break_lvl or 0.0)
        eff_r = _resolved_long_resistance(eff_s, swing_r, swing_s, px, trade_r)
    if eff_s > 0 and px < eff_s * 0.9998 and eff_r > 0:
        eff_r = min(eff_r, broken_lo or eff_r, break_lvl or eff_r)
        nxt = _nearest_swing_low_below(px)
        eff_s = (
            nxt
            if nxt > 0
            else _post_break_target_below(eff_r, swing_r, swing_s)
        )

    return eff_r, eff_s


def _promoted_long_support(
    break_lvl: float, swing_r: float, swing_s: float, px: float
) -> float:
    """LONG: işlem desteği = zemin (floor); break_lvl = derin invalidasyon."""
    inv = break_lvl if break_lvl > 0 else (swing_s if swing_s > 0 else 0.0)
    floor = _long_floor_support(px, inv)
    if inv > 0 and floor > inv:
        return floor
    return floor if floor > 0 else (inv if inv > 0 else swing_s)


def _promoted_short_resistance(
    break_lvl: float, swing_r: float, swing_s: float, px: float
) -> float:
    base = break_lvl if break_lvl > 0 else (swing_r if swing_r > 0 else 0.0)
    broken = _last_broken_swing_low(px)
    if broken > 0:
        return min(base, broken) if base > 0 else broken
    return base if base > 0 else swing_r


def _recent_range_high(lookback: int = 96) -> float:
    """Son N 15m mumun en yüksek noktası — gerçek rally tepesi (hayalet swing değil)."""
    try:
        from engine.structure import get_bars_15m

        bars = get_bars_15m(lookback)
        if bars:
            return max(float(b["high"]) for b in bars)
    except Exception:
        pass
    return 0.0


def _flipped_resistances_as_support(px: float, break_lvl: float) -> list[float]:
    """Kırılmış swing tepeleri → destek (fiyatın altında kalanlar)."""
    if px <= 0:
        return []
    out: list[float] = []
    for h in _swing_high_prices():
        if break_lvl > 0 and h <= break_lvl * 1.0002:
            continue
        if h >= px * 0.9995:
            continue
        if px > _break_threshold(h, "LONG"):
            out.append(h)
    return out


def _level_lookback_hours() -> dict:
    """Strateji seviyelerinin veri penceresi (dashboard / log)."""
    from engine.structure_cookie import cookie_meta

    n15 = int(getattr(cfg, "CHART_BARS_15M", 96) or 96)
    lb = int(getattr(cfg, "SWING_LB_15M", 10) or 10)
    ck = cookie_meta()
    return {
        "bars_15m": n15,
        "hours_15m": round(n15 * 15 / 60, 1),
        "swing_neighborhood_bars": lb * 2 + 1,
        "cookie_bars": ck.get("cookie_bars_macro", ck.get("cookie_bars", 32)),
        "cookie_hours": ck.get("cookie_hours", 8.0),
    }


def _long_floor_support(px: float, break_lvl: float) -> float:
    """
    Aktif destek = kırılım seviyesinin altındaki swing dip.
    """
    if px <= 0:
        return break_lvl if break_lvl > 0 else 0.0
    return _support_from_break_swing(break_lvl, px)


def _active_support_long(px: float, break_lvl: float) -> float:
    return _long_floor_support(px, break_lvl)


def _rally_ceiling_long(eff_s: float) -> float:
    """Pullback direnç tavanı: mum tepe + swing tepelerinin en yükseği (tek kaynak)."""
    if eff_s <= 0:
        return 0.0
    hi = 0.0
    for lb in (96, 48, 24):
        hi = max(hi, _recent_range_high(lb))
    for h in _swing_high_prices():
        if h > eff_s * 1.0002:
            hi = max(hi, h)
    return round(hi, 2) if hi > 0 else 0.0


def _recent_range_low(lookback: int = 96) -> float:
    try:
        from engine.structure import get_bars_15m

        bars = get_bars_15m(lookback)
        if bars:
            return min(float(b["low"]) for b in bars)
    except Exception:
        pass
    return 0.0


def _next_resistance_long(
    px: float, eff_s: float, trade_r: float = 0
) -> float:
    """
    Üst direnç:
    - Pullback: rally tavanı (max mum high + swing tepeleri)
    - Rally kırıldı: fiyat üstündeki ilk swing / TP1
    """
    if px <= 0 or eff_s <= 0:
        return 0.0

    rally = _rally_ceiling_long(eff_s)
    if rally > px * 1.0002:
        return rally

    highs = sorted(_swing_high_prices())
    above_px = [h for h in highs if h > px * 1.0002 and h > eff_s * 1.0002]
    if above_px:
        return above_px[0]

    tp1 = float(state.pos_tp1 or trade_r or 0)
    if tp1 > eff_s and tp1 > px * 1.0002:
        return tp1

    if rally > eff_s:
        return rally

    if not highs:
        return 0.0

    for h in highs:
        if h <= eff_s * 1.0002:
            continue
        if h > px * 1.0002 and px <= _break_threshold(h, "LONG"):
            return h

    nearest = _nearest_swing_high_above(px)
    if nearest > eff_s:
        return nearest

    above_break = [h for h in highs if h > eff_s * 1.0002]
    return min(above_break) if above_break else 0.0


def _resolved_long_resistance(
    eff_s: float, swing_r: float, swing_s: float, px: float, trade_r: float
) -> float:
    r = _next_resistance_long(px, eff_s, trade_r)
    if r > 0:
        return r
    return _post_break_target_above(eff_s, swing_r, swing_s)


def _hybrid_local_trigger_resistance(
    px: float, eff_s: float, swing_r: float, ck_meta: dict
) -> tuple[float, str]:
    """
    Hibrit tetik direnci:
    Uzak rally ceiling yerine fiyata en yakın çalışılabilir cap'i seç.
    """
    if (
        getattr(cfg, "ENTRY_MODE", "break").lower() != "hybrid"
        or state.in_position
        or px <= 0
        or eff_s <= 0
    ):
        return 0.0, ""

    floor = max(px * 1.0002, eff_s * 1.0002)
    candidates: list[tuple[float, str]] = []
    for key, label in (
        ("micro_resistance", "micro_accept"),
        ("meso_resistance", "meso_accept"),
        ("range_resistance", "range_accept"),
        ("resistance", "cookie_cap"),
    ):
        lv = float(ck_meta.get(key, 0) or 0)
        if lv > floor:
            candidates.append((lv, label))
    if swing_r > floor:
        candidates.append((float(swing_r), "swing_cap"))
    if not candidates:
        return 0.0, ""
    candidates.sort(key=lambda item: item[0])
    return round(candidates[0][0], 2), candidates[0][1]


def _hybrid_local_trigger_support(
    px: float, eff_r: float, swing_s: float, ck_meta: dict
) -> tuple[float, str]:
    if (
        getattr(cfg, "ENTRY_MODE", "break").lower() != "hybrid"
        or state.in_position
        or px <= 0
        or eff_r <= 0
    ):
        return 0.0, ""

    ceil = min(px * 0.9998, eff_r * 0.9998)
    candidates: list[tuple[float, str]] = []
    for key, label in (
        ("micro_support", "micro_accept"),
        ("meso_support", "meso_accept"),
        ("range_support", "range_accept"),
        ("support", "cookie_floor"),
    ):
        lv = float(ck_meta.get(key, 0) or 0)
        if 0 < lv < ceil:
            candidates.append((lv, label))
    if 0 < swing_s < ceil:
        candidates.append((float(swing_s), "swing_floor"))
    if not candidates:
        return 0.0, ""
    candidates.sort(key=lambda item: item[0], reverse=True)
    return round(candidates[0][0], 2), candidates[0][1]


def _sync_promoted_structure(px: float, eff_s: float, eff_r: float) -> None:
    """Pozisyon kaydını çözümlenmiş yapı ile senkronla (SL/çıkış/grafik)."""
    pb = state.position_breakout
    if not pb or not state.in_position or px <= 0:
        return
    side = (state.pos_side or pb.get("direction") or "").upper()
    confirm_pct = 0.003  # %0.3 ters tepki ile swing onayi
    if side == "LONG" and eff_s > 0:
        old = float(pb.get("structural_support") or pb.get("break_level") or 0)
        cand = float(pb.get("pending_structural_support") or 0)
        if cand <= 0 or eff_s < cand - 0.01:
            cand = eff_s
            pb["pending_structural_support"] = cand
        rebound_ok = px >= cand * (1.0 + confirm_pct)
        if rebound_ok and cand > 0:
            # LONG: yapisal destek bir kez onaylandiktan sonra asagi cekilmez.
            if old <= 0 or cand > old + 0.01:
                pb["structural_support"] = cand
                pb["active_support"] = cand
                old_txt = f" (onceki {old:.2f})" if old > 0 else ""
                log.debug(f"Yapısal destek: {cand:.2f}{old_txt}")
            pb["pending_structural_support"] = float(pb.get("structural_support") or cand)
        else:
            trail_s = max(float(pb.get("active_support") or 0), float(pb.get("structural_support") or 0), cand)
            if trail_s > float(pb.get("active_support") or 0) + 0.01:
                pb["active_support"] = trail_s
        state.position_breakout = pb
    elif side == "SHORT" and eff_r > 0:
        old = float(pb.get("structural_resistance") or pb.get("break_level") or 0)
        cand = float(pb.get("pending_structural_resistance") or 0)
        if cand <= 0 or eff_r > cand + 0.01:
            cand = eff_r
            pb["pending_structural_resistance"] = cand
        pullback_ok = px <= cand * (1.0 - confirm_pct)
        if pullback_ok and cand > 0:
            # SHORT: yapisal direnç bir kez onaylandiktan sonra yukari alinmaz.
            if old <= 0 or cand < old - 0.01:
                pb["structural_resistance"] = cand
                pb["active_resistance"] = cand
                log.debug(f"Yapısal direnç: {cand:.2f}" + (f" (onceki {old:.2f})" if old > 0 else ""))
            pb["pending_structural_resistance"] = float(pb.get("structural_resistance") or cand)
        else:
            trail_r = min(
                x for x in (
                    float(pb.get("active_resistance") or 0),
                    float(pb.get("structural_resistance") or 0),
                    cand,
                ) if x > 0
            ) if any(float(pb.get(k) or 0) > 0 for k in ("active_resistance", "structural_resistance", "pending_structural_resistance")) else 0
            if trail_r > 0 and (
                float(pb.get("active_resistance") or 0) <= 0
                or trail_r < float(pb.get("active_resistance") or 0) - 0.01
            ):
                pb["active_resistance"] = trail_r
        state.position_breakout = pb


def _resolve_trading_levels(
    price: float = 0, base_override: dict | None = None
) -> dict:
    """
    Stratejinin kullandığı destek/direnç — swing + kırılım flip + fiyat bağlamı.
    Grafik ve giriş/çıkış aynı haritayı görür.
    """
    px = float(price or state.mark_price or state.price or 0)
    base = dict(base_override if base_override is not None else _levels)
    pb = state.position_breakout or {}

    swing_r = float(
        base.get("swing_resistance") or base.get("resistance") or 0
    )
    swing_s = float(base.get("swing_support") or base.get("support") or 0)
    ext_r, ext_s = _swing_channel_extents()
    chan_r, chan_s = _channel_swing_bounds(px)
    if ext_r > ext_s > 0:
        swing_r, swing_s = ext_r, ext_s
    elif chan_r > chan_s > 0:
        swing_r, swing_s = chan_r, chan_s
    if swing_r > 0 and swing_s > 0 and swing_r <= swing_s:
        hi, lo = max(swing_r, swing_s), min(swing_r, swing_s)
        if hi > lo:
            swing_r, swing_s = hi, lo

    trade_r = float(base.get("trade_resistance") or 0)
    trade_s = float(base.get("trade_support") or 0)
    break_lvl = float(
        base.get("entry_break_level") or pb.get("break_level") or 0
    )
    flipped = bool(base.get("flipped") or pb.get("break_mode"))

    eff_r, eff_s = swing_r, swing_s
    layer_meta: dict = {}
    pos_side = (pb.get("direction") or state.pos_side or "").upper()

    if state.in_position and pos_side == "LONG":
        if break_lvl <= 0:
            break_lvl = _infer_break_level_long(
                px, float(state.pos_sl or pb.get("sl") or 0)
            )
        inv_s = break_lvl if break_lvl > 0 else swing_s
        display_s = _active_support_long(px, inv_s)
        trail_s = _promoted_long_support(break_lvl, swing_r, swing_s, px)
        ck_s, ck_r, ck_meta = 0.0, 0.0, {}
        try:
            from engine.structure_cookie import cookie_channel

            ck_s, ck_r, ck_meta = cookie_channel(px, inv_s, "LONG")
            layer_meta = dict(ck_meta or {})
        except Exception:
            pass
        if ck_meta.get("quality") in ("ok", "kısmi", "dar") and ck_s > 0 and ck_s < px:
            eff_s = ck_s
            display_s = ck_s
            trail_s = ck_s
        else:
            if px > 0 and trail_s >= px:
                trail_s = display_s if display_s < px else _nearest_swing_low_below(px)
            eff_s = trail_s if trail_s > inv_s * 1.0005 else inv_s
            if px > 0 and eff_s >= px:
                eff_s = inv_s
        if (
            ck_meta.get("quality") in ("ok", "kısmi", "dar")
            and ck_r > eff_s
            and ck_r > px * 0.9995
        ):
            eff_r = ck_r
        else:
            eff_r = _resolved_long_resistance(eff_s, swing_r, swing_s, px, trade_r)
        _sync_promoted_structure(px, trail_s, eff_r)
    elif state.in_position and pos_side == "SHORT":
        inv_r = break_lvl if break_lvl > 0 else swing_r
        ck_s, ck_r, ck_meta = 0.0, 0.0, {}
        try:
            from engine.structure_cookie import cookie_channel

            ck_s, ck_r, ck_meta = cookie_channel(px, inv_r, "SHORT")
            layer_meta = dict(ck_meta or {})
        except Exception:
            pass
        if (
            ck_meta.get("quality") in ("ok", "kısmi", "dar")
            and ck_r > px
            and ck_s > 0
            and ck_r > ck_s
        ):
            eff_s, eff_r = ck_s, ck_r
        else:
            eff_r = _promoted_short_resistance(break_lvl, swing_r, swing_s, px)
            nxt = _nearest_swing_low_below(px)
            if trade_s > 0 and trade_s < eff_r:
                eff_s = trade_s
            elif nxt > 0 and nxt < eff_r:
                eff_s = nxt
            else:
                eff_s = _post_break_target_below(eff_r, swing_r, swing_s)
        _sync_promoted_structure(px, eff_s, eff_r)
    else:
        eff_r, eff_s = 0.0, 0.0
        ck_m: dict = {}
        try:
            from engine.structure_cookie import cookie_channel

            ck_s, ck_r, ck_m = cookie_channel(px, break_lvl)
            layer_meta = dict(ck_m or {})
        except Exception:
            pass

        fixed_r, fixed_s, fixed_src = _fixed_swing_trade_band(px, base)
        if fixed_r > fixed_s > 0:
            eff_r, eff_s = fixed_r, fixed_s
            layer_meta["support"] = eff_s
            layer_meta["resistance"] = eff_r
            layer_meta["range_support"] = eff_s
            layer_meta["range_resistance"] = eff_r
            layer_meta["support_source"] = fixed_src or "15m_swing_box"
            layer_meta["resistance_source"] = fixed_src or "15m_swing_box"
            layer_meta["support_reason"] = f"15m sabit destek {eff_s:.2f}"
            layer_meta["resistance_reason"] = f"15m sabit direnç {eff_r:.2f}"
            layer_meta["quality"] = str(base.get("fixed_trade_quality") or "ok")
            layer_meta["tradeable"] = True
            layer_meta["support_confidence"] = max(
                0.75,
                float(
                    base.get("fixed_trade_support_confidence")
                    or layer_meta.get("support_confidence", 0)
                    or 0
                ),
            )
            layer_meta["resistance_confidence"] = max(
                0.75,
                float(
                    base.get("fixed_trade_resistance_confidence")
                    or layer_meta.get("resistance_confidence", 0)
                    or 0
                ),
            )
            layer_meta["min_edge_confidence"] = max(
                0.75,
                float(
                    base.get("fixed_trade_min_edge_confidence")
                    or layer_meta.get("min_edge_confidence", 0)
                    or 0
                ),
            )

        if eff_r <= 0 or eff_s <= 0:
            eff_r, eff_s = _apply_flat_level_map(
                px, swing_r, swing_s, break_lvl, trade_r, trade_s, flipped
            )

    published_s = _support_from_break_swing(break_lvl, px, eff_s or swing_s)
    if state.in_position and eff_s > 0:
        published_s = eff_s
    if published_s <= 0:
        published_s = eff_s

    if not state.in_position and published_s > 0:
        eff_s = published_s
        if eff_r <= eff_s:
            eff_r = _resolved_long_resistance(eff_s, swing_r, swing_s, px, trade_r)

    if eff_r > 0 and eff_s > 0 and eff_r <= eff_s:
        eff_r = max(eff_r, eff_s * 1.001)

    inv_support = (
        break_lvl
        if not state.in_position and break_lvl > 0
        else (break_lvl if break_lvl > 0 else eff_s)
    )
    display_support = (
        _active_support_long(px, inv_support)
        if pos_side == "LONG" and px > 0
        else published_s
    )

    ck_meta: dict = dict(layer_meta or {})
    if not ck_meta:
        try:
            from engine.structure_cookie import cookie_channel

            _, _, ck_meta = cookie_channel(px, inv_support)
        except Exception:
            pass

    tactical_r = eff_r
    tactical_s = published_s if not state.in_position and published_s > 0 else eff_s
    sticky_structural_r = float(
        base.get("structural_major_resistance") or 0
    )
    sticky_structural_s = float(
        base.get("structural_major_support") or 0
    )
    active_major_r, deep_major_r = _resolve_major_resistance_layers(
        tactical_r,
        swing_r,
        ck_meta,
        sticky_structural_r,
    )
    active_major_s, deep_major_s = _resolve_major_support_layers(
        tactical_s,
        swing_s,
        ck_meta,
        sticky_structural_s,
    )
    if active_major_r > 0 and active_major_s > 0 and active_major_r <= active_major_s:
        active_major_r = max(active_major_r, tactical_r, swing_r)
        active_major_s = min(active_major_s, tactical_s, swing_s)
    if not state.in_position:
        fixed_active_r, fixed_active_s = _fixed_active_major_band(px, base)
        if fixed_active_r > fixed_active_s > 0:
            active_major_r = fixed_active_r
            active_major_s = fixed_active_s
    structural_r = sticky_structural_r or active_major_r
    structural_s = sticky_structural_s or active_major_s
    deep_structural_r = (
        deep_major_r
        or (structural_r if structural_r > active_major_r * 1.002 else 0.0)
        or 0.0
    )
    deep_structural_s = (
        deep_major_s
        or (structural_s if 0 < structural_s < active_major_s * 0.998 else 0.0)
        or 0.0
    )

    out = {**base}
    out.update(
        {
            "resistance": tactical_r,
            "support": tactical_s,
            "invalidation_support": inv_support,
            "active_support": display_support,
            "swing_resistance": swing_r,
            "swing_support": swing_s,
            "trade_resistance": trade_r or eff_r,
            "trade_support": trade_s or eff_s,
            "tactical_cap_resistance": tactical_r,
            "tactical_floor_support": tactical_s,
            "active_major_resistance": active_major_r,
            "active_major_support": active_major_s,
            "deep_major_resistance": deep_major_r,
            "deep_major_support": deep_major_s,
            "structural_major_resistance": structural_r,
            "structural_major_support": structural_s,
            "deep_structural_major_resistance": deep_structural_r,
            "deep_structural_major_support": deep_structural_s,
            "break_level": break_lvl,
            "flipped": flipped,
            "cookie_support": 0.0,
            "cookie_resistance": float(ck_meta.get("resistance", 0) or 0),
            "cookie_quality": str(ck_meta.get("quality", "") or ""),
            "cookie_regime": str(ck_meta.get("regime", "") or ""),
            "support_confidence": float(ck_meta.get("support_confidence", 0) or 0),
            "resistance_confidence": float(ck_meta.get("resistance_confidence", 0) or 0),
            "min_edge_confidence": float(ck_meta.get("min_edge_confidence", 0) or 0),
            "cookie_layer": str(ck_meta.get("layer", "") or ""),
            "structural_major_quality": str(
                base.get("structural_major_quality") or ck_meta.get("quality", "") or ""
            ),
            "structural_major_layer": str(
                base.get("structural_major_layer") or "macro"
            ),
            "structural_resistance_source": str(
                base.get("structural_resistance_source")
                or ck_meta.get("resistance_source", "")
                or ""
            ),
            "structural_support_source": str(
                base.get("structural_support_source")
                or ck_meta.get("support_source", "")
                or ""
            ),
            "structural_resistance_reason": str(
                base.get("structural_resistance_reason")
                or ck_meta.get("resistance_reason", "")
                or ""
            ),
            "structural_support_reason": str(
                base.get("structural_support_reason")
                or ck_meta.get("support_reason", "")
                or ""
            ),
            "resistance_source": str(
                ck_meta.get("resistance_source", "next_cap") or "next_cap"
            ),
            "resistance_reason": str(ck_meta.get("resistance_reason", "") or ""),
            "support_source": (
                "swing_below_break"
                if break_lvl > 0
                else str(ck_meta.get("support_source", "swing_below_break") or "swing_below_break")
            ),
            "support_reason": (
                f"kırılım {break_lvl:.2f} altı swing {published_s:.2f}"
                if break_lvl > 0 and published_s > 0
                else str(ck_meta.get("support_reason", "en yakın swing dip") or "en yakın swing dip")
            ),
            "micro_resistance": float(ck_meta.get("micro_resistance", 0) or 0),
            "micro_support": float(ck_meta.get("micro_support", 0) or 0),
            "meso_resistance": float(ck_meta.get("meso_resistance", 0) or 0),
            "meso_support": float(ck_meta.get("meso_support", 0) or 0),
            "range_resistance": float(ck_meta.get("range_resistance", 0) or eff_r),
            "range_support": float(ck_meta.get("range_support", 0) or published_s),
            "deep_resistance": float(ck_meta.get("deep_resistance", 0) or 0),
            "deep_support": float(ck_meta.get("deep_support", 0) or 0),
            "band_width_bps": round((eff_r - eff_s) / px * 10000.0, 1)
            if eff_r > eff_s > 0 and px > 0
            else 0.0,
        }
    )
    return out


def _apply_resolved_levels(price: float = 0) -> None:
    """_levels içindeki resistance/support = strateji seviyeleri."""
    resolved = _resolve_trading_levels(price)
    _levels["resistance"] = resolved["resistance"]
    _levels["support"] = resolved["support"]
    _levels["swing_resistance"] = resolved["swing_resistance"]
    _levels["swing_support"] = resolved["swing_support"]
    _levels["updated_ts"] = time.time()


def _refresh_sticky_structure_levels(
    px: float, swing_r: float = 0.0, swing_s: float = 0.0
) -> None:
    if px <= 0:
        return
    struct_s = struct_r = 0.0
    meta: dict = {}
    try:
        from engine.structure_cookie import cookie_structural_channel

        struct_s, struct_r, meta = cookie_structural_channel(px)
    except Exception:
        meta = {}

    ext_r, ext_s = _swing_channel_extents()
    if struct_r <= 0:
        struct_r = ext_r or swing_r or _major_swing_high_above(px)
    if struct_s <= 0:
        struct_s = ext_s or swing_s or _major_swing_low_below(px)

    if struct_r > 0:
        _levels["structural_major_resistance"] = round(struct_r, 2)
        _levels["deep_structural_major_resistance"] = round(struct_r, 2)
    if struct_s > 0:
        _levels["structural_major_support"] = round(struct_s, 2)
        _levels["deep_structural_major_support"] = round(struct_s, 2)
    _levels["structural_major_quality"] = str(meta.get("quality", "") or "")
    _levels["structural_major_layer"] = str(meta.get("layer", "macro") or "macro")
    _levels["structural_resistance_source"] = str(
        meta.get("resistance_source", meta.get("source", "")) or ""
    )
    _levels["structural_support_source"] = str(
        meta.get("support_source", meta.get("source", "")) or ""
    )
    _levels["structural_resistance_reason"] = str(
        meta.get("resistance_reason", "") or ""
    )
    _levels["structural_support_reason"] = str(meta.get("support_reason", "") or "")


def get_active_levels(price: float = 0) -> dict:
    """Range/breakout/SL — çözümlenmiş destek-direnç haritası."""
    px = float(price or state.mark_price or state.price or 0)
    if not _levels:
        r_sw, s_sw = _channel_swing_bounds(px)
        if r_sw > 0 and s_sw > 0:
            return _resolve_trading_levels(
                px,
                base_override={
                    "swing_resistance": r_sw,
                    "swing_support": s_sw,
                },
            )
        return {}
    return _resolve_trading_levels(px)


def get_swing_channel() -> tuple[float, float]:
    """Ham 15m swing kanalı — fiyatı çevreleyen swing tepe/dip."""
    px = float(state.mark_price or state.price or 0)
    return _channel_swing_bounds(px)


def _sync_channel_levels_from_swings(
    swing_r: float, swing_s: float, pb: dict | None = None
) -> None:
    """Swing güncelle → strateji seviyelerini yeniden çöz."""
    global _levels
    pb = pb or state.position_breakout or {}
    r_sw = float(swing_r or _levels.get("swing_resistance", 0) or 0)
    s_sw = float(swing_s or _levels.get("swing_support", 0) or 0)
    if r_sw > 0 and s_sw > 0 and r_sw <= s_sw:
        hi, lo = max(r_sw, s_sw), min(r_sw, s_sw)
        if hi > lo:
            r_sw, s_sw = hi, lo
    if r_sw > 0:
        _levels["swing_resistance"] = r_sw
    if s_sw > 0:
        _levels["swing_support"] = s_sw
    if pb:
        tr = float(pb.get("active_resistance") or 0)
        ts = float(pb.get("active_support") or 0)
        if tr > 0:
            _levels["trade_resistance"] = tr
        if ts > 0:
            _levels["trade_support"] = ts
        bl = float(pb.get("break_level") or 0)
        if bl > 0:
            _levels["entry_break_level"] = bl
        if pb.get("break_mode"):
            _levels["flipped"] = True
    _refresh_sticky_structure_levels(
        float(state.mark_price or state.price or 0), r_sw, s_sw
    )
    _apply_resolved_levels()


def feeds_ok() -> tuple[bool, str]:
    return _feeds_ok()


def level_on_cooldown(level: float) -> bool:
    return _level_on_cooldown(level)


def mark_level_entered(level: float) -> None:
    _mark_level_entered(level)


def _record_level_event(level: float, role: str, kind: str) -> None:
    """kind: approach | failed | broken"""
    k = _level_key(level)
    rec = _level_tests.setdefault(
        k, {"tests": 0, "failed": 0, "broken": 0, "role": role, "last_ts": 0.0}
    )
    rec["role"] = role
    rec["last_ts"] = time.time()
    if kind == "failed":
        rec["failed"] += 1
        rec["tests"] += 1
        log.info(
            f"Seviye testi: {role} {level:.2f} başarısız kırılım "
            f"(toplam test={rec['tests']} failed={rec['failed']})"
        )
    elif kind == "approach":
        rec["tests"] += 1
    elif kind == "broken":
        rec["broken"] += 1
        rec["tests"] += 1


def _next_swing_above(price: float) -> float:
    from engine.structure_levels import nearest_swing_above

    sw = nearest_swing_above(price, state.swing_highs_15m or [])
    if sw > 0:
        return sw
    tp1 = float(state.struct_tp1_target or 0)
    return tp1 if tp1 > price else 0.0


def _next_swing_below(price: float) -> float:
    from engine.structure_levels import nearest_swing_below

    return nearest_swing_below(price, state.swing_lows_15m or [])


def _post_break_target_above(
    break_level: float, swing_r: float, swing_s: float
) -> float:
    """Kırılım sonrası üst hedef — komşu swing / kanal span."""
    from engine.structure_thresholds import post_break_min_bps
    from engine.structure_levels import nearest_swing_above_min_bps

    px = float(state.mark_price or state.price or 0)
    min_bps = post_break_min_bps(break_level, px)
    highs = state.swing_highs_15m or []
    far = nearest_swing_above_min_bps(break_level, highs, min_bps)
    if far > 0:
        return far
    if swing_s > 0 and break_level > swing_s:
        from engine.structure_thresholds import bar_noise_bps, channel_band_bps

        span = break_level - swing_s
        band = channel_band_bps(px)
        breath = bar_noise_bps(px) / band if band > 0 else 0.15
        return round(
            break_level + max(span * breath, break_level * min_bps / 10000.0),
            2,
        )
    return round(break_level * (1.0 + min_bps / 10000.0), 2)


def _post_break_target_below(
    break_level: float, swing_r: float, swing_s: float
) -> float:
    from engine.structure_thresholds import post_break_min_bps
    from engine.structure_levels import nearest_swing_below_min_bps

    px = float(state.mark_price or state.price or 0)
    min_bps = post_break_min_bps(break_level, px)
    lows = state.swing_lows_15m or []
    far = nearest_swing_below_min_bps(break_level, lows, min_bps)
    if far > 0:
        return far
    if swing_r > 0 and swing_r > break_level:
        from engine.structure_thresholds import bar_noise_bps, channel_band_bps

        span = swing_r - break_level
        band = channel_band_bps(px)
        breath = bar_noise_bps(px) / band if band > 0 else 0.15
        return round(
            break_level - max(span * breath, break_level * min_bps / 10000.0),
            2,
        )
    return round(break_level * (1.0 - min_bps / 10000.0), 2)


def _break_threshold(level: float, direction: str) -> float:
    from engine.structure_thresholds import break_threshold_price

    return break_threshold_price(level, direction)


def _proximity_ratio(price: float, level: float) -> float:
    if level <= 0 or price <= 0:
        return 1.0
    return abs(price - level) / level


def _level_distance_bps(price: float, level: float) -> float:
    if level <= 0 or price <= 0:
        return 99999.0
    return abs(price - level) / level * 10000.0


def is_inside_band(price: float, support: float, resistance: float) -> bool:
    """S–R arası = kanal sistemi; alt/üst = kırılım sistemi."""
    if support <= 0 or resistance <= 0 or price <= 0:
        return False
    return support <= price <= resistance


def _outside_break_candidate(
    price: float, support: float, resistance: float
) -> tuple[str, float]:
    """Destek altı → SHORT; direnç üstü → LONG (swing kanalı + uzantı filtresi)."""
    from engine.structure_thresholds import is_late_same_level_entry

    if support > 0 and price < _break_threshold(support, "SHORT"):
        late, _ = is_late_same_level_entry(price, support, "SHORT")
        if late:
            lv = _resolve_trading_levels(price)
            if price > lv["support"]:
                return "", 0.0
        return "SHORT", support

    if resistance > 0 and price > _break_threshold(resistance, "LONG"):
        late, _ = is_late_same_level_entry(price, resistance, "LONG")
        if late:
            lv = _resolve_trading_levels(price)
            if price < lv["resistance"]:
                return "", 0.0
        return "LONG", resistance

    return "", 0.0


def _breakout_trigger_levels(price: float, lv: dict | None = None) -> tuple[float, float]:
    """
    Breakout tetiği için birincil seviye:
    önce lokal/taktik band, yoksa ham swing kanal.
    Ekran ve karar motoru aynı çizgiyi kullanmalı.
    """
    px = float(price or state.mark_price or state.price or 0)
    levels = dict(lv or get_active_levels(px))

    tactical_r = float(
        levels.get("tactical_cap_resistance") or levels.get("resistance") or 0
    )
    tactical_s = float(
        levels.get("tactical_floor_support") or levels.get("support") or 0
    )
    swing_r = float(levels.get("swing_resistance") or tactical_r or 0)
    swing_s = float(levels.get("swing_support") or tactical_s or 0)

    trigger_r = tactical_r if tactical_r > 0 else swing_r
    trigger_s = tactical_s if tactical_s > 0 else swing_s

    if trigger_r > 0 and trigger_s > 0 and trigger_r <= trigger_s:
        if swing_r > swing_s > 0:
            trigger_r, trigger_s = swing_r, swing_s
        else:
            hi, lo = max(trigger_r, trigger_s), min(trigger_r, trigger_s)
            trigger_r, trigger_s = hi, lo

    return round(trigger_r, 2) if trigger_r > 0 else 0.0, (
        round(trigger_s, 2) if trigger_s > 0 else 0.0
    )


def _nearest_major_resistance(trigger: float, *levels: float) -> float:
    vals = sorted({round(float(v), 2) for v in levels if float(v or 0) > 0})
    if not vals:
        return 0.0
    if trigger > 0:
        above = [v for v in vals if v > trigger * 1.0002]
        if above:
            return above[0]
    return max(vals)


def _nearest_major_support(trigger: float, *levels: float) -> float:
    vals = sorted({round(float(v), 2) for v in levels if float(v or 0) > 0})
    if not vals:
        return 0.0
    if trigger > 0:
        below = [v for v in vals if v < trigger * 0.9998]
        if below:
            return below[-1]
    return min(vals)


def _next_major_resistance_above(trigger: float, *levels: float) -> float:
    vals = sorted({round(float(v), 2) for v in levels if float(v or 0) > 0})
    if not vals or trigger <= 0:
        return 0.0
    above = [v for v in vals if v > trigger * 1.0002]
    return above[0] if above else 0.0


def _next_major_support_below(trigger: float, *levels: float) -> float:
    vals = sorted({round(float(v), 2) for v in levels if float(v or 0) > 0})
    if not vals or trigger <= 0:
        return 0.0
    below = [v for v in vals if v < trigger * 0.9998]
    return below[-1] if below else 0.0


def _latched_major_key(direction: str) -> str:
    return (
        "latched_active_major_resistance"
        if (direction or "").upper() == "LONG"
        else "latched_active_major_support"
    )


def _clear_latched_major(direction: str = "") -> None:
    if direction:
        _levels.pop(_latched_major_key(direction), None)
        return
    _levels.pop("latched_active_major_resistance", None)
    _levels.pop("latched_active_major_support", None)


def _latched_major_level(
    direction: str,
    trigger_level: float,
    current_major: float,
    price: float,
) -> float:
    d = (direction or "").upper()
    key = _latched_major_key(d)
    if d == "LONG":
        if price <= _break_threshold(trigger_level, "LONG"):
            _levels.pop(key, None)
            return current_major
        latched = float(_levels.get(key) or 0)
        if latched > trigger_level * 1.0002:
            return latched
        if current_major > trigger_level * 1.0002:
            _levels[key] = round(current_major, 2)
            return current_major
        return current_major

    if price >= _break_threshold(trigger_level, "SHORT"):
        _levels.pop(key, None)
        return current_major
    latched = float(_levels.get(key) or 0)
    if 0 < latched < trigger_level * 0.9998:
        return latched
    if 0 < current_major < trigger_level * 0.9998:
        _levels[key] = round(current_major, 2)
        return current_major
    return current_major


def _remember_outside_break(direction: str, level: float) -> None:
    if level <= 0:
        return
    _levels["latched_outside_dir"] = (direction or "").upper()
    _levels["latched_outside_level"] = round(level, 2)
    _levels["latched_outside_ts"] = time.time()


def _clear_outside_break() -> None:
    _levels.pop("latched_outside_dir", None)
    _levels.pop("latched_outside_level", None)
    _levels.pop("latched_outside_ts", None)


def _latched_outside_break_candidate(
    price: float, support: float, resistance: float
) -> tuple[str, float]:
    if price <= 0:
        return "", 0.0
    d = str(_levels.get("latched_outside_dir") or "").upper()
    level = float(_levels.get("latched_outside_level") or 0)
    ts = float(_levels.get("latched_outside_ts") or 0)
    if not d or level <= 0 or ts <= 0:
        return "", 0.0

    max_age = float(getattr(cfg, "HYBRID_RETEST_WINDOW_SEC", 420) or 420)
    if time.time() - ts > max_age:
        _clear_outside_break()
        return "", 0.0

    from engine.structure_thresholds import bar_noise_bps, proximity_bps

    retest_bps = max(bar_noise_bps(price) * 2.0, proximity_bps(price) * 1.15, 18.0)
    band = retest_bps / 10000.0
    if d == "LONG":
        if support > 0 and price < support * (1.0 - band * 1.4):
            _clear_outside_break()
            return "", 0.0
        if price >= level * (1.0 - band):
            return "LONG", level
        _clear_outside_break()
        return "", 0.0

    if resistance > 0 and price > resistance * (1.0 + band * 1.4):
        _clear_outside_break()
        return "", 0.0
    if price <= level * (1.0 + band):
        return "SHORT", level
    _clear_outside_break()
    return "", 0.0


def hybrid_continuation_candidate(
    price: float,
    support: float,
    resistance: float,
) -> tuple[str, float, str]:
    """Sığ retest ile breakout continuation adayı."""
    if price <= 0 or support <= 0 or resistance <= support:
        return "", 0.0, ""

    d, level = _latched_outside_break_candidate(price, support, resistance)
    if d and level > 0:
        return d, level, f"retest {level:.2f}"

    from engine.structure_thresholds import bar_noise_bps, proximity_bps

    retest_bps = max(bar_noise_bps(price) * 2.0, proximity_bps(price) * 1.15, 18.0)
    band = retest_bps / 10000.0

    broken_hi = _last_broken_swing_high(price)
    if (
        broken_hi > 0
        and price >= broken_hi * (1.0 - band)
        and price <= broken_hi * (1.0 + band * 1.6)
    ):
        if price >= support * (1.0 - band * 1.4):
            return "LONG", broken_hi, f"sığ retest {broken_hi:.2f}"

    broken_lo = _last_broken_swing_low(price)
    if (
        broken_lo > 0
        and price <= broken_lo * (1.0 + band)
        and price >= broken_lo * (1.0 - band * 1.6)
    ):
        if price <= resistance * (1.0 + band * 1.4):
            return "SHORT", broken_lo, f"sığ retest {broken_lo:.2f}"

    return "", 0.0, ""


def hybrid_pressure_candidate(
    price: float,
    support: float,
    resistance: float,
    lv: dict | None = None,
) -> tuple[str, float, float, str]:
    """
    Hibrit band içinde erken baskı kurulumu.
    LONG: üst banda sıkışma, SHORT: alt banda baskı.
    """
    if (
        getattr(cfg, "ENTRY_MODE", "break").lower() != "hybrid"
        or state.in_position
        or price <= 0
        or support <= 0
        or resistance <= support
        or not is_inside_band(price, support, resistance)
    ):
        return "", 0.0, 0.0, ""

    lv = dict(lv or get_active_levels(price))
    width_bps = _channel_width_bps(price, support, resistance)
    if width_bps <= 0:
        return "", 0.0, 0.0, ""

    from engine.structure_thresholds import bar_noise_bps, proximity_bps

    tv = dict(state.trend_view or {})
    bias = str(tv.get("bias") or "").upper()
    strength = int(tv.get("strength", 0) or 0)
    rise_active = bool(tv.get("rise_active"))
    drop_active = bool(tv.get("drop_active"))
    band_pct = (price - support) / (resistance - support)

    edge_bps = max(
        proximity_bps(price) * 1.35,
        bar_noise_bps(price) * 1.8,
        min(width_bps * 0.24, 24.0),
    )
    flow_long_soft = _flow_ok("LONG") or (
        float(state.cvd_5m or 0) >= 0 and float(state.taker_ratio or 0) >= 0.50
    )
    flow_short_soft = _flow_ok("SHORT") or (
        float(state.cvd_5m or 0) <= 0 and float(state.taker_ratio or 0) <= 0.50
    )

    swing_r = float(lv.get("swing_resistance") or resistance)
    swing_s = float(lv.get("swing_support") or support)
    long_trigger, long_src = _hybrid_local_trigger_resistance(
        price, support, swing_r, lv
    )
    short_trigger, short_src = _hybrid_local_trigger_support(
        price, resistance, swing_s, lv
    )
    long_inv = _nearest_major_support(
        price,
        float(lv.get("micro_support") or 0),
        float(lv.get("meso_support") or 0),
        float(lv.get("range_support") or 0),
        float(lv.get("tactical_floor_support") or lv.get("support") or 0),
        float(lv.get("deep_support") or 0),
        float(lv.get("invalidation_support") or 0),
    )
    short_inv = _nearest_major_resistance(
        price,
        float(lv.get("micro_resistance") or 0),
        float(lv.get("meso_resistance") or 0),
        float(lv.get("range_resistance") or 0),
        float(lv.get("tactical_cap_resistance") or lv.get("resistance") or 0),
        float(lv.get("deep_resistance") or 0),
    )

    long_gap = _level_distance_bps(price, long_trigger) if long_trigger > 0 else 99999.0
    short_gap = (
        _level_distance_bps(price, short_trigger) if short_trigger > 0 else 99999.0
    )

    long_bias_ok = flow_long_soft or (
        (bias == "UP" and strength >= 36) or rise_active
    )
    short_bias_ok = flow_short_soft or (
        (bias == "DOWN" and strength >= 36) or drop_active
    )
    long_hard_block = bias == "DOWN" and strength >= 58 and not flow_long_soft
    short_hard_block = bias == "UP" and strength >= 58 and not flow_short_soft

    if (
        long_trigger > price * 1.0002
        and 0 < long_inv < price * 0.9995
        and band_pct >= 0.58
        and long_gap <= edge_bps
        and long_bias_ok
        and not long_hard_block
    ):
        return (
            "LONG",
            round(long_trigger, 2),
            round(long_inv, 2),
            f"üst bant baskı {long_src or 'local_cap'} {long_trigger:.2f}",
        )

    if (
        0 < short_trigger < price * 0.9998
        and short_inv > price * 1.0005
        and band_pct <= 0.42
        and short_gap <= edge_bps
        and short_bias_ok
        and not short_hard_block
    ):
        return (
            "SHORT",
            round(short_trigger, 2),
            round(short_inv, 2),
            f"alt bant baskı {short_src or 'local_floor'} {short_trigger:.2f}",
        )

    return "", 0.0, 0.0, ""


def _resolve_major_resistance_layers(
    tactical_r: float,
    swing_r: float,
    ck_meta: dict,
    sticky_r: float,
) -> tuple[float, float]:
    micro_r = float(ck_meta.get("micro_resistance", 0) or 0)
    acceptance_r = float(ck_meta.get("range_resistance", 0) or 0)
    chosen_r = float(ck_meta.get("resistance", 0) or 0)
    deep_r = float(ck_meta.get("deep_resistance", 0) or 0)

    # Aktif majör = mevcut auction içinde tekrar test edilen yakın cap.
    active_r = (
        micro_r
        or tactical_r
        or chosen_r
        or acceptance_r
        or swing_r
        or sticky_r
        or deep_r
        or 0.0
    )
    deep_major_r = _next_major_resistance_above(
        active_r,
        acceptance_r,
        deep_r,
        sticky_r,
        chosen_r,
        swing_r,
        tactical_r,
    )
    if deep_major_r <= 0 and sticky_r > active_r * 1.002:
        deep_major_r = sticky_r
    return round(active_r, 2) if active_r > 0 else 0.0, (
        round(deep_major_r, 2) if deep_major_r > 0 else 0.0
    )


def _resolve_major_support_layers(
    tactical_s: float,
    swing_s: float,
    ck_meta: dict,
    sticky_s: float,
) -> tuple[float, float]:
    micro_s = float(ck_meta.get("micro_support", 0) or 0)
    range_s = float(ck_meta.get("range_support", 0) or 0)
    chosen_s = float(ck_meta.get("support", 0) or 0)
    deep_s = float(ck_meta.get("deep_support", 0) or 0)

    active_s = (
        micro_s
        or tactical_s
        or chosen_s
        or range_s
        or swing_s
        or sticky_s
        or deep_s
        or 0.0
    )
    deep_major_s = _next_major_support_below(
        active_s,
        range_s,
        deep_s,
        sticky_s,
        chosen_s,
        swing_s,
        tactical_s,
    )
    if deep_major_s <= 0 and 0 < sticky_s < active_s * 0.998:
        deep_major_s = sticky_s
    return round(active_s, 2) if active_s > 0 else 0.0, (
        round(deep_major_s, 2) if deep_major_s > 0 else 0.0
    )


def _hybrid_breakout_headroom_ok(
    price: float, direction: str, level: float, lv: dict
) -> tuple[bool, str]:
    if price <= 0 or level <= 0:
        return True, ""
    from engine.structure_thresholds import bar_noise_bps

    noise = bar_noise_bps(price)
    d = (direction or "").upper()
    if d == "LONG":
        support_ref = float(
            lv.get("active_support")
            or lv.get("tactical_floor_support")
            or lv.get("support")
            or lv.get("invalidation_support")
            or 0
        )
        rally = _rally_ceiling_long(support_ref) if support_ref > 0 else 0.0
        local_cap = _nearest_major_resistance(
            level,
            float(lv.get("micro_resistance") or 0),
            float(lv.get("meso_resistance") or 0),
            float(lv.get("range_resistance") or 0),
            float(lv.get("tactical_cap_resistance") or lv.get("resistance") or 0),
            rally,
        )
        structural_cap = float(lv.get("structural_major_resistance") or 0)
        deep_structural_cap = float(lv.get("deep_structural_major_resistance") or 0)
        cap = 0.0
        cap_src = ""
        if local_cap > price * 1.0002 and local_cap > level * 1.0002:
            cap = local_cap
            cap_src = "local cap"
        elif deep_structural_cap > level * 1.0002:
            cap = deep_structural_cap
            cap_src = "deep cap"
        elif structural_cap > level * 1.0002:
            cap = structural_cap
            cap_src = "major cap"
        else:
            cap = _nearest_major_resistance(
                level,
                float(lv.get("tactical_cap_resistance") or lv.get("resistance") or 0),
                float(lv.get("cookie_resistance") or 0),
                float(lv.get("range_resistance") or 0),
                float(lv.get("deep_resistance") or 0),
                rally,
            )
            cap_src = "next cap"
        if cap <= price * 1.0002 or cap <= level * 1.0002:
            return True, ""
        full_span = (cap - level) / level * 10000.0
        remain = (cap - price) / price * 10000.0
        min_headroom = max(noise * 1.0, full_span * 0.28)
        if remain <= min_headroom:
            return False, (
                f"Hybrid LONG: {cap_src} {cap:.2f} çok yakın "
                f"({remain:.0f}bps kaldı)"
            )
        return True, ""

    local_floor = _nearest_major_support(
        level,
        float(lv.get("micro_support") or 0),
        float(lv.get("meso_support") or 0),
        float(lv.get("range_support") or 0),
        float(lv.get("tactical_floor_support") or lv.get("support") or 0),
    )
    structural_floor = float(lv.get("structural_major_support") or 0)
    deep_structural_floor = float(lv.get("deep_structural_major_support") or 0)
    floor = 0.0
    floor_src = ""
    if 0 < local_floor < price * 0.9998 and 0 < local_floor < level * 0.9998:
        floor = local_floor
        floor_src = "local floor"
    elif 0 < deep_structural_floor < level * 0.9998:
        floor = deep_structural_floor
        floor_src = "deep floor"
    elif 0 < structural_floor < level * 0.9998:
        floor = structural_floor
        floor_src = "major destek"
    else:
        floor = _nearest_major_support(
            level,
            float(lv.get("tactical_floor_support") or lv.get("support") or 0),
            float(lv.get("range_support") or 0),
            float(lv.get("deep_support") or 0),
            float(lv.get("invalidation_support") or 0),
        )
        floor_src = "next floor"
    if floor <= 0 or floor >= price * 0.9998 or floor >= level * 0.9998:
        return True, ""
    full_span = (level - floor) / level * 10000.0
    remain = (price - floor) / price * 10000.0
    min_headroom = max(noise * 1.0, full_span * 0.28)
    if remain <= min_headroom:
        return False, (
            f"Hybrid SHORT: {floor_src} {floor:.2f} çok yakın "
            f"({remain:.0f}bps kaldı)"
        )
    return True, ""


def _status_line(code: str, detail: str = "") -> str:
    return f"{code} — {detail}" if detail else code


def refresh_levels(force: bool = False) -> None:
    """15m kapanışında swing seviyelerini güncelle (pozisyon yokken tam reset)."""
    global _hold_start, _exit_hold_start, _last_touch, _levels

    px = float(state.mark_price or state.price or 0)
    resist, support = _channel_swing_bounds(px)
    outer_r, outer_s, outer_src = _fixed_swing_trade_band(
        px,
        {
            "swing_resistance": resist,
            "swing_support": support,
        },
    )
    fixed_r, fixed_s, fixed_meta = _compute_refresh_tactical_band(px, resist, support)
    fixed_active_r, fixed_active_s = _fixed_active_major_band(
        px,
        {
            "swing_resistance": resist,
            "swing_support": support,
            "fixed_trade_resistance": fixed_r or resist,
            "fixed_trade_support": fixed_s or support,
        },
    )
    highs = state.swing_highs_15m or []
    lows = state.swing_lows_15m or []

    if state.in_position and state.position_breakout:
        _sync_channel_levels_from_swings(resist, support, state.position_breakout)
        state.breakout_view = get_status_snapshot(state.price or state.mark_price)
        try:
            from engine.range_trade import get_range_snapshot

            state.range_view = get_range_snapshot(state.price or state.mark_price)
        except Exception:
            pass
        lv = get_active_levels()
        bl = float((state.position_breakout or {}).get("break_level") or 0)
        ext_r, ext_s = _swing_channel_extents()
        rally = _rally_ceiling_long(bl or float(lv.get("support") or 0))
        log.info(
            f"15m seviye (pozisyon): direnç={lv.get('resistance', 0):.2f} "
            f"destek={lv.get('support', 0):.2f} kırılım={bl:.2f} "
            f"rally_tavan={rally:.2f} | yerel={resist:.2f}/{support:.2f} "
            f"swing={ext_r:.2f}/{ext_s:.2f}"
        )
        return

    _levels = {
        "swing_resistance": resist,
        "swing_support": support,
        "fixed_trade_resistance": fixed_r or resist,
        "fixed_trade_support": fixed_s or support,
        "fixed_trade_source": str(
            fixed_meta.get("resistance_source")
            or fixed_meta.get("support_source")
            or outer_src
            or "15m_local_band"
        ),
        "fixed_trade_quality": str(fixed_meta.get("quality", "") or "ok"),
        "fixed_trade_support_confidence": float(
            fixed_meta.get("support_confidence", 0) or 0
        ),
        "fixed_trade_resistance_confidence": float(
            fixed_meta.get("resistance_confidence", 0) or 0
        ),
        "fixed_trade_min_edge_confidence": float(
            fixed_meta.get("min_edge_confidence", 0) or 0
        ),
        "fixed_active_major_resistance": fixed_active_r,
        "fixed_active_major_support": fixed_active_s,
        "updated_ts": time.time(),
    }
    _refresh_sticky_structure_levels(px, resist, support)
    _apply_resolved_levels()
    _hold_start = {"LONG": 0.0, "SHORT": 0.0}
    _exit_hold_start = {"LONG": 0.0, "SHORT": 0.0}
    _last_touch = ""

    state.breakout_view = get_status_snapshot(state.price or state.mark_price)
    try:
        from engine.range_trade import get_range_snapshot

        state.range_view = get_range_snapshot(state.price or state.mark_price)
    except Exception:
        pass
    n_hi = len(highs)
    n_lo = len(lows)
    min_swings = 2
    if resist <= 0 or support <= 0 or n_hi < min_swings or n_lo < min_swings:
        log.warning(
            f"Swing yetersiz — seviye izleme kapalı "
            f"(highs={n_hi} lows={n_lo} R={resist:.2f} S={support:.2f}). "
            f"15m backfill bekleyin veya botu yeniden başlatın."
        )
    elif resist > 0 or support > 0:
        lv = get_active_levels(px)
        ck_s = float(lv.get("support") or 0)
        ck_r = float(lv.get("cookie_resistance") or lv.get("resistance") or 0)
        major_r = float(lv.get("active_major_resistance") or lv.get("structural_major_resistance") or 0)
        major_s = float(lv.get("active_major_support") or lv.get("structural_major_support") or 0)
        sticky_r = float(lv.get("structural_major_resistance") or 0)
        sticky_s = float(lv.get("structural_major_support") or 0)
        ck_q = lv.get("cookie_quality") or "—"
        bw = float(lv.get("band_width_bps") or 0)
        mc = float(lv.get("min_edge_confidence") or 0)
        src = lv.get("support_source") or "?"
        rng_s = float(lv.get("range_support") or 0)
        log.info(
            f"Seviyeler: direnç={ck_r:.2f} destek={ck_s:.2f} "
            f"(kırılım altı swing={rng_s:.2f} | "
            f"{bw:.0f}bps, {ck_q}, conf={mc:.2f}, src={src}) | "
            f"sabit_bant={fixed_r:.2f}/{fixed_s:.2f} ({_levels.get('fixed_trade_source') or '—'}) | "
            f"dış_kutu={outer_r:.2f}/{outer_s:.2f} ({outer_src or '—'}) | "
            f"sabit_aktif={fixed_active_r:.2f}/{fixed_active_s:.2f} | "
            f"aktif majör={major_r:.2f}/{major_s:.2f} "
            f"sticky={sticky_r:.2f}/{sticky_s:.2f} | "
            f"swing R={resist:.2f} S={support:.2f} (highs={n_hi} lows={n_lo})"
        )


def on_entry_filled(details: dict) -> None:
    """
    Giriş sonrası seviye çevirme: kırılan direnç → destek (LONG) vb.
    """
    global _hold_start, _exit_hold_start, _last_touch

    direction = details.get("direction", "")
    break_level = float(details.get("break_level", 0) or 0)
    if not direction or break_level <= 0:
        return

    from engine.market_narrative import on_level_entered

    on_level_entered(direction, break_level)

    swing_r = float(
        details.get("range_resistance")
        or _levels.get("swing_resistance")
        or _levels.get("resistance", 0)
        or 0
    )
    swing_s = float(
        details.get("range_support")
        or _levels.get("swing_support")
        or _levels.get("support", 0)
        or 0
    )

    if direction == "LONG":
        new_support = break_level
        new_resist = _post_break_target_above(break_level, swing_r, swing_s)
        if new_resist <= break_level:
            new_resist = float(details.get("tp1", 0)) or _next_swing_above(break_level)
        struct_exit = new_support
    else:
        new_resist = break_level
        new_support = _post_break_target_below(break_level, swing_r, swing_s)
        if new_support >= break_level:
            new_support = float(details.get("tp1", 0)) or _next_swing_below(break_level)
        struct_exit = new_resist

    _levels["swing_resistance"] = swing_r or _levels.get("swing_resistance", 0)
    _levels["swing_support"] = swing_s or _levels.get("swing_support", 0)
    _levels["trade_resistance"] = new_resist
    _levels["trade_support"] = new_support
    _levels["entry_break_level"] = break_level
    _levels["flipped"] = True
    _clear_latched_major()
    _clear_outside_break()
    _apply_resolved_levels()
    _record_level_event(break_level, "break", "broken")

    state.position_breakout = {
        "direction": direction,
        "entry_mode": "break",
        "break_mode": True,
        "sl_profile": "break_retest",
        "break_level": break_level,
        "structural_support": break_level if direction == "LONG" else new_support,
        "structural_resistance": break_level if direction == "SHORT" else new_resist,
        "active_support": new_support,
        "active_resistance": new_resist,
        "range_support": float(details.get("range_support", 0) or new_support),
        "range_resistance": float(details.get("range_resistance", 0) or new_resist),
        "tp1": float(details.get("tp1", 0) or state.pos_tp1),
        "tp2": float(details.get("tp2", 0) or state.pos_tp2),
        "tp1_break_confirmed": False,
        "tp1_reject_count": 0,
        "tp1_runner_ok": False,
        "entry_ts": time.time(),
    }
    if direction == "LONG":
        state.position_breakout["structural_support"] = break_level
    else:
        state.position_breakout["structural_resistance"] = break_level

    _hold_start = {"LONG": 0.0, "SHORT": 0.0}
    _exit_hold_start = {"LONG": 0.0, "SHORT": 0.0}
    _last_touch = ""

    log.info(
        f"Seviye çevrildi ({direction}): kırılım={break_level:.2f} → "
        f"destek={new_support:.2f} direnç={new_resist:.2f} | yapısal çıkış seviyesi={struct_exit:.2f}"
    )
    state.breakout_view = get_status_snapshot(state.price or state.mark_price)


def on_pressure_entry_filled(details: dict) -> None:
    """Pressure girişi sonrası yapısal seviyeleri invalidasyon çevresinde sabitle."""
    global _hold_start, _exit_hold_start, _last_touch

    direction = str(details.get("direction") or "").upper()
    if not direction:
        return

    inv = float(
        details.get("pressure_invalidation")
        or details.get("break_level")
        or 0
    )
    trigger = float(details.get("pressure_trigger_level") or 0)
    if inv <= 0:
        return

    from engine.market_narrative import on_level_entered

    on_level_entered(direction, trigger if trigger > 0 else inv)

    swing_r = float(
        details.get("range_resistance")
        or _levels.get("swing_resistance")
        or _levels.get("resistance", 0)
        or 0
    )
    swing_s = float(
        details.get("range_support")
        or _levels.get("swing_support")
        or _levels.get("support", 0)
        or 0
    )
    tp1 = float(details.get("tp1", 0) or state.pos_tp1)
    tp2 = float(details.get("tp2", 0) or state.pos_tp2)

    if direction == "LONG":
        active_support = inv
        active_resist = trigger if trigger > inv else (tp1 or swing_r)
        if active_resist <= inv:
            active_resist = _post_break_target_above(inv, swing_r, swing_s) or tp1
        struct_exit = active_support
    else:
        active_resist = inv
        active_support = trigger if 0 < trigger < inv else (tp1 or swing_s)
        if active_support <= 0 or active_support >= inv:
            active_support = _post_break_target_below(inv, swing_r, swing_s) or tp1
        struct_exit = active_resist

    _levels["swing_resistance"] = swing_r or _levels.get("swing_resistance", 0)
    _levels["swing_support"] = swing_s or _levels.get("swing_support", 0)
    _levels["trade_resistance"] = active_resist
    _levels["trade_support"] = active_support
    _levels["entry_break_level"] = inv
    _levels["flipped"] = False
    _clear_latched_major()
    _clear_outside_break()
    _apply_resolved_levels()

    state.position_breakout = {
        "direction": direction,
        "entry_mode": "pressure",
        "pressure_mode": True,
        "break_mode": False,
        "sl_profile": "pressure_hold",
        "break_level": inv,
        "pressure_trigger_level": trigger,
        "structural_support": inv if direction == "LONG" else active_support,
        "structural_resistance": inv if direction == "SHORT" else active_resist,
        "active_support": active_support,
        "active_resistance": active_resist,
        "range_support": float(details.get("range_support", 0) or swing_s),
        "range_resistance": float(details.get("range_resistance", 0) or swing_r),
        "tp1": tp1,
        "tp2": tp2,
        "tp1_break_confirmed": False,
        "tp1_reject_count": 0,
        "tp1_runner_ok": False,
        "entry_ts": time.time(),
    }

    _hold_start = {"LONG": 0.0, "SHORT": 0.0}
    _exit_hold_start = {"LONG": 0.0, "SHORT": 0.0}
    _last_touch = ""

    log.info(
        f"Pressure yapı aktif ({direction}): inv={inv:.2f} trigger={trigger:.2f} "
        f"destek={active_support:.2f} direnç={active_resist:.2f} "
        f"| yapısal çıkış={struct_exit:.2f}"
    )
    state.breakout_view = get_status_snapshot(state.price or state.mark_price)
    try:
        from engine.range_trade import get_range_snapshot

        state.range_view = get_range_snapshot(state.price or state.mark_price)
    except Exception:
        pass


_last_struct_exit_ts: float = 0.0


def on_position_closed() -> None:
    """Pozisyon kapandı — pozisyon seviyelerini hemen sıfırla, swing'e dön."""
    global _exit_hold_start, _levels, _struct_closing

    state.position_breakout = {}
    _struct_closing = False
    _exit_hold_start = {"LONG": 0.0, "SHORT": 0.0}
    _levels.pop("entry_break_level", None)
    _levels.pop("flipped", None)
    _levels.pop("active_support", None)
    _levels.pop("active_resistance", None)
    _levels.pop("trade_resistance", None)
    _levels.pop("trade_support", None)
    _clear_latched_major()
    _clear_outside_break()

    if not state.in_position:
        refresh_levels(force=True)
        log.info("Pozisyon kapandı — seviyeler swing 15m ile sıfırlandı")


def _feeds_ok() -> tuple[bool, str]:
    now = time.time()
    if not trade_is_fresh(max_age_sec=5):
        age = int(now - state.trade_last_update) if state.trade_last_update else -1
        return False, f"aggTrade eski ({age}s)"
    if state.kline_last_update <= 0 or (now - state.kline_last_update) > 120:
        age = int(now - state.kline_last_update) if state.kline_last_update else -1
        return False, f"kline eski ({age}s)"
    return True, ""


def _feeds_ok_structural() -> tuple[bool, str]:
    """Yapısal kırılım: bookTicker fiyatı yeterli (aggTrade şartı yok)."""
    px = state.mark_price or state.price or state.bid or state.ask
    if px and px > 0:
        return True, ""
    return False, "fiyat yok"


def make_break_trade_details(
    direction: str,
    entry_price: float,
    break_level: float,
    support: float,
    resistance: float,
) -> tuple[str, dict]:
    """Destek/direnç kırılımı — yakın TP1 + extension TP2."""
    from engine.structure_levels import calc_break_levels

    price = float(entry_price)
    if price <= 0:
        return "FLAT", {"reason": "fiyat yok"}

    entry, sl, tp1, tp2 = calc_break_levels(
        direction, price, break_level, support, resistance, state
    )

    sl_dist = abs(entry - sl)
    tp1_dist = abs(tp1 - entry)
    rr = tp1_dist / sl_dist if sl_dist > 0 else 1.0
    rr2 = abs(tp2 - entry) / sl_dist if sl_dist > 0 else 0
    min_rr = float(getattr(cfg, "BREAK_TP1_MIN_RR", 1.2))
    if sl_dist <= 0 or rr < min_rr:
        return "FLAT", {
            "reason": f"Kırılım R:R={rr:.2f} yetersiz (min {min_rr})",
        }

    return direction, {
        "direction": direction,
        "price": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "rr": round(rr, 2),
        "rr_tp2": round(rr2, 2),
        "break_mode": True,
        "break_level": break_level,
        "range_support": support,
        "range_resistance": resistance,
    }


def make_pressure_trade_details(
    direction: str,
    entry_price: float,
    trigger_level: float,
    invalidation_level: float,
    support: float,
    resistance: float,
) -> tuple[str, dict]:
    """Hibrit band içi baskı girişi — invalidasyon tabanlı erken giriş planı."""
    from engine.structure_levels import calc_structure_levels

    price = float(entry_price)
    trigger = float(trigger_level)
    inv = float(invalidation_level)
    if price <= 0 or trigger <= 0 or inv <= 0:
        return "FLAT", {"reason": "pressure seviyesi yok"}

    if direction == "LONG":
        if inv >= price:
            return "FLAT", {"reason": "pressure long invalidation yukarida"}
        tp1_target = (
            _next_major_resistance_above(
                trigger,
                float(resistance or 0),
                float(state.breakout_view.get("major_resistance", 0) if isinstance(state.breakout_view, dict) else 0),
                float(state.breakout_view.get("deep_major_resistance", 0) if isinstance(state.breakout_view, dict) else 0),
            )
            or trigger
        )
    else:
        if inv <= price:
            return "FLAT", {"reason": "pressure short invalidation asagida"}
        tp1_target = (
            _next_major_support_below(
                trigger,
                float(support or 0),
                float(state.breakout_view.get("major_support", 0) if isinstance(state.breakout_view, dict) else 0),
                float(state.breakout_view.get("deep_major_support", 0) if isinstance(state.breakout_view, dict) else 0),
            )
            or trigger
        )

    entry, sl, tp1, tp2 = calc_structure_levels(direction, price, inv, tp1_target, state)
    sl_dist = abs(entry - sl)
    tp1_dist = abs(tp1 - entry)
    rr = tp1_dist / sl_dist if sl_dist > 0 else 0.0
    rr2 = abs(tp2 - entry) / sl_dist if sl_dist > 0 else 0.0
    min_rr = float(cfg.MIN_RR)
    if sl_dist <= 0 or rr < min_rr:
        return "FLAT", {
            "reason": f"Pressure R:R={rr:.2f} yetersiz (min {min_rr})",
        }

    return direction, {
        "direction": direction,
        "price": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "rr": round(rr, 2),
        "rr_tp2": round(rr2, 2),
        "pressure_mode": True,
        "pressure_trigger_level": trigger,
        "pressure_invalidation": inv,
        "break_level": inv,
        "range_support": support,
        "range_resistance": resistance,
    }


def _flow_ok(direction: str) -> bool:
    from engine.structure_thresholds import flow_ok

    ok, _msg = flow_ok(direction)
    return ok


def _oi_ok(direction: str) -> bool:
    from engine.structure_thresholds import oi_entry_ok

    ok, _msg = oi_entry_ok(direction)
    return ok


def _htf_allows(direction: str) -> tuple[bool, str, float]:
    s1h = state.structure_1h or "UNCLEAR"
    min_rr = float(cfg.MIN_RR)
    if s1h == "UP" and direction == "SHORT":
        return False, "1h UP — SHORT kırılım kapalı", min_rr
    if s1h == "DOWN" and direction == "LONG":
        return False, "1h DOWN — LONG kırılım kapalı", min_rr
    if s1h == "UNCLEAR":
        return True, "1h UNCLEAR — MIN_RR yükseltildi", float(
            getattr(cfg, "BREAK_MIN_RR_UNCLEAR", 2.0)
        )
    return True, "", min_rr


def _pressure_htf_allows(direction: str) -> tuple[bool, str, float]:
    s1h = state.structure_1h or "UNCLEAR"
    min_rr = float(cfg.MIN_RR)
    if s1h == "UNCLEAR":
        min_rr = max(min_rr, float(getattr(cfg, "BREAK_MIN_RR_UNCLEAR", 2.0)))

    tv = dict(state.trend_view or {})
    bias = str(tv.get("bias") or "").upper()
    soft_ok = _flow_ok(direction) or (
        direction == "LONG"
        and float(state.cvd_5m or 0) >= 0
        and float(state.taker_ratio or 0) >= 0.50
    ) or (
        direction == "SHORT"
        and float(state.cvd_5m or 0) <= 0
        and float(state.taker_ratio or 0) <= 0.50
    )

    if s1h == "UP" and direction == "SHORT":
        if soft_ok and bias in ("DOWN", "RANGE"):
            return True, "1h UP ama yerel short baskı", max(min_rr, float(cfg.MIN_RR) + 0.2)
        return False, "1h UP — SHORT pressure kapalı", max(min_rr, float(cfg.MIN_RR) + 0.2)
    if s1h == "DOWN" and direction == "LONG":
        if soft_ok and bias in ("UP", "RANGE"):
            return True, "1h DOWN ama yerel long baskı", max(min_rr, float(cfg.MIN_RR) + 0.2)
        return False, "1h DOWN — LONG pressure kapalı", max(min_rr, float(cfg.MIN_RR) + 0.2)
    return True, "", min_rr


def _range_too_tight(price: float) -> bool:
    from engine.structure_thresholds import channel_band_bps, min_channel_bps

    if price <= 0:
        return False
    width_bps = channel_band_bps(price)
    if width_bps <= 0:
        return False
    return width_bps < min_channel_bps(price)


def _update_touch(price: float) -> None:
    global _last_touch
    r, s = get_swing_channel()
    from engine.structure_thresholds import proximity_bps

    prox = proximity_bps(price) / 10000.0

    near_high = r > 0 and _proximity_ratio(price, r) <= prox
    near_low = s > 0 and _proximity_ratio(price, s) <= prox

    if near_high and near_low and _range_too_tight(price):
        return
    if near_high and (not near_low or _proximity_ratio(price, r) <= _proximity_ratio(price, s)):
        _last_touch = "high"
        _record_level_event(r, "resistance", "approach")
    elif near_low:
        _last_touch = "low"
        _record_level_event(s, "support", "approach")


def _maybe_record_failed_break(price: float) -> None:
    """Hold başladı ama kırılım tamamlanmadan geri döndü → seviye güçlenir."""
    global _hold_start
    r = _levels.get("resistance", 0.0)
    s = _levels.get("support", 0.0)

    if _hold_start["LONG"] > 0 and r > 0 and price < r * 0.9998:
        _record_level_event(r, "resistance", "failed")
        _hold_start["LONG"] = 0.0
    if _hold_start["SHORT"] > 0 and s > 0 and price > s * 1.0002:
        _record_level_event(s, "support", "failed")
        _hold_start["SHORT"] = 0.0


def _hold_confirmed(
    price: float, direction: str, level: float, hold_sec: float | None = None
) -> bool:
    global _hold_start
    thresh = _break_threshold(level, direction)
    now = time.time()
    if hold_sec is None:
        hold_sec = float(getattr(cfg, "BREAK_HOLD_SEC", 2.0))

    if direction == "LONG":
        crossed = price >= thresh
    else:
        crossed = price <= thresh

    if crossed:
        if _hold_start[direction] <= 0:
            _hold_start[direction] = now
        elif now - _hold_start[direction] >= hold_sec:
            return True
    else:
        if _hold_start[direction] > 0:
            _maybe_record_failed_break(price)
        _hold_start[direction] = 0.0
    return False


def _exit_hold_confirmed(price: float, side: str, level: float) -> bool:
    """Pozisyon yapısal çıkış: LONG için destek kırılımı (SHORT yön hold)."""
    global _exit_hold_start
    exit_dir = "SHORT" if side == "LONG" else "LONG"
    thresh = _break_threshold(level, exit_dir)
    now = time.time()
    hold_sec = float(
        getattr(cfg, "BREAK_STRUCTURAL_HOLD_SEC", None)
        or getattr(cfg, "BREAK_HOLD_SEC", 2.0)
    )

    if exit_dir == "SHORT":
        crossed = price <= thresh
    else:
        crossed = price >= thresh

    if crossed:
        if _exit_hold_start[side] <= 0:
            _exit_hold_start[side] = now
        elif now - _exit_hold_start[side] >= hold_sec:
            return True
    else:
        _exit_hold_start[side] = 0.0
    return False


def _channel_width_bps(price: float, support: float, resistance: float) -> float:
    if price <= 0 or resistance <= support:
        return 0.0
    return (resistance - support) / price * 10000.0


def _structural_exit_level(
    side: str, lv: dict, price: float
) -> tuple[float, str]:
    """
    Yapısal çıkış seviyesi — yalnızca ana invalidasyon (kırılım seviyesi).
    Ara raf / wide_pullback çıkışları kaldırıldı; risk SL ile yönetilir.
    """
    side = (side or "").upper()
    pb = state.position_breakout or {}
    if side == "LONG":
        # LONG invalidation mutlaka fiyatin ALTINDA bir destek seviyesi olmali.
        # Restart/restore akışında swing high kaynaklı (fiyat üstü) hatalı break_level
        # gelebileceği için sadece fiyat altındaki destek adaylarını kullan.
        support_candidates = [
            float(pb.get("structural_support") or 0),
            float(pb.get("active_support") or 0),
            float(lv.get("active_support") or 0),
            float(lv.get("support") or 0),
            float(pb.get("break_level") or 0),
            float(lv.get("invalidation_support") or 0),
        ]
        below = [s for s in support_candidates if s > 0 and s < price * 0.9995]
        inv = max(below) if below else 0.0
        if inv <= 0:
            inv = float(lv.get("support") or 0)
        if inv <= 0 or inv >= price:
            return 0.0, ""
        return inv, "invalidation"

    if side == "SHORT":
        # SHORT invalidation mutlaka fiyatin USTUNDE bir direnç seviyesi olmali.
        resistance_candidates = [
            float(pb.get("structural_resistance") or 0),
            float(pb.get("active_resistance") or 0),
            float(lv.get("active_resistance") or 0),
            float(lv.get("resistance") or 0),
            float(pb.get("break_level") or 0),
        ]
        above = [r for r in resistance_candidates if r > price * 1.0005]
        inv = min(above) if above else 0.0
        if inv <= 0 or inv <= price:
            return 0.0, ""
        return inv, "invalidation"

    return 0.0, ""


def update_position_context(price: float) -> None:
    """TP1 kırılım vs retest — position_manager her tick."""
    if not state.in_position or not state.position_breakout:
        return

    pb = state.position_breakout
    tp1 = float(pb.get("tp1", 0) or state.pos_tp1)
    if tp1 <= 0 or price <= 0:
        return

    side = state.pos_side
    from engine.structure_thresholds import proximity_bps

    prox = proximity_bps(price) / 10000.0

    if side == "LONG":
        near_tp1 = _proximity_ratio(price, tp1) <= prox * 1.5
        if not near_tp1:
            pb["tp1_reject_active"] = False
        elif not pb.get("tp1_break_confirmed"):
            if _hold_confirmed(price, "LONG", tp1):
                pb["tp1_break_confirmed"] = True
                pb["tp1_runner_ok"] = True
                pb["tp1_reject_active"] = False
                log.info(f"TP1 kırılım onaylı ({tp1:.2f}) — runner TP2 mümkün")
            elif price < tp1 * 0.9995:
                if not pb.get("tp1_reject_active"):
                    pb["tp1_reject_active"] = True
                    pb["tp1_reject_count"] = pb.get("tp1_reject_count", 0) + 1
                    log.info(
                        f"TP1 retest reddedildi ({tp1:.2f}) "
                        f"count={pb['tp1_reject_count']} — runner bekle"
                    )
            else:
                pb["tp1_reject_active"] = False
    else:
        near_tp1 = _proximity_ratio(price, tp1) <= prox * 1.5
        if not near_tp1:
            pb["tp1_reject_active"] = False
        elif not pb.get("tp1_break_confirmed"):
            if _hold_confirmed(price, "SHORT", tp1):
                pb["tp1_break_confirmed"] = True
                pb["tp1_runner_ok"] = True
                pb["tp1_reject_active"] = False
                log.info(f"TP1 kırılım onaylı ({tp1:.2f}) — runner TP2 mümkün")
            elif price > tp1 * 1.0005:
                if not pb.get("tp1_reject_active"):
                    pb["tp1_reject_active"] = True
                    pb["tp1_reject_count"] = pb.get("tp1_reject_count", 0) + 1
                    log.info(
                        f"TP1 retest reddedildi ({tp1:.2f}) "
                        f"count={pb['tp1_reject_count']} — runner bekle"
                    )
            else:
                pb["tp1_reject_active"] = False

    state.position_breakout = pb
    state.breakout_view = get_status_snapshot(price)


def check_structural_exit(price: float) -> Optional[str]:
    """
    Yapısal çıkış — yalnızca bookTicker (entry_timer) tarafından çağrılmalı.
    """
    global _last_struct_exit_ts, _struct_closing

    if not state.in_position or price <= 0 or _struct_closing:
        return None
    if time.time() - _last_struct_exit_ts < 5.0:
        return None

    pb = state.position_breakout or {}
    side = state.pos_side

    lv = get_active_levels(price)
    level, kind = _structural_exit_level(side, lv, price)
    if level > 0 and _exit_hold_confirmed(price, side, level):
        _last_struct_exit_ts = time.time()
        tag = kind or "invalidation"
        if side == "LONG":
            return f"struct_break_support@{level:.2f} ({tag})"
        return f"struct_break_resistance@{level:.2f} ({tag})"
    return None


def mark_structural_close_started() -> None:
    global _struct_closing
    _struct_closing = True


async def handle_tp1_retest(executor) -> Optional[str]:
    """
    TP1 retest reddi + zayıf flow → BE veya çıkış (1m position_manager).
    Yapısal çıkış bookTicker'da öncelikli — _struct_closing ise atla.
    """
    if _struct_closing or not state.in_position:
        return None

    pb = state.position_breakout or {}
    if not pb or state.pos_tp1_hit:
        return None
    if pb.get("tp1_runner_ok"):
        return None

    rejects = int(pb.get("tp1_reject_count", 0))
    min_rej = int(getattr(cfg, "TP1_RETEST_MAX_REJECTS", 2))
    exit_after = int(getattr(cfg, "TP1_RETEST_EXIT_AFTER", 3))
    if rejects < min_rej:
        return None

    side = state.pos_side
    cvd = state.cvd_5m
    from engine.structure_thresholds import cvd_weak_exit_min

    cvd_min = cvd_weak_exit_min()
    weak_flow = (
        (side == "LONG" and cvd < cvd_min)
        or (side == "SHORT" and cvd > -cvd_min)
    )

    if rejects >= exit_after and weak_flow:
        log.info(
            f"TP1 {rejects}x retest + zayıf flow (cvd={cvd:+.0f}) → pozisyon kapatılıyor "
            f"(1m posmgr; yapısal çıkış bookTicker öncelikli)"
        )
        await executor.close_position("tp1_retest_weak_flow")
        return "TP1_RETEST_EXIT"

    if not state.pos_be_active:
        log.info(
            f"TP1 {rejects}x retest, kırılım yok"
            + (" + zayıf flow → BE" if weak_flow else " → BE önlem")
        )
        await executor.move_to_breakeven()
        return "TP1_RETEST_BE"

    return None


def get_status_snapshot(price: float = 0.0) -> dict:
    px = price or state.mark_price or state.price or 0.0
    lv = get_active_levels(px)
    r = float(lv.get("tactical_cap_resistance") or lv.get("resistance", 0))
    s = float(lv.get("tactical_floor_support") or lv.get("support", 0))
    sw_r = float(lv.get("swing_resistance", r))
    sw_s = float(lv.get("swing_support", s))
    if r > s > 0:
        band_sw_r, band_sw_s = _swings_within_band(s, r, px=px)
        sw_r = band_sw_r or sw_r
        sw_s = band_sw_s or sw_s
    trigger_r, trigger_s = _breakout_trigger_levels(px, lv)
    channel_r, channel_s, channel_src = _inner_channel_levels(px, s, r)
    break_lvl = float(lv.get("break_level", 0))
    pb = state.position_breakout or {}
    inv_s = float(lv.get("invalidation_support") or break_lvl or 0)
    act_s = float(lv.get("active_support") or s)
    active_major_r = float(lv.get("active_major_resistance") or 0)
    active_major_s = float(lv.get("active_major_support") or 0)
    sticky_struct_r = float(lv.get("structural_major_resistance") or 0)
    sticky_struct_s = float(lv.get("structural_major_support") or 0)
    struct_r = active_major_r or sticky_struct_r
    struct_s = active_major_s or sticky_struct_s

    active_dir = ""
    active_level = 0.0
    status_code = "BAND_DISI_BEKLE"
    status_detail = ""

    if state.in_position and pb:
        entry_mode = str(pb.get("entry_mode", "") or "").lower()
        if entry_mode == "range":
            status_code = "POZISYON_RANGE"
        elif entry_mode == "pressure":
            status_code = "POZISYON_PRESSURE"
        else:
            status_code = "POZISYON_BREAKOUT"
        active_dir = pb.get("direction", state.pos_side)
        if active_dir == "LONG":
            active_level = act_s if act_s > 0 else float(pb.get("active_support") or s or 0)
        else:
            active_level = r if r > 0 else float(pb.get("structural_resistance") or 0)
    elif r > 0 and s > 0 and px > 0:
        out_dir, out_lvl = _outside_break_candidate(px, trigger_s, trigger_r)
        if not out_dir:
            out_dir, out_lvl = _latched_outside_break_candidate(px, s, r)
        cont_dir = cont_msg = ""
        cont_lvl = 0.0
        pressure_dir = pressure_msg = ""
        pressure_lvl = pressure_inv = 0.0
        if not out_dir and getattr(cfg, "ENTRY_MODE", "break").lower() == "hybrid":
            cont_dir, cont_lvl, cont_msg = hybrid_continuation_candidate(px, s, r)
            if cont_dir:
                out_dir, out_lvl = cont_dir, cont_lvl
            elif is_inside_band(px, s, r):
                pressure_dir, pressure_lvl, pressure_inv, pressure_msg = hybrid_pressure_candidate(
                    px, s, r, lv
                )
        if out_dir:
            _remember_outside_break(out_dir, out_lvl)
            if out_dir == "LONG":
                struct_r = _latched_major_level("LONG", out_lvl, struct_r, px)
            else:
                struct_s = _latched_major_level("SHORT", out_lvl, struct_s, px)
            active_dir, active_level = out_dir, out_lvl
            ok_nar, nar_reason = _narrative_trade_entry_allowed(
                out_dir, out_lvl, px
            )
            hint = _narrative_display_hint(out_dir, out_lvl)
            if not ok_nar:
                status_code = (
                    "TRIGGER_LONG" if out_dir == "LONG" else "TRIGGER_SHORT"
                )
                status_detail = cont_msg or hint or "retest bekleniyor"
            else:
                hold_ts = _hold_start.get(out_dir, 0.0)
                hold_sec = float(getattr(cfg, "BREAK_HOLD_SEC", 2.0))
                if hold_ts > 0 and (time.time() - hold_ts) >= hold_sec:
                    status_code = (
                        "BREAKOUT_LONG_HAZIR"
                        if out_dir == "LONG"
                        else "BREAKOUT_SHORT_HAZIR"
                    )
                elif hold_ts > 0:
                    status_code = (
                        "BREAKOUT_LONG_TUTUYOR"
                        if out_dir == "LONG"
                        else "BREAKOUT_SHORT_TUTUYOR"
                    )
                else:
                    status_code = (
                        "TRIGGER_LONG" if out_dir == "LONG" else "TRIGGER_SHORT"
                    )
                    status_detail = cont_msg
            if not ok_nar and nar_reason:
                state.no_entry_reason = nar_reason
        elif pressure_dir:
            _clear_latched_major()
            _clear_outside_break()
            active_dir, active_level = pressure_dir, pressure_lvl
            status_code = "PRESSURE_LONG" if pressure_dir == "LONG" else "PRESSURE_SHORT"
            status_detail = pressure_msg or f"inv {pressure_inv:.2f}"
        elif _range_too_tight(px):
            _clear_latched_major()
            _clear_outside_break()
            status_code = "KANAL_DAR"
        else:
            _clear_latched_major()
            _clear_outside_break()
            if _last_touch == "high":
                active_dir, active_level = "LONG", trigger_r if trigger_r > 0 else r
                status_code = (
                    "BREAKOUT_LONG_BEKLIYOR"
                    if px < active_level
                    else "TRIGGER_LONG"
                )
            elif _last_touch == "low":
                active_dir, active_level = "SHORT", trigger_s if trigger_s > 0 else s
                status_code = (
                    "BREAKOUT_SHORT_BEKLIYOR"
                    if px > active_level
                    else "TRIGGER_SHORT"
                )
            elif is_inside_band(px, s, r):
                ck_q = str(lv.get("cookie_quality") or "")
                dh, dl = _proximity_ratio(px, r), _proximity_ratio(px, s)
                from engine.structure_thresholds import proximity_bps

                if ck_q and ck_q not in ("ok",):
                    status_code = "KANAL_KALITESI_DUSUK"
                    status_detail = ck_q.upper()
                elif min(dh, dl) * 10000 < proximity_bps(px):
                    active_dir = "LONG" if dh < dl else "SHORT"
                    active_level = r if dh < dl else s
                    status_code = (
                        "TAKTIK_SHORT_ADAY" if dh < dl else "TAKTIK_LONG_ADAY"
                    )
                    status_detail = (
                        f"{'cap' if dh < dl else 'floor'} {active_level:.2f} yaklasiyor"
                    )
                else:
                    status_code = "BAND_ICI_BEKLE"
            else:
                status_code = "BAND_DISI_BEKLE"

    r_tests = _level_tests.get(_level_key(r), {}) if r > 0 else {}
    s_tests = _level_tests.get(_level_key(s), {}) if s > 0 else {}

    rally_ceil = _rally_ceiling_long(float(act_s or inv_s or 0))
    major_r = (
        sticky_struct_r
        if sticky_struct_r > 0
        else (
            active_major_r
            if active_major_r > 0
            else _nearest_major_resistance(
                trigger_r or sw_r,
                r,
                float(lv.get("cookie_resistance", 0) or 0),
                float(lv.get("range_resistance", 0) or 0),
                rally_ceil,
            )
        )
    )
    major_s = (
        sticky_struct_s
        if sticky_struct_s > 0
        else (
            active_major_s
            if active_major_s > 0
            else _nearest_major_support(
                trigger_s or sw_s or s,
                s,
                float(lv.get("range_support", 0) or 0),
                float(lv.get("deep_support", 0) or 0),
                inv_s,
            )
        )
    )
    deep_major_r = float(
        lv.get("deep_structural_major_resistance")
        or lv.get("deep_resistance")
        or 0
    )
    deep_major_s = float(
        lv.get("deep_structural_major_support")
        or lv.get("deep_support")
        or 0
    )
    exit_lvl, exit_kind = (
        _structural_exit_level(state.pos_side or "", lv, px)
        if state.in_position and px > 0
        else (0.0, "")
    )
    lb = _level_lookback_hours()

    return {
        "status": _status_line(status_code, status_detail),
        "status_code": status_code,
        "status_detail": status_detail,
        "active_direction": active_dir,
        "active_level": active_level,
        "main_resistance": r,
        "main_support": s,
        "channel_resistance": channel_r,
        "channel_support": channel_s,
        "channel_source": channel_src,
        "resistance": r,
        "support": s,
        "active_support": act_s,
        "invalidation_support": inv_s,
        "tactical_cap_resistance": r,
        "tactical_floor_support": s,
        "trigger_resistance": trigger_r,
        "major_resistance": major_r,
        "deep_major_resistance": deep_major_r,
        "trigger_support": trigger_s,
        "major_support": major_s,
        "deep_major_support": deep_major_s,
        "active_major_resistance": active_major_r,
        "active_major_support": active_major_s,
        "structural_major_resistance": sticky_struct_r or major_r,
        "structural_major_support": sticky_struct_s or major_s,
        "deep_structural_major_resistance": deep_major_r or sticky_struct_r,
        "deep_structural_major_support": deep_major_s or sticky_struct_s,
        "structural_exit_level": exit_lvl,
        "structural_exit_kind": exit_kind,
        "level_lookback": lb,
        "rally_ceiling": rally_ceil,
        "tp1": float(state.pos_tp1 or 0),
        "swing_resistance": sw_r,
        "swing_support": sw_s,
        "trade_resistance": float(lv.get("trade_resistance", 0)),
        "trade_support": float(lv.get("trade_support", 0)),
        "break_level": break_lvl or float(pb.get("break_level", 0)),
        "structural_support": float(pb.get("structural_support") or s) if pb else s,
        "structural_resistance": float(pb.get("structural_resistance") or r)
        if pb
        else r,
        "band_width_bps": round((r - s) / px * 10000.0, 1)
        if r > s > 0 and px > 0
        else (round((sw_r - sw_s) / px * 10000.0, 1) if sw_r > sw_s > 0 and px > 0 else 0),
        "flipped": bool(lv.get("flipped")),
        "last_touch": _last_touch,
        "price": px,
        "distance": round(abs(px - active_level), 2) if active_level else 0,
        "cvd_5m": state.cvd_5m,
        "taker": state.taker_ratio,
        "flow_long": _flow_ok("LONG"),
        "flow_short": _flow_ok("SHORT"),
        "oi_rising": state.oi_rising,
        "feeds_ok": _feeds_ok()[0],
        "feeds_msg": _feeds_ok()[1],
        "resistance_tests": r_tests.get("tests", 0),
        "resistance_failed": r_tests.get("failed", 0),
        "support_tests": s_tests.get("tests", 0),
        "support_failed": s_tests.get("failed", 0),
        "tp1_break_confirmed": pb.get("tp1_break_confirmed", False),
        "tp1_reject_count": pb.get("tp1_reject_count", 0),
        "tp1_runner_ok": pb.get("tp1_runner_ok", False),
        "position_break": pb.get("break_level", 0),
        "narrative_hint": _narrative_display_hint(active_dir, active_level)
        if active_dir and active_level
        else "",
        "cookie_support": 0.0,
        "cookie_resistance": float(lv.get("cookie_resistance", 0) or 0),
        "cookie_quality": str(lv.get("cookie_quality", "") or ""),
        "support_confidence": float(lv.get("support_confidence", 0) or 0),
        "resistance_confidence": float(lv.get("resistance_confidence", 0) or 0),
        "min_edge_confidence": float(lv.get("min_edge_confidence", 0) or 0),
        "structural_major_quality": str(
            lv.get("structural_major_quality", "") or ""
        ),
        "structural_major_layer": str(lv.get("structural_major_layer", "") or ""),
        "structural_resistance_source": str(
            lv.get("structural_resistance_source", "") or ""
        ),
        "structural_support_source": str(
            lv.get("structural_support_source", "") or ""
        ),
        "structural_resistance_reason": str(
            lv.get("structural_resistance_reason", "") or ""
        ),
        "structural_support_reason": str(
            lv.get("structural_support_reason", "") or ""
        ),
        "micro_support": float(lv.get("micro_support", 0) or 0),
        "range_support": float(lv.get("range_support", 0) or 0),
        "deep_support": float(lv.get("deep_support", 0) or 0),
        "micro_resistance": float(lv.get("micro_resistance", 0) or 0),
        "range_resistance": float(lv.get("range_resistance", 0) or 0),
        "deep_resistance": float(lv.get("deep_resistance", 0) or 0),
        "resistance_source": str(lv.get("resistance_source", "") or ""),
        "resistance_reason": str(lv.get("resistance_reason", "") or ""),
    }


def check_breakout(price: float) -> Optional[dict]:
    global _last_status_log, _last_touch

    if price <= 0 or not cfg.AUTO_TRADE_ENABLED:
        return None

    from execution.executor import is_position_opening

    if is_position_opening():
        return None

    mode = getattr(cfg, "ENTRY_MODE", "break").lower()
    if mode not in ("break", "realtime", "hybrid"):
        return None

    lv = get_active_levels(price)
    r = float(lv.get("resistance", 0))
    s = float(lv.get("support", 0))
    sw_r, sw_s = get_swing_channel()
    trigger_r, trigger_s = _breakout_trigger_levels(price, lv)
    if (r <= 0 and s <= 0) or (trigger_r <= 0 and trigger_s <= 0):
        state.breakout_view = get_status_snapshot(price)
        return None

    _narrative_update_tick(price, r, s)
    outside_dir, outside_level = _outside_break_candidate(price, trigger_s, trigger_r)
    if not outside_dir:
        outside_dir, outside_level = _latched_outside_break_candidate(price, s, r)
    continuation = False
    pressure = False
    pressure_inv = 0.0
    continuation_msg = ""
    if (
        not outside_dir
        and mode == "hybrid"
        and is_inside_band(price, s, r)
    ):
        cont_dir, cont_level, cont_msg = hybrid_continuation_candidate(price, s, r)
        if cont_dir:
            outside_dir, outside_level = cont_dir, cont_level
            continuation = True
            continuation_msg = cont_msg
    if (
        not outside_dir
        and mode == "hybrid"
        and is_inside_band(price, s, r)
    ):
        pressure_dir, pressure_level, pressure_inv, pressure_msg = hybrid_pressure_candidate(
            price, s, r, lv
        )
        if pressure_dir:
            pressure = True
            outside_dir = pressure_dir
            outside_level = pressure_level
            continuation_msg = pressure_msg
    if not outside_dir and not pressure:
        _clear_latched_major()
        _clear_outside_break()
    elif not pressure:
        _remember_outside_break(outside_dir, outside_level)

    if trigger_r > 0 and trigger_s > 0 and _range_too_tight(price) and not outside_dir and not pressure:
        w_bps = (r - s) / price * 10000.0
        from engine.structure_thresholds import min_channel_bps

        min_bps = min_channel_bps(price)
        state.no_entry_reason = _status_line(
            "KANAL_DAR", f"{w_bps:.0f}bps < {min_bps:.0f}"
        )
        state.breakout_view = get_status_snapshot(price)
        return None

    inside = is_inside_band(price, s, r)

    # Hibrit: band içi → sadece range; kırılım burada çalışmaz
    if mode == "hybrid" and inside and not outside_dir and not pressure:
        state.breakout_view = get_status_snapshot(price)
        return None

    direction, level = "", 0.0
    break_path = ""

    structural = bool(outside_dir) and not continuation and not pressure
    mandatory = structural and getattr(cfg, "BREAK_STRUCTURAL_MANDATORY", True)

    if outside_dir and not pressure:
        if mode == "hybrid":
            ok_room, room_msg = _hybrid_breakout_headroom_ok(
                price, outside_dir, outside_level, lv
            )
            if not ok_room:
                state.no_entry_reason = _status_line("HEADROOM_YETERSIZ", room_msg)
                state.breakout_view = get_status_snapshot(price)
                return None
        if not mandatory:
            from engine.structure_thresholds import outside_max_bps

            max_bps = outside_max_bps(outside_level, price, outside_dir)
            dist = _level_distance_bps(price, outside_level)
            if dist > max_bps:
                state.no_entry_reason = _status_line(
                    "BREAKOUT_GEC",
                    f"{dist:.0f}bps > {max_bps:.0f}",
                )
                _maybe_record_failed_break(price)
                state.breakout_view = get_status_snapshot(price)
                return None
        _last_touch = "low" if outside_dir == "SHORT" else "high"
        direction, level = outside_dir, outside_level
        break_path = "retest" if continuation else "yapısal"
    elif pressure:
        ok_room, room_msg = _hybrid_breakout_headroom_ok(
            price, outside_dir, outside_level, lv
        )
        if not ok_room:
            state.no_entry_reason = _status_line("HEADROOM_YETERSIZ", room_msg)
            state.breakout_view = get_status_snapshot(price)
            return None
        _update_touch(price)
        direction, level = outside_dir, outside_level
        break_path = "pressure"
    else:
        from engine.structure_thresholds import proximity_bps

        prox = proximity_bps(price) / 10000.0
        near = (r > 0 and _proximity_ratio(price, r) <= prox * 2) or (
            s > 0 and _proximity_ratio(price, s) <= prox * 2
        )
        if not near:
            _maybe_record_failed_break(price)
            state.breakout_view = get_status_snapshot(price)
            return None

        _update_touch(price)
        _maybe_record_failed_break(price)
        if _last_touch == "high" and r > 0:
            direction, level = "LONG", r
        elif _last_touch == "low" and s > 0:
            direction, level = "SHORT", s
        else:
            state.breakout_view = get_status_snapshot(price)
            return None
        break_path = "yakın"

    ok_nar, nar_msg = _narrative_trade_entry_allowed(direction, level, price)
    if not ok_nar:
        state.no_entry_reason = _status_line("NARRATIVE_BLOK", nar_msg)
        _hold_start[direction] = 0.0
        state.breakout_view = get_status_snapshot(price)
        return None

    state.breakout_view = get_status_snapshot(price)

    hold_sec = (
        float(getattr(cfg, "BREAK_STRUCTURAL_HOLD_SEC", 1.0))
        if mandatory
        else None
    )
    if not _hold_confirmed(price, direction, level, hold_sec=hold_sec):
        if mandatory:
            state.no_entry_reason = _status_line(
                "BREAKOUT_TUTUNMADI",
                f"{direction} {getattr(cfg, 'BREAK_STRUCTURAL_HOLD_SEC', 1):.0f}s",
            )
        return None

    if mandatory:
        ok_feed, feed_msg = _feeds_ok_structural()
        if not ok_feed:
            state.no_entry_reason = _status_line("FEED_BLOK", feed_msg)
            return None

        d, details = make_break_trade_details(direction, price, level, s, r)
        if d == "FLAT":
            state.no_entry_reason = _status_line(
                "BREAKOUT_PLAN_YOK", details.get("reason", "kirilim plani yok")
            )
            return None

        failed = 0
        details["entry_reason"] = (
            f"KIRILIM ZORUNLU {direction} — destek/direnç kırıldı "
            f"seviye={level:.2f} @ {price:.2f} "
            f"(CVD/taker/HTF/OI filtre yok)"
        )
    else:
        ok_feed, feed_msg = _feeds_ok()
        if not ok_feed:
            state.no_entry_reason = _status_line("FEED_BLOK", feed_msg)
            return None

        htf_ok, htf_msg, min_rr = (
            _pressure_htf_allows(direction) if pressure else _htf_allows(direction)
        )
        if not htf_ok:
            state.no_entry_reason = _status_line("HTF_BLOK", htf_msg)
            _hold_start[direction] = 0.0
            return None

        from engine.structure_thresholds import flow_ok as flow_gate

        ok_flow, flow_msg = flow_gate(direction)
        if not ok_flow:
            state.no_entry_reason = _status_line(
                "FLOW_BLOK",
                flow_msg
                or f"cvd={state.cvd_5m:+.0f} taker={state.taker_ratio:.0%}",
            )
            return None

        if not _oi_ok(direction):
            state.no_entry_reason = _status_line("OI_BLOK", "son 30s yon onayi yok")
            return None

        failed = _level_tests.get(_level_key(level), {}).get("failed", 0)
        if not pressure and failed >= int(getattr(cfg, "BREAK_MAX_FAILED_TESTS", 8)):
            state.no_entry_reason = _status_line(
                "SEVIYE_YORGUN",
                f"{level:.2f} fail={failed}",
            )
            return None

        if pressure:
            d, details = make_pressure_trade_details(
                direction,
                price,
                level,
                pressure_inv,
                s,
                r,
            )
        elif continuation:
            d, details = make_break_trade_details(direction, price, level, s, r)
        else:
            from engine.signal import make_trade_details

            d, details = make_trade_details(direction, entry_price=price)
        if d == "FLAT":
            state.no_entry_reason = _status_line(
                "BREAKOUT_PLAN_YOK", details.get("reason", "plan yok")
            )
            return None

        if details.get("rr", 0) < min_rr:
            state.no_entry_reason = _status_line(
                "RR_YETERSIZ",
                f"{'pressure' if pressure else 'breakout'} min={min_rr}",
            )
            return None

        if pressure:
            details["entry_reason"] = (
                f"hybrid pressure {direction} tetik={level:.2f} inv={pressure_inv:.2f} "
                f"@ {price:.2f} cvd={state.cvd_5m:+.0f} taker={state.taker_ratio:.0%}"
            )
            if continuation_msg:
                details["entry_reason"] += f" | {continuation_msg}"
        else:
            details["entry_reason"] = (
                f"kırılım ({break_path}) {direction} seviye={level:.2f} @ {price:.2f} "
                f"cvd={state.cvd_5m:+.0f} taker={state.taker_ratio:.0%} "
                f"seviye_test={failed}"
            )
            if continuation and continuation_msg:
                details["entry_reason"] += f" | {continuation_msg}"

    if state.in_position and direction == state.pos_side:
        return None

    _hold_start[direction] = 0.0
    _mark_level_entered(level)
    if not pressure:
        details["break_level"] = level
        details["break_mode"] = True
    details["signal_price"] = price
    state.no_entry_reason = ""

    log.info(
        f"{'PRESSURE ONAY' if pressure else ('KIRILIM ZORUNLU' if mandatory else 'KIRILIM ONAY')}: "
        f"{direction} @ {price:.2f}  "
        f"seviye={level:.2f}  SL={details.get('sl')} TP1={details.get('tp1')}"
    )
    state.signal = direction
    state.signal_reason = details["entry_reason"]
    state.breakout_view = get_status_snapshot(price)
    state.breakout_view["status"] = "GIRIS"
    return details
