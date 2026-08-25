#!/usr/bin/env python3
"""
scripts/reject_reason_report.py — Reddedilen sinyal ozeti (TEK REJECT_REASON).

Kullanim:
  python scripts/reject_reason_report.py
  python scripts/reject_reason_report.py --limit 100
  python scripts/reject_reason_report.py --hours 48
  python scripts/reject_reason_report.py --session
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _print_table(title: str, rows: list[tuple[str, int]]) -> None:
    print(f"\n=== {title} ===\n")
    if not rows:
        print("(kayit yok)\n")
        return
    w = max(len(r[0]) for r in rows)
    print(f"{'Sebep':<{w}}  Adet")
    print(f"{'-' * w}  ----")
    total = 0
    for code, cnt in rows:
        print(f"{code:<{w}}  {cnt:4d}")
        total += cnt
    print(f"{'TOPLAM':<{w}}  {total:4d}")
    print()


def main() -> None:
    p = argparse.ArgumentParser(description="V3 REJECT_REASON raporu")
    p.add_argument("--limit", type=int, default=100, help="Son N WAIT kaydi (DB)")
    p.add_argument("--hours", type=int, default=0, help="Son N saat (0=limit modu)")
    p.add_argument(
        "--session",
        action="store_true",
        help="Bu oturum bellegindeki sayaclar (bot calisirken)",
    )
    args = p.parse_args()

    if args.session:
        from engine.reject_reason_v3 import get_reject_counters

        rows = sorted(
            get_reject_counters().items(),
            key=lambda x: -x[1],
        )
        _print_table("Oturum REJECT sayaclari", rows)
        return

    from botlog.db import init, reject_reason_stats

    init()
    hours = int(args.hours or 0)
    limit = max(int(args.limit), 1)
    title = (
        f"Son {hours} saat REJECT_REASON"
        if hours > 0
        else f"Son {limit} reddedilen sinyal (DB)"
    )
    rows = reject_reason_stats(limit=limit, hours=hours)
    _print_table(title, rows)

    if not rows:
        print(
            "Ipucu: Botu V3 ile calistirin; logda [REJECT] NO_ACTIVE_LEVEL #N gorunur.\n"
            "Oturum sayaci: python scripts/reject_reason_report.py --session\n"
        )


if __name__ == "__main__":
    main()
