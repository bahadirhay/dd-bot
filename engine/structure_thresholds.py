"""
engine/structure_thresholds.py — Yapısal hesap (sabit % ve ATR yok).

Birimler:
- Kanal geometrisi (swing R/S, genişlik, hedef mesafe)
- Mum gürültüsü (median TR — piyasanın nefesi)
- Swing bacakları (median leg — piyasanın adımı)
- İlerleme oranı (0..1 hedefe, >1 hedef ötesi)
- Flow yüzdelik (CVD kendi dağılımında)
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from core.config import cfg
from core.state import state


def global_structure_mode() -> bool:
    return getattr(cfg, "GLOBAL_STRUCTURE_MODE", True)


def _px(price: float = 0) -> float:
    return float(price or state.mark_price or state.price or 0)


def _median(vals: list[float]) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2.0


def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))


def _percentile_rank(value: float, samples: list[float]) -> float:
    """0..1 — değer dağılımda nerede."""
    if not samples:
        return 0.5
    s = sorted(samples)
    below = sum(1 for x in s if x < value)
    return below / len(s)


@dataclass
class ChannelGeometry:
    swing_r: float
    swing_s: float
    width: float
    mid: float
    price: float
    band_pos: float


def channel_geometry(price: float = 0) -> ChannelGeometry:
    px = _px(price)
    sw_r = sw_s = 0.0
    try:
        from engine.breakout import get_swing_channel

        sw_r, sw_s = get_swing_channel()
    except Exception:
        pass
    width = max(0.0, sw_r - sw_s) if sw_r > sw_s else 0.0
    mid = (sw_r + sw_s) / 2.0 if width > 0 else px
    pos = (px - sw_s) / width if width > 0 and px > 0 else 0.5
    return ChannelGeometry(sw_r, sw_s, width, mid, px, pos)


def _bar_true_ranges_bps(price: float, *, use_1m: bool = True) -> list[float]:
    px = _px(price)
    if px <= 0:
        return []
    if use_1m:
        try:
            from dashboard.binance_chart import get_bars_1m

            bars = get_bars_1m(24)
        except Exception:
            bars = []
    else:
        from engine.structure import get_bars_15m

        bars = get_bars_15m(20)
    if len(bars) < 2:
        return []
    out: list[float] = []
    for i in range(1, len(bars)):
        h = float(bars[i]["high"])
        lo = float(bars[i]["low"])
        prev_c = float(bars[i - 1]["close"])
        tr = max(h - lo, abs(h - prev_c), abs(lo - prev_c))
        if tr > 0:
            out.append(tr / px * 10000.0)
    return out


def bar_noise_bps(price: float = 0) -> float:
    """Tipik mum nefesi (median TR) — yüzde değil, ölçülen volatilite."""
    base = float(getattr(cfg, "FS_STRUCT_BREAK_BPS", 8.0))
    if not global_structure_mode():
        return base
    trs = _bar_true_ranges_bps(price, use_1m=True)
    if len(trs) < 3:
        trs = _bar_true_ranges_bps(price, use_1m=False)
    if not trs:
        return base
    return max(base, _median(trs))


def swing_leg_median_bps(price: float = 0) -> float:
    """Swing adımlarının median büyüklüğü — yapının doğal adımı."""
    px = _px(price)
    if px <= 0:
        return bar_noise_bps(price) * 4.0
    from engine.structure_levels import _swing_prices

    legs: list[float] = []
    highs = sorted(_swing_prices(state.swing_highs_15m or []))
    lows = sorted(_swing_prices(state.swing_lows_15m or []))
    for seq in (highs, lows):
        for i in range(1, len(seq)):
            legs.append(abs(seq[i] - seq[i - 1]) / px * 10000.0)
    if not legs:
        return bar_noise_bps(px) * 4.0
    return max(bar_noise_bps(px) * 2.0, _median(legs))


def _next_target_price(level: float, direction: str, price: float) -> float:
    d = (direction or "").upper()
    px = _px(price)
    from engine.structure_levels import _swing_prices

    if d == "LONG":
        above = sorted(
            p for p in _swing_prices(state.swing_highs_15m or []) if p > level * 1.0002
        )
        if above:
            return above[0]
        geo = channel_geometry(px)
        return geo.swing_r if geo.swing_r > level else level + geo.width
    below = sorted(
        (p for p in _swing_prices(state.swing_lows_15m or []) if p < level * 0.9998),
        reverse=True,
    )
    if below:
        return below[0]
    geo = channel_geometry(px)
    return geo.swing_s if geo.swing_s < level else level - geo.width


def extension_progress(price: float, level: float, direction: str) -> float:
    """
    Kırılım sonrası ilerleme: 0 = seviyede, 1 = ilk yapısal hedefte, >1 = hedef ötesi.
    Sabit bps yok — payda hedef mesafesi.
    """
    if level <= 0 or price <= 0:
        return 0.0
    d = (direction or "").upper()
    target = _next_target_price(level, d, price)
    if d == "LONG":
        span = target - level
        if span <= 0:
            return 0.0
        return (price - level) / span
    span = level - target
    if span <= 0:
        return 0.0
    return (level - price) / span


def is_late_same_level_entry(
    price: float, level: float, direction: str
) -> tuple[bool, str]:
    """
    Aynı seviyede kör giriş geç mi? — oran + post-break bölge (bps eşiği değil).
    """
    if not global_structure_mode():
        ext_bps = leg_extension_bps(price, level, direction)
        fallback = float(getattr(cfg, "NARRATIVE_EXTENDED_MIN_BPS", 120.0))
        if ext_bps >= fallback:
            return True, f"uzanti {ext_bps:.0f}bps"
        return False, ""

    prog = extension_progress(price, level, direction)
    if prog >= 1.0:
        return True, f"ilk hedef geçildi (ilerleme {prog:.2f})"

    try:
        from engine.breakout import get_active_levels

        lv = get_active_levels(price)
        eff_r = float(lv.get("resistance", 0))
        eff_s = float(lv.get("support", 0))
        d = (direction or "").upper()
        span_bps = break_span_bps(level, direction)
        noise_frac = (
            bar_noise_bps(price) / span_bps if span_bps > 0 else 0.08
        )
        if d == "LONG" and level > 0 and price > level:
            if eff_r > level and price < eff_r and prog > noise_frac:
                return True, f"kırılım bölgesi (ilerleme {prog:.2f})"
        if d == "SHORT" and level > 0 and price < level:
            if eff_s < level and price > eff_s and prog > noise_frac:
                return True, f"kırılım bölgesi (ilerleme {prog:.2f})"
        if span_bps > 0 and prog > 1.0 - noise_frac:
            return True, f"hedefe 1 gürültü birimi (ilerleme {prog:.2f})"
    except Exception:
        pass

    return False, ""


def structure_break_bps(price: float = 0) -> float:
    if not global_structure_mode():
        return float(getattr(cfg, "FS_STRUCT_BREAK_BPS", 8.0))
    return bar_noise_bps(price)


def break_threshold_price(level: float, direction: str, price: float = 0) -> float:
    if level <= 0:
        return 0.0
    bps = structure_break_bps(price)
    if (direction or "").upper() == "LONG":
        return level * (1.0 + bps / 10000.0)
    return level * (1.0 - bps / 10000.0)


def breakout_close_beyond(close: float, level: float, side: str, price: float = 0) -> bool:
    """15m kapanis seviyeyi yapısal esik ile asti mi (LONG=ust, SHORT=alt)."""
    if level <= 0 or close <= 0:
        return False
    s = (side or "").upper()
    px = price or close
    if s in ("LONG", "BUY"):
        return close > break_threshold_price(level, "LONG", px)
    if s in ("SHORT", "SELL"):
        return close < break_threshold_price(level, "SHORT", px)
    return False


def close_broke_below(close: float, level: float, price: float = 0) -> bool:
    """15m kapanis seviye altinda (SHORT kirilim / LONG tez bozulmasi)."""
    if level <= 0 or close <= 0:
        return False
    px = price or close
    return close < break_threshold_price(level, "SHORT", px)


def close_broke_above(close: float, level: float, price: float = 0) -> bool:
    """15m kapanis seviye ustunde (LONG kirilim / SHORT tez bozulmasi)."""
    if level <= 0 or close <= 0:
        return False
    px = price or close
    return close > break_threshold_price(level, "LONG", px)


def proximity_bps(price: float = 0) -> float:
    """API uyumu: yakınlık = 2× mum gürültüsü (bps cinsinden mesafe)."""
    if not global_structure_mode():
        return float(getattr(cfg, "BREAK_PROXIMITY_BPS", 35.0))
    return bar_noise_bps(price) * 2.0


def is_near_level(price: float, level: float) -> bool:
    if level <= 0 or price <= 0:
        return False
    dist_bps = abs(price - level) / level * 10000.0
    return dist_bps <= bar_noise_bps(price) * 2.5


def min_channel_bps(price: float = 0) -> float:
    """Kanal, en az 2 swing adımı veya 3× gürültü kadar geniş olmalı."""
    if not global_structure_mode():
        return float(getattr(cfg, "BREAK_MIN_RANGE_BPS", 50.0))
    leg = swing_leg_median_bps(price)
    noise = bar_noise_bps(price)
    return max(leg * 2.0, noise * 3.0)


def outside_max_bps(level: float, price: float, direction: str) -> float:
    """Maks uzaklık = kırılım → ilk hedef mesafesinin üst sınırı."""
    if not global_structure_mode():
        return float(getattr(cfg, "BREAK_OUTSIDE_MAX_BPS", 500.0))
    if level <= 0:
        return 500.0
    target = _next_target_price(level, direction, price)
    d = (direction or "").upper()
    noise = bar_noise_bps(price)
    if d == "LONG" and target > level:
        return (target - level) / level * 10000.0 + noise
    if d == "SHORT" and target < level:
        return (level - target) / level * 10000.0 + noise
    geo = channel_geometry(price)
    if geo.width > 0 and geo.price > 0:
        return geo.width / geo.price * 10000.0
    return 400.0


def post_break_min_bps(level: float = 0, price: float = 0) -> float:
    """TP hedefi: en az bir swing adımı uzak."""
    if not global_structure_mode():
        return float(getattr(cfg, "BREAK_POST_BREAK_MIN_BPS", 80.0))
    return swing_leg_median_bps(price or level)


def channel_band_bps(price: float = 0) -> float:
    geo = channel_geometry(price)
    if geo.price <= 0 or geo.width <= 0:
        return 0.0
    return geo.width / geo.price * 10000.0


def break_span_bps(level: float, direction: str) -> float:
    if level <= 0:
        return 0.0
    target = _next_target_price(level, direction, state.price or state.mark_price)
    d = (direction or "").upper()
    if d == "LONG" and target > level:
        return (target - level) / level * 10000.0
    if d == "SHORT" and target < level:
        return (level - target) / level * 10000.0
    return 0.0


def leg_extension_bps(price: float, level: float, direction: str) -> float:
    if level <= 0 or price <= 0:
        return 0.0
    d = (direction or "").upper()
    if d == "LONG":
        return max(0.0, (price - level) / level * 10000.0)
    return max(0.0, (level - price) / level * 10000.0)


def extended_min_bps(
    level: float = 0,
    price: float = 0,
    direction: str = "LONG",
) -> float:
    """Geriye uyum — bps gösterimi; karar is_late_same_level_entry ile."""
    if level <= 0:
        return float(getattr(cfg, "NARRATIVE_EXTENDED_MIN_BPS", 120.0))
    px = _px(price)
    span = break_span_bps(level, direction)
    if span <= 0:
        return float(getattr(cfg, "NARRATIVE_EXTENDED_MIN_BPS", 120.0))
    noise_frac = bar_noise_bps(px) / span if span > 0 else 0.08
    return span * max(noise_frac, 1.0 - noise_frac)


def retest_zone_bps(level: float = 0, price: float = 0) -> float:
    return bar_noise_bps(price or level) * 2.0 if global_structure_mode() else float(
        getattr(cfg, "NARRATIVE_RETEST_ZONE_BPS", 80.0)
    )


def level_touch_bps(level: float, price: float = 0) -> float:
    return bar_noise_bps(price or level) * 2.0 if global_structure_mode() else float(
        getattr(cfg, "RANGE_LEVEL_TOUCH_BPS", 25.0)
    )


def range_min_width_bps(price: float = 0) -> float:
    return min_channel_bps(price)


def range_min_score(
    band_width_bps: float = 0.0,
    chop_crosses: int = 0,
    edge_confidence: float = 1.0,
) -> float:
    """
    Giriş eşiği (0–100): chop yükseldikçe artar; kenar güveni yüksekse hafif düşer.
    Dar bant tek başına eşik yükseltmez (çift ceza kaldırıldı).
    """
    base = float(getattr(cfg, "RANGE_MIN_SCORE", 65.0))
    if not global_structure_mode():
        return base

    chop_adj = 0.0
    if chop_crosses >= 3:
        chop_adj = min(14.0, (chop_crosses - 2) * 4.0)
    elif chop_crosses >= 2:
        chop_adj = 6.0

    conf_relief = 0.0
    if edge_confidence > 0.55:
        conf_relief = min(12.0, (edge_confidence - 0.55) * 24.0)

    return _clamp(base + chop_adj - conf_relief, 58.0, base + 16.0)


def pulse_min_bps(price: float = 0) -> float:
    return bar_noise_bps(price) if global_structure_mode() else 5.0


def _metrics_history_snapshot() -> list[dict]:
    """Dashboard ve feed aynı deque'ye yazabilir; iterasyon öncesi kopya."""
    return list(state.metrics_history)


