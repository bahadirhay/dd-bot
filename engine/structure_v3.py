"""
engine/structure_v3.py

1h kapanis egimi — yalnizca bilgi (karar kapisi degil).
"""
from __future__ import annotations

from core.config import cfg
from core.state import state
from core.logger import get_logger
from engine.v3_common import bars_1h

log = get_logger("StructV3")


def _close_trend_params() -> tuple[int, float]:
    n = max(int(getattr(cfg, "V3_STRUCTURE_1H_CLOSE_BARS", 6) or 6), 3)
    min_move = float(getattr(cfg, "V3_STRUCTURE_1H_MIN_MOVE_PCT", 0.002) or 0.002)
    return n, min_move


def _direction_from_closes(bars: list[dict], n: int, min_move: float) -> str:
    if len(bars) < n or n < 2:
        return "UNCLEAR"
    recent = bars[-n:]
    closes = [float(b.get("close", 0) or 0) for b in recent]
    if any(c <= 0 for c in closes):
        return "UNCLEAR"
    up_steps = sum(1 for i in range(1, len(closes)) if closes[i] > closes[i - 1])
    dn_steps = sum(1 for i in range(1, len(closes)) if closes[i] < closes[i - 1])
    steps = len(closes) - 1
    if steps <= 0:
        return "UNCLEAR"
    chg_pct = (closes[-1] - closes[0]) / closes[0] if closes[0] else 0.0
    if up_steps >= max(steps - 1, 1) and chg_pct >= min_move:
        return "UP"
    if dn_steps >= max(steps - 1, 1) and chg_pct <= -min_move:
        return "DOWN"
    if up_steps > dn_steps and chg_pct > min_move * 0.5:
        return "UP"
    if dn_steps > up_steps and chg_pct < -min_move * 0.5:
        return "DOWN"
    soft = min_move * 0.25
    if chg_pct <= -soft and dn_steps >= up_steps:
        return "DOWN"
    if chg_pct >= soft and up_steps >= dn_steps:
        return "UP"
    return "UNCLEAR"


def update_structure() -> dict:
    n, min_move = _close_trend_params()
    bars = bars_1h(100)
    direction = _direction_from_closes(bars, n, min_move)
    closes = [float(b.get("close", 0) or 0) for b in bars[-n:]] if len(bars) >= n else []
    s1h = {
        "direction": direction,
        "last_bos": None,
        "last_choch": None,
        "swing_highs": [],
        "swing_lows": [],
        "details": {
            "timeframe": "1h",
            "method": "close_trend",
            "close_bars": n,
            "min_move_pct": min_move,
            "first_close": closes[0] if closes else 0.0,
            "last_close": closes[-1] if closes else 0.0,
            "change_pct": round(
                ((closes[-1] - closes[0]) / closes[0] * 100.0) if closes and closes[0] else 0.0,
                3,
            ),
        },
    }
    alignment = {
        "aligned": True,
        "direction": direction,
        "strength": "INFO",
        "info_only": True,
        "details": {"1h": direction},
    }
    snap = {"1h": s1h, "alignment": alignment}
    state.v3_structure = snap
    d1h = s1h.get("details") or {}
    log.info(
        f"[STRUCT] 1h close_trend n={d1h.get('close_bars')} "
        f"chg={d1h.get('change_pct')}% dir={direction} (bilgi, kapı degil)"
    )
    return snap


def get_structure_snapshot() -> dict:
    snap = state.v3_structure or {}
    if not snap:
        snap = update_structure()
    return snap
