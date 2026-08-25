#!/usr/bin/env python3
"""Canli bot state veya son log'dan Structure/Trend cift sayim raporu."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    p = argparse.ArgumentParser(description="V3 score attribution rolling ozet")
    p.add_argument(
        "--json",
        type=str,
        default="",
        help="state export JSON (v3_score_attribution_rolling)",
    )
    args = p.parse_args()

    summary = None
    if args.json:
        data = json.loads(Path(args.json).read_text(encoding="utf-8"))
        summary = data.get("v3_score_attribution_rolling") or data
    else:
        from core.state import state

        summary = getattr(state, "v3_score_attribution_rolling", None)

    if not summary or int(summary.get("count", 0) or 0) <= 0:
        print("Ornek yok. Bot calisirken [ATTRIBUTION_ROLLING] loglarini bekleyin.")
        return 1

    from engine.direction_score_v3 import format_attribution_rolling_stats

    print(format_attribution_rolling_stats(summary))
    avg = summary.get("avg_pct") or {}
    st = float(summary.get("structure_trend_combined_pct", 0) or 0)
    print()
    if st >= 55:
        print("Yorum: Structure+Trend yuksek — ayni rejim iki modulde agirlikli olabilir.")
    else:
        print("Yorum: Structure+Trend orta/dusuk — moduller daha bagimsiz.")
    for k, v in sorted(avg.items(), key=lambda x: -x[1]):
        if v > 0:
            print(f"  {k}: {v:.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