def _cvd_samples(lookback_sec: float = 3600.0) -> list[float]:
    now = time.time()
    return [
        float(h.get("cvd", 0))
        for h in _metrics_history_snapshot()
        if now - float(h.get("ts", 0)) <= lookback_sec
    ]


def cvd_min_for_entry() -> float:
    """Giriş: |CVD| kendi geçmişinin üst yüzdelik diliminde olmalı."""
    base = float(getattr(cfg, "CVD_MIN", 200.0))
    if not global_structure_mode():
        return base
    samples = [abs(x) for x in _cvd_samples() if x != 0]
    if len(samples) < 20:
        return base
    pct = float(getattr(cfg, "GS_CVD_ENTRY_PERCENTILE", 0.55))
    s = sorted(samples)
    idx = int(min(len(s) - 1, max(0, pct * len(s))))
    return max(base * 0.4, s[idx])


def cvd_slope_min_for_range(price: float = 0) -> float:
    """Range CVD eğimi: son penceredeki tipik eğim büyüklüğü."""
    base = float(getattr(cfg, "RANGE_CVD_SLOPE_MIN", 60.0))
    if not global_structure_mode():
        return base
    look = float(getattr(cfg, "RANGE_CVD_SLOPE_SEC", 90))
    now = time.time()
    pts = [h for h in _metrics_history_snapshot() if now - h["ts"] <= look * 3]
    if len(pts) < 5:
        return max(base * 0.5, bar_noise_bps(price) * swing_leg_median_bps(price) / 100.0)
    slopes: list[float] = []
    for i in range(1, len(pts)):
        dt = float(pts[i]["ts"]) - float(pts[i - 1]["ts"])
        if dt <= 0:
            continue
        slopes.append(abs(float(pts[i]["cvd"]) - float(pts[i - 1]["cvd"])))
    if not slopes:
        return base * 0.5
    return max(base * 0.45, _median(slopes))


