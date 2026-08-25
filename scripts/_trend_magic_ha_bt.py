"""
Enhanced Trend Magic + Swings — Heikin Ashi backtest.

Sinyaller HA mumlari uzerinde hesaplanir; fill/PL gercek (regular) close ile yapilir.
Veri: bot.db market_snapshots (~1.9M tick -> OHLC bar).

Kullanim:
    python scripts/_trend_magic_ha_bt.py
    python scripts/_trend_magic_ha_bt.py --tf 3600 --fee 8 --slip 2
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.config import cfg


# ── Pine input defaults ──────────────────────────────────────────────────────

@dataclass
class Params:
    cci_period: int = 14
    atr_period: int = 5
    ma_period: int = 10
    cci_threshold: float = 80.0
    trend_persistence: int = 2
    price_distance: float = 0.8
    swing_range: int = 12
    volume_increase_pct: float = 20.0


# ── Data ─────────────────────────────────────────────────────────────────────

def load_ticks(db_path: str) -> tuple[np.ndarray, np.ndarray]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT ts, price FROM market_snapshots WHERE price > 0 ORDER BY ts ASC"
    )
    ts_list: list[float] = []
    px_list: list[float] = []
    last_ts = -1.0
    for ts, px in rows:
        ts = float(ts)
        px = float(px)
        if ts <= last_ts:
            continue
        last_ts = ts
        ts_list.append(ts)
        px_list.append(px)
    con.close()
    return np.array(ts_list, dtype=np.float64), np.array(px_list, dtype=np.float64)


def build_ohlc(ts: np.ndarray, px: np.ndarray, tf_sec: int) -> dict[str, np.ndarray]:
    buckets = (ts // tf_sec).astype(np.int64)
    uniq, first_idx, counts = np.unique(buckets, return_index=True, return_counts=True)
    n = len(uniq)
    o = np.empty(n)
    h = np.empty(n)
    l = np.empty(n)
    c = np.empty(n)
    bar_ts = uniq.astype(np.float64) * tf_sec
    for i, start in enumerate(first_idx):
        end = start + counts[i]
        seg = px[start:end]
        o[i] = seg[0]
        h[i] = seg.max()
        l[i] = seg.min()
        c[i] = seg[-1]
    return {"ts": bar_ts, "open": o, "high": h, "low": l, "close": c}


def heikin_ashi(o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray) -> dict[str, np.ndarray]:
    n = len(c)
    ha_c = (o + h + l + c) / 4.0
    ha_o = np.empty(n)
    ha_h = np.empty(n)
    ha_l = np.empty(n)
    ha_o[0] = (o[0] + c[0]) / 2.0
    for i in range(1, n):
        ha_o[i] = (ha_o[i - 1] + ha_c[i - 1]) / 2.0
    ha_h = np.maximum(h, np.maximum(ha_o, ha_c))
    ha_l = np.minimum(l, np.minimum(ha_o, ha_c))
    return {"open": ha_o, "high": ha_h, "low": ha_l, "close": ha_c}


# ── Indicators (on HA series) ────────────────────────────────────────────────

def ema(arr: np.ndarray, period: int) -> np.ndarray:
    out = np.full(len(arr), np.nan)
    if period <= 0 or len(arr) < period:
        return out
    alpha = 2.0 / (period + 1)
    out[period - 1] = np.nanmean(arr[:period])
    for i in range(period, len(arr)):
        out[i] = alpha * arr[i] + (1 - alpha) * out[i - 1]
    return out


def atr(h: np.ndarray, l: np.ndarray, c: np.ndarray, period: int) -> np.ndarray:
    n = len(c)
    tr = np.zeros(n)
    tr[0] = h[0] - l[0]
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    return ema(tr, period)


def cci(hlc3: np.ndarray, period: int) -> np.ndarray:
    n = len(hlc3)
    out = np.full(n, np.nan)
    for i in range(period - 1, n):
        win = hlc3[i - period + 1 : i + 1]
        m = win.mean()
        mad = np.mean(np.abs(win - m))
        out[i] = 0.0 if mad == 0 else (hlc3[i] - m) / (0.015 * mad)
    return out


def is_swing_high(i: int, high: np.ndarray, swing_range: int) -> bool:
    if i < swing_range or i + swing_range >= len(high):
        return False
    hi = high[i]
    for j in range(1, swing_range + 1):
        if hi <= high[i - j] or hi <= high[i + j]:
            return False
    return True


def is_swing_low(i: int, low: np.ndarray, swing_range: int) -> bool:
    if i < swing_range or i + swing_range >= len(low):
        return False
    lo = low[i]
    for j in range(1, swing_range + 1):
        if lo >= low[i - j] or lo >= low[i + j]:
            return False
    return True


def trend_valid(i: int, up: bool, close: np.ndarray, ma: np.ndarray, persistence: int) -> bool:
    need = max(1, int(persistence * 2 / 3))
    count = 0
    for k in range(persistence):
        j = i - k
        if j < 0:
            break
        if np.isnan(ma[j]):
            continue
        if up and close[j] > ma[j]:
            count += 1
        elif not up and close[j] < ma[j]:
            count += 1
    return count >= need


# ── Strategy engine ──────────────────────────────────────────────────────────

@dataclass
class Trade:
    side: str
    entry_i: int
    entry_px: float
    qty: float
    exit_i: int = -1
    exit_px: float = 0.0
    pnl_bps: float = 0.0


def run_backtest(
    real: dict[str, np.ndarray],
    ha: dict[str, np.ndarray],
    p: Params,
    *,
    fee_bps: float = 8.0,
    slip_bps: float = 2.0,
) -> list[Trade]:
    n = len(real["close"])
    hlc3 = (ha["high"] + ha["low"] + ha["close"]) / 3.0
    cci_v = cci(hlc3, p.cci_period)
    atr_v = atr(ha["high"], ha["low"], ha["close"], p.atr_period)
    ma_v = ema(ha["close"], p.ma_period)

    swing_high = np.zeros(n, dtype=bool)
    swing_low = np.zeros(n, dtype=bool)
    for i in range(n):
        swing_high[i] = is_swing_high(i, ha["high"], p.swing_range)
        swing_low[i] = is_swing_low(i, ha["low"], p.swing_range)

    trend_dir = np.zeros(n, dtype=np.int8)
    buffer = np.full(n, np.nan)
    tdir = 0
    buf = np.nan

    for i in range(n):
        if np.isnan(cci_v[i]) or np.isnan(atr_v[i]) or np.isnan(ma_v[i]):
            trend_dir[i] = tdir
            buffer[i] = buf
            continue

        pot_up = ha["close"][i] > ma_v[i] and cci_v[i] >= p.cci_threshold
        pot_dn = ha["close"][i] < ma_v[i] and cci_v[i] <= -p.cci_threshold
        if swing_low[i]:
            pot_up = True
        if swing_high[i]:
            pot_dn = True

        up = pot_up and trend_valid(i, True, ha["close"], ma_v, p.trend_persistence)
        dn = pot_dn and trend_valid(i, False, ha["close"], ma_v, p.trend_persistence)

        dist = abs(ha["close"][i] - ma_v[i]) / atr_v[i] if atr_v[i] else 0.0
        sig_move = dist >= p.price_distance

        if up and (sig_move or tdir == 1):
            tdir = 1
            buf = ha["low"][i] - atr_v[i] * 0.5
            if not np.isnan(buffer[i - 1] if i else np.nan):
                buf = max(buf, buffer[i - 1])
        elif dn and (sig_move or tdir == -1):
            tdir = -1
            buf = ha["high"][i] + atr_v[i] * 0.5
            if not np.isnan(buffer[i - 1] if i else np.nan):
                buf = min(buf, buffer[i - 1])
        else:
            pass  # keep tdir/buf

        trend_dir[i] = tdir
        buffer[i] = buf

    trades: list[Trade] = []
    pos: Trade | None = None
    qty = 1.0
    last_pl: float | None = None

    def close_trade(i: int, reason_close: bool = True) -> float:
        nonlocal pos, last_pl
        if pos is None:
            return 0.0
        px = real["close"][i]
        pos.exit_i = i
        pos.exit_px = px
        if pos.side == "LONG":
            raw_bps = (px - pos.entry_px) / pos.entry_px * 1e4
        else:
            raw_bps = (pos.entry_px - px) / pos.entry_px * 1e4
        pos.pnl_bps = raw_bps - fee_bps - 2 * slip_bps
        last_pl = pos.pnl_bps if reason_close else last_pl
        trades.append(pos)
        pl = pos.pnl_bps
        pos = None
        return pl

    warmup = max(p.cci_period, p.atr_period, p.ma_period, p.swing_range * 2) + 2
    for i in range(warmup, n):
        td = trend_dir[i]
        td_prev = trend_dir[i - 1]

        # Close long when trend flips down
        if pos and pos.side == "LONG" and td == -1:
            pl = close_trade(i)
            if pl > 0:
                qty = 1.0

        # Close short when trend flips up
        if pos and pos.side == "SHORT" and td == 1:
            pl = close_trade(i)
            if pl > 0:
                qty = 1.0

        # Entry on flip
        if td == 1 and td_prev == -1:
            if pos and pos.side == "SHORT":
                close_trade(i)
            if last_pl is not None and last_pl < 0:
                qty *= 1.0 + p.volume_increase_pct / 100.0
            pos = Trade("LONG", i, real["close"][i], qty)

        elif td == -1 and td_prev == 1:
            if pos and pos.side == "LONG":
                close_trade(i)
            if last_pl is not None and last_pl < 0:
                qty *= 1.0 + p.volume_increase_pct / 100.0
            pos = Trade("SHORT", i, real["close"][i], qty)

    if pos is not None:
        close_trade(n - 1)
    return trades


# ── Reporting ────────────────────────────────────────────────────────────────

def summarize(trades: list[Trade], label: str = "") -> dict:
    if not trades:
        print(f"{label:40} islem=0")
        return {}
    pnls = np.array([t.pnl_bps * t.qty for t in trades])
    wins = int((pnls > 0).sum())
    tot = float(pnls.sum())
    wr = 100.0 * wins / len(trades)
    print(
        f"{label:40} net={tot:+8.1f} bps | isl={len(trades):4d} | "
        f"isabet={wr:5.1f}% | avg={pnls.mean():+.1f} bps"
    )
    return {"net": tot, "n": len(trades), "wr": wr}


def walk_forward(trades: list[Trade], bar_ts: np.ndarray, train_frac: float = 0.6) -> None:
    if not trades:
        return
    split_ts = bar_ts[int(len(bar_ts) * train_frac)]
    tr = [t for t in trades if bar_ts[t.entry_i] < split_ts]
    oos = [t for t in trades if bar_ts[t.entry_i] >= split_ts]
    summarize(tr, "TRAIN")
    summarize(oos, "OOS")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(cfg.DB_PATH))
    ap.add_argument("--tf", type=int, default=900, help="Bar saniye (900=15m, 3600=1h)")
    ap.add_argument("--fee", type=float, default=8.0, help="Round-trip fee bps")
    ap.add_argument("--slip", type=float, default=2.0, help="Slippage bps per side")
    args = ap.parse_args()

    print(f"DB: {args.db}")
    print("Tick yukleniyor...")
    ts, px = load_ticks(args.db)
    print(f"  tick={len(px):,}  fiyat={px[0]:.2f}->{px[-1]:.2f}")

    real = build_ohlc(ts, px, args.tf)
    ha = heikin_ashi(real["open"], real["high"], real["low"], real["close"])
    n = len(real["close"])
    tf_label = f"{args.tf // 60}m" if args.tf < 3600 else f"{args.tf // 3600}h"
    print(f"  bar={n:,} tf={tf_label} HA=evet")

    p = Params()
    trades = run_backtest(real, ha, p, fee_bps=args.fee, slip_bps=args.slip)

    print(f"\n=== Enhanced Trend Magic + Swings | HA sinyal, gercek fill | fee={args.fee} slip={args.slip} ===")
    summarize(trades, f"ALL ({tf_label})")
    walk_forward(trades, real["ts"])

    # Buy & hold benchmark
    bh = (real["close"][-1] - real["close"][0]) / real["close"][0] * 1e4
    print(f"\nBuy&Hold: {bh:+.0f} bps")

    # Multi-TF quick scan
    if args.tf == 900:
        print("\n--- Diger timeframe karsilastirma ---")
        for tf, lbl in ((300, "5m"), (1800, "30m"), (3600, "1h")):
            r = build_ohlc(ts, px, tf)
            h = heikin_ashi(r["open"], r["high"], r["low"], r["close"])
            tr = run_backtest(r, h, p, fee_bps=args.fee, slip_bps=args.slip)
            summarize(tr, lbl)


if __name__ == "__main__":
    main()
