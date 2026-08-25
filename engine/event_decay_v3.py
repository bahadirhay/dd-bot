"""
engine/event_decay_v3.py — Olay gecerlilik suresi (anlik gerceklik, hafiza degil).

event_strength *= exp(-decay_rate * hours_passed)
"""
from __future__ import annotations

import math
import time

from core.config import cfg


def _base_strength(event_type: str) -> float:
    t = str(event_type or "").upper()
    return {
        "SWEEP_LOW": 78.0,
        "SWEEP_HIGH": 78.0,
        "STRUCTURE_BREAK_DOWN": 72.0,
        "LOWER_HIGH": 68.0,
        "COMPRESSION": 52.0,
    }.get(t, 45.0)


def decay_factor(hours_ago: float, decay_rate: float | None = None) -> float:
    rate = float(decay_rate if decay_rate is not None else getattr(cfg, "V3_EVENT_DECAY_RATE", 0.85) or 0.85)
    return math.exp(-rate * max(0.0, float(hours_ago or 0)))


def apply_decay_to_events(
    events: list[dict],
    now_ts: float | None = None,
) -> list[dict]:
    """Her olaya decayed_strength ve hours_ago ekler."""
    now = float(now_ts or time.time())
    max_age = float(getattr(cfg, "V3_EVENT_MAX_AGE_HOURS", 4.0) or 4.0)
    min_strength = float(getattr(cfg, "V3_EVENT_MIN_DECAYED_STRENGTH", 22.0) or 22.0)
    out: list[dict] = []

    for e in events:
        ev = dict(e)
        ts = float(ev.get("ts", 0) or 0)
        if ts <= 0:
            hours_ago = 999.0
        else:
            hours_ago = max(0.0, (now - ts) / 3600.0)
        ev["hours_ago"] = round(hours_ago, 2)
        base = float(ev.get("base_strength", 0) or _base_strength(str(ev.get("type") or "")))
        ev["base_strength"] = base
        factor = decay_factor(hours_ago)
        ev["decay_factor"] = round(factor, 3)
        ev["decayed_strength"] = round(base * factor, 1)
        ev["stale"] = hours_ago > max_age or ev["decayed_strength"] < min_strength
        out.append(ev)
    return out


def decayed_event_flags(events: list[dict]) -> dict:
    """Yalnizca taze ve guclu olaylar aktif."""
    min_strength = float(getattr(cfg, "V3_EVENT_MIN_DECAYED_STRENGTH", 22.0) or 22.0)
    fresh = [e for e in events if not e.get("stale") and float(e.get("decayed_strength", 0) or 0) >= min_strength]

    def _best(typ: str) -> dict | None:
        pool = [e for e in fresh if str(e.get("type") or "") == typ]
        if not pool:
            return None
        return max(pool, key=lambda x: float(x.get("decayed_strength", 0) or 0))

    sl = _best("SWEEP_LOW")
    sh = _best("SWEEP_HIGH")
    comp = _best("COMPRESSION")
    brk = _best("STRUCTURE_BREAK_DOWN") or _best("LOWER_HIGH")

    return {
        "sweep_low": sl is not None,
        "sweep_high": sh is not None,
        "compression": comp is not None,
        "structure_break": brk is not None,
        "best_sweep_low": sl,
        "best_sweep_high": sh,
        "best_compression": comp,
        "best_break": brk,
    }


def aggregate_decayed_score(events: list[dict]) -> float:
    fresh = [e for e in events if not e.get("stale")]
    if not fresh:
        return 0.0
    return max(float(e.get("decayed_strength", 0) or 0) for e in fresh)