def taker_min_for_entry(direction: str = "LONG") -> float:
    if not global_structure_mode():
        return float(getattr(cfg, "TAKER_MIN", 0.60))
    now = time.time()
    samples = [
        float(h.get("taker", 0.5))
        for h in _metrics_history_snapshot()
        if now - float(h.get("ts", 0)) < 3600
    ]
    if len(samples) < 20:
        return float(getattr(cfg, "TAKER_MIN", 0.60))
    med = _median(samples)
    d = (direction or "").upper()
    if d == "LONG":
        return _clamp(med + 0.04, 0.52, 0.72)
    return _clamp((1.0 - med) + 0.04, 0.52, 0.72)


def taker_min_for_range(direction: str = "LONG") -> float:
    return taker_min_for_entry(direction) - 0.03


def cvd_weak_exit_min() -> float:
    return cvd_min_for_entry() * 0.5


def oi_entry_ok(direction: str) -> tuple[bool, str]:
    hist = [h for h in state.oi_history if float(h.get("oi", 0) or 0) > 0]
    if len(hist) < 2:
        return True, ""
    cutoff = time.time() - float(getattr(cfg, "OI_ENTRY_LOOKBACK_SEC", 30.0))
    recent = [h for h in hist if float(h["ts"]) >= cutoff]
    if len(recent) < 2:
        return True, ""
    o0 = float(recent[0]["oi"])
    o1 = float(recent[-1]["oi"])
    if o0 <= 0:
        return True, ""
    rel = (o1 - o0) / o0
    d = (direction or "").upper()
    if d == "LONG" and rel >= 0:
        return True, ""
    if d == "SHORT" and rel <= 0:
        return True, ""
    return False, f"OI ters (rel={rel * 100:.2f}%)"


