"""
engine/trend_magic_v3.py — Enhanced Trend Magic + Swings (Heikin Ashi).

Sinyaller HA mumlari uzerinde; canli/paper fill gercek fiyatla.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.config import cfg


@dataclass
class TrendMagicParams:
    cci_period: int = 14
    atr_period: int = 5
    ma_period: int = 10
    cci_threshold: float = 80.0
    trend_persistence: int = 2
    price_distance: float = 0.8
    swing_range: int = 12
    volume_increase_pct: float = 20.0


def _params() -> TrendMagicParams:
    return TrendMagicParams(
        cci_period=int(getattr(cfg, "V3_TM_CCI_PERIOD", 14) or 14),
        atr_period=int(getattr(cfg, "V3_TM_ATR_PERIOD", 5) or 5),
        ma_period=int(getattr(cfg, "V3_TM_MA_PERIOD", 10) or 10),
        cci_threshold=float(getattr(cfg, "V3_TM_CCI_THRESHOLD", 80.0) or 80.0),
        trend_persistence=int(getattr(cfg, "V3_TM_TREND_PERSIST", 2) or 2),
        price_distance=float(getattr(cfg, "V3_TM_PRICE_DIST", 0.8) or 0.8),
        swing_range=int(getattr(cfg, "V3_TM_SWING_RANGE", 12) or 12),
        volume_increase_pct=float(getattr(cfg, "V3_TM_VOLUME_INCREASE_PCT", 20.0) or 20.0),
    )


def _bar_ohlc(b: dict) -> tuple[float, float, float, float]:
    o = float(b.get("open") or b.get("close") or 0)
    h = float(b.get("high") or o or 0)
    l = float(b.get("low") or o or 0)
    c = float(b.get("close") or o or 0)
    return o, h, l, c


def heikin_ashi_series(bars: list[dict]) -> list[dict]:
    out: list[dict] = []
    ha_o = ha_c = 0.0
    for i, b in enumerate(bars):
        o, h, l, c = _bar_ohlc(b)
        if c <= 0:
            continue
        ha_c = (o + h + l + c) / 4.0
        if i == 0:
            ha_o = (o + c) / 2.0
        else:
            ha_o = (out[-1]["open"] + out[-1]["close"]) / 2.0
        ha_h = max(h, ha_o, ha_c)
        ha_l = min(l, ha_o, ha_c)
        out.append({"open": ha_o, "high": ha_h, "low": ha_l, "close": ha_c, "ts": b.get("ts", 0)})
    return out


def _ema(values: list[float], period: int) -> list[float]:
    n = len(values)
    out = [float("nan")] * n
    if period <= 0 or n < period:
        return out
    alpha = 2.0 / (period + 1)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, n):
        prev = alpha * values[i] + (1 - alpha) * prev
        out[i] = prev
    return out


def _rma(values: list[float], period: int) -> list[float]:
    """Pine ta.rma / ta.atr smoothing (Wilder)."""
    n = len(values)
    out = [float("nan")] * n
    if period <= 0 or n < period:
        return out
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, n):
        prev = (prev * (period - 1) + values[i]) / period
        out[i] = prev
    return out


def _atr(ha: list[dict], period: int) -> list[float]:
    n = len(ha)
    tr = [0.0] * n
    if not n:
        return tr
    tr[0] = ha[0]["high"] - ha[0]["low"]
    for i in range(1, n):
        h, l, c_prev = ha[i]["high"], ha[i]["low"], ha[i - 1]["close"]
        tr[i] = max(h - l, abs(h - c_prev), abs(l - c_prev))
    return _rma(tr, period)


def _cci(ha: list[dict], period: int) -> list[float]:
    hlc3 = [(x["high"] + x["low"] + x["close"]) / 3.0 for x in ha]
    n = len(hlc3)
    out = [float("nan")] * n
    for i in range(period - 1, n):
        win = hlc3[i - period + 1 : i + 1]
        m = sum(win) / period
        mad = sum(abs(x - m) for x in win) / period
        out[i] = 0.0 if mad == 0 else (hlc3[i] - m) / (0.015 * mad)
    return out


def _is_swing_high(i: int, ha: list[dict], swing_range: int) -> bool:
    if i < swing_range or i + swing_range >= len(ha):
        return False
    hi = ha[i]["high"]
    for j in range(1, swing_range + 1):
        if hi <= ha[i - j]["high"] or hi <= ha[i + j]["high"]:
            return False
    return True


def _is_swing_low(i: int, ha: list[dict], swing_range: int) -> bool:
    if i < swing_range or i + swing_range >= len(ha):
        return False
    lo = ha[i]["low"]
    for j in range(1, swing_range + 1):
        if lo >= ha[i - j]["low"] or lo >= ha[i + j]["low"]:
            return False
    return True


def _trend_valid(i: int, up: bool, ha: list[dict], ma: list[float], persistence: int) -> bool:
    need = max(1, int(persistence * 2 / 3))
    count = 0
    for k in range(persistence):
        j = i - k
        if j < 0 or j >= len(ma) or ma[j] != ma[j]:
            continue
        if up and ha[j]["close"] > ma[j]:
            count += 1
        elif not up and ha[j]["close"] < ma[j]:
            count += 1
    return count >= need


def compute_series(bars: list[dict], p: TrendMagicParams | None = None) -> dict[str, Any]:
    """Tam seri: trend_dir (+1/-1/0), buffer, swing noktalari."""
    p = p or _params()
    if len(bars) < max(p.cci_period, p.atr_period, p.ma_period, p.swing_range * 2) + 2:
        return {"ready": False, "bars": [], "trend_dir": [], "buffer": [], "swing_high": [], "swing_low": []}

    ha = heikin_ashi_series(bars)
    n = len(ha)
    ma = _ema([x["close"] for x in ha], p.ma_period)
    atr = _atr(ha, p.atr_period)
    cci = _cci(ha, p.cci_period)

    trend_dir: list[int] = []
    buffer: list[float] = []
    swing_high: list[bool] = []
    swing_low: list[bool] = []
    tdir = 0
    buf = float("nan")

    for i in range(n):
        sh = _is_swing_high(i, ha, p.swing_range)
        sl = _is_swing_low(i, ha, p.swing_range)
        swing_high.append(sh)
        swing_low.append(sl)

        cci_i, atr_i, ma_i = cci[i], atr[i], ma[i]
        if cci_i != cci_i or atr_i != atr_i or ma_i != ma_i:
            trend_dir.append(tdir)
            buffer.append(buf)
            continue

        pot_up = ha[i]["close"] > ma_i and cci_i >= p.cci_threshold
        pot_dn = ha[i]["close"] < ma_i and cci_i <= -p.cci_threshold
        if sl:
            pot_up = True
        if sh:
            pot_dn = True

        up = pot_up and _trend_valid(i, True, ha, ma, p.trend_persistence)
        dn = pot_dn and _trend_valid(i, False, ha, ma, p.trend_persistence)
        dist = abs(ha[i]["close"] - ma_i) / atr_i if atr_i else 0.0
        sig_move = dist >= p.price_distance

        if up and (sig_move or tdir == 1):
            tdir = 1
            buf = ha[i]["low"] - atr_i * 0.5
            if buffer and buffer[-1] == buffer[-1]:
                buf = max(buf, buffer[-1])
        elif dn and (sig_move or tdir == -1):
            tdir = -1
            buf = ha[i]["high"] + atr_i * 0.5
            if buffer and buffer[-1] == buffer[-1]:
                buf = min(buf, buffer[-1])

        trend_dir.append(tdir)
        buffer.append(buf)

    out_bars = []
    for i, b in enumerate(bars[:n]):
        out_bars.append(
            {
                "ts": b.get("ts", 0),
                "buffer": buffer[i] if i < len(buffer) else float("nan"),
                "trend_dir": trend_dir[i] if i < len(trend_dir) else 0,
                "swing_high": swing_high[i] if i < len(swing_high) else False,
                "swing_low": swing_low[i] if i < len(swing_low) else False,
            }
        )
    return {
        "ready": True,
        "bars": out_bars,
        "trend_dir": trend_dir,
        "buffer": buffer,
        "swing_high": swing_high,
        "swing_low": swing_low,
        "ha": ha,
    }


def snapshot(bars: list[dict], p: TrendMagicParams | None = None) -> dict[str, Any]:
    """Son kapali bar trend ozeti."""
    ser = compute_series(bars, p)
    if not ser.get("ready") or not ser["trend_dir"]:
        return {"ready": False}
    i = len(ser["trend_dir"]) - 1
    prev = ser["trend_dir"][i - 1] if i > 0 else 0
    td = ser["trend_dir"][i]
    buf = ser["buffer"][i]
    flip = td != prev and prev != 0 and td != 0
    entry_long = td == 1 and prev == -1
    entry_short = td == -1 and prev == 1
    return {
        "ready": True,
        "trend_dir": td,
        "prev_dir": prev,
        "buffer": buf if buf == buf else 0.0,
        "flip": flip,
        "entry_long": entry_long,
        "entry_short": entry_short,
        "side": "LONG" if td == 1 else ("SHORT" if td == -1 else "FLAT"),
        "swing_high": bool(ser["swing_high"][i]),
        "swing_low": bool(ser["swing_low"][i]),
        "bar_ts": float(bars[i].get("ts", 0) or 0) if i < len(bars) else 0.0,
    }


def bars_for_tf(tf_sec: int, limit: int = 200) -> list[dict]:
    """5m=1m birlestir; 30m=15m birlestir; 15m/1h dogrudan."""
    if tf_sec == 900:
        from engine.v3_common import bars_15m

        return list(bars_15m(limit) or [])
    if tf_sec == 3600:
        from engine.v3_common import bars_1h

        return list(bars_1h(limit) or [])
    if tf_sec == 1800:
        # Eval ile ayni: resmi Binance 30m kline (15m birlestirme degil).
        direct = fetch_binance_bars("30m", limit)
        if len(direct) >= 50:
            return direct
        from engine.v3_common import bars_15m

        b15 = list(bars_15m(limit * 2 + 4) or [])
        return resample_bars(b15, 1800, 900)
    if tf_sec == 300:
        try:
            from engine.bars_1m import get_bars_1m

            b1 = list(get_bars_1m(limit * 5 + 10) or [])
            if len(b1) >= 50:
                return resample_bars(b1, 300, 60)
        except Exception:
            pass
        return fetch_binance_bars("5m", limit)
    return []


def resample_bars(bars: list[dict], target_sec: int, base_sec: int) -> list[dict]:
    if not bars or target_sec <= base_sec:
        return list(bars)
    out: list[dict] = []
    chunk: list[dict] = []
    cur_bucket: int | None = None
    for b in bars:
        ts = float(b.get("ts", 0) or 0)
        bucket = int(ts // target_sec)
        if cur_bucket is None:
            chunk = [b]
            cur_bucket = bucket
            continue
        if bucket != cur_bucket:
            out.append(_merge_chunk(chunk, float(cur_bucket * target_sec)))
            chunk = [b]
            cur_bucket = bucket
        else:
            chunk.append(b)
    if chunk and cur_bucket is not None:
        out.append(_merge_chunk(chunk, float(cur_bucket * target_sec)))
    return out


def _merge_chunk(chunk: list[dict], ts: float) -> dict:
    o = float(chunk[0].get("open") or chunk[0].get("close") or 0)
    c = float(chunk[-1].get("close") or 0)
    hi = max(float(x.get("high", 0) or 0) for x in chunk)
    lo = min(float(x.get("low", 0) or c) for x in chunk if float(x.get("low", 0) or 0) > 0)
    vol = sum(float(x.get("volume", 0) or 0) for x in chunk)
    return {"ts": ts, "open": o, "high": hi, "low": lo, "close": c, "volume": vol}


def closed_bars(bars: list[dict], tf_sec: int) -> list[dict]:
    if not bars:
        return []
    import time as _time

    now = _time.time()
    if float(bars[-1].get("ts", 0) or 0) + tf_sec > now - 5:
        return bars[:-1]
    return bars


def is_tf_bar_close_from_15m(candle: dict) -> bool:
    """15m mum kapandiginda 30m (veya 1h) sinir mi?"""
    ts = float(candle.get("ts") or candle.get("open_time") or 0)
    if ts > 1e12:
        ts /= 1000.0
    close_ts = int(ts + 900)
    tf = int(getattr(cfg, "V3_TREND_MAGIC_TF_SEC", 1800) or 1800)
    return close_ts % tf == 0


@dataclass
class TmTrade:
    side: str
    entry_i: int
    entry_px: float
    qty: float
    pnl_bps: float = 0.0


def run_backtest(
    bars: list[dict],
    p: TrendMagicParams | None = None,
    *,
    fee_bps: float = 8.0,
    slip_bps: float = 2.0,
) -> list[TmTrade]:
    """Pine strateji: HA sinyal, gercek close fill, flip giris/cikis, martingale."""
    p = p or _params()
    ser = compute_series(bars, p)
    if not ser.get("ready"):
        return []
    n = len(bars)
    td = ser["trend_dir"]
    trades: list[TmTrade] = []
    pos: TmTrade | None = None
    qty = 1.0
    last_pl: float | None = None
    warmup = max(p.cci_period, p.atr_period, p.ma_period, p.swing_range * 2) + 2

    def _close(i: int) -> float:
        nonlocal pos, last_pl
        if pos is None:
            return 0.0
        px = float(bars[i]["close"])
        raw = ((px - pos.entry_px) if pos.side == "LONG" else (pos.entry_px - px)) / pos.entry_px * 1e4
        pos.pnl_bps = raw - fee_bps - 2 * slip_bps
        last_pl = pos.pnl_bps
        trades.append(pos)
        pl = pos.pnl_bps
        pos = None
        return pl

    for i in range(warmup, n):
        cur = td[i]
        prev = td[i - 1]
        if pos and pos.side == "LONG" and cur == -1:
            pl = _close(i)
            if pl > 0:
                qty = 1.0
        if pos and pos.side == "SHORT" and cur == 1:
            pl = _close(i)
            if pl > 0:
                qty = 1.0
        if cur == 1 and prev == -1:
            if pos and pos.side == "SHORT":
                _close(i)
            if last_pl is not None and last_pl < 0:
                qty *= 1.0 + p.volume_increase_pct / 100.0
            pos = TmTrade("LONG", i, float(bars[i]["close"]), qty)
        elif cur == -1 and prev == 1:
            if pos and pos.side == "LONG":
                _close(i)
            if last_pl is not None and last_pl < 0:
                qty *= 1.0 + p.volume_increase_pct / 100.0
            pos = TmTrade("SHORT", i, float(bars[i]["close"]), qty)
    if pos is not None:
        _close(n - 1)
    return trades


def fetch_binance_bars(interval: str, limit: int = 1500) -> list[dict]:
    try:
        from dashboard.binance_chart import fetch_klines

        return fetch_klines(interval, limit)
    except Exception:
        return []
