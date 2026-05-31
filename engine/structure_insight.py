"""
engine/structure_insight.py — Yapı etiketi (UP/DOWN/UNCLEAR) için insan okunur açıklama.
"""
from __future__ import annotations

from core.state import state


def _fmt_prices(points: list, n: int = 3) -> str:
    if not points:
        return "—"
    tail = points[-n:]
    return " → ".join(f"{p['price']:.1f}" for p in tail)


def _swing_trend(points: list) -> str:
    if len(points) < 2:
        return "yetersiz swing"
    tail = points[-3:] if len(points) >= 3 else points[-2:]
    prices = [p["price"] for p in tail]
    if all(prices[i] > prices[i - 1] for i in range(1, len(prices))):
        return "yükseliyor"
    if all(prices[i] < prices[i - 1] for i in range(1, len(prices))):
        return "düşüyor"
    return "karışık"


def structure_detail_text(timeframe: str) -> str:
    """15m veya 1h için swing özet metni."""
    if timeframe == "15m":
        highs, lows = state.swing_highs_15m, state.swing_lows_15m
        label = state.structure_15m or "?"
    else:
        highs, lows = state.swing_highs_1h, state.swing_lows_1h
        label = state.structure_1h or "?"

    if not highs and not lows:
        return f"{timeframe}={label} (swing henüz hesaplanmadı)"

    h_trend = _swing_trend(highs)
    l_trend = _swing_trend(lows)
    return (
        f"{timeframe}={label}: tepeler {h_trend} ({_fmt_prices(highs)}), "
        f"dipler {l_trend} ({_fmt_prices(lows)})"
    )


def why_not_down() -> str | None:
    """DOWN etiketi yoksa kısa sebep (momentum DOWN olsa bile)."""
    highs_15 = state.swing_highs_15m
    lows_15 = state.swing_lows_15m
    if len(highs_15) < 2 or len(lows_15) < 2:
        return "15m: yeterli swing noktası yok (lookback geniş)."

    h_ok = _swing_trend(highs_15) == "düşüyor"
    l_ok = _swing_trend(lows_15) == "düşüyor"
    parts = []
    if not h_ok:
        parts.append(f"tepeler düşmüyor ({_fmt_prices(highs_15)})")
    if not l_ok:
        parts.append(
            f"dipler yükseliyor — son dip tepeden sonra yukarı "
            f"({_fmt_prices(lows_15)}); bu 'aşağı yapı' sayılmaz"
        )
    if not parts:
        return None
    return "15m " + "; ".join(parts) + "."


def why_not_down_1h() -> str | None:
    highs = state.swing_highs_1h
    lows = state.swing_lows_1h
    if len(highs) < 2 or len(lows) < 2:
        return "1h: yeterli swing yok."
    h_ok = _swing_trend(highs) == "düşüyor"
    l_ok = _swing_trend(lows) == "düşüyor"
    if h_ok and l_ok:
        return None
    return (
        f"1h: tepeler {_swing_trend(highs)}, dipler {_swing_trend(lows)} "
        f"(DOWN için ikisi de düşmeli)."
    )
