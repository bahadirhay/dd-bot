"""
engine/cvd_v3.py

Referans klasordeki CVD teyidinin mevcut aggTrade penceresine uyarlanmis hali.
"""
from __future__ import annotations

from core.config import cfg
from core.state import state
from engine.v3_common import bars_1m, bars_15m, aggregate_5m


def _trade_window() -> list[dict]:
    ticks = list(state.ticks)
    limit = max(int(cfg.V3_CVD_WINDOW_TRADES), 1)
    return ticks[-limit:]


def _breakout_price_confirmation(cvd_direction: str, breakout_side: str) -> bool:
    """Kirilim: 15m kapanis yonu CVD ile uyumluysa teyit (5m penceresi cok sikici)."""
    side = str(breakout_side or "").upper()
    if side not in ("BUY", "SELL"):
        return False
    b15 = bars_15m(3)
    if not b15:
        return False
    last = b15[-1]
    o = float(last.get("open", 0) or 0)
    c = float(last.get("close", 0) or 0)
    if c <= 0 or o <= 0:
        return False
    if side == "BUY" and cvd_direction == "BULL":
        return c > o
    if side == "SELL" and cvd_direction == "BEAR":
        return c < o
    return False


def _price_confirmation(cvd_direction: str, zone: str = "", breakout_side: str = "") -> bool:
    if _breakout_price_confirmation(cvd_direction, breakout_side):
        return True

    bars5 = aggregate_5m(bars_1m(30))
    if len(bars5) < 3:
        return False
    closed = bars5[-3:]
    price_up = float(closed[-1].get("close", 0) or 0) > float(closed[0].get("close", 0) or 0)

    # Normal teyit: fiyat yonu CVD ile ayni
    if price_up and cvd_direction == "BULL":
        return True
    if (not price_up) and cvd_direction == "BEAR":
        return True

    zone = str(zone or "").upper()
    # Divergence teyit (range kenari): dağıtım / birikim
    if cvd_direction == "BEAR" and zone == "NEAR_RESISTANCE":
        return True
    if cvd_direction == "BULL" and zone == "NEAR_SUPPORT":
        return True

    return False


def update_cvd_snapshot(zone: str = "", breakout_side: str = "") -> dict:
    window = _trade_window()
    total_buy = sum(float(t.get("qty", 0) or 0) for t in window if float(t.get("delta", 0) or 0) > 0)
    total_sell = sum(abs(float(t.get("qty", 0) or 0)) for t in window if float(t.get("delta", 0) or 0) < 0)
    cumulative = sum(float(t.get("delta", 0) or 0) for t in window)
    total_volume = total_buy + total_sell
    buy_ratio = total_buy / total_volume if total_volume > 0 else 0.5
    if buy_ratio >= 0.55:
        direction = "BULL"
    elif buy_ratio <= 0.45:
        direction = "BEAR"
    else:
        direction = "NEUTRAL"
    snap = {
        "cumulative": cumulative,
        "direction": direction,
        "confirmed": _price_confirmation(direction, zone, breakout_side),
        "buy_volume": total_buy,
        "sell_volume": total_sell,
        "buy_ratio": buy_ratio,
    }
    state.v3_cvd = snap
    return snap


def get_divergence() -> str:
    cvd = state.v3_cvd or update_cvd_snapshot()
    bars5 = aggregate_5m(bars_1m(30))
    if len(bars5) < 3:
        return "NONE"
    closed = bars5[-3:]
    price_up = float(closed[-1].get("close", 0) or 0) > float(closed[0].get("close", 0) or 0)
    cvd_dir = str(cvd.get("direction") or "NEUTRAL")
    if price_up and cvd_dir == "BEAR":
        return "BEARISH_DIVERGENCE"
    if (not price_up) and cvd_dir == "BULL":
        return "BULLISH_DIVERGENCE"
    return "NONE"


def get_cvd_snapshot() -> dict:
    snap = state.v3_cvd or {}
    if not snap:
        snap = update_cvd_snapshot()
    snap = dict(snap)
    snap["divergence"] = get_divergence()
    return snap
