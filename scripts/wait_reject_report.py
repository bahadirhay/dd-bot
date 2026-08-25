#!/usr/bin/env python3
"""Son N WAIT — REJECT_REASON yuzdeleri."""
from __future__ import annotations

import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import cfg

ALIAS = {
    "NO_ACTIVE_LEVEL": "NO_ACTIVE_LEVEL",
    "RR_TOO_LOW": "RR_BLOCK",
    "SCENARIO_WAIT": "SCENARIO_BLOCK",
    "STRUCTURE_BLOCK": "STRUCTURE_BLOCK",
    "CLUSTER_BLOCK": "CLUSTER_BLOCK",
    "VERDICT_BLOCK": "VERDICT_BLOCK",
    "CVD_BLOCK": "CVD_BLOCK",
}

PB_MAP = {
    "levels_weak": "NO_ACTIVE_LEVEL",
    "range_invalid": "NO_ACTIVE_LEVEL",
    "rr": "RR_BLOCK",
    "scenario_wait": "SCENARIO_BLOCK",
    "zone_mid": "SCENARIO_BLOCK",
    "collapse": "STRUCTURE_BLOCK",
    "cvd": "CVD_BLOCK",
    "cluster": "CLUSTER_BLOCK",
    "liquidity_chase": "CLUSTER_BLOCK",
    "expected_move": "CLUSTER_BLOCK",
    "verdict_timing": "VERDICT_BLOCK",
}

ORDER = [
    "NO_ACTIVE_LEVEL",
    "RR_BLOCK",
    "SCENARIO_BLOCK",
    "STRUCTURE_BLOCK",
    "CLUSTER_BLOCK",
    "VERDICT_BLOCK",
    "CVD_BLOCK",
]


def classify(row: sqlite3.Row) -> str:
    rr = (row["reject_reason"] or "").strip()
    if rr:
        return ALIAS.get(rr, rr)
    pb = (row["primary_block"] or "").strip().lower()
    if pb in PB_MAP:
        return PB_MAP[pb]
    t = (row["reason_text"] or "").lower()
    if "destek/direnc" in t or "range gecersiz" in t or "kirilim referans" in t:
        return "NO_ACTIVE_LEVEL"
    if "rr yetersiz" in t:
        return "RR_BLOCK"
    if "cvd" in t:
        return "CVD_BLOCK"
    if "collapse" in t:
        return "STRUCTURE_BLOCK"
    if "verdict" in t or "trade_later" in t:
        return "VERDICT_BLOCK"
    if "cluster" in t or "expected move" in t or "em zayif" in t:
        return "CLUSTER_BLOCK"
    if "senaryo" in t or "band ortasinda" in t:
        return "SCENARIO_BLOCK"
    return "UNMAPPED"


def main(limit: int = 200) -> None:
    conn = sqlite3.connect(cfg.DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT reject_reason, primary_block, reason_text
        FROM v3_attribution
        WHERE action = 'WAIT'
        ORDER BY ts DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()

    n = len(rows)
    print(f"Son {n} WAIT (kaynak: v3_attribution)\n")
    if n == 0:
        print("Kayit yok.")
        return

    cats: Counter = Counter()
    for r in rows:
        cats[classify(r)] += 1

    for k in ORDER:
        c = cats.get(k, 0)
        pct = 100.0 * c / n
        print(f"{k:<20} {pct:5.1f}%")

    extra = {k: v for k, v in cats.items() if k not in ORDER}
    if extra:
        ex_n = sum(extra.values())
        print(f"{'(diger)':<20} {100.0 * ex_n / n:5.1f}%  {extra}")
    print(f"\nToplam: {n}")


if __name__ == "__main__":
    lim = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    main(lim)
