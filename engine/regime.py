"""
engine/regime.py — Geriye uyumluluk; asıl mantık engine/trend.py
"""
from core.state import state
from engine.trend import update_trend, STRENGTH_TRADE

REGIME_MIN = 3  # sinyal katmanı trade_direction kullanır


def evaluate(direction: str = "") -> tuple[str, int, dict]:
    tv = update_trend("regime")
    ans = dict(state.regime_answers)

    if direction == "SHORT":
        score = min(4, tv["down_score"] // 25)
        regime = "TREND" if tv["bias"] == "DOWN" and tv["strength"] >= STRENGTH_TRADE else "RANGE"
    elif direction == "LONG":
        score = min(4, tv["up_score"] // 25)
        regime = "TREND" if tv["bias"] == "UP" and tv["strength"] >= STRENGTH_TRADE else "RANGE"
    else:
        score = min(4, tv["strength"] // 25)
        regime = "TREND" if tv["trade_ok"] else "RANGE"

    return regime, score, ans
