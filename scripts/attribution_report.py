#!/usr/bin/env python3
"""
scripts/attribution_report.py — V3 attribution ozet raporu.

Kullanim:
  python scripts/attribution_report.py
  python scripts/attribution_report.py --hours 336

Cikti: hangi blok kac firsati engelledi, kac tanesi sonradan kârli gorunuyordu.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from botlog.db import (
    attribution_block_stats,
    attribution_blocked_with_outcome,
    init,
)


def main() -> None:
    p = argparse.ArgumentParser(description="V3 attribution raporu")
    p.add_argument("--hours", type=int, default=24 * 14, help="Son N saat")
    p.add_argument("--forward", type=float, default=4.0, help="Counterfactual saat")
    args = p.parse_args()

    init()
    hours = max(int(args.hours), 1)
    forward_sec = float(args.forward) * 3600.0

    print(f"\n=== V3 Attribution — son {hours} saat ===\n")

    blocks = attribution_block_stats(hours=hours)
    if not blocks:
        print("Henuz v3_attribution kaydi yok. Bot V3 ile calistirildikten sonra dolar.\n")
        return

    print("--- Engel sayilari (primary_block) ---")
    for row in blocks:
        pb = row.get("primary_block") or "?"
        cnt = int(row.get("cnt") or 0)
        print(f"  {pb:20s}  {cnt:4d} engel")

    outcomes = attribution_blocked_with_outcome(hours=hours, forward_sec=forward_sec)
    by_block: dict[str, list] = defaultdict(list)
    for o in outcomes:
        by_block[str(o.get("primary_block") or "other")].append(o)

    print(f"\n--- Counterfactual (~{args.forward}h sonraki fiyat, market_snapshots) ---")
    total_blocked = len(outcomes)
    total_would_win = sum(1 for o in outcomes if o.get("outcome") == "would_win")
    unknown = sum(1 for o in outcomes if o.get("outcome") == "unknown")
    print(f"  Toplam engellenen firsat: {total_blocked}")
    print(f"  Veri var + hareket uygun (would_win): {total_would_win}")
    print(f"  Veri yok (unknown): {unknown}")
    if total_blocked - unknown > 0:
        pct = total_would_win / max(total_blocked - unknown, 1) * 100
        print(f"  Engel sonrasi 'kârlı gorunen' oran: {pct:.1f}%")

    print("\n--- Blok bazinda (would_win / toplam) ---")
    for pb, rows in sorted(by_block.items(), key=lambda x: -len(x[1])):
        ww = sum(1 for r in rows if r.get("outcome") == "would_win")
        known = sum(1 for r in rows if r.get("outcome") != "unknown")
        print(f"  {pb:20s}  {ww:3d} / {len(rows):3d}  (bilinen: {known})")

    print("\n--- trade_reason ortalamalari (acilan islemler) ---")
    try:
        import sqlite3
        from core.config import cfg

        conn = sqlite3.connect(cfg.DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT trade_reason_json, net_score
            FROM v3_attribution
            WHERE ts > ? AND entered=1
            """,
            (__import__("time").time() - hours * 3600,),
        ).fetchall()
        sums: dict[str, list[int]] = defaultdict(list)
        for r in rows:
            tr = json.loads(r["trade_reason_json"] or "{}")
            for k, v in tr.items():
                sums[k].append(int(v))
        for k, vals in sorted(sums.items()):
            avg = sum(vals) / len(vals) if vals else 0
            print(f"  {k:16s}  ort={avg:.1f}  n={len(vals)}")
        conn.close()
    except Exception as e:
        print(f"  (trade_reason ozet atlandi: {e})")

    print("\nRapor tamam. Yeni ozellik eklemeden once 3-4 hafta veri biriktir.\n")


if __name__ == "__main__":
    main()
