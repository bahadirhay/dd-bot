"""
engine/execution_brain_v3.py — EXECUTION FILTER (karar vermez).

Yalnizca: urgency (NOW/LATER/SKIP), execution_quality, time_validity.
COLLAPSE tek otorite; bu katman sadece zaman + giris kalitesi.
"""
from __future__ import annotations

import math
import time

from core.config import cfg
from core.logger import get_logger
from core.state import state
from engine.v3_common import avg_body, bars_15m

log = get_logger("ExecFilter")

_BAR_SEC = 15 * 60


def _volatility_factor(bars15: list[dict], price: float) -> float:
    if not bars15 or price <= 0:
        return 1.0
    recent = bars15[-12:]
    ranges = []
    for b in recent:
        h = float(b.get("high", 0) or 0)
        l = float(b.get("low", 0) or 0)
        if h > l > 0:
            ranges.append((h - l) / price * 100.0)
    if not ranges:
        return 1.0
    avg_rng = sum(ranges) / len(ranges)
    body = avg_body(recent) or 1.0
    body_pct = body / price * 100.0
    if avg_rng > body_pct * 2.5:
        return 1.35
    if avg_rng < body_pct * 0.8:
        return 0.75
    return 1.0


def _best_commitment_event(events: dict) -> dict | None:
    decay = events.get("decay") or {}
    for key in ("best_sweep_low", "best_sweep_high", "best_break", "best_compression"):
        ev = decay.get(key)
        if ev and not ev.get("stale"):
            return ev
    latest = events.get("latest") or []
    return latest[-1] if latest else None


def _event_fingerprint(ev: dict) -> str:
    return f"{ev.get('type')}@{round(float(ev.get('price', 0) or 0), 2)}"


def _window_bars(event_strength: float, vol_f: float) -> int:
    scale = float(getattr(cfg, "V3_EXEC_WINDOW_SCALE", 0.06) or 0.06)
    raw = event_strength * vol_f * scale
    min_b = int(getattr(cfg, "V3_EXEC_WINDOW_MIN_BARS", 1) or 1)
    max_b = int(getattr(cfg, "V3_EXEC_WINDOW_MAX_BARS", 5) or 5)
    return max(min_b, min(max_b, int(round(raw)) or min_b))


def opportunity_half_life_minutes(
    decayed_strength: float,
    base_strength: float | None = None,
) -> float:
    base = float(base_strength or decayed_strength or 50)
    floor = float(getattr(cfg, "V3_EVENT_MIN_DECAYED_STRENGTH", 22.0) or 22.0)
    if base <= floor or decayed_strength <= floor:
        return 0.0
    rate = float(getattr(cfg, "V3_EVENT_DECAY_RATE", 0.85) or 0.85)
    if rate <= 0:
        return 999.0
    hours = math.log(max(base / floor, 1.01)) / rate
    remaining = hours * 60.0
    if decayed_strength > floor and base > 0:
        cur_hours = -math.log(max(decayed_strength / base, 0.01)) / rate
        remaining = max(0.0, hours * 60.0 - cur_hours * 60.0)
    return round(remaining, 1)


