#!/usr/bin/env python3
"""CLI: Support & Resistance Calculator (Pine SR Ultimate port)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from engine.sr_calculator import calculate_sr_levels


if __name__ == "__main__":
    np.random.seed(42)
    n = 300
    prices = 2000 + np.cumsum(np.random.randn(n) * 3)
    df_demo = pd.DataFrame(
        {
            "open": prices + np.random.randn(n) * 1,
            "high": prices + np.abs(np.random.randn(n) * 4),
            "low": prices - np.abs(np.random.randn(n) * 4),
            "close": prices + np.random.randn(n) * 1,
            "volume": np.random.randint(100, 5000, n).astype(float),
        }
    )

    result = calculate_sr_levels(
        df_demo,
        use_pivot=True,
        use_poc=False,
        source="close",
        lookback_left=50,
        lookback_right=20,
        quick_right=10,
        num_levels=6,
    )

    px = float(df_demo["close"].iloc[-1])
    lines = result.get("display_lines") or result.get("all") or []
    print("=" * 50)
    print(f"Güncel fiyat: {px:.2f}")
    print("PINE level1-6 (TV cizgileri):")
    for lvl in lines:
        print(
            f"  L{lvl.slot} {lvl.price:>10.2f}  slot={lvl.slot_role}  "
            f"renk={lvl.direction}  güç={lvl.importance}/5  dokunuş={lvl.touches}"
        )
    act_s = result.get("active_support")
    act_r = result.get("active_resistance")
    if act_s and act_r:
        print(f"Aktif bant: S={act_s.price:.2f} R={act_r.price:.2f}")
    print("=" * 50)
