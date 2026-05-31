"""Belirli 15m kapanış anında bot ne görürdü? python scripts/replay_at.py "2026-05-22 11:15" """
from __future__ import annotations

import sys
import os
from collections import deque
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.config import cfg
from core import state as st
from dashboard.binance_chart import fetch_15m_klines, fetch_1h_klines, fetch_1m_klines
from engine.bars_1m import set_bars_1m
from engine.structure import _detect_swings, _determine_structure
import engine.structure as struct_mod
from engine.trend import update_trend, momentum_explain
from engine.structure_explain import analyze_structure_at


def parse_when(s: str) -> float:
    s = s.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    raise SystemExit(f"Tarih parse edilemedi: {s}")


def main():
    when = sys.argv[1] if len(sys.argv) > 1 else "2026-05-22 11:15"
    target_ts = parse_when(when)
    label = datetime.fromtimestamp(target_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    bars_all = fetch_15m_klines(96)
    bars_1h_all = fetch_1h_klines(48)
    bars = [b for b in bars_all if b["ts"] <= target_ts]
    bars_1h = [b for b in bars_1h_all if b["ts"] <= target_ts]

    print(f"=== {label} (15m kapanis) ===\n")
    print("Son 8 mum:")
    for b in bars[-8:]:
        t = datetime.fromtimestamp(b["ts"], tz=timezone.utc).strftime("%H:%M")
        col = "Y" if b["close"] > b["open"] else "K"
        ch = (b["close"] - b["open"]) / b["open"] * 100
        mark = " <-- secilen" if abs(b["ts"] - target_ts) < 1 else ""
        print(f"  {t} {col}  {b['open']:.1f} -> {b['close']:.1f}  ({ch:+.2f}%){mark}")

    h, l = _detect_swings(bars, cfg.SWING_LB_15M)
    s15 = _determine_structure(h, l)
    h1, l1 = _detect_swings(bars_1h, cfg.SWING_LB_1H)
    s1h = _determine_structure(h1, l1)

    print(f"\n15m yapi: {s15}")
    print(f"  son tepeler: {[round(x['price'], 1) for x in h[-3:]]}")
    print(f"  son dipler:  {[round(x['price'], 1) for x in l[-3:]]}")
    print(f"1h yapi: {s1h}")
    print(f"  son tepeler: {[round(x['price'], 1) for x in h1[-3:]]}")
    print(f"  son dipler:  {[round(x['price'], 1) for x in l1[-3:]]}")

    cutoff_1m = target_ts - cfg.PULSE_BARS_1M * 60
    b1 = [b for b in fetch_1m_klines(120) if cutoff_1m < b["ts"] <= target_ts + 60]
    set_bars_1m(b1[-cfg.PULSE_BARS_1M :])
    print(f"1m nabiz: {len(b1)} mum (son {cfg.PULSE_BARS_1M}dk)")

    struct_mod._bars_15m = deque(bars, maxlen=200)
    st.structure_15m = s15
    st.structure_1h = s1h
    st.forming_15m = {}
    tv = update_trend("replay")
    print(f"\nTrend: {tv['bias']}  guc={tv['strength']}%  (up={tv['up_score']} down={tv['down_score']})")
    for ln in (tv.get("chart_lines") or []):
        print(" ", ln.replace("\u2192", "->").replace("\u2191", "^").replace("\u2193", "v"))
    print(momentum_explain(bars[-cfg.TIMING_BARS_15M :]))
    print(f"\nTrade kapisi: guc>=60 + yapı uyumu -> {tv['trade_ok']}")

    exp = analyze_structure_at(target_ts)
    print(f"\n--- Yapı açıklama (15m) ---")
    p = exp.get("15m", {})
    print(p.get("rule", ""))
    print(f"  tepeler {p.get('high_prices')} ({p.get('high_trend')})")
    print(f"  dipler  {p.get('low_prices')} ({p.get('low_trend')})")


if __name__ == "__main__":
    main()
