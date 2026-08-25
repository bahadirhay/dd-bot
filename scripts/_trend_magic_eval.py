"""Tam TF değerlendirmesi — Binance resmi kline, walk-forward, canli kapı."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.trend_magic_eval import evaluate_all, format_report, live_gate_reason, save_eval


def main() -> None:
    print("Binance kline yukleniyor (5m/15m/30m/1h)...")
    result = evaluate_all(limit=1500)
    save_eval(result)
    print(format_report(result))
    print(f"\nKayit: data/tm_eval.json")
    gate = live_gate_reason()
    if gate:
        print(f"\nCanli gate (su anki config): BLOK — {gate}")
    else:
        print("\nCanli gate: GECTI (V3_STRATEGY_TM_ENABLED=true ise)")


if __name__ == "__main__":
    main()
