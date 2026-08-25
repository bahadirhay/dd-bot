"""
engine/zone_trend_v3.py — Cok zaman dilimli trend skoru.

trend_score = 4H*0.50 + 1H*0.35 + 15M*0.15 (yonlu isaret)
"""
from __future__ import annotations

from core.config import cfg
from engine.v3_common import aggregate_4h_from_1h, bars_15m, bars_1h


def _direction_from_closes(bars: list[dict], n: int, min_move: float) -> tuple[str, float]:
    if len(bars) < n or n < 2:
        return "UNCLEAR", 0.0
    recent = bars[-n:]
    closes = [float(b.get("close", 0) or 0) for b in recent]
    if any(c <= 0 for c in closes):
        return "UNCLEAR", 0.0
    up_steps = sum(1 for i in range(1, len(closes)) if closes[i] > closes[i - 1])
    dn_steps = sum(1 for i in range(1, len(closes)) if closes[i] < closes[i - 1])
    steps = len(closes) - 1
    if steps <= 0:
        return "UNCLEAR", 0.0
    chg_pct = (closes[-1] - closes[0]) / closes[0] if closes[0] else 0.0
    if up_steps >= max(steps - 1, 1) and chg_pct >= min_move:
        return "UP", chg_pct
    if dn_steps >= max(steps - 1, 1) and chg_pct <= -min_move:
        return "DOWN", chg_pct
    if up_steps > dn_steps and chg_pct > min_move * 0.5:
        return "UP", chg_pct
    if dn_steps > up_steps and chg_pct < -min_move * 0.5:
        return "DOWN", chg_pct
    soft = min_move * 0.25
    if chg_pct <= -soft and dn_steps >= up_steps:
        return "DOWN", chg_pct
    if chg_pct >= soft and up_steps >= dn_steps:
        return "UP", chg_pct
    return "UNCLEAR", chg_pct


def _tf_score(bars: list[dict], n: int, min_move: float) -> tuple[int, str]:
    """0-100 yonlu skor: DOWN yuksek negatif etki icin magnitude."""
    direction, chg = _direction_from_closes(bars, n, min_move)
    mag = min(100, int(abs(chg) / max(min_move, 1e-9) * 25))
    if direction == "DOWN":
        return mag, "DOWN"
    if direction == "UP":
        return mag, "UP"
    return max(0, mag // 3), "NEUTRAL"


def multi_tf_trend() -> dict:
    """
    Agirlikli trend.

    Returns:
        direction: UP | DOWN | NEUTRAL
        trend_score: 0-100
        components: {4h, 1h, 15m}
    """
    w4 = float(getattr(cfg, "V3_TREND_W_4H", 0.50) or 0.50)
    w1 = float(getattr(cfg, "V3_TREND_W_1H", 0.35) or 0.35)
    w15 = float(getattr(cfg, "V3_TREND_W_15M", 0.15) or 0.15)

    bars1h = bars_1h(120)
    bars4h = aggregate_4h_from_1h(bars1h)
    bars15 = bars_15m(80)

    s4, d4 = _tf_score(
        bars4h,
        max(int(getattr(cfg, "V3_TREND_4H_BARS", 6) or 6), 3),
        float(getattr(cfg, "V3_TREND_4H_MIN_MOVE", 0.004) or 0.004),
    )
    s1, d1 = _tf_score(
        bars1h,
        max(int(getattr(cfg, "V3_STRUCTURE_1H_CLOSE_BARS", 6) or 6), 3),
        float(getattr(cfg, "V3_STRUCTURE_1H_MIN_MOVE_PCT", 0.002) or 0.002),
    )
    s15, d15 = _tf_score(
        bars15,
        max(int(getattr(cfg, "V3_STRUCTURE_15M_CLOSE_BARS", 8) or 8), 3),
        float(getattr(cfg, "V3_STRUCTURE_15M_MIN_MOVE_PCT", 0.0004) or 0.0004),
    )

    def _signed(sc: int, d: str) -> float:
        if d == "DOWN":
            return -float(sc)
        if d == "UP":
            return float(sc)
        return 0.0

    composite = w4 * _signed(s4, d4) + w1 * _signed(s1, d1) + w15 * _signed(s15, d15)
    trend_score = min(100, int(abs(composite)))

    if composite <= -12:
        direction = "DOWN"
    elif composite >= 12:
        direction = "UP"
    else:
        direction = "NEUTRAL"

    return {
        "direction": direction,
        "trend_score": trend_score,
        "composite": round(composite, 2),
        "4h": {"score": s4, "direction": d4},
        "1h": {"score": s1, "direction": d1},
        "15m": {"score": s15, "direction": d15},
    }
