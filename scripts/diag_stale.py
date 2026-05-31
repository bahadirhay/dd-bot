"""Bot state vs taze Binance — takılı mı? PYTHONPATH=. python scripts/diag_stale.py"""
from __future__ import annotations

import time

from core.config import cfg
from core.state import state
from dashboard.binance_chart import fetch_15m_klines, fetch_1h_klines
from engine.structure import (
    _bars_15m,
    _bars_1h,
    add_bar_15m,
    add_bar_1h,
    _update_structure_15m,
    _update_structure_1h,
)
from engine.trend import update_trend


def _scores_from_bars(bars):
    from engine.trend import _bar_direction

    dirs = [_bar_direction(b) for b in bars[-cfg.TREND_BARS_15M :]]
    g = sum(1 for d in dirs if d > 0)
    r = sum(1 for d in dirs if d < 0)
    return g, r


def main():
    now = time.time()
    print("=== BOT STATE (main.py calisiyorsa) ===")
    tv = state.trend_view or {}
    if tv:
        age = int(now - tv.get("ts", 0)) if tv.get("ts") else -1
        print(f"  trend_view: {tv.get('bias')} guc={tv.get('strength')}%  ({age}s once)")
    else:
        print("  trend_view: YOK (bot kapali?)")
    k_age = int(now - state.kline_last_update) if state.kline_last_update else -1
    print(f"  structure: 15m={state.structure_15m} 1h={state.structure_1h}")
    print(f"  kline son guncelleme: {k_age}s once")
    print(f"  bot 15m bar sayisi: {len(_bars_15m)}")

    print("\n=== TAZE BINANCE REST (simdi) ===")
    b15 = fetch_15m_klines(96)
    for c in b15:
        add_bar_15m(c)
    for c in fetch_1h_klines(48):
        add_bar_1h(c)
    _update_structure_15m()
    _update_structure_1h()
    tv2 = update_trend("stale_check")
    g, r = _scores_from_bars(b15)
    print(f"  structure: 15m={state.structure_15m} 1h={state.structure_1h}")
    print(f"  trend: {tv2['bias']} guc={tv2['strength']}% (down={tv2['down_score']} up={tv2['up_score']})")
    print(f"  son {cfg.TREND_BARS_15M} mum: yesil={g} kirmizi={r}")
    print(f"  son mum UTC: {time.strftime('%Y-%m-%d %H:%M', time.gmtime(b15[-1]['ts']))}")

    if tv:
        if abs(tv.get("strength", 0) - tv2["strength"]) <= 3 and tv.get("bias") == tv2["bias"]:
            print("\nSONUC: Bot degeri ile taze hesap UYUMLU — piyasa gercekten benzer (RANGE/UNCLEAR).")
        elif k_age > 300:
            print("\nSONUC: Kline feed ESKI — dashboard takili olabilir; main.py / websocket kontrol.")
        else:
            print("\nSONUC: Fark var — bot bellek vs REST; kline akisini kontrol edin.")


if __name__ == "__main__":
    main()
