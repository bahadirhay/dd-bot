from __future__ import annotations

from statistics import mean

from core.config import cfg


def trade_band_sr(levels: dict) -> tuple[float, float]:
    """Levels trade band — pivot-authority sonrasi tek S/R otoritesi."""
    s = float(levels.get("active_support") or 0)
    r = float(levels.get("active_resistance") or 0)
    if s <= 0 or r <= s:
        sup = levels.get("support") or {}
        res = levels.get("resistance") or {}
        s = float(sup.get("price", 0) or 0)
        r = float(res.get("price", 0) or 0)
    return s, r


def calculate_channel_zone(
    price: float,
    active_support: float,
    active_resistance: float,
) -> str:
    """
    Tek zone otoritesi — LevelsV3 ve ChannelDecision ayni fonksiyonu kullanir.
    Aktif S/R bandinin kenar bolgesi (V3_CHANNEL_EDGE_FRAC).
    """
    s = float(active_support or 0)
    r = float(active_resistance or 0)
    p = float(price or 0)
    if s <= 0 or r <= s or p <= 0:
        return "MID_RANGE"
    edge_frac = max(float(getattr(cfg, "V3_CHANNEL_EDGE_FRAC", 0.18) or 0.18), 0.05)
    edge = (r - s) * edge_frac
    if p <= s:
        return "NEAR_SUPPORT"
    if p >= r:
        return "NEAR_RESISTANCE"
    if p - s <= edge:
        return "NEAR_SUPPORT"
    if r - p <= edge:
        return "NEAR_RESISTANCE"
    return "MID_RANGE"


def bars_15m(limit: int = 200) -> list[dict]:
    try:
        from engine.structure import get_bars_15m

        return get_bars_15m(limit) or []
    except Exception:
        return []


def bars_1h(limit: int = 100) -> list[dict]:
    try:
        from engine.structure import get_bars_1h

        return get_bars_1h(limit) or []
    except Exception:
        return []


def aggregate_4h_from_1h(bars1h: list[dict] | None = None, *, limit: int = 60) -> list[dict]:
    """1h mumlardan 4h sentez (4 bar birlestir)."""
    bars = list(bars1h or bars_1h(limit * 4 + 8))
    if not bars:
        return []
    out: list[dict] = []
    chunk = 4
    for i in range(0, len(bars), chunk):
        block = bars[i : i + chunk]
        if len(block) < 2:
            continue
        o = float(block[0].get("open", 0) or block[0].get("close", 0) or 0)
        c = float(block[-1].get("close", 0) or 0)
        hi = max(float(b.get("high", 0) or 0) for b in block)
        lo = min(float(b.get("low", 0) or 0) for b in block if float(b.get("low", 0) or 0) > 0)
        vol = sum(float(b.get("volume", 0) or 0) for b in block)
        if o <= 0 or c <= 0:
            continue
        out.append(
            {
                "ts": float(block[-1].get("ts", 0) or 0),
                "open": o,
                "high": hi,
                "low": lo,
                "close": c,
                "volume": vol,
                "closed": True,
            }
        )
    return out[-limit:] if limit > 0 else out


def bars_1m(limit: int = 300) -> list[dict]:
    try:
        from engine.bars_1m import get_bars_1m

        return get_bars_1m(limit) or []
    except Exception:
        return []


def aggregate_3m(source_bars: list[dict]) -> list[dict]:
    if not source_bars:
        return []
    out: list[dict] = []
    current: dict | None = None
    for bar in source_bars:
        ts = int(float(bar.get("ts", 0) or 0))
        bucket_ts = ts - (ts % 180)
        if current is None or int(current["ts"]) != bucket_ts:
            current = {
                "ts": float(bucket_ts),
                "open": float(bar.get("open", 0) or 0),
                "high": float(bar.get("high", 0) or 0),
                "low": float(bar.get("low", 0) or 0),
                "close": float(bar.get("close", 0) or 0),
                "volume": float(bar.get("volume", 0) or 0),
                "closed": True,
            }
            out.append(current)
            continue
        current["high"] = max(float(current["high"]), float(bar.get("high", 0) or 0))
        current["low"] = min(float(current["low"]), float(bar.get("low", 0) or 0))
        current["close"] = float(bar.get("close", 0) or 0)
        current["volume"] += float(bar.get("volume", 0) or 0)
    return out


def aggregate_5m(source_bars: list[dict]) -> list[dict]:
    if not source_bars:
        return []
    out: list[dict] = []
    current: dict | None = None
    for bar in source_bars:
        ts = int(float(bar.get("ts", 0) or 0))
        bucket_ts = ts - (ts % 300)
        if current is None or int(current["ts"]) != bucket_ts:
            current = {
                "ts": float(bucket_ts),
                "open": float(bar.get("open", 0) or 0),
                "high": float(bar.get("high", 0) or 0),
                "low": float(bar.get("low", 0) or 0),
                "close": float(bar.get("close", 0) or 0),
                "volume": float(bar.get("volume", 0) or 0),
                "closed": True,
            }
            out.append(current)
            continue
        current["high"] = max(float(current["high"]), float(bar.get("high", 0) or 0))
        current["low"] = min(float(current["low"]), float(bar.get("low", 0) or 0))
        current["close"] = float(bar.get("close", 0) or 0)
        current["volume"] += float(bar.get("volume", 0) or 0)
    return out


