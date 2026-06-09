"""
Support & Resistance — Pine Script "Support Resistance Ultimate" (Julien_Eche) bire bir port.

TEK HESAP KAPISI: compute_sr_snapshot() -> SRSnapshot
  - pine_lines / chart_lines: grafik (dashboard state.v3_levels.chart_levels)
  - trade_supports / trade_resistances: strateji havuzu
  - active_support / active_resistance: aktif bant

Referans: scripts/pine_sr_ultimate_reference.pine (Julien_Eche, sizin verdiginiz kod).

Diger moduller (sr_levels_v3, levels_v3, dashboard) yeniden pivot hesaplamaz;
yalnizca snapshot'u dict'e cevirir veya strateji kurallari uygular.

Pivot: ta.valuewhen ile level1–level8; num_lines_to_show kadar cizgi.

Pine gorunum (Julien_Eche SR Ultimate):
- Cizgi FIYATI pivot valuewhen ile sabit (L1..L6).
- RENK her barda: close >= seviye -> destek rengi, else direnc (TV cizgi rengi).
- L1,L3,L5 = quick/pivot LOW kaynagi (yapisal destek); L2,L4,L6 = HIGH (direnc).
- quick_prev_support = valuewhen(quick_pivot_support, close[quick_right], occurrence=1):
  onceki L1 (or. TV'de ~1975). Yeni L1=1968 olunca 1975 kaybolmaz — strateji bandi bunu kullanir.
- V3_SR_USE_PINE_DIRECTION_BAND=false: aktif bant yapisal slot + quick_prev (renk degil).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class SRLevel:
    price: float
    direction: str  # "support" | "resistance" (close>=fiyat renk kurali veya slot)
    source: str  # "pivot" | "poc"
    touches: int
    volume_around: float
    importance: int  # 1-5 kalinlik
    bar_index: int  # cizgi baslangic bar (Pine start_index)
    slot: int = 0  # Pine level1=1 .. level8=8
    slot_role: str = ""  # "support" | "resistance" (tek/ cift slot — alert mantigi)
    tag: str = ""  # opsiyonel: quick_prev_support gibi


@dataclass
class SRSnapshot:
    """
    Tek S/R hesap ciktisi — grafik ve strateji bunu kullanir (yeniden hesaplamaz).
    """

    timeframe: str
    price: float
    close: float
    pine_lines: list  # L1-L6 slot sirasi
    chart_lines: list  # grafikte cizilecek (varsayilan = pine_lines)
    trade_supports: list
    trade_resistances: list
    active_support: SRLevel | None
    active_resistance: SRLevel | None
    all_levels: list
    params: dict


def pivot_low(series: np.ndarray, left: int, right: int) -> np.ndarray:
    """Pine ta.pivotlow — deger pivot onay barinda (merkez + right)."""
    n = len(series)
    result = np.full(n, np.nan)
    for center in range(left, n - right):
        val = series[center]
        neigh = np.concatenate(
            (series[center - left : center], series[center + 1 : center + right + 1])
        )
        if neigh.size == 0:
            continue
        if np.all(val < neigh):
            confirm = center + right
            if confirm < n:
                result[confirm] = val
    return result


def pivot_high(series: np.ndarray, left: int, right: int) -> np.ndarray:
    """Pine ta.pivothigh — deger pivot onay barinda (merkez + right)."""
    n = len(series)
    result = np.full(n, np.nan)
    for center in range(left, n - right):
        val = series[center]
        neigh = np.concatenate(
            (series[center - left : center], series[center + 1 : center + right + 1])
        )
        if neigh.size == 0:
            continue
        if np.all(val > neigh):
            confirm = center + right
            if confirm < n:
                result[confirm] = val
    return result


def valuewhen(
    condition: np.ndarray,
    source: np.ndarray,
    offset: int,
    occurrence: int = 0,
) -> tuple[Optional[float], Optional[int]]:
    """
    Pine ta.valuewhen(not na(cond), source[offset], occurrence).
    Kosul saglanan barda source[confirm_bar - offset] doner.
  occurrence 0 = en son, 1 = bir onceki, ...
    """
    hits = np.flatnonzero(~np.isnan(condition))
    if len(hits) == 0:
        return None, None
    pick = -(occurrence + 1)
    if abs(pick) > len(hits):
        return None, None
    confirm_bar = int(hits[pick])
    src_idx = confirm_bar - int(offset)
    if src_idx < 0 or src_idx >= len(source):
        return None, None
    return float(source[src_idx]), src_idx


def count_touches(
    level: float,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    start_idx: int,
) -> int:
    """Pine f_count_touches_adjusted (pine_sr_ultimate_reference.pine L62-67)."""
    last = len(close) - 1
    if start_idx < 0 or start_idx > last or level <= 0:
        return 0
    touches = 0
    # Pine: for i = 0 to bar_index - start_index  (i bars back from son bar)
    for i in range(0, last - int(start_idx) + 1):
        idx = last - i
        if (high[idx] >= level and low[idx] <= level) or close[idx] == level:
            touches += 1
    return touches


def volume_around_level(
    level: float,
    high: np.ndarray,
    low: np.ndarray,
    volume: np.ndarray,
    start_idx: int,
    range_pct: float = 0.001,
) -> float:
    """Pine f_calculate_volume_around_level (reference L44-49)."""
    last = len(volume) - 1
    if start_idx < 0 or level <= 0:
        return 0.0
    total = 0.0
    for i in range(0, last - int(start_idx) + 1):
        idx = last - i
        if (
            abs(high[idx] - level) / level <= range_pct
            or abs(low[idx] - level) / level <= range_pct
        ):
            total += float(volume[idx])
    return total


def calculate_importance(
    touches: int,
    vol_around: float,
    max_vol: float,
    weight_touches: float = 0.05,
    weight_volume: float = 0.25,
) -> int:
    score_touches = touches * weight_touches
    score_volume = (vol_around / max_vol) * weight_volume if max_vol > 0 else 0
    raw = (score_touches + score_volume) * 5
    return int(max(1, min(5, round(raw))))


def _pine_overlap_thickness(prices: list[float], thicknesses: list[int]) -> list[int]:
    """Pine L289-303: levelN == levelM -> thickness + overlap_increase (1)."""
    out = list(thicknesses)
    overlap_increase = 1
    n = min(8, len(prices), len(out))

    def _eq(a: float, b: float) -> bool:
        return not (np.isnan(a) or np.isnan(b)) and a == b

    p = [float(prices[i]) if i < len(prices) and not np.isnan(prices[i]) else float("nan") for i in range(8)]

    if n > 0 and any(_eq(p[0], p[j]) for j in range(1, 8) if not np.isnan(p[j])):
        out[0] = min(5, out[0] + overlap_increase)
    if n > 1 and any(_eq(p[1], p[j]) for j in range(2, 8) if not np.isnan(p[j])):
        out[1] = min(5, out[1] + overlap_increase)
    if n > 2 and any(_eq(p[2], p[j]) for j in range(3, 8) if not np.isnan(p[j])):
        out[2] = min(5, out[2] + overlap_increase)
    if n > 3 and any(_eq(p[3], p[j]) for j in range(4, 8) if not np.isnan(p[j])):
        out[3] = min(5, out[3] + overlap_increase)
    if n > 4 and any(_eq(p[4], p[j]) for j in range(5, 8) if not np.isnan(p[j])):
        out[4] = min(5, out[4] + overlap_increase)
    if n > 5 and any(_eq(p[5], p[j]) for j in range(6, 8) if not np.isnan(p[j])):
        out[5] = min(5, out[5] + overlap_increase)
    if n > 6 and _eq(p[6], p[7]):
        out[6] = min(5, out[6] + overlap_increase)
    return out


def _direction_from_close(price: float, close_px: float) -> str:
    """Pine: close >= level ? support_color : resistance_color."""
    if price is None or np.isnan(price):
        return "resistance"
    return "support" if close_px >= float(price) else "resistance"


def _slot_role(slot: int) -> str:
    """Pine alert: tek slot (1,3,5,7) destek; cift (2,4,6,8) direnc."""
    return "support" if slot % 2 == 1 else "resistance"


def find_pine_sr_ultimate(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    *,
    lookback_left: int = 50,
    lookback_right: int = 20,
    quick_right: int = 10,
    source: str = "close",
    num_lines_to_show: int = 6,
    range_pct: float = 0.001,
    include_prev_quick_support: bool = False,  # Pine'da yok — yok say
    use_poc: bool = False,
    poc_lookback: int = 5,
) -> list[SRLevel]:
    """
    Julien_Eche Support Resistance Ultimate — scripts/pine_sr_ultimate_reference.pine
    L85-92 level1-8, L251-257 start_index, L241-248 renk, L289-303 overlap.
    """
    n = len(close)
    if n < lookback_left + lookback_right + 2:
        return []

    src = str(source or "close").lower()
    use_close = src in ("close", "kapat", "kapanis")

    # L69-82 pivot serileri
    pivot_support = pivot_low(close if use_close else low, lookback_left, lookback_right)
    pivot_resistance = pivot_high(close if use_close else high, lookback_left, lookback_right)
    quick_pivot_support = pivot_low(close if use_close else low, lookback_left, quick_right)
    quick_pivot_resistance = pivot_high(
        close if use_close else high, lookback_left, quick_right
    )

    bar_index = np.arange(n, dtype=float)

    # L85-92: (slot, kosul, fiyat kaynagi offset, occurrence)
    if use_close:
        level_defs = [
            (1, quick_pivot_support, close, quick_right, 0),
            (2, quick_pivot_resistance, close, quick_right, 0),
            (3, pivot_support, close, lookback_right, 0),
            (4, pivot_resistance, close, lookback_right, 0),
            (5, pivot_support, close, lookback_right, 1),
            (6, pivot_resistance, close, lookback_right, 1),
            (7, pivot_support, close, lookback_right, 2),
            (8, pivot_resistance, close, lookback_right, 2),
        ]
    else:
        level_defs = [
            (1, quick_pivot_support, high, quick_right, 0),
            (2, quick_pivot_resistance, low, quick_right, 0),
            (3, pivot_support, high, lookback_right, 0),
            (4, pivot_resistance, low, lookback_right, 0),
            (5, pivot_support, high, lookback_right, 1),
            (6, pivot_resistance, low, lookback_right, 1),
            (7, pivot_support, high, lookback_right, 2),
            (8, pivot_resistance, low, lookback_right, 2),
        ]

    max_vol = float(np.max(volume[-lookback_left:])) if n >= lookback_left else float(np.max(volume))
    close_px = float(close[-1])

    prices8: list[float] = [float("nan")] * 8
    starts8: list[int] = [-1] * 8
    touches8: list[int] = [0] * 8
    vols8: list[float] = [0.0] * 8

    for slot, cond, price_src, off, occ in level_defs:
        i = slot - 1
        px, _ = valuewhen(cond, price_src, off, occurrence=occ)
        st, _ = valuewhen(cond, bar_index, off, occurrence=occ)
        if px is None or st is None:
            continue
        px = round(float(px), 2)
        st = int(st)
        prices8[i] = px
        starts8[i] = st
        touches8[i] = count_touches(px, high, low, close, st)
        vols8[i] = volume_around_level(px, high, low, volume, st, range_pct)

    thicknesses8 = [
        calculate_importance(touches8[i], vols8[i], max_vol) for i in range(8)
    ]
    thicknesses8 = _pine_overlap_thickness(prices8, thicknesses8)

    levels: list[SRLevel] = []
    show_n = max(1, min(int(num_lines_to_show), 8))
    for i in range(show_n):
        px = prices8[i]
        if np.isnan(px):
            continue
        st = starts8[i]
        slot = i + 1
        levels.append(
            SRLevel(
                price=float(px),
                direction=_direction_from_close(px, close_px),
                source="pivot",
                touches=int(touches8[i]),
                volume_around=float(vols8[i]),
                importance=int(thicknesses8[i]),
                bar_index=st,
                slot=slot,
                slot_role=_slot_role(slot),
            )
        )

    # Pine L85: L1 = quick_pivot_support occ 0; occ 1 = bir onceki quick destek (~1975 TV)
    if include_prev_quick_support:
        q_src = close if use_close else low
        px_prev, st_prev = valuewhen(
            quick_pivot_support, q_src, quick_right, occurrence=1
        )
        st_idx, _ = valuewhen(
            quick_pivot_support, bar_index, quick_right, occurrence=1
        )
        if px_prev is not None:
            px_prev = round(float(px_prev), 2)
            l1_px = prices8[0] if not np.isnan(prices8[0]) else None
            dup = any(
                not np.isnan(prices8[i]) and abs(px_prev - float(prices8[i])) < 0.05
                for i in range(show_n)
            )
            if not dup:
                st_i = int(st_idx) if st_idx is not None else -1
                levels.append(
                    SRLevel(
                        price=float(px_prev),
                        direction=_direction_from_close(px_prev, close_px),
                        source="pivot",
                        touches=count_touches(px_prev, high, low, close, st_i)
                        if st_i >= 0
                        else 0,
                        volume_around=volume_around_level(
                            px_prev, high, low, volume, st_i, range_pct
                        )
                        if st_i >= 0
                        else 0.0,
                        importance=1,
                        bar_index=st_i,
                        slot=0,
                        slot_role="support",
                        tag="quick_prev_support",
                    )
                )

    if use_poc:
        levels.extend(
            _pine_poc_levels(
                high,
                low,
                close,
                volume,
                lookback_left=lookback_left,
                lookback_right=lookback_right,
                quick_right=quick_right,
                poc_lookback=poc_lookback,
                num_lines_to_show=num_lines_to_show,
                range_pct=range_pct,
            )
        )

    return levels


def rolling_poc(
    high: np.ndarray,
    low: np.ndarray,
    volume: np.ndarray,
    lookback: int = 5,
    rows: int = 200,
) -> np.ndarray:
    n = len(high)
    poc_arr = np.full(n, np.nan)
    for i in range(lookback - 1, n):
        h = high[i - lookback + 1 : i + 1]
        l = low[i - lookback + 1 : i + 1]
        v = volume[i - lookback + 1 : i + 1]
        hi = np.max(h)
        lo = np.min(l)
        if hi == lo:
            poc_arr[i] = hi
            continue
        levels = np.linspace(lo, hi, rows + 1)
        bucket_vol = np.zeros(rows)
        for j in range(rows):
            mask = (h > levels[j]) & (l < levels[j + 1])
            bucket_vol[j] = np.sum(v[mask])
        best = int(np.argmax(bucket_vol))
        poc_arr[i] = (levels[best] + levels[best + 1]) / 2.0
    return poc_arr


def _pine_poc_levels(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    *,
    lookback_left: int,
    lookback_right: int,
    quick_right: int,
    poc_lookback: int,
    num_lines_to_show: int,
    range_pct: float,
) -> list[SRLevel]:
    """Pine POC blok — pivot_high(POC)=support, pivot_low(POC)=resistance."""
    poc = rolling_poc(high, low, volume, poc_lookback)
    n = len(close)
    if n < lookback_left + lookback_right + 2:
        return []

    pivot_support_poc = pivot_high(poc, lookback_left, lookback_right)
    pivot_resistance_poc = pivot_low(poc, lookback_left, lookback_right)
    quick_pivot_support_poc = pivot_high(poc, lookback_left, quick_right)
    quick_pivot_resistance_poc = pivot_low(poc, lookback_left, quick_right)

    level_specs = [
        (1, quick_pivot_support_poc, quick_right),
        (2, quick_pivot_resistance_poc, quick_right),
        (3, pivot_support_poc, lookback_right),
        (4, pivot_resistance_poc, lookback_right),
        (5, pivot_support_poc, lookback_right),
        (6, pivot_resistance_poc, lookback_right),
        (7, pivot_support_poc, lookback_right),
        (8, pivot_resistance_poc, lookback_right),
    ]
    occurrences = [0, 0, 0, 0, 1, 1, 2, 2]

    max_vol = (
        float(np.max(volume[-lookback_left:]))
        if n >= lookback_left
        else float(np.max(volume))
    )
    close_px = float(close[-1])
    poc_last = poc[-1] if not np.isnan(poc[-1]) else close_px

    prices: list[float] = []
    starts: list[int] = []
    for (slot, cond_arr, off), occ in zip(level_specs, occurrences):
        px, start = valuewhen(cond_arr, poc, off, occurrence=occ)
        if px is None:
            prices.append(float("nan"))
            starts.append(-1)
        else:
            prices.append(round(float(px), 2))
            starts.append(int(start) if start is not None else -1)

    importances: list[int] = []
    for px, st in zip(prices, starts):
        if np.isnan(px) or st < 0:
            importances.append(1)
            continue
        t = count_touches(px, high, low, close, st)
        v = volume_around_level(px, high, low, volume, st, range_pct)
        importances.append(calculate_importance(t, v, max_vol))
    importances = _pine_overlap_thickness(prices, importances)

    out: list[SRLevel] = []
    show_n = max(1, min(int(num_lines_to_show), 8))
    for i in range(show_n):
        px = prices[i]
        if np.isnan(px):
            continue
        st = starts[i]
        slot = i + 1
        direction = "support" if poc_last >= px else "resistance"
        out.append(
            SRLevel(
                price=float(px),
                direction=direction,
                source="poc",
                touches=count_touches(px, high, low, close, st) if st >= 0 else 0,
                volume_around=volume_around_level(px, high, low, volume, st, range_pct)
                if st >= 0
                else 0.0,
                importance=importances[i],
                bar_index=st,
                slot=slot,
                slot_role=_slot_role(slot),
            )
        )
    return out


# Geriye uyumluluk
find_pivot_levels = find_pine_sr_ultimate


def _dedupe_sr_level_list(levels: list[SRLevel]) -> list[SRLevel]:
    seen: set[tuple[int, float]] = set()
    out: list[SRLevel] = []
    for lvl in levels:
        key = (int(lvl.slot or 0), round(float(lvl.price), 2))
        if key in seen:
            continue
        seen.add(key)
        out.append(lvl)
    return out


def _levels_for_active_band(all_levels: list[SRLevel], *, for_active: bool) -> list[SRLevel]:
    """
  Trade/aktif bant: varsayilan yalnizca Pine slot 1-8.
  quick_prev_support yalnizca grafik katmani (TV'de yok).
    """
    if not for_active:
        return list(all_levels)
    from core.config import cfg

    if getattr(cfg, "V3_SR_PREV_QUICK_FOR_ACTIVE_BAND", False):
        return list(all_levels)
    return [
        l
        for l in all_levels
        if str(getattr(l, "tag", "") or "") != "quick_prev_support"
    ]


def _pine_display_side(level: SRLevel, close_px: float) -> str:
    """TV ile ayni: close >= seviye -> destek, aksi direnc."""
    return str(level.direction or _direction_from_close(level.price, close_px))


def _structural_support(level: SRLevel) -> bool:
    """Pine L1/L3/L5 + onceki quick destek (occurrence=1)."""
    if str(getattr(level, "tag", "") or "") == "quick_prev_support":
        return True
    return str(level.slot_role or "") == "support"


def _structural_resistance(level: SRLevel) -> bool:
    if str(getattr(level, "tag", "") or "") == "quick_prev_support":
        return False
    return str(level.slot_role or "") == "resistance"


def split_sr_by_price(
    all_levels: list[SRLevel],
    px: float,
    *,
    near_pct: float = 0.001,
    active_min_sep_pct: float = 0.0025,
    for_active: bool = True,
) -> dict:
    """
    Aktif bant:
    - use_pine_dir=true: TV renk kurali (close>=fiyat) — kirilan destek listeden duser.
    - use_pine_dir=false: yapisal slot (L1/L3/L5 + quick_prev occ1) — ~1975 korunur.
    """
    from core.config import cfg

    buf, active_sep = max(px * float(near_pct or 0.001), 1.0), max(
        px * float(active_min_sep_pct or 0.0025), 4.0
    )

    pool = _levels_for_active_band(all_levels, for_active=for_active)
    use_pine_dir = getattr(cfg, "V3_SR_USE_PINE_DIRECTION_BAND", False)
    if use_pine_dir:
        supports = [l for l in pool if _pine_display_side(l, px) == "support"]
        resistances = [l for l in pool if _pine_display_side(l, px) == "resistance"]
        below = sorted([l for l in supports if l.price < px], key=lambda x: -x.price)
        above = sorted([l for l in resistances if l.price > px], key=lambda x: x.price)
        active_s = below[0] if below else None
        active_r = above[0] if above else None
        if active_s is None and supports:
            active_s = max(supports, key=lambda x: x.price)
        if active_r is None and resistances:
            active_r = min(resistances, key=lambda x: x.price)
    else:
        struct_s = [l for l in pool if _structural_support(l)]
        struct_r = [l for l in pool if _structural_resistance(l)]
        at_or_below = sorted(
            [l for l in struct_s if l.price <= px], key=lambda x: -x.price
        )
        above_s = sorted([l for l in struct_s if l.price > px], key=lambda x: x.price)
        # Kirilan destek: fiyat hemen altindaysa en yakin ustteki yapisal destek (or. 1975)
        active_s = None
        if above_s and (above_s[0].price - px) <= buf:
            active_s = above_s[0]
        elif at_or_below:
            active_s = at_or_below[0]
        elif above_s:
            active_s = above_s[0]
        below_r = sorted([l for l in struct_r if l.price < px], key=lambda x: -x.price)
        above_r = sorted([l for l in struct_r if l.price >= px], key=lambda x: x.price)
        active_r = above_r[0] if above_r else (below_r[-1] if below_r else None)
        below = at_or_below
        above = above_r
        supports = struct_s
        resistances = [l for l in struct_r if l.price > px]

        # Trade bandi canli role gore kurulmalı: Pine slotu "support" olan bir
        # cizgi fiyat altinda kalinca artik reclaim edilene kadar direnc gibi
        # davranir. Yapısal ref_s/ref_r korunur; sadece daha yakin canli band
        # kenari varsa aktif kanal okumasina alinir.
        live_below = sorted([l for l in pool if l.price < px], key=lambda x: -x.price)
        live_above = sorted([l for l in pool if l.price > px], key=lambda x: x.price)
        if live_below:
            live_s = live_below[0]
            if active_s is None or (
                active_s.price < px and live_s.price > active_s.price
            ):
                active_s = live_s
        if live_above:
            min_r = float(active_s.price) if active_s is not None else 0.0
            live_r = next((l for l in live_above if l.price > min_r), None)
            if live_r is not None and (
                active_r is None
                or (active_r.price > px and live_r.price < active_r.price)
            ):
                active_r = live_r

        # ── FLIP: Kırılan destek → direnç ────────────────────────────────────
        # Fiyatın üstündeki her Pine seviyesi (slot'tan bağımsız) "flipped"
        # direnç olarak resistance havuzuna eklenir. active_r için de öncelik
        # verilir: mevcut active_r yoksa veya çok uzaksa, en yakın flipped level kullanılır.
        flipped_resistances = [
            l for l in pool
            if l.price > px
            and l not in above  # zaten struct_r'da yoksa
        ]
        if flipped_resistances:
            # En yakın flipped seviye → potential active_r
            closest_flip = min(flipped_resistances, key=lambda x: x.price)
            if active_r is None or closest_flip.price < active_r.price:
                active_r = closest_flip
            # Resistance havuzuna ekle (dedupe)
            existing_r_prices = {round(l.price, 2) for l in above}
            for fl in flipped_resistances:
                if round(fl.price, 2) not in existing_r_prices:
                    above = sorted(above + [fl], key=lambda x: x.price)
                    resistances = above
        # ─────────────────────────────────────────────────────────────────────

    return {
        "support": below,
        "resistance": above,
        "active_support": active_s,
        "active_resistance": active_r,
        "near_price_buffer": buf,
        "active_min_sep": active_sep,
    }


def calculate_sr_levels(
    df: pd.DataFrame,
    use_pivot: bool = True,
    use_poc: bool = False,
    current_price: Optional[float] = None,
    **kwargs,
) -> dict:
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    volume = df["volume"].to_numpy(dtype=float)

    px = float(current_price if current_price is not None else close[-1])

    lookback_left = int(kwargs.get("lookback_left", 50))
    lookback_right = int(kwargs.get("lookback_right", 20))
    quick_right = int(kwargs.get("quick_right", 10))
    source = str(kwargs.get("source", "close"))
    num_lines = int(kwargs.get("num_levels", kwargs.get("num_lines_to_show", 6)))
    range_pct = float(kwargs.get("range_pct", 0.001))
    poc_lookback = int(kwargs.get("poc_lookback", 5))
    include_prev_quick_support = bool(
        kwargs.get("include_prev_quick_support", False)
    )

    all_levels: list[SRLevel] = []
    if use_pivot:
        all_levels = find_pine_sr_ultimate(
            high,
            low,
            close,
            volume,
            lookback_left=lookback_left,
            lookback_right=lookback_right,
            quick_right=quick_right,
            source=source,
            num_lines_to_show=num_lines,
            range_pct=range_pct,
            include_prev_quick_support=include_prev_quick_support,
            use_poc=False,
        )
    if use_poc:
        all_levels = _dedupe_sr_level_list(
            all_levels
            + _pine_poc_levels(
                high,
                low,
                close,
                volume,
                lookback_left=lookback_left,
                lookback_right=lookback_right,
                quick_right=quick_right,
                poc_lookback=poc_lookback,
                num_lines_to_show=num_lines,
                range_pct=range_pct,
            )
        )

    split = split_sr_by_price(
        all_levels,
        px,
        near_pct=float(kwargs.get("near_price_pct", 0.001) or 0.001),
        active_min_sep_pct=float(kwargs.get("active_min_sep_pct", 0.0025) or 0.0025),
        for_active=True,
    )
    return {
        "support": split["support"],
        "resistance": split["resistance"],
        "active_support": split["active_support"],
        "active_resistance": split["active_resistance"],
        "all": all_levels,
        "display_lines": all_levels,
        "near_price_buffer": split["near_price_buffer"],
        "active_min_sep": split["active_min_sep"],
    }


def bars_to_dataframe(bars: list[dict]) -> pd.DataFrame:
    rows = []
    for b in bars:
        vol = float(b.get("volume", 0) or 0)
        if vol <= 0:
            vol = float(b.get("buy_vol", 0) or 0) + float(b.get("sell_vol", 0) or 0)
        rows.append(
            {
                "open": float(b.get("open", 0) or 0),
                "high": float(b.get("high", 0) or 0),
                "low": float(b.get("low", 0) or 0),
                "close": float(b.get("close", 0) or 0),
                "volume": vol if vol > 0 else 1.0,
            }
        )
    return pd.DataFrame(rows)


def bars_to_ohlcv(bars: list[dict]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    df = bars_to_dataframe(bars)
    return (
        df["high"].to_numpy(dtype=float),
        df["low"].to_numpy(dtype=float),
        df["close"].to_numpy(dtype=float),
        df["volume"].to_numpy(dtype=float),
    )


def sr_params_from_config(timeframe: str) -> dict:
    """cfg -> Pine parametreleri (tek kaynak)."""
    from core.config import cfg

    if timeframe == "1h":
        return {
            "lookback_left": int(getattr(cfg, "V3_SR_1H_LOOKBACK_LEFT", 50) or 50),
            "lookback_right": int(getattr(cfg, "V3_SR_1H_LOOKBACK_RIGHT", 20) or 20),
            "quick_right": int(getattr(cfg, "V3_SR_1H_QUICK_RIGHT", 10) or 10),
            "num_levels": int(getattr(cfg, "V3_SR_1H_NUM_LEVELS", 6) or 6),
            "source": str(getattr(cfg, "V3_SR_SOURCE", "close") or "close"),
            "range_pct": float(getattr(cfg, "V3_SR_TOUCH_RANGE_PCT", 0.001) or 0.001),
            "poc_lookback": int(getattr(cfg, "V3_SR_POC_LOOKBACK", 5) or 5),
            "timeframe": "1h",
        }
    return {
        "lookback_left": int(getattr(cfg, "V3_SR_LOOKBACK_LEFT", 50) or 50),
        "lookback_right": int(getattr(cfg, "V3_SR_LOOKBACK_RIGHT", 20) or 20),
        "quick_right": int(getattr(cfg, "V3_SR_QUICK_RIGHT", 10) or 10),
        "num_levels": int(getattr(cfg, "V3_SR_NUM_LEVELS", 6) or 6),
        "source": str(getattr(cfg, "V3_SR_SOURCE", "close") or "close"),
        "range_pct": float(getattr(cfg, "V3_SR_TOUCH_RANGE_PCT", 0.001) or 0.001),
        "poc_lookback": int(getattr(cfg, "V3_SR_POC_LOOKBACK", 5) or 5),
        "timeframe": "15m",
    }


def min_bars_for_sr_params(p: dict) -> int:
    return int(p["lookback_left"]) + int(p["lookback_right"]) + 2


def compute_sr_snapshot(
    bars: list[dict],
    *,
    price: float | None = None,
    timeframe: str = "15m",
) -> SRSnapshot | None:
    """
    TEK S/R HESABI — Pine L1-L8, aktif bant, grafik cizgileri.
    Baska modul bu fonksiyon disinda pivot hesaplamamali.
    """
    from core.config import cfg

    p = sr_params_from_config(timeframe)
    if len(bars) < min_bars_for_sr_params(p):
        return None

    px = float(price or 0) or float(bars[-1].get("close", 0) or 0)
    near_pct = float(getattr(cfg, "V3_SR_NEAR_PRICE_PCT", 0.001) or 0.001)
    active_sep = float(getattr(cfg, "V3_SR_ACTIVE_MIN_SEP_PCT", 0.0025) or 0.0025)

    raw = calculate_sr_levels_from_bars(
        bars,
        use_pivot=bool(getattr(cfg, "V3_SR_USE_PIVOT", True)),
        use_poc=bool(getattr(cfg, "V3_SR_USE_POC", False)),
        current_price=px if px > 0 else None,
        near_price_pct=near_pct,
        active_min_sep_pct=active_sep,
        lookback_left=p["lookback_left"],
        lookback_right=p["lookback_right"],
        quick_right=p["quick_right"],
        source=p["source"],
        num_levels=p["num_levels"],
        range_pct=p["range_pct"],
        include_prev_quick_support=bool(
            getattr(cfg, "V3_SR_INCLUDE_PREV_QUICK_SUPPORT", True)
        ),
        poc_lookback=p["poc_lookback"],
    )

    all_levels: list[SRLevel] = list(raw.get("all") or [])
    max_lines = int(getattr(cfg, "V3_SR_CHART_MAX_LINES", 6) or 6)
    pine_lines = sorted(
        [lv for lv in all_levels if 1 <= int(getattr(lv, "slot", 0) or 0) <= 8],
        key=lambda x: int(getattr(x, "slot", 0) or 0),
    )[:max_lines]

    chart_lines = list(pine_lines)
    if bool(getattr(cfg, "V3_SR_CHART_SHOW_PREV_QUICK", False)):
        for lv in all_levels:
            if str(getattr(lv, "tag", "") or "") == "quick_prev_support":
                chart_lines.append(lv)

    close_px = float(bars[-1].get("close", 0) or px)
    return SRSnapshot(
        timeframe=str(p["timeframe"]),
        price=px,
        close=close_px,
        pine_lines=pine_lines,
        chart_lines=chart_lines,
        trade_supports=list(raw.get("support") or []),
        trade_resistances=list(raw.get("resistance") or []),
        active_support=raw.get("active_support"),
        active_resistance=raw.get("active_resistance"),
        all_levels=all_levels,
        params=dict(p),
    )


def level_to_dict(sr: SRLevel, timeframe: str, *, current_price: float = 0) -> dict:
    """SRLevel -> bot level dict (grafik + strateji ortak format)."""
    imp = int(sr.importance)
    # Dinamik rol: fiyatın üstündeki level → resistance, altındaki → support
    # (Pine slot rengi değil, gerçek konum bazlı)
    if current_price > 0:
        kind = "resistance" if sr.price > current_price else "support"
    else:
        kind = str(sr.direction)
    return {
        "price": round(float(sr.price), 2),
        "kind": kind,
        "timeframe": timeframe,
        "bar_index": int(sr.bar_index),
        "is_swing": True,
        "is_htf": timeframe == "1h",
        "sr_source": str(sr.source),
        "sr_importance": imp,
        "sr_touches": int(sr.touches),
        "sr_volume_around": float(sr.volume_around),
        "strength": "STRONG" if imp >= 4 else "MEDIUM",
        "score": max(6, imp + 3),
        "touch_count": int(sr.touches),
        "pivot_kind": kind,
        "slot_role": str(sr.slot_role or ""),
        "sr_tag": str(sr.tag or ""),
        "sr_slot": int(sr.slot or 0),
        "primary": False,
    }


def snapshot_trade_level_dicts(snap: SRSnapshot) -> list[dict]:
    tf = snap.timeframe
    px = float(snap.price or snap.close or 0)
    out: list[dict] = []
    for sr in snap.trade_supports:
        out.append(level_to_dict(sr, tf, current_price=px))
    for sr in snap.trade_resistances:
        out.append(level_to_dict(sr, tf, current_price=px))
    return out


def snapshot_chart_level_dicts(
    snap: SRSnapshot,
    *,
    active_support: float = 0,
    active_resistance: float = 0,
) -> list[dict]:
    """Grafik: Pine L1-L6 slot sirasi; kalinlik = cakisma (TV), volume yok."""
    tf = snap.timeframe
    main_s = float(active_support or 0)
    main_r = float(active_resistance or 0)
    lines = list(snap.chart_lines)
    price_counts: dict[float, int] = {}
    for sr in lines:
        pk = round(float(sr.price), 2)
        price_counts[pk] = price_counts.get(pk, 0) + 1

    out: list[dict] = []
    for sr in sorted(lines, key=lambda x: int(getattr(x, "slot", 0) or 0)):
        d = level_to_dict(sr, tf)
        p = float(d["price"])
        kind = str(d["kind"])
        pk = round(p, 2)
        overlap = max(1, int(price_counts.get(pk, 1)))
        d["line_width"] = float(sr.importance)  # Pine level_thicknesses
        d["sr_importance"] = int(sr.importance)
        d["primary"] = (kind == "support" and main_s > 0 and abs(p - main_s) < 0.05) or (
            kind == "resistance" and main_r > 0 and abs(p - main_r) < 0.05
        )
        out.append(d)
    return out


def calculate_sr_levels_from_bars(
    bars: list[dict],
    *,
    use_pivot: bool = True,
    use_poc: bool = False,
    current_price: Optional[float] = None,
    near_price_pct: Optional[float] = None,
    active_min_sep_pct: Optional[float] = None,
    importance_cap: bool = False,  # Pine'da yok; yok sayilir
    **kwargs,
) -> dict:
    df = bars_to_dataframe(bars)
    if df.empty:
        return {
            "support": [],
            "resistance": [],
            "active_support": None,
            "active_resistance": None,
            "all": [],
            "display_lines": [],
            "near_price_buffer": 0.0,
            "active_min_sep": 0.0,
        }
    kw = dict(kwargs)
    if near_price_pct is not None:
        kw["near_price_pct"] = near_price_pct
    if active_min_sep_pct is not None:
        kw["active_min_sep_pct"] = active_min_sep_pct
    return calculate_sr_levels(
        df,
        use_pivot=use_pivot,
        use_poc=use_poc,
        current_price=current_price,
        **kw,
    )
