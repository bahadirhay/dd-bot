"""
engine/intra_15m.py — 15m mum kapanmadan ÖNCE, o periyot içindeki 1m hareketleri.

Kapanmış 15m = resmi rejim/sinyal (observe_15m_close).
Açık 15m periyot = forming mum + 1m adımlar (canlı düşüş/yükseliş).
"""
from __future__ import annotations

import time
from core.config import cfg
from core.state import state, effective_price
from core.logger import get_logger

log = get_logger("Intra15m")

PERIOD_SEC = 900  # 15 dakika
_LOG_MOVE_PCT = 0.20  # %0.2 hareket log eşiği
_ALERT_DROP_PCT = 0.35


def _period_start(ts: float) -> int:
    return int(ts // PERIOD_SEC) * PERIOD_SEC


def _empty_forming(period_start: int, candle: dict) -> dict:
    return {
        "period_start": period_start,
        "open": candle["open"],
        "high": candle["high"],
        "low": candle["low"],
        "close": candle["close"],
        "volume": candle.get("volume", 0.0),
        "delta_sum": candle.get("delta", 0.0),
        "bars_1m": 1,
        "started_ts": candle["ts"],
        "updated_ts": candle["ts"],
    }


def touch_forming_from_price(price: float) -> None:
    """aggTrade/book ile acik 15m mumunu anlik guncelle (1m poll beklenmez)."""
    if price <= 0:
        return
    ps = _period_start(time.time())
    f = state.forming_15m
    if not f or f.get("period_start") != ps:
        try:
            from engine.structure import get_bars_15m

            bars = get_bars_15m(1)
            if bars and int(float(bars[-1].get("ts", 0) or 0)) == ps:
                b = bars[-1]
                f = {
                    "period_start": ps,
                    "open": float(b.get("open", price) or price),
                    "high": float(b.get("high", price) or price),
                    "low": float(b.get("low", price) or price),
                    "close": float(b.get("close", price) or price),
                    "volume": float(b.get("volume", 0) or 0),
                    "delta_sum": 0.0,
                    "bars_1m": int((state.forming_15m or {}).get("bars_1m", 0) or 0),
                    "started_ts": ps,
                    "updated_ts": time.time(),
                }
            else:
                f = {
                    "period_start": ps,
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "volume": 0.0,
                    "delta_sum": 0.0,
                    "bars_1m": 0,
                    "started_ts": ps,
                    "updated_ts": time.time(),
                }
        except Exception:
            return
    f["close"] = price
    f["high"] = max(float(f.get("high", price) or price), price)
    f["low"] = min(float(f.get("low", price) or price), price)
    f["updated_ts"] = time.time()
    state.forming_15m = f


def on_1m_tick(candle: dict) -> dict:
    """
    Her 1m kapanışında: şu anki 15m periyodunu güncelle.
    Dashboard + log için state.intra_15m_summary doldurulur.
    """
    ts = candle["ts"]
    ps = _period_start(ts)
    f = state.forming_15m

    if not f or f.get("period_start") != ps:
        f = _empty_forming(ps, candle)
    else:
        f["high"] = max(f["high"], candle["high"])
        f["low"] = min(f["low"], candle["low"])
        f["close"] = candle["close"]
        f["volume"] = f.get("volume", 0.0) + candle.get("volume", 0.0)
        f["delta_sum"] = f.get("delta_sum", 0.0) + candle.get("delta", 0.0)
        f["bars_1m"] = f.get("bars_1m", 0) + 1
        f["updated_ts"] = ts

    state.forming_15m = f

    o, c, h, l = f["open"], f["close"], f["high"], f["low"]
    chg_pct = ((c - o) / o * 100.0) if o else 0.0
    range_pct = ((h - l) / o * 100.0) if o else 0.0
    bearish = c < o
    mins_left = max(0, int((ps + PERIOD_SEC - ts) / 60))

    # Canlı orderflow (15m içi anlık)
    cvd5 = state.cvd_5m
    taker = state.taker_ratio
    sell_pressure = (1 - taker) >= cfg.TAKER_MIN
    buy_pressure = taker >= cfg.TAKER_MIN

    intra = {
        "period_start": ps,
        "bars_1m": f["bars_1m"],
        "mins_left": mins_left,
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "chg_pct": round(chg_pct, 3),
        "range_pct": round(range_pct, 3),
        "bearish": bearish,
        "delta_sum": f.get("delta_sum", 0.0),
        "cvd_5m": cvd5,
        "taker": taker,
        "structure_15m": state.structure_15m,
        "structure_1h": state.structure_1h,
        "sell_pressure_5m": sell_pressure,
        "buy_pressure_5m": buy_pressure,
        "alert_drop": bearish and chg_pct <= -_ALERT_DROP_PCT,
        "alert_rise": (not bearish) and chg_pct >= _ALERT_DROP_PCT,
    }
    state.intra_15m_summary = intra

    # Önemli 1m adımı (son 1m mum)
    m1_chg = ((candle["close"] - candle["open"]) / candle["open"] * 100) if candle["open"] else 0
    intra["last_1m_chg"] = round(m1_chg, 3)
    intra["last_1m_bearish"] = candle["close"] < candle["open"]

    prev_log = getattr(state, "_intra_last_log_pct", 0.0)
    if abs(chg_pct - prev_log) >= _LOG_MOVE_PCT:
        state._intra_last_log_pct = chg_pct
        arrow = "▼" if bearish else "▲"
        log.info(
            f"15m İÇİ {arrow}  [{f['bars_1m']}/15 × 1m]  "
            f"O={o:.2f} H={h:.2f} L={l:.2f} şimdi={c:.2f}  "
            f"({chg_pct:+.2f}%  range={range_pct:.2f}%)  "
            f"~{mins_left}dk kaldı  |  "
            f"son1m={m1_chg:+.2f}%  CVD5m={cvd5:+.0f}  taker={taker:.0%}  "
            f"yapı {state.structure_15m}/{state.structure_1h}"
        )
        if intra["alert_drop"]:
            tv = state.trend_view or {}
            log.info(
                f"       ↳ 15m içi düşüş — trend: {tv.get('summary', 'hesaplanıyor')}"
            )
        if getattr(cfg, "STRATEGY_V3_ENABLED", False):
            from engine.decision_v3 import update_decision, log_decision_diag

            snap = update_decision()
            log_decision_diag(snap, tag="15m-ici", force=True)

    return intra


def finalize_on_15m_close(closed_candle: dict):
    """15m kapandı — forming bir sonraki 1m ile yeniden başlar."""
    state.forming_15m = {}
    state._intra_last_log_pct = 0.0


def get_forming_for_chart() -> dict | None:
    """Dashboard: acik 15m mumu — canli fiyatla birlikte."""
    f = state.forming_15m
    px = float(effective_price() or state.mark_price or state.price or 0)
    if px > 0:
        touch_forming_from_price(px)
        f = state.forming_15m
    if not f or not f.get("open"):
        if px <= 0:
            return None
        ps = _period_start(time.time())
        try:
            from engine.structure import get_bars_15m

            bars = get_bars_15m(1)
            if bars and int(float(bars[-1].get("ts", 0) or 0)) == ps:
                b = bars[-1]
                return {
                    "ts": ps,
                    "open": float(b.get("open", px) or px),
                    "high": max(float(b.get("high", px) or px), px),
                    "low": min(float(b.get("low", px) or px), px),
                    "close": px,
                    "forming": True,
                }
        except Exception:
            pass
        return None
    close = px if px > 0 else float(f["close"])
    high = max(float(f["high"]), close)
    low = min(float(f["low"]), close)
    return {
        "ts": f["period_start"],
        "open": f["open"],
        "high": high,
        "low": low,
        "close": close,
        "forming": True,
    }
