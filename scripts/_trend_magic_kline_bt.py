"""Trend Magic — Binance RESMI kline backtest (Pine parity engine)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.trend_magic_v3 import TrendMagicParams, fetch_binance_bars, run_backtest


def rep(label: str, trades, train_frac: float = 0.6) -> None:
    if not trades:
        print(f"{label:36} islem=0")
        return
    pnls = [t.pnl_bps * t.qty for t in trades]
    n = len(trades)
    split = int(n * train_frac)
    tr = sum(pnls[:split])
    oo = sum(pnls[split:])
    print(
        f"{label:36} net={sum(pnls):+7.0f} | isl={n:3d} | isabet={100*sum(1 for x in pnls if x>0)/n:4.1f}% "
        f"| TRAIN={tr:+.0f} OOS={oo:+.0f}"
    )


def main() -> None:
    p = TrendMagicParams()
    print("=== Binance RESMI kline | Trend Magic HA | fee8 slip2 | martingale ===\n")
    for interval, lbl in (("30m", "30m"), ("1h", "1h"), ("15m", "15m")):
        bars = fetch_binance_bars(interval, 1500)
        if len(bars) < 100:
            print(f"{lbl}: veri yok ({len(bars)} bar)")
            continue
        tr = run_backtest(bars, p, fee_bps=8.0, slip_bps=2.0)
        rep(f"{lbl} (n={len(bars)})", tr)
        if bars:
            bh = (bars[-1]["close"] - bars[0]["close"]) / bars[0]["close"] * 1e4
            print(f"  Buy&Hold: {bh:+.0f} bps\n")


if __name__ == "__main__":
    main()
