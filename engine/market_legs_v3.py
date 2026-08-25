"""
engine/market_legs_v3.py — Piyasa bacaklari: impulse, bounce, lower high, compression.

Insan okumasi: 1992->1960 impulse, 1960->1979 weak bounce, LH, compression.
"""
from __future__ import annotations

from core.config import cfg
from engine.v3_common import avg_body, bars_15m


def _pct_move(a: float, b: float) -> float:
    if a <= 0:
        return 0.0
    return (b - a) / a * 100.0


def extract_swings(bars: list[dict], lookback: int | None = None) -> list[dict]:
    """Zaman sirali swing high/low listesi."""
    from engine.levels_v3 import _find_swing_candidates

    lb = int(lookback or getattr(cfg, "V3_SWING_LOOKBACK", 3) or 3)
    if len(bars) < lb * 2 + 3:
        return []
    highs, lows = _find_swing_candidates(bars, lb)
    out: list[dict] = []
    for h in highs:
        out.append(
            {
                "kind": "high",
                "price": float(h.get("price", 0) or 0),
                "bar_index": int(h.get("bar_index", 0) or 0),
            }
        )
    for lo in lows:
        out.append(
            {
                "kind": "low",
                "price": float(lo.get("price", 0) or 0),
                "bar_index": int(lo.get("bar_index", 0) or 0),
            }
        )
    out.sort(key=lambda x: x["bar_index"])
    return [p for p in out if p["price"] > 0]


def _legs_from_swings(swings: list[dict]) -> list[dict]:
    if len(swings) < 2:
        return []
    legs: list[dict] = []
    for i in range(1, len(swings)):
        a, b = swings[i - 1], swings[i]
        pa, pb = float(a["price"]), float(b["price"])
        move = _pct_move(pa, pb)
        if a["kind"] == "low" and b["kind"] == "high":
            direction = "UP"
        elif a["kind"] == "high" and b["kind"] == "low":
            direction = "DOWN"
        else:
            direction = "UP" if pb > pa else "DOWN"
        legs.append(
            {
                "from": pa,
                "to": pb,
                "direction": direction,
                "move_pct": round(move, 3),
                "from_kind": a["kind"],
                "to_kind": b["kind"],
            }
        )
    return legs


def _compression(bars: list[dict], lookback: int = 6) -> bool:
    recent = list(bars[-lookback:]) if bars else []
    if len(recent) < 4:
        return False
    highs = [float(b.get("high", 0) or 0) for b in recent]
    lows = [float(b.get("low", 0) or 0) for b in recent]
    if not highs or not lows:
        return False
    span = max(highs) - min(lows)
    mid = (max(highs) + min(lows)) / 2.0
    if mid <= 0:
        return False
    body = avg_body(recent) or 1.0
    span_pct = span / mid * 100.0
    max_pct = float(getattr(cfg, "V3_STORY_COMPRESSION_PCT", 0.35) or 0.35)
    return span_pct <= max_pct and span <= body * 2.5


def build_market_story(
    bars15: list[dict] | None = None,
    price: float = 0,
) -> dict:
    bars = list(bars15 or bars_15m(96))
    px = float(price or 0)
    if not bars:
        return {"pattern": "UNKNOWN", "bias": "NEUTRAL", "summary": "veri yok"}

    swings = extract_swings(bars)
    legs = _legs_from_swings(swings)
    impulse_min = float(getattr(cfg, "V3_STORY_IMPULSE_MIN_PCT", 0.55) or 0.55)
    bounce_max_ratio = float(getattr(cfg, "V3_STORY_BOUNCE_MAX_RATIO", 0.52) or 0.52)

    last_down: dict | None = None
    last_up: dict | None = None
    for leg in legs:
        if leg["direction"] == "DOWN" and abs(leg["move_pct"]) >= impulse_min:
            if not last_down or abs(leg["move_pct"]) > abs(last_down["move_pct"]):
                last_down = leg
        if leg["direction"] == "UP":
            last_up = leg

    highs = [s["price"] for s in swings if s["kind"] == "high"]
    lows = [s["price"] for s in swings if s["kind"] == "low"]
    is_lower_high = False
    bounce_high = 0.0
    if len(highs) >= 2:
        is_lower_high = highs[-1] < highs[-2] * 0.999
        bounce_high = float(highs[-1])

    weak_bounce = False
    if last_down and last_up and last_down["direction"] == "DOWN":
        down_sz = abs(last_down["move_pct"])
        up_sz = abs(last_up["move_pct"])
        if down_sz > 0 and up_sz < down_sz * bounce_max_ratio:
            weak_bounce = True

    compression = _compression(bars)
    pattern = "RANGE"
    if last_down and weak_bounce and is_lower_high:
        pattern = "IMPULSE_DOWN_BOUNCE_LH"
    elif last_down and weak_bounce:
        pattern = "IMPULSE_DOWN_WEAK_BOUNCE"
    elif last_down:
        pattern = "IMPULSE_DOWN"
    elif compression:
        pattern = "COMPRESSION"

    bias = "NEUTRAL"
    if pattern in ("IMPULSE_DOWN", "IMPULSE_DOWN_WEAK_BOUNCE", "IMPULSE_DOWN_BOUNCE_LH"):
        bias = "BEAR"
    elif last_up and not last_down and abs(last_up.get("move_pct", 0)) >= impulse_min:
        bias = "BULL"

    if compression and bias == "BEAR":
        pattern = "IMPULSE_DOWN_BOUNCE_LH" if is_lower_high else pattern

    parts: list[str] = []
    if last_down:
        parts.append(
            f"impulse {last_down['from']:.0f}->{last_down['to']:.0f} "
            f"({last_down['move_pct']:+.2f}%)"
        )
    if weak_bounce and bounce_high > 0:
        parts.append(f"zayif toparlanma -> {bounce_high:.0f}")
    if is_lower_high:
        parts.append("lower high")
    if compression:
        parts.append("compression")
    summary = " | ".join(parts) if parts else "belirsiz yapi"

    return {
        "pattern": pattern,
        "bias": bias,
        "compression": compression,
        "weak_bounce": weak_bounce,
        "is_lower_high": is_lower_high,
        "impulse_from": float(last_down["from"]) if last_down else 0.0,
        "impulse_to": float(last_down["to"]) if last_down else 0.0,
        "bounce_high": bounce_high,
        "legs": legs[-6:],
        "swings": swings[-8:],
        "summary": summary,
    }


def story_log_line(story: dict | None) -> str:
    s = story or {}
    return (
        f"[STORY] {s.get('pattern', '?')} bias={s.get('bias', '?')} | "
        f"{s.get('summary', '—')}"
    )
