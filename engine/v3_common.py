from __future__ import annotations

from statistics import mean


def bars_15m(limit: int = 200) -> list[dict]:
    try:
        from engine.structure import get_bars_15m

        return get_bars_15m(limit) or []
    except Exception:
        return []


def bars_1h(limit: int = 100) -> list[dict]:
    try:
        from engine.structure import get_bars_1h

        return get_bars_1h(limit) or []
    except Exception:
        return []


def bars_1m(limit: int = 300) -> list[dict]:
    try:
        from engine.bars_1m import get_bars_1m

        return get_bars_1m(limit) or []
    except Exception:
        return []


def aggregate_5m(source_bars: list[dict]) -> list[dict]:
    if not source_bars:
        return []
    out: list[dict] = []
    current: dict | None = None
    for bar in source_bars:
        ts = int(float(bar.get("ts", 0) or 0))
        bucket_ts = ts - (ts % 300)
        if current is None or int(current["ts"]) != bucket_ts:
            current = {
                "ts": float(bucket_ts),
                "open": float(bar.get("open", 0) or 0),
                "high": float(bar.get("high", 0) or 0),
                "low": float(bar.get("low", 0) or 0),
                "close": float(bar.get("close", 0) or 0),
                "volume": float(bar.get("volume", 0) or 0),
                "closed": True,
            }
            out.append(current)
            continue
        current["high"] = max(float(current["high"]), float(bar.get("high", 0) or 0))
        current["low"] = min(float(current["low"]), float(bar.get("low", 0) or 0))
        current["close"] = float(bar.get("close", 0) or 0)
        current["volume"] += float(bar.get("volume", 0) or 0)
    return out


def avg_body(bars: list[dict]) -> float:
    if not bars:
        return 0.0
    return float(
        mean(abs(float(b.get("close", 0) or 0) - float(b.get("open", 0) or 0)) for b in bars)
    )