def update_execution_anchor(
    events: dict,
    price: float = 0,
    bars15: list[dict] | None = None,
) -> dict:
    px = float(price or 0)
    bars = list(bars15 or bars_15m(24))
    vol_f = _volatility_factor(bars, px)
    event_strength = float(events.get("decayed_score", 0) or 0)
    min_ev = float(getattr(cfg, "V3_EXEC_WINDOW_MIN_EVENT", 35.0) or 35.0)
    best = _best_commitment_event(events)
    now = time.time()
    prev = dict(getattr(state, "v3_exec_anchor", None) or {})
    window_bars = _window_bars(event_strength, vol_f)

    if not best or event_strength < min_ev:
        if prev and now < float(prev.get("expires_ts", 0) or 0):
            prev["stale_event"] = True
            return prev
        state.v3_exec_anchor = {}
        return {"valid": False, "reason": "event zayif", "event_strength": round(event_strength, 1)}

    fp = _event_fingerprint(best)
    anchor_ts = float(best.get("ts", 0) or 0) or now
    expires_ts = anchor_ts + window_bars * _BAR_SEC

    renew = (
        not prev
        or now >= float(prev.get("expires_ts", 0) or 0)
        or fp != str(prev.get("fingerprint", ""))
        or event_strength > float(prev.get("event_strength", 0) or 0) + 8
    )
    if renew:
        anchor = {
            "valid": True,
            "fingerprint": fp,
            "event_type": str(best.get("type") or ""),
            "event_price": float(best.get("price", 0) or 0),
            "anchor_ts": anchor_ts,
            "expires_ts": expires_ts,
            "window_bars": window_bars,
            "event_strength": round(event_strength, 1),
            "decayed_strength": float(best.get("decayed_strength", 0) or event_strength),
            "volatility_factor": round(vol_f, 2),
        }
    else:
        anchor = dict(prev)
        anchor["event_strength"] = round(event_strength, 1)

    elapsed = max(0.0, now - float(anchor.get("anchor_ts", anchor_ts)))
    total_sec = max(1.0, float(anchor.get("expires_ts", expires_ts)) - float(anchor.get("anchor_ts", anchor_ts)))
    anchor["remaining_sec"] = round(max(0.0, float(anchor.get("expires_ts", expires_ts)) - now), 0)
    anchor["remaining_pct"] = round(max(0.0, 1.0 - elapsed / total_sec) * 100, 1)
    anchor["open"] = now < float(anchor.get("expires_ts", 0) or 0)
    anchor["half_life_min"] = opportunity_half_life_minutes(
        float(anchor.get("decayed_strength", event_strength)),
        float(best.get("base_strength", 0) or event_strength),
    )
    state.v3_exec_anchor = anchor
    return anchor


def _liquidity_quality_factor(liquidity: dict | None) -> float:
    """0.5–1.2 çarpan (düşük kalite = kısa ömür)."""
    liq = liquidity or {}
    avg_q = int(liq.get("avg_quality", 0) or 0)
    if avg_q <= 0:
        pools = liq.get("pools") or []
        qs = [int(p.get("quality_score", 0) or 0) for p in pools if p.get("quality_score")]
        avg_q = int(sum(qs) / len(qs)) if qs else 50
    if avg_q >= 65:
        return 1.15
    if avg_q >= 40:
        return 1.0
    return 0.72


def _lifetime_thresholds() -> tuple[float, float, float, float]:
    """
    INSTANT ≥ READY+ ≥ READY- ≥ WATCH
    READY- (58-61): kirilgan — trade yok, monitor
    READY+ (62-69): trend continuation — PASS
    """
    instant = float(getattr(cfg, "V3_LIFETIME_INSTANT", 70) or 70)
    ready_plus = float(
        getattr(cfg, "V3_LIFETIME_READY_PLUS", None)
        or getattr(cfg, "V3_LIFETIME_READY", 62)
        or 62
    )
    ready_minus = float(getattr(cfg, "V3_LIFETIME_READY_MINUS", 58) or 58)
    watch = float(getattr(cfg, "V3_LIFETIME_WATCH", 48) or 48)
    ready_plus = min(ready_plus, instant - 1)
    ready_minus = min(ready_minus, ready_plus - 1)
    watch = min(watch, ready_minus - 1)
    return instant, ready_plus, ready_minus, watch


