"""Pine L1-L8 her bar: 1975 yakin seviye var mi?"""
import asyncio

import numpy as np

from feeds.chart_backfill import backfill_15m_bars
from engine.sr_calculator import find_pine_sr_ultimate, bars_to_ohlcv
from engine.v3_common import bars_15m


async def main() -> None:
    await backfill_15m_bars(500)
    bars = bars_15m(500)
    h, l, c, v = bars_to_ohlcv(bars)
    px = float(c[-1])
    print(f"son close={px:.2f} n={len(c)}")

    lvls = find_pine_sr_ultimate(
        h, l, c, v, source="close", num_lines_to_show=8, include_prev_quick_support=False
    )
    print("simdiki L1-L8:")
    for x in lvls:
        print(f"  L{x.slot} {x.price:.2f} dir={x.direction}")

    near = [x for x in lvls if abs(x.price - 1975) < 2.0]
    print("1975+-2:", [(x.slot, x.price) for x in near] or "yok")

    # son 40 barda L1-L2 degisimi
    print("\nson 40 bar L1/L2:")
    for end in range(len(c) - 40, len(c)):
        sub_h, sub_l, sub_c, sub_v = h[: end + 1], l[: end + 1], c[: end + 1], v[: end + 1]
        if len(sub_c) < 70:
            continue
        sub_lv = find_pine_sr_ultimate(
            sub_h, sub_l, sub_c, sub_v, source="close", num_lines_to_show=6
        )
        by_slot = {x.slot: x.price for x in sub_lv}
        l1 = by_slot.get(1)
        l2 = by_slot.get(2)
        if l1 and abs(l1 - 1975) < 1.5:
            print(f"  bar {end} close={sub_c[-1]:.2f} L1={l1:.2f} L2={by_slot.get(2, 0):.2f}")
        if l2 and abs(l2 - 1975) < 1.5:
            print(f"  bar {end} close={sub_c[-1]:.2f} L1={by_slot.get(1, 0):.2f} L2={l2:.2f}")


if __name__ == "__main__":
    asyncio.run(main())
