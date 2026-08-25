"""
engine/zone_engine_v3.py — Bolge yasam dongusu (lifecycle).

NEW -> ACTIVE -> TRANSITION -> BROKEN -> ROLE_REVERSAL -> DECAY -> DELETE

Kirilim kalitesi: kapanis + displacement + hacim
Strength: touch (azalan marj) + rej + sweep - accept; * freshness * age
Likidite: liquidity_target_above / below
Trend: dinamik bias_strength (1h)
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from statistics import mean

from core.config import cfg
from core.logger import get_logger
from core.state import state
from engine.liquidity_map_v3 import attach_zone_bands, zone_half_width
from engine.v3_common import avg_body, bars_15m, bars_1h
from engine.zone_cluster_v3 import build_zone_clusters, cluster_blocks_immediate_chase
from engine.zone_liquidity_v3 import (
    detect_liquidity_pools,
    global_liquidity_bias,
    global_vacuum_score,
    liquidity_chase_blocks,
    score_zone_liquidity_targets,
)
from engine.zone_trend_v3 import multi_tf_trend

log = get_logger("ZoneEngineV3")

_PERSIST = Path(__file__).resolve().parent.parent / "data" / "v3_zones.json"
_last_status_log: dict[str, str] = {}

# lifecycle durumlari
LC_NEW = "NEW"
LC_ACTIVE = "ACTIVE"
LC_TRANSITION = "TRANSITION"
LC_BROKEN = "BROKEN"
LC_ROLE_REVERSAL = "ROLE_REVERSAL"
LC_DECAY = "DECAY"
LC_ARCHIVED = "ARCHIVED"

TRADEABLE_LIFECYCLE = frozenset({LC_ACTIVE, LC_ROLE_REVERSAL})
ARCHIVE_STRENGTH = 5


def _zone_id(center: float, role: str) -> str:
    return f"{role}:{round(center, 2)}"


def _empty_zone(
    center: float,
    role: str,
    *,
    low: float = 0.0,
    high: float = 0.0,
    tf: str = "15m",
    is_htf: bool = False,
) -> dict:
    half = (high - center) if high > center else zone_half_width(center)
    if low <= 0:
        low = center - half
    if high <= 0:
        high = center + half
    now = time.time()
    return {
        "id": _zone_id(center, role),
        "center": round(center, 2),
        "zone_low": round(low, 2),
        "zone_high": round(high, 2),
        "role": role,
        "original_role": role,
        "timeframe": tf,
        "is_htf": bool(is_htf),
        "touches": 0,
        "rejections": 0,
        "sweeps": 0,
        "acceptances": 0,
        "retest_rejections": 0,
        "strength": 50,
        "freshness": 1.0,
        "age_hours": 0.0,
        "liquidity_target_above": 0,
        "liquidity_target_below": 0,
        "liquidity_score": 0,
        "lifecycle": LC_NEW,
        "status": role,
        "acceptance_score": 0.0,
        "displacement_factor": 0.0,
        "volume_factor": 1.0,
        "consecutive_break": 0,
        "break_volume_avg": 0.0,
        "hours_below_zone": 0.0,
        "hours_above_zone": 0.0,
        "time_factor": 0.0,
        "memory_strength": 0,
        "cluster_id": "",
        "archived_ts": 0.0,
        "created_ts": now,
        "updated_ts": now,
        "last_update": now,
    }


def _touch_component(touches: int) -> int:
    """Cok dokunus = zayiflayan bolge — azalan marj."""
    t = int(touches or 0)
    w = int(getattr(cfg, "V3_ZONE_W_TOUCH", 5) or 5)
    cap = max(int(getattr(cfg, "V3_ZONE_TOUCH_CAP", 6) or 6), 3)
    if t <= cap:
        return t * w
    return cap * w + (t - cap) * max(w // 3, 1)


def _raw_strength(z: dict) -> int:
    r = int(z.get("rejections", 0) or 0)
    sw = int(z.get("sweeps", 0) or 0)
    acc = int(z.get("acceptances", 0) or 0)
    w_r = int(getattr(cfg, "V3_ZONE_W_REJECTION", 10) or 10)
    w_s = int(getattr(cfg, "V3_ZONE_W_SWEEP", 15) or 15)
    w_a = int(getattr(cfg, "V3_ZONE_W_ACCEPT", 20) or 20)
    return _touch_component(int(z.get("touches", 0) or 0)) + r * w_r + sw * w_s - acc * w_a


def _compute_age_hours(z: dict) -> float:
    now = time.time()
    created = float(z.get("created_ts", now) or now)
    age = max(0.0, (now - created) / 3600.0)
    z["age_hours"] = round(age, 2)
    z["last_update"] = now
    return age


def _compute_freshness(z: dict) -> float:
    age_h = _compute_age_hours(z)
    touches = int(z.get("touches", 0) or 0)

    if age_h < 1.0:
        age_f = 0.88
    elif age_h < 6.0:
        age_f = 1.0
    elif age_h < 48.0:
        age_f = 0.96
    elif age_h < 72.0:
        age_f = 0.78
    else:
        age_f = 0.58

    if touches <= 3:
        touch_f = 1.0
    elif touches <= 6:
        touch_f = 0.93
    elif touches <= 10:
        touch_f = 0.78
    else:
        touch_f = 0.60

    fresh = round(age_f * touch_f, 3)
    z["freshness"] = fresh
    return fresh


def _normalize_strength(z: dict) -> int:
    raw = _raw_strength(z)
    cap = max(int(getattr(cfg, "V3_ZONE_STRENGTH_CAP", 130) or 130), 40)
    sc = max(0, min(100, int(raw / cap * 100)))
    if z.get("is_htf"):
        sc = min(100, sc + 5)

    fresh = float(z.get("freshness", 1.0) or 1.0)
    sc = int(sc * fresh)

    age_h = float(z.get("age_hours", 0) or 0)
    age_boost = float(getattr(cfg, "V3_ZONE_AGE_BOOST_HOURS", 12) or 12)
    if age_h < age_boost:
        sc = min(100, int(sc * 1.05))
    elif age_h > 72:
        sc = max(0, int(sc * 0.92))

    z["strength"] = sc
    return sc


def _trend_bias() -> tuple[str, int]:
    mt = multi_tf_trend()
    state.v3_multi_tf_trend = mt
    direction = str(mt.get("direction") or "NEUTRAL").upper()
    score = int(mt.get("trend_score", 0) or 0)
    if direction in ("UP", "DOWN"):
        return direction, max(score, 10)
    return "NEUTRAL", score


def _apply_dynamic_trend_bias(z: dict, trend_dir: str, trend_score: int) -> None:
    """Dinamik carpani: trend_score 0-100."""
    role = str(z.get("role") or "")
    sc = int(z.get("strength", 0) or 0)
    ts = max(0, min(100, int(trend_score or 0)))
    down_mul = ts * float(getattr(cfg, "V3_ZONE_TREND_BIAS_DOWN", 0.002) or 0.002)
    up_mul = ts * float(getattr(cfg, "V3_ZONE_TREND_BIAS_UP", 0.0015) or 0.0015)

    if trend_dir == "DOWN":
        if role == "support":
            z["strength"] = max(0, int(sc * (1.0 - down_mul)))
        elif role == "resistance":
            z["strength"] = min(100, int(sc * (1.0 + up_mul)))
    elif trend_dir == "UP":
        if role == "support":
            z["strength"] = min(100, int(sc * (1.0 + up_mul)))
        elif role == "resistance":
            z["strength"] = max(0, int(sc * (1.0 - down_mul)))


def _hours_outside_level(bars15: list[dict], level: float, side: str) -> float:
    """Ardışık 15m kapanışlarin toplam saati (zone altinda/ustunde)."""
    n = _consecutive_closes_outside(bars15, level, side)
    bar_hours = 0.25
    return round(n * bar_hours, 2)


def _consecutive_closes_outside(bars15: list[dict], level: float, side: str) -> int:
    if not bars15 or level <= 0:
        return 0
    n = 0
    for bar in reversed(bars15):
        close = float(bar.get("close", 0) or 0)
        if close <= 0:
            break
        if side == "below" and close < level:
            n += 1
        elif side == "above" and close > level:
            n += 1
        else:
            break
    return n


def _bar_volume(bar: dict) -> float:
    return float(bar.get("volume", 0) or bar.get("v", 0) or 0)


def _break_quality(
    bars15: list[dict],
    brk: float,
    side: str,
    *,
    zone_center: float = 0.0,
) -> dict:
    """
    Kirilim kalitesi — sadece mum sayisi degil.

    acceptance_score = closes + volume_factor*0.5 + displacement_factor
    broken: (closes>=2 and disp>=1.5) OR closes>=3 OR score>=4.0
    """
    if not bars15 or brk <= 0:
        return {
            "closes": 0,
            "displacement_factor": 0.0,
            "volume_factor": 1.0,
            "acceptance_score": 0.0,
            "is_broken": False,
            "is_transition": False,
        }

    window = bars15[-6:]
    body = avg_body(bars15[-24:]) or avg_body(bars15) or 1.0
    vols_all = [_bar_volume(b) for b in bars15[-24:] if _bar_volume(b) > 0]
    avg_vol = mean(vols_all) if vols_all else 1.0

    closes_out = _consecutive_closes_outside(bars15, brk, side)
    displacement = 0.0
    break_vols: list[float] = []

    for bar in window:
        close = float(bar.get("close", 0) or 0)
        if side == "below" and close < brk:
            displacement += max(0.0, brk - close)
            v = _bar_volume(bar)
            if v > 0:
                break_vols.append(v)
        elif side == "above" and close > brk:
            displacement += max(0.0, close - brk)
            v = _bar_volume(bar)
            if v > 0:
                break_vols.append(v)

    disp_factor = displacement / max(body, 0.01)
    break_vol = mean(break_vols) if break_vols else 0.0
    vol_factor = min(2.5, break_vol / avg_vol) if avg_vol > 0 and break_vol > 0 else 1.0

    hours_out = _hours_outside_level(bars15, brk, side)
    time_w = float(getattr(cfg, "V3_ZONE_TIME_FACTOR_WEIGHT", 0.5) or 0.5)
    time_factor = hours_out * time_w
    acc_score = closes_out + vol_factor * 0.5 + disp_factor + time_factor
    min_closes = max(int(getattr(cfg, "V3_ZONE_BREAK_MIN_CLOSES", 2) or 2), 1)
    min_disp = float(getattr(cfg, "V3_ZONE_BREAK_MIN_DISP", 1.5) or 1.5)
    min_score = float(getattr(cfg, "V3_ZONE_BREAK_MIN_SCORE", 4.0) or 4.0)

    is_broken = (
        (closes_out >= min_closes and disp_factor >= min_disp)
        or closes_out >= max(int(getattr(cfg, "V3_ZONE_ACCEPT_BARS", 3) or 3), 3)
        or acc_score >= min_score
    )
    is_transition = not is_broken and (
        closes_out >= 1 or acc_score >= float(getattr(cfg, "V3_ZONE_TRANSITION_SCORE", 2.0) or 2.0)
    )

    return {
        "closes": closes_out,
        "displacement_factor": round(disp_factor, 3),
        "volume_factor": round(vol_factor, 3),
        "acceptance_score": round(acc_score, 3),
        "hours_outside": hours_out,
        "time_factor": round(time_factor, 3),
        "is_broken": is_broken,
        "is_transition": is_transition,
        "break_volume_avg": break_vol,
    }


def _structural_break_level(zone: dict, side: str, price: float) -> float:
    from engine.structure_thresholds import break_threshold_price

    c = float(zone.get("center", 0) or 0)
    if c <= 0:
        return 0.0
    if side == "below":
        return break_threshold_price(c, "SHORT", price)
    return break_threshold_price(c, "LONG", price)


def _bar_touches_zone(bar: dict, z: dict) -> bool:
    low = float(bar.get("low", 0) or 0)
    high = float(bar.get("high", 0) or 0)
    zl = float(z.get("zone_low", 0) or 0)
    zh = float(z.get("zone_high", 0) or 0)
    return high >= zl and low <= zh


def _update_zone_on_bar(z: dict, bar: dict, prev_bar: dict | None, price: float) -> None:
    if not bar:
        return
    role = str(z.get("role") or z.get("original_role") or "support")
    center = float(z.get("center", 0) or 0)
    if center <= 0:
        return
    low_b = float(bar.get("low", 0) or 0)
    high = float(bar.get("high", 0) or 0)
    close = float(bar.get("close", 0) or 0)
    if close <= 0:
        return
    tol = max(zone_half_width(center) * 0.35, center * 0.0001, 0.3)
    touched = _bar_touches_zone(bar, z)
    brk_below = _structural_break_level(z, "below", price)
    brk_above = _structural_break_level(z, "above", price)

    if role == "support" and brk_below > 0 and close < brk_below:
        z["acceptances"] = int(z.get("acceptances", 0) or 0) + 1
    elif role == "resistance" and brk_above > 0 and close > brk_above:
        z["acceptances"] = int(z.get("acceptances", 0) or 0) + 1

    if role == "support":
        if low_b <= float(z.get("zone_high", center)) + tol:
            z["touches"] = int(z.get("touches", 0) or 0) + 1
        if touched and close >= center - tol * 0.25:
            z["rejections"] = int(z.get("rejections", 0) or 0) + 1
        if low_b < center - tol and close >= center - tol * 0.25:
            z["sweeps"] = int(z.get("sweeps", 0) or 0) + 1
    else:
        if high >= float(z.get("zone_low", center)) - tol:
            z["touches"] = int(z.get("touches", 0) or 0) + 1
        if touched and close <= center + tol * 0.25:
            z["rejections"] = int(z.get("rejections", 0) or 0) + 1
        if high > center + tol and close <= center + tol * 0.25:
            z["sweeps"] = int(z.get("sweeps", 0) or 0) + 1

    z["updated_ts"] = time.time()


def _count_retest_rejections(
    z: dict, bars15: list[dict], *, lookback: int = 5
) -> int:
    """Kirilmis bolgede retest red mumlari."""
    center = float(z.get("center", 0) or 0)
    if center <= 0 or not bars15:
        return 0
    orig = str(z.get("original_role") or z.get("role") or "support")
    zl = float(z.get("zone_low", 0) or 0)
    zh = float(z.get("zone_high", 0) or 0)
    tol = max(zone_half_width(center) * 0.3, 0.25)
    n = 0
    for bar in bars15[-lookback:]:
        close = float(bar.get("close", 0) or 0)
        high = float(bar.get("high", 0) or 0)
        low_b = float(bar.get("low", 0) or 0)
        if not (zl <= close <= zh or (low_b <= zh and high >= zl)):
            continue
        if orig == "support" and high >= center and close < center - tol * 0.2:
            n += 1
        elif orig == "resistance" and low_b <= center and close > center + tol * 0.2:
            n += 1
    return n


def _retest_volume_declining(z: dict, bars15: list[dict]) -> bool:
    """Retest hacmi kirilim hacminden dusuk mu."""
    brk_vol = float(z.get("break_volume_avg", 0) or 0)
    if brk_vol <= 0 or len(bars15) < 4:
        return True
    retest_vols = [_bar_volume(b) for b in bars15[-4:] if _bar_volume(b) > 0]
    if not retest_vols:
        return True
    retest_avg = mean(retest_vols)
    ratio = float(getattr(cfg, "V3_ZONE_RETEST_VOL_RATIO", 0.85) or 0.85)
    return retest_avg < brk_vol * ratio


def _try_role_reversal(z: dict, bars15: list[dict]) -> bool:
    """BROKEN + >=2 retest red + hacim dususu -> ROLE_REVERSAL."""
    if str(z.get("lifecycle") or "") != LC_BROKEN:
        return False
    need_rej = max(int(getattr(cfg, "V3_ZONE_RETEST_REJECTIONS", 2) or 2), 2)
    rej = _count_retest_rejections(z, bars15)
    z["retest_rejections"] = rej
    if rej < need_rej:
        return False
    if not _retest_volume_declining(z, bars15):
        return False

    orig = str(z.get("original_role") or "support")
    if orig == "support":
        z["role"] = "resistance"
        z["status"] = "resistance"
    else:
        z["role"] = "support"
        z["status"] = "support"
    z["lifecycle"] = LC_ROLE_REVERSAL
    return True


def _recompute_lifecycle(z: dict, bars15: list[dict], price: float) -> None:
    role = str(z.get("role") or "support")
    center = float(z.get("center", 0) or 0)
    prev_lc = str(z.get("lifecycle") or LC_NEW)
    touches = int(z.get("touches", 0) or 0)
    rejections = int(z.get("rejections", 0) or 0)
    strength = int(z.get("strength", 0) or 0)

    if role == "support":
        brk = _structural_break_level(z, "below", price)
        bq = _break_quality(bars15, brk, "below", zone_center=center)
    else:
        brk = _structural_break_level(z, "above", price)
        bq = _break_quality(bars15, brk, "above", zone_center=center)

    z["consecutive_break"] = int(bq["closes"])
    z["acceptance_score"] = float(bq["acceptance_score"])
    z["displacement_factor"] = float(bq["displacement_factor"])
    z["volume_factor"] = float(bq["volume_factor"])
    z["time_factor"] = float(bq.get("time_factor", 0) or 0)
    if role == "support":
        z["hours_below_zone"] = float(bq.get("hours_outside", 0) or 0)
    else:
        z["hours_above_zone"] = float(bq.get("hours_outside", 0) or 0)

    if bq.get("is_broken"):
        if float(bq.get("break_volume_avg", 0) or 0) > 0:
            z["break_volume_avg"] = float(bq["break_volume_avg"])
        z["lifecycle"] = LC_BROKEN
        z["status"] = "broken"
        if _try_role_reversal(z, bars15):
            pass
    elif bq.get("is_transition"):
        z["lifecycle"] = LC_TRANSITION
        z["status"] = "transition"
    elif prev_lc == LC_BROKEN:
        if _try_role_reversal(z, bars15):
            pass
        else:
            z["lifecycle"] = LC_BROKEN
            z["status"] = "broken"
    elif prev_lc == LC_ROLE_REVERSAL:
        z["lifecycle"] = LC_ROLE_REVERSAL
        z["status"] = str(z.get("role") or role)
    elif touches >= 2 or rejections >= 1:
        z["lifecycle"] = LC_ACTIVE
        z["status"] = str(z.get("role") or role)
    else:
        z["lifecycle"] = LC_NEW
        z["status"] = str(z.get("role") or role)

    min_st = max(int(getattr(cfg, "V3_ZONE_MIN_STRENGTH", 40) or 40), 0)
    decay_below = max(min_st + 15, 55)
    age_h = float(z.get("age_hours", 0) or 0)
    if (
        strength < decay_below
        and age_h > 48
        and z["lifecycle"] in (LC_ACTIVE, LC_ROLE_REVERSAL, LC_NEW)
    ):
        z["lifecycle"] = LC_DECAY

    if prev_lc != z.get("lifecycle"):
        key = str(z.get("id", ""))
        msg = (
            f"[ZONE] {center:.2f} {prev_lc} -> {z.get('lifecycle')} "
            f"(role={z.get('role')} acc_sc={z.get('acceptance_score')} "
            f"disp={z.get('displacement_factor')} str={z.get('strength')} "
            f"fresh={z.get('freshness')})"
        )
        if _last_status_log.get(key) != msg:
            _last_status_log[key] = msg
            log.info(msg)


def _apply_liquidity_scores(z: dict, pools: list[dict], price: float) -> None:
    above, below, liq = score_zone_liquidity_targets(
        float(z.get("center", 0) or 0), pools, price
    )
    z["liquidity_target_above"] = above
    z["liquidity_target_below"] = below
    z["liquidity_score"] = liq


def _find_near_zone(
    zones: list[dict],
    center: float,
    role: str,
    *,
    include_archived: bool = False,
) -> dict | None:
    if center <= 0:
        return None
    tol = max(zone_half_width(center), center * 0.0015, 0.8)
    for z in zones:
        if str(z.get("role") or "") != role:
            continue
        lc = str(z.get("lifecycle") or "")
        if lc == LC_ARCHIVED and not include_archived:
            continue
        if abs(float(z.get("center", 0) or 0) - center) <= tol:
            return z
    return None


def _migrate_zone_fields(z: dict) -> dict:
    """Eski persist formatini yeni alanlara tasi."""
    if "lifecycle" not in z:
        st = str(z.get("status") or z.get("role") or "support")
        if st == "broken":
            z["lifecycle"] = LC_BROKEN
        elif st == "transition":
            z["lifecycle"] = LC_TRANSITION
        elif st in ("support", "resistance"):
            z["lifecycle"] = LC_ACTIVE
        else:
            z["lifecycle"] = LC_NEW
    for key, default in (
        ("freshness", 1.0),
        ("age_hours", 0.0),
        ("liquidity_target_above", 0),
        ("liquidity_target_below", 0),
        ("liquidity_score", 0),
        ("retest_rejections", 0),
        ("acceptance_score", 0.0),
        ("time_factor", 0.0),
        ("hours_below_zone", 0.0),
        ("hours_above_zone", 0.0),
        ("memory_strength", 0),
        ("cluster_id", ""),
        ("archived_ts", 0.0),
    ):
        z.setdefault(key, default)
    if str(z.get("lifecycle") or "") == LC_DECAY:
        z["lifecycle"] = LC_ARCHIVED
    return z


def _birth_from_level(zones: list[dict], level: dict, bars15: list[dict]) -> None:
    px = float(level.get("price", 0) or 0)
    kind = str(level.get("kind") or "")
    if px <= 0 or kind not in ("support", "resistance"):
        return
    archived = _find_near_zone(zones, px, kind, include_archived=True)
    if archived and str(archived.get("lifecycle") or "") == LC_ARCHIVED:
        return
    if _find_near_zone(zones, px, kind):
        return
    attach_zone_bands(level, bars15)
    z = _empty_zone(
        px,
        kind,
        low=float(level.get("zone_low", 0) or 0),
        high=float(level.get("zone_high", 0) or 0),
        tf=str(level.get("timeframe") or "15m"),
        is_htf=bool(level.get("is_htf")),
    )
    z["touches"] = max(int(level.get("touch_count", 0) or 0), 1)
    z["rejections"] = max(int(level.get("failed_break_count", 0) or 0), 0)
    if z["touches"] >= 2 or z["rejections"] >= 1:
        z["lifecycle"] = LC_ACTIVE
    log.info(
        f"[ZONE] dogdu {kind} {px:.2f} [{z['zone_low']:.2f}-{z['zone_high']:.2f}] "
        f"lifecycle={z['lifecycle']} tf={z.get('timeframe')}"
    )
    zones.append(z)


def _archive_zone(z: dict, reason: str = "") -> None:
    """Silme — hafizaya al (strength=5). Piyasa unutmaz."""
    prev = int(z.get("strength", 0) or 0)
    z["memory_strength"] = max(prev, int(z.get("memory_strength", 0) or 0))
    z["lifecycle"] = LC_ARCHIVED
    z["status"] = "archived"
    z["strength"] = ARCHIVE_STRENGTH
    z["archived_ts"] = time.time()
    z["updated_ts"] = time.time()
    if reason:
        log.debug(f"[ZONE] arsiv {z.get('center')} — {reason}")


def _process_zone_memory(zones: list[dict]) -> list[dict]:
    """Zone silinmez; yalnizca eski/kullanilmayanlar ARCHIVED (hafiza)."""
    max_archive_age = max(
        int(getattr(cfg, "V3_ZONE_ARCHIVE_MAX_AGE_SEC", 15 * 24 * 3600) or 0),
        86400,
    )
    min_age_archive = max(
        float(getattr(cfg, "V3_ZONE_ARCHIVE_MIN_AGE_HOURS", 6) or 6), 1.0
    )
    now = time.time()
    for z in zones:
        lc = str(z.get("lifecycle") or "")
        if lc == LC_ARCHIVED:
            continue
        age_h = float(z.get("age_hours", 0) or 0)
        age_sec = now - float(z.get("created_ts", now) or now)
        # ACTIVE/NEW hemen arsivleme — dusuk strength tek basina yetmez
        if lc == LC_DECAY and age_h >= min_age_archive:
            _archive_zone(z, "decay")
        elif age_sec > max_archive_age and lc in (LC_BROKEN, LC_DECAY):
            _archive_zone(z, f"age>{max_archive_age // 86400}d")
    return zones


def _reactivate_archived_zones(
    zones: list[dict], price: float, bars15: list[dict]
) -> None:
    """Fiyat eski bolgeye donunce hafiza canlanir (1965, 15 gun sonra)."""
    px = float(price or 0)
    if px <= 0:
        return
    reactivate_dist = float(getattr(cfg, "V3_ZONE_MEMORY_REACTIVATE_PCT", 0.004) or 0.004)
    boost = int(getattr(cfg, "V3_ZONE_MEMORY_REACTIVATE_STRENGTH", 35) or 35)
    for z in zones:
        if str(z.get("lifecycle") or "") != LC_ARCHIVED:
            continue
        zl = float(z.get("zone_low", 0) or 0)
        zh = float(z.get("zone_high", 0) or 0)
        center = float(z.get("center", 0) or 0)
        if center <= 0:
            continue
        band_tol = max(center * reactivate_dist, zone_half_width(center))
        in_band = zl - band_tol <= px <= zh + band_tol
        touched = False
        if bars15:
            last = bars15[-1]
            touched = _bar_touches_zone(last, z) if last else False
        if not in_band and not touched:
            continue
        mem = int(z.get("memory_strength", 0) or 0)
        z["lifecycle"] = LC_TRANSITION
        z["status"] = "transition"
        z["strength"] = max(boost, min(mem // 2, 55), ARCHIVE_STRENGTH + 5)
        z["freshness"] = 0.85
        z["updated_ts"] = time.time()
        log.info(
            f"[ZONE] hafiza canlandi {center:.2f} role={z.get('role')} "
            f"st={z['strength']} mem={mem} px={px:.2f}"
        )


def _save_persist(zones: list[dict]) -> None:
    try:
        _PERSIST.parent.mkdir(parents=True, exist_ok=True)
        _PERSIST.write_text(
            json.dumps({"zones": zones, "ts": time.time()}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        log.debug(f"[ZONE] persist yazilamadi: {e}")


def _load_persist() -> list[dict]:
    if not _PERSIST.exists():
        return []
    try:
        data = json.loads(_PERSIST.read_text(encoding="utf-8"))
        return [_migrate_zone_fields(z) for z in list(data.get("zones") or [])]
    except Exception:
        return []


def get_zones() -> list[dict]:
    raw = list(state.v3_zones or _load_persist())
    return [_migrate_zone_fields(z) for z in raw]


def filter_tradeable_levels(
    levels: list[dict], zones: list[dict] | None = None
) -> list[dict]:
    zones = list(zones or get_zones())
    if not zones:
        return list(levels)
    kept: list[dict] = []
    for lvl in levels:
        px = float(lvl.get("price", 0) or 0)
        kind = str(lvl.get("kind") or "")
        if px <= 0 or kind not in ("support", "resistance"):
            kept.append(lvl)
            continue
        z = _find_near_zone(zones, px, kind)
        if not z:
            kept.append(lvl)
            continue
        lc = str(z.get("lifecycle") or "")
        role = str(z.get("role") or kind)
        if lc in (LC_BROKEN, LC_TRANSITION, LC_DECAY, LC_ARCHIVED) and role == kind:
            continue
        kept.append(lvl)
    return kept


def tick_zone_lifecycle(
    merged_levels: list[dict],
    price: float,
    bars15: list[dict] | None = None,
) -> list[dict]:
    px = float(price or 0)
    if px <= 0:
        return []
    bars = list(bars15) if bars15 else bars_15m(80)
    if len(bars) < 3:
        return get_zones()

    pools = detect_liquidity_pools(px, bars, bars_1h(168))
    state.v3_liquidity_pools = pools
    state.v3_liquidity_bias = global_liquidity_bias(pools, px)
    state.v3_vacuum_score = global_vacuum_score(px, bars)

    zones = [_migrate_zone_fields(z) for z in (_load_persist() if not state.v3_zones else list(state.v3_zones))]
    for lvl in merged_levels:
        _birth_from_level(zones, lvl, bars)

    closed = bars[:-1] if len(bars) > 1 else bars
    if not closed:
        return zones
    last_closed = closed[-1]
    bar_key = str(last_closed.get("close_time") or last_closed.get("t") or len(closed))
    new_bar = bar_key != str(getattr(state, "v3_zone_last_bar_key", "") or "")
    if new_bar:
        state.v3_zone_last_bar_key = bar_key

    trend_dir, trend_score = _trend_bias()
    for z in zones:
        if new_bar:
            prev_bar = closed[-2] if len(closed) >= 2 else None
            _update_zone_on_bar(z, last_closed, prev_bar, px)
        _compute_freshness(z)
        _normalize_strength(z)
        _apply_dynamic_trend_bias(z, trend_dir, trend_score)
        _recompute_lifecycle(z, closed, px)
        _apply_liquidity_scores(z, pools, px)

    _reactivate_archived_zones(zones, px, closed)
    zones = _process_zone_memory(zones)
    clusters = build_zone_clusters(zones, px)
    for cl in clusters:
        for z in zones:
            cid = str(z.get("id", ""))
            if cid and cid in (cl.get("members") or []):
                z["cluster_id"] = str(cl.get("id", ""))
    state.v3_zone_clusters = clusters
    state.v3_zones = zones
    _save_persist(zones)
    return zones


def _zone_to_level_dict(z: dict) -> dict:
    lc = str(z.get("lifecycle") or LC_ACTIVE)
    st = str(z.get("status") or z.get("role") or "support")
    if lc == LC_TRANSITION:
        st = "transition"
    elif lc == LC_BROKEN:
        st = "broken"
    elif lc in (LC_ACTIVE, LC_ROLE_REVERSAL, LC_NEW):
        st = str(z.get("role") or "support")

    strength_label = "STRONG" if int(z.get("strength", 0) or 0) >= 70 else "MEDIUM"
    if int(z.get("strength", 0) or 0) < 45:
        strength_label = "WEAK"
    return {
        "price": float(z.get("center", 0) or 0),
        "zone_low": float(z.get("zone_low", 0) or 0),
        "zone_high": float(z.get("zone_high", 0) or 0),
        "kind": str(z.get("role") or "support"),
        "zone_status": st,
        "lifecycle": lc,
        "timeframe": str(z.get("timeframe") or "15m"),
        "is_htf": bool(z.get("is_htf")),
        "touch_count": int(z.get("touches", 0) or 0),
        "failed_break_count": int(z.get("rejections", 0) or 0),
        "acceptance_bars": int(z.get("acceptances", 0) or 0),
        "acceptance_score": float(z.get("acceptance_score", 0) or 0),
        "freshness": float(z.get("freshness", 1) or 1),
        "age_hours": float(z.get("age_hours", 0) or 0),
        "liquidity_target_above": int(z.get("liquidity_target_above", 0) or 0),
        "liquidity_target_below": int(z.get("liquidity_target_below", 0) or 0),
        "liquidity_score": int(z.get("liquidity_score", 0) or 0),
        "score": int(z.get("strength", 0) or 0) // 6,
        "strength": strength_label,
        "lifecycle_strength": int(z.get("strength", 0) or 0),
    }


def _recent_swing_support(bars15: list[dict], price: float) -> dict | None:
    """Kirilgan piyasada: son N mumun anlamli dibi (24 mum min degil)."""
    n = max(int(getattr(cfg, "V3_SWING_SUPPORT_BARS", 8) or 8), 4)
    recent = list(bars15 or [])[-n:]
    if not recent or price <= 0:
        return None
    lows = [float(b.get("low", 0) or 0) for b in recent if float(b.get("low", 0) or 0) > 0]
    if not lows:
        return None
    s_price = min(lows)
    if s_price >= price:
        return None
    half = zone_half_width(s_price, recent)
    return {
        "price": round(s_price, 2),
        "zone_low": round(s_price - half, 2),
        "zone_high": round(s_price + half, 2),
        "kind": "support",
        "zone_status": "support",
        "lifecycle": LC_ACTIVE,
        "timeframe": "15m",
        "is_htf": False,
        "touch_count": 0,
        "failed_break_count": 0,
        "score": 6,
        "strength": "MEDIUM",
        "lifecycle_strength": 55,
        "is_swing_support": True,
    }


def _zone_pick_weight(z: dict) -> float:
    st = int(z.get("strength", 0) or 0)
    fresh = float(z.get("freshness", 1) or 1)
    lc = str(z.get("lifecycle") or "")
    lc_bonus = {
        LC_ACTIVE: 100,
        LC_ROLE_REVERSAL: 90,
        LC_TRANSITION: 70,
        LC_BROKEN: 40,
        LC_NEW: 50,
        LC_ARCHIVED: 10,
    }.get(lc, 0)
    return lc_bonus + st * fresh


def _pick_zone_side(
    zones: list[dict],
    price: float,
    role: str,
    *,
    above: bool,
) -> dict | None:
    """ACTIVE > ROLE_REVERSAL > TRANSITION > BROKEN (yapisal harita)."""
    px = float(price or 0)
    if px <= 0:
        return None
    order = (LC_ACTIVE, LC_ROLE_REVERSAL, LC_TRANSITION, LC_BROKEN, LC_NEW, LC_ARCHIVED)
    candidates = [
        z
        for z in zones
        if str(z.get("role") or "") == role
        and (
            (above and float(z.get("center", 0) or 0) > px)
            or (not above and float(z.get("center", 0) or 0) < px)
        )
    ]
    if not candidates:
        return None

    for lc_pref in order:
        pool = [z for z in candidates if str(z.get("lifecycle") or "") == lc_pref]
        if not pool:
            continue
        if above:
            return min(pool, key=lambda z: (float(z.get("center", 0) or 0), -_zone_pick_weight(z)))
        return max(pool, key=lambda z: (_zone_pick_weight(z), float(z.get("center", 0) or 0)))
    return None


def _pick_structural_support(
    zones: list[dict], price: float, resistance_center: float
) -> dict | None:
    """
    Bant destegi: fiyat altindaki en yuksek bolge; yoksa kirilan en yakin raf (1970 gibi).
    center < price filtresi tek basina yetmez — fiyat destegin altina inince swing dibe dusmesin.
    """
    px = float(price or 0)
    rc = float(resistance_center or 0)
    if px <= 0 or rc <= px:
        return None
    order = (LC_ACTIVE, LC_ROLE_REVERSAL, LC_TRANSITION, LC_BROKEN, LC_NEW)
    supports = [
        z
        for z in zones
        if str(z.get("role") or "") == "support"
        and str(z.get("lifecycle") or "") != LC_ARCHIVED
        and float(z.get("center", 0) or 0) < rc
    ]
    if not supports:
        return None

    below = [z for z in supports if float(z.get("center", 0) or 0) < px]
    if below:
        for lc_pref in order:
            pool = [z for z in below if str(z.get("lifecycle") or "") == lc_pref]
            if pool:
                return max(
                    pool,
                    key=lambda z: (_zone_pick_weight(z), float(z.get("center", 0) or 0)),
                )

    above = [z for z in supports if float(z.get("center", 0) or 0) >= px]
    if not above:
        return None
    for lc_pref in order:
        pool = [z for z in above if str(z.get("lifecycle") or "") == lc_pref]
        if pool:
            return min(pool, key=lambda z: (float(z.get("center", 0) or 0), -_zone_pick_weight(z)))
    return min(above, key=lambda z: float(z.get("center", 0) or 0))


def _lifecycle_band_price_valid(px: float, sup_out: dict, res_out: dict) -> bool:
    s = float(sup_out.get("price") or 0)
    r = float(res_out.get("price") or 0)
    if s >= r or r <= 0 or px <= 0:
        return False
    if s < px < r:
        return True
    if px >= r:
        return False
    zst = str(sup_out.get("zone_status") or "").lower()
    lc = str(sup_out.get("lifecycle") or "")
    zlo = float(sup_out.get("zone_low") or 0)
    if s >= px and (
        sup_out.get("structural_shelf")
        or zst in ("broken", "transition")
        or lc in (LC_BROKEN, LC_TRANSITION)
        or (zlo > 0 and px < zlo)
    ):
        return True
    return False


def pick_active_from_zones(
    price: float,
    zones: list[dict] | None = None,
    bars15: list[dict] | None = None,
) -> tuple[dict | None, dict | None]:
    """
    Lifecycle bant: destek/direnc ayri secilir.
    Tum destekler kirilgan olsa bile yapisal direnc + guncel dip kullanilir.
    """
    px = float(price or 0)
    zones = list(zones or get_zones())
    if px <= 0:
        return None, None

    bars = list(bars15) if bars15 else bars_15m(40)
    resistance_z = _pick_zone_side(zones, px, "resistance", above=True)
    if not resistance_z:
        return None, None

    res_center = float(resistance_z.get("center", 0) or 0)
    support_z = _pick_structural_support(zones, px, res_center)

    sup_out: dict | None = None
    if support_z:
        sup_out = _zone_to_level_dict(support_z)
        if float(support_z.get("center", 0) or 0) >= px:
            sup_out["structural_shelf"] = True
    else:
        swing = _recent_swing_support(bars, px)
        if swing:
            sup_out = swing
        else:
            return None, None

    res_out = _zone_to_level_dict(resistance_z)
    if float(sup_out.get("price", 0) or 0) >= float(res_out.get("price", 0) or 0):
        return None, None
    if not _lifecycle_band_price_valid(px, sup_out, res_out):
        return None, None
    return sup_out, res_out


def liquidity_blocks_chase(side: str, price: float = 0) -> tuple[bool, str]:
    px = float(price or 0)
    pools = list(getattr(state, "v3_liquidity_pools", None) or [])
    if not pools and px > 0:
        pools = detect_liquidity_pools(px)
    blocked, msg = liquidity_chase_blocks(side, px, pools)
    if blocked:
        return blocked, msg
    clusters = list(getattr(state, "v3_zone_clusters", None) or [])
    zones = get_zones()
    broken_centers = [
        float(z.get("center", 0) or 0)
        for z in zones
        if str(z.get("lifecycle") or "") == LC_BROKEN
    ]
    for bc in broken_centers:
        blk, cmsg = cluster_blocks_immediate_chase(side, bc, clusters, px)
        if blk:
            return blk, cmsg
    return False, ""


def get_archived_zones_near(price: float, limit: int = 5) -> list[dict]:
    px = float(price or 0)
    archived = [z for z in get_zones() if str(z.get("lifecycle") or "") == LC_ARCHIVED]
    archived.sort(key=lambda z: abs(float(z.get("center", 0) or 0) - px))
    return archived[:limit]


def zones_snapshot(price: float = 0) -> dict:
    zones = get_zones()
    px = float(price or 0)
    near = sorted(zones, key=lambda z: abs(float(z.get("center", 0) or 0) - px))[:8]
    bias = getattr(state, "v3_liquidity_bias", None) or {}
    archived_n = sum(1 for z in zones if str(z.get("lifecycle") or "") == LC_ARCHIVED)
    return {
        "count": len(zones),
        "archived_count": archived_n,
        "vacuum_score": int(getattr(state, "v3_vacuum_score", 0) or 0),
        "liquidity_bias": bias,
        "multi_tf_trend": getattr(state, "v3_multi_tf_trend", None) or {},
        "clusters": len(getattr(state, "v3_zone_clusters", None) or []),
        "near": [
            {
                "center": z.get("center"),
                "low": z.get("zone_low"),
                "high": z.get("zone_high"),
                "lifecycle": z.get("lifecycle"),
                "role": z.get("role"),
                "strength": z.get("strength"),
                "freshness": z.get("freshness"),
                "age_hours": z.get("age_hours"),
                "acceptance_score": z.get("acceptance_score"),
                "liq_above": z.get("liquidity_target_above"),
                "liq_below": z.get("liquidity_target_below"),
            }
            for z in near
        ],
    }