def compute_opportunity_lifetime(
    anchor: dict,
    events: dict,
    liquidity: dict | None = None,
    bars15: list[dict] | None = None,
    price: float = 0,
) -> dict:
    """
    Surekli setup_strength + ayrık bantlar:

    INSTANT  (≥70)     → NOW + PASS
    READY+   (62-69)   → LATER + PASS (continuation)
    READY-   (58-61)   → BLOCK + observable (kirilgan)
    WATCH    (48-57)   → BLOCK + observable (erken reversal izle)
    EXPIRED  (<48)     → SKIP + BLOCK
    """
    instant_thr, ready_plus_thr, ready_minus_thr, watch_thr = _lifetime_thresholds()

    if not anchor.get("valid"):
        return {
            "half_life_score": 0,
            "effective_lifetime": 0,
            "setup_strength": 0,
            "trade_probability": 0.0,
            "tier": "NONE",
            "pass_gate": True,
            "observable": False,
            "event_monitor": False,
            "max_trade_min": 0,
            "now_window_min": 0,
            "reason": "event yok — timing gate pasif",
        }

    px = float(price or 0)
    bars = list(bars15 or bars_15m(12))
    vol_f = float(anchor.get("volatility_factor", 0) or _volatility_factor(bars, px))
    liq_f = _liquidity_quality_factor(liquidity)
    decayed = float(anchor.get("decayed_strength", 0) or anchor.get("event_strength", 0) or 0)
    events_score = float(events.get("decayed_score", 0) or 0) or decayed
    event_part = max(decayed, events_score)

    physics_score = min(
        100.0,
        event_part * 0.50 * vol_f * liq_f + (liq_f - 0.5) * 25.0,
    )
    # Bantlar: decayed event skoru (logdaki eff ile ayni olcek)
    setup_strength = min(100.0, max(event_part, physics_score * 0.85))

    remaining_pct = float(anchor.get("remaining_pct", 0) or 0) / 100.0
    hl_min = float(anchor.get("half_life_min", 0) or 0)
    time_mult = 0.35 + 0.65 * remaining_pct
    effective = min(100.0, setup_strength * time_mult)

    max_trade_min = max(
        1.0,
        hl_min * remaining_pct
        if hl_min > 0
        else (anchor.get("window_bars", 1) or 1) * 15 * remaining_pct,
    )
    span_plus = max(1.0, instant_thr - ready_plus_thr)
    now_window_min = round(max_trade_min * 0.45, 1) if setup_strength >= instant_thr else round(
        max_trade_min * (0.15 + 0.25 * max(0.0, setup_strength - ready_plus_thr) / span_plus),
        1,
    )

    open_win = bool(anchor.get("open"))
    time_ok = effective >= watch_thr
    trade_probability = round(min(1.0, effective / 100.0), 3)
    observable = False
    event_monitor = False
    band = ""
    if not open_win:
        tier = "EXPIRED"
        pass_gate = False
        band = "closed"
        reason = "pencere kapandi"
    elif setup_strength >= instant_thr:
        tier = "INSTANT"
        pass_gate = time_ok
        band = "strong"
        reason = (
            f"anlik setup={setup_strength:.0f} eff={effective:.0f} p={trade_probability:.2f}"
            if time_ok
            else f"setup guclu zaman dustu eff={effective:.0f}"
        )
    elif setup_strength >= ready_plus_thr:
        tier = "READY_PLUS"
        pass_gate = time_ok
        band = "continuation"
        reason = (
            f"READY+ continuation setup={setup_strength:.0f} "
            f"({ready_plus_thr:.0f}–{instant_thr - 1:.0f}) eff={effective:.0f}"
            if time_ok
            else f"READY+ ama eff={effective:.0f} dusuk"
        )
    elif setup_strength >= ready_minus_thr:
        tier = "READY_MINUS"
        pass_gate = False
        observable = True
        event_monitor = True
        band = "fragile"
        reason = (
            f"READY- kirilgan setup={setup_strength:.0f} "
            f"({ready_minus_thr:.0f}–{ready_plus_thr - 1:.0f}) — trade yok, izle"
        )
    elif setup_strength >= watch_thr:
        tier = "WATCH"
        pass_gate = False
        observable = True
        event_monitor = True
        band = "early_reversal"
        reason = (
            f"WATCH setup={setup_strength:.0f} "
            f"({watch_thr:.0f}–{ready_minus_thr - 1:.0f}) — trade yok, event monitor"
        )
    else:
        tier = "EXPIRED"
        pass_gate = False
        band = "dead"
        reason = f"setup={setup_strength:.0f} < {watch_thr:.0f}"

    return {
        "half_life_score": round(setup_strength, 1),
        "setup_strength": round(setup_strength, 1),
        "effective_lifetime": round(effective, 1),
        "trade_probability": trade_probability,
        "tier": tier,
        "band": band,
        "thresholds": {
            "instant": instant_thr,
            "ready_plus": ready_plus_thr,
            "ready_minus": ready_minus_thr,
            "watch": watch_thr,
        },
        "pass_gate": pass_gate,
        "observable": observable,
        "event_monitor": event_monitor,
        "max_trade_min": round(max_trade_min, 1),
        "now_window_min": now_window_min,
        "event_part": round(event_part, 1),
        "vol_factor": round(vol_f, 2),
        "liq_factor": round(liq_f, 2),
        "time_mult": round(time_mult, 2),
        "reason": reason,
    }


