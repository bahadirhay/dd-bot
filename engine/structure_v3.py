"""
engine/structure_v3.py

1h kapanis egimi — yalnizca bilgi (karar kapisi degil).

DÜZELTME: Range tespiti eklendi.
Fiyat uzun süre aynı kanalda dolaşıyorsa (range_locked=True)
direction "DOWN" veya "UP" olsa bile karar motoruna "RANGE_LOCKED"
olarak iletilir — bu sayede RANGE_BUY bloğu kırılmaz.
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


def _detect_range_lock(bars: list[dict], lookback: int = 24) -> bool:
    """
    Son `lookback` 1h barında fiyat dar bir kanalda mı dolaşıyor?

    Kural: son 24 barın high-low aralığı, fiyatın %3'ünden küçükse
    ve fiyat bu sürede net bir yön oluşturmamışsa → range_locked=True.

    Bu durumda 1h DOWN olsa bile RANGE_BUY engellenmez.
    """
    if len(bars) < lookback:
        lookback = len(bars)
    if lookback < 6:
        return False

    recent = bars[-lookback:]
    highs  = [float(b.get("high", 0) or 0) for b in recent if b.get("high")]
    lows   = [float(b.get("low",  0) or 0) for b in recent if b.get("low")]

    if not highs or not lows:
        return False

    range_high = max(highs)
    range_low  = min(lows)
    mid        = (range_high + range_low) / 2
    if mid <= 0:
        return False

    range_pct = (range_high - range_low) / mid

    # Eşik: %4'ten küçük toplam hareket = range
    RANGE_LOCK_PCT = float(getattr(cfg, "V3_RANGE_LOCK_PCT", 0.04) or 0.04)
    if range_pct > RANGE_LOCK_PCT:
        return False

    # Ek kontrol: ilk ve son kapanış farkı da küçük mü?
    first_close = float(recent[0].get("close", 0) or 0)
    last_close  = float(recent[-1].get("close", 0) or 0)
    if first_close <= 0:
        return False
    net_move = abs(last_close - first_close) / first_close

    NET_MOVE_LOCK_PCT = float(getattr(cfg, "V3_RANGE_LOCK_NET_PCT", 0.02) or 0.02)
    return net_move < NET_MOVE_LOCK_PCT


def update_structure() -> dict:
    n, min_move = _close_trend_params()
    bars = bars_1h(100)
    direction = _direction_from_closes(bars, n, min_move)

    # ── DÜZELTME: Range lock tespiti ─────────────────────────────────────────
    range_locked = _detect_range_lock(bars, lookback=24)
    if range_locked:
        log.info(
            f"[STRUCT] 1h yönü={direction} ama range_locked=True "
            f"(son 24 bar dar kanal) → RANGE modunu bloklama"
        )
    # ─────────────────────────────────────────────────────────────────────────

    closes = [float(b.get("close", 0) or 0) for b in bars[-n:]] if len(bars) >= n else []
    s1h = {
        "direction": direction,
        "range_locked": range_locked,
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
            "range_locked": range_locked,
        },
    }
    alignment = {
        "aligned": True,
        "direction": direction,
        "range_locked": range_locked,
        "strength": "INFO",
        "info_only": True,
        "details": {"1h": direction},
    }
    snap = {"1h": s1h, "alignment": alignment}
    state.v3_structure = snap
    state.v3_range_locked = range_locked

    d1h = s1h.get("details") or {}
    log.info(
        f"[STRUCT] 1h close_trend n={d1h.get('close_bars')} "
        f"chg={d1h.get('change_pct')}% dir={direction} "
        f"range_locked={range_locked} (bilgi, kapı degil)"
    )
    return snap


def get_structure_snapshot() -> dict:
    snap = state.v3_structure or {}
    if not snap:
        snap = update_structure()
    return snap