def avg_body(bars: list[dict]) -> float:
    if not bars:
        return 0.0
    return float(
        mean(abs(float(b.get("close", 0) or 0) - float(b.get("open", 0) or 0)) for b in bars)
    )


def _layer_field_prices(layers: dict, keys: tuple[str, ...], fields: tuple[str, ...]) -> list[float]:
    out: list[float] = []
    if not isinstance(layers, dict):
        return out
    for key in keys:
        layer = layers.get(key) or {}
        if not isinstance(layer, dict):
            continue
        for field in fields:
            v = float(layer.get(field) or 0)
            if v > 0:
                out.append(v)
    return out


def range_channel_tp_ladder(
    side: str,
    px: float,
    ref_s: float,
    ref_r: float,
    *,
    levels: dict | None = None,
    swing_lows: list | None = None,
    swing_highs: list | None = None,
) -> tuple[float, float]:
    """
    Kanal icinde kademeli TP (indikatör yok):
      SHORT: TP1 = giris altinda en yakin yapisal (swing / band ortasi / supply alti)
             TP2 = band destek veya demand (runner)
      LONG:  simetrik — TP1 yakin, TP2 band direnc
    """
    side_u = str(side or "").upper()
    px = float(px or 0)
    ref_s = float(ref_s or 0)
    ref_r = float(ref_r or 0)
    layers = {}
    if isinstance(levels, dict):
        layers = levels.get("zone_layers") or levels.get("layers") or {}

    if side_u in ("SHORT", "SELL"):
        if px <= 0 or ref_s <= 0 or ref_r <= ref_s or px <= ref_s:
            return 0.0, 0.0
        band_mid = ref_s + (ref_r - ref_s) * 0.5
        partial_cands: list[float] = [band_mid]
        if swing_lows:
            from engine.structure_levels import nearest_swing_below

            sw = float(nearest_swing_below(px, swing_lows) or 0)
            if sw > 0:
                partial_cands.append(sw)
        partial_cands.extend(
            _layer_field_prices(layers, ("supply_mid", "supply_major"), ("high", "center", "low"))
        )
        min_bps = float(getattr(cfg, "V3_RANGE_TP1_MIN_BPS", 60) or 60)
        min_drop = px * min_bps / 10000.0
        partial = [c for c in partial_cands if ref_s < c < px - min_drop]
        if not partial:
            partial = [c for c in partial_cands if ref_s < c < px]
        tp1 = max(partial) if partial else band_mid
        if px - tp1 < min_drop:
            min_frac = max(float(getattr(cfg, "V3_RANGE_TP1_MIN_BAND_FRAC", 0.25) or 0.25), 0.1)
            tp1 = min(tp1, px - (px - ref_s) * min_frac)

        runner_cands: list[float] = []
        if ref_s > 0:
            runner_cands.append(ref_s)
        runner_cands.extend(
            _layer_field_prices(layers, ("demand_weak", "demand_liq"), ("high", "center", "low"))
        )
        below_tp1 = [c for c in runner_cands if ref_s < c < tp1]
        if below_tp1:
            tp2 = max(below_tp1)
        else:
            far = [c for c in runner_cands if 0 < c < tp1]
            tp2 = min(far) if far else ref_s
        if tp2 <= 0 or tp2 >= tp1:
            tp2 = ref_s
        return round(tp1, 2), round(tp2, 2)

    if side_u in ("LONG", "BUY"):
        if px <= 0 or ref_s <= 0 or ref_r <= ref_s or px >= ref_r:
            return 0.0, 0.0
        band_mid = ref_s + (ref_r - ref_s) * 0.5
        partial_cands: list[float] = [band_mid]
        if swing_highs:
            from engine.structure_levels import nearest_swing_above

            sw = float(nearest_swing_above(px, swing_highs) or 0)
            if sw > 0:
                partial_cands.append(sw)
        partial_cands.extend(
            _layer_field_prices(layers, ("demand_weak", "demand_liq"), ("low", "center", "high"))
        )
        min_bps = float(getattr(cfg, "V3_RANGE_TP1_MIN_BPS", 60) or 60)
        min_rise = px * min_bps / 10000.0
        partial = [c for c in partial_cands if px + min_rise < c < ref_r]
        if not partial:
            partial = [c for c in partial_cands if px < c < ref_r]
        tp1 = min(partial) if partial else band_mid
        if tp1 - px < min_rise:
            min_frac = max(float(getattr(cfg, "V3_RANGE_TP1_MIN_BAND_FRAC", 0.25) or 0.25), 0.1)
            tp1 = max(tp1, px + (ref_r - px) * min_frac)

        runner_cands: list[float] = []
        if ref_r > 0:
            runner_cands.append(ref_r)
        runner_cands.extend(
            _layer_field_prices(layers, ("supply_mid", "supply_major"), ("low", "center", "high"))
        )
        above_tp1 = [c for c in runner_cands if tp1 < c <= ref_r]
        if above_tp1:
            tp2 = min(above_tp1)
        else:
            far = [c for c in runner_cands if c > tp1]
            tp2 = max(far) if far else ref_r
        if tp2 <= px or tp2 <= tp1:
            tp2 = ref_r
        return round(tp1, 2), round(tp2, 2)

    return 0.0, 0.0