def _urgency_from_lifetime(lifetime: dict, anchor: dict) -> dict:
    tier = str(lifetime.get("tier") or "NONE")
    eff = float(lifetime.get("effective_lifetime", 0) or 0)
    setup = float(lifetime.get("setup_strength", eff) or eff)
    instant_thr, ready_plus_thr, ready_minus_thr, _watch_thr = _lifetime_thresholds()
    span_plus = max(1.0, instant_thr - ready_plus_thr)

    if tier == "INSTANT":
        action = "NOW"
        pressure = min(100.0, eff)
        reason = lifetime.get("reason", "NOW")
    elif tier == "READY_PLUS":
        action = "LATER"
        pressure = min(
            92.0,
            ready_plus_thr + (setup - ready_plus_thr) / span_plus * (instant_thr - ready_plus_thr),
        )
        reason = lifetime.get("reason", "LATER READY+ continuation")
    elif tier == "READY_MINUS":
        action = "OBSERVE"
        pressure = max(0.0, setup * 0.55)
        reason = lifetime.get("reason", "OBSERVE READY- kirilgan")
    elif tier == "WATCH":
        action = "OBSERVE"
        pressure = max(0.0, eff * 0.65)
        reason = lifetime.get("reason", "OBSERVE WATCH — event monitor")
    elif tier == "EXPIRED":
        action = "SKIP"
        pressure = max(0.0, eff * 0.4)
        reason = lifetime.get("reason", "SKIP")
    else:
        action = "SKIP"
        pressure = 0
        reason = "event yok"

    return {
        "action": action,
        "pressure": round(pressure, 1),
        "reason": reason,
        "tier": tier,
        "band": lifetime.get("band", ""),
        "observable": bool(lifetime.get("observable")),
        "event_monitor": bool(lifetime.get("event_monitor")),
        "trade_probability": lifetime.get("trade_probability", 0),
        "now_window_min": lifetime.get("now_window_min", 0),
    }


def _compute_execution_quality(anchor: dict, lifetime: dict | None = None) -> int:
    lt = lifetime or {}
    eff = float(lt.get("effective_lifetime", 0) or 0)
    if eff > 0:
        return max(0, min(100, int(eff * 0.85 + float(anchor.get("remaining_pct", 0) or 0) * 0.15)))
    if not anchor.get("valid"):
        return 0
    if not anchor.get("open"):
        return 5
    q = 25.0 + min(40.0, float(anchor.get("decayed_strength", 0) or 0) * 0.5)
    q += float(anchor.get("remaining_pct", 0) or 0) * 0.35
    return max(0, min(100, int(q)))