def flow_ok(direction: str) -> tuple[bool, str]:
    cvd = float(state.cvd_5m)
    taker = float(state.taker_ratio)
    d = (direction or "").upper()
    if global_structure_mode():
        samples = [abs(x) for x in _cvd_samples() if x != 0]
        if len(samples) >= 15:
            rank = _percentile_rank(abs(cvd), samples)
            need = float(getattr(cfg, "GS_CVD_ENTRY_PERCENTILE", 0.55))
            if d == "LONG" and cvd > 0 and rank < need:
                return False, f"CVD zayıf (pct {rank:.0%} < {need:.0%})"
            if d == "SHORT" and cvd < 0 and rank < need:
                return False, f"CVD zayıf (pct {rank:.0%})"
    cvd_need = cvd_min_for_entry()
    if d == "LONG":
        if cvd < cvd_need:
            return False, f"CVD düşük ({cvd:+.0f} < {cvd_need:.0f})"
        tk = taker_min_for_entry(d)
        if taker < tk:
            return False, f"taker {taker:.0%} < {tk:.0%}"
    else:
        if cvd > -cvd_need:
            return False, f"CVD düşük ({cvd:+.0f})"
        tk = taker_min_for_entry(d)
        if (1.0 - taker) < tk:
            return False, f"satış baskısı zayıf"
    return True, ""


def sl_buffer_bps(price: float = 0) -> float:
    if not global_structure_mode():
        return float(getattr(cfg, "FS_STRUCT_SL_BUFFER_BPS", 10.0))
    return bar_noise_bps(price)


def tp1_max_distance_bps(entry: float = 0, price: float = 0) -> float:
    if not global_structure_mode():
        return float(getattr(cfg, "TP1_MAX_DISTANCE_BPS", 120.0))
    leg = swing_leg_median_bps(price or entry)
    return leg + bar_noise_bps(price or entry)


def threshold_snapshot(
    level: float, price: float, direction: str = "LONG"
) -> dict[str, float]:
    px = _px(price)
    return {
        "band_bps": round(channel_band_bps(px), 1),
        "noise_bps": round(bar_noise_bps(px), 1),
        "leg_bps": round(swing_leg_median_bps(px), 1),
        "extension_progress": round(extension_progress(px, level, direction), 2),
        "retest_zone_bps": round(retest_zone_bps(level, px), 1),
        "cvd_min": round(cvd_min_for_entry(), 1),
    }
