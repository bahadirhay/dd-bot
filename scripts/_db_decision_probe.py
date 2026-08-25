#!/usr/bin/env python3
"""Quick probe of v3_attribution for decision-block analysis."""
import json
import sqlite3
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT))
from core.config import cfg

def main(hours: int = 24):
    conn = sqlite3.connect(cfg.DB_PATH)
    conn.row_factory = sqlite3.Row
    cutoff = time.time() - hours * 3600
    n = conn.execute(
        "SELECT COUNT(*) FROM v3_attribution WHERE ts > ? AND action='WAIT'",
        (cutoff,),
    ).fetchone()[0]
    print(f"DB: {cfg.DB_PATH}")
    print(f"WAIT rows last {hours}h: {n}\n")

    print("=== reject_reason (24h) ===")
    for r in conn.execute(
        """
        SELECT reject_reason, COUNT(*) cnt
        FROM v3_attribution
        WHERE ts > ? AND action='WAIT'
        GROUP BY reject_reason ORDER BY cnt DESC LIMIT 20
        """,
        (cutoff,),
    ):
        print(f"  {(r['reject_reason'] or '(empty)'):<22} {r['cnt']}")

    print("\n=== scenario (channel waits, 24h) ===")
    for r in conn.execute(
        """
        SELECT scenario, reject_reason, COUNT(*) cnt
        FROM v3_attribution
        WHERE ts > ? AND action='WAIT' AND scenario LIKE 'CHANNEL%'
        GROUP BY scenario, reject_reason ORDER BY cnt DESC LIMIT 15
        """,
        (cutoff,),
    ):
        print(f"  {r['scenario']:<16} {r['reject_reason']:<20} {r['cnt']}")

    print("\n=== reject_layer (24h, context veya kod haritasi) ===")
    layer_ctr = Counter()
    for r in conn.execute(
        """
        SELECT reject_reason, context_json FROM v3_attribution
        WHERE ts > ? AND action='WAIT' AND reject_reason != ''
        """,
        (cutoff,),
    ):
        ctx = {}
        try:
            ctx = json.loads(r["context_json"] or "{}")
        except Exception:
            pass
        ly = ctx.get("reject_layer")
        if not ly:
            from engine.reject_reason_v3 import reject_layer_for

            ly = reject_layer_for(r["reject_reason"] or "")
        layer_ctr[str(ly)] += 1
    for ly, cnt in layer_ctr.most_common():
        print(f"  {ly:<12} {cnt}")

    linked = conn.execute(
        "SELECT COUNT(*) FROM v3_attribution WHERE ts>? AND trade_id IS NOT NULL AND trade_id>0",
        (cutoff,),
    ).fetchone()[0]
    trades = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE open_ts>?", (cutoff,)
    ).fetchone()[0]
    print(f"\n  trade links: {linked} attr / {trades} trades (24h)")

    print("\n=== RR_TOO_LOW samples (sl/tp in reason_text) ===")
    rows = conn.execute(
        """
        SELECT ts_human, price, reason_text, context_json
        FROM v3_attribution
        WHERE ts > ? AND reject_reason='RR_TOO_LOW'
        ORDER BY ts DESC LIMIT 5
        """,
        (cutoff,),
    ).fetchall()
    for r in rows:
        ctx = {}
        try:
            ctx = json.loads(r["context_json"] or "{}")
        except Exception:
            pass
        print(f"  {r['ts_human']} px={r['price']:.2f} | {r['reason_text'][:80]}")
        if ctx:
            print(f"    context keys: {list(ctx.keys())}")

    conn.close()

if __name__ == "__main__":
    h = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    main(h)