def build_execution_filter(
    events: dict,
    price: float = 0,
    bars15: list[dict] | None = None,
    *,
    liquidity: dict | None = None,
    collapse: dict | None = None,
) -> dict:
    """
    Execution filter — yalnizca zaman/tier.
    collapse (read-only): allow_trade=False ise PASS uretmez (cift yorum onlenir).
    """
    anchor = update_execution_anchor(events, price, bars15)
    lifetime = compute_opportunity_lifetime(
        anchor, events, liquidity=liquidity, bars15=bars15, price=price
    )
    c = collapse or {}
    if c and not c.get("allow_trade"):
        lifetime = dict(lifetime)
        lifetime["pass_gate"] = False
        lifetime["tier"] = "DEFERRED"
        lifetime["observable"] = False
        lifetime["event_monitor"] = False
        lifetime["reason"] = (
            f"Collapse {c.get('mode')} — exec filter collapse ile hizalandi (PASS yok)"
        )
    urgency = _urgency_from_lifetime(lifetime, anchor)
    quality = _compute_execution_quality(anchor, lifetime)

    return {
        "urgency": urgency,
        "execution_quality": quality,
        "opportunity_lifetime": lifetime,
        "pass_gate": bool(lifetime.get("pass_gate", True)),
        "observable": bool(lifetime.get("observable")),
        "event_monitor": bool(lifetime.get("event_monitor")),
        "trade_probability": lifetime.get("trade_probability", 0),
        "time_validity": anchor,
        "execution_window": anchor,
        "opportunity": {
            "half_life_min": anchor.get("half_life_min", 0),
            "half_life_score": lifetime.get("half_life_score", 0),
            "effective_lifetime": lifetime.get("effective_lifetime", 0),
            "tier": lifetime.get("tier", "NONE"),
            "band": lifetime.get("band", ""),
            "max_trade_min": lifetime.get("max_trade_min", 0),
            "now_window_min": lifetime.get("now_window_min", 0),
            "lifetime_open": bool(anchor.get("open")),
            "remaining_sec": anchor.get("remaining_sec", 0),
            "commitment_event": anchor.get("event_type", ""),
            "trade_probability": lifetime.get("trade_probability", 0),
            "event_monitor": bool(lifetime.get("event_monitor")),
            "observable": bool(lifetime.get("observable")),
        },
        "role": "timing_filter_only",
    }


def execution_window_open(window: dict | None) -> bool:
    w = window or {}
    if not w.get("valid"):
        return False
    if "open" in w:
        return bool(w.get("open"))
    return time.time() < float(w.get("expires_ts", 0) or 0)


def execution_timing_allows(
    market_state: dict | None,
    *,
    require_event: bool = True,
    scenario_name: str = "",
    side: str = "",
) -> tuple[bool, str]:
    """Geriye uyumluluk — tek kapi trade_verdict_v3."""
    from engine.trade_verdict_v3 import trade_entry_allowed

    ms = market_state or {}
    events = ms.get("events") or {}
    decayed = float(events.get("decayed_score", 0) or 0)
    min_ev = float(getattr(cfg, "V3_EXEC_WINDOW_MIN_EVENT", 35.0) or 35.0)
    if not require_event or decayed < min_ev:
        return True, ""
    if scenario_name and side:
        return trade_entry_allowed(ms, scenario_name, side)
    v = ms.get("trade_verdict") or {}
    if v:
        return bool(v.get("allow_entry")), str(v.get("reason", ""))
    return bool((ms.get("execution_filter") or {}).get("pass_gate")), ""


# Geriye uyumluluk
build_execution_brain = build_execution_filter


def snapshot_trade_brain() -> dict:
    ms = dict(getattr(state, "v3_market_state", None) or {})
    collapse = ms.get("collapse") or {}
    snap = {
        "ts": time.time(),
        "collapse": dict(collapse),
        "execution_filter": dict(ms.get("execution_filter") or {}),
        "events_flags": dict((ms.get("events") or {}).get("flags") or {}),
        "controller": str(collapse.get("controller") or ""),
    }
    state.v3_trade_brain_snapshot = snap
    return snap


def execution_filter_log_line(ef: dict | None) -> str:
    x = ef or {}
    u = x.get("urgency") or {}
    o = x.get("opportunity") or {}
    lt = x.get("opportunity_lifetime") or {}
    tier = lt.get("tier") or o.get("tier") or "—"
    band = lt.get("band") or o.get("band") or ""
    eff = lt.get("effective_lifetime") or o.get("effective_lifetime") or 0
    pg = "PASS" if x.get("pass_gate", lt.get("pass_gate", True)) else "BLOCK"
    band_tag = f" ({band})" if band else ""
    mon = " monitor" if lt.get("event_monitor") or x.get("event_monitor") else ""
    prob = lt.get("trade_probability") or o.get("trade_probability") or 0
    return (
        f"[EXEC-FILTER] {pg} tier={tier}{band_tag}{mon} "
        f"eff={eff:.0f} p={prob:.2f} "
        f"urgency={u.get('action')} "
        f"quality={x.get('execution_quality', 0)} "
        f"| kalan={o.get('remaining_sec', 0)}s"
    )


execution_brain_log_line = execution_filter_log_line
