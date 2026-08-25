"""Analyze 10:57-11:04 WAIT window from bot.db."""
import sqlite3
import json
from datetime import datetime
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "bot.db"
# User logs show 10:57 on 2026-06-06 — adjust if needed
START = datetime(2026, 6, 6, 10, 57, 0).timestamp()
END = datetime(2026, 6, 6, 11, 5, 0).timestamp()

def main():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row

    print("=== TABLES ===")
    for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        print(" ", r[0])

    print("\n=== TRADES (recent) ===")
    for r in c.execute(
        "SELECT id, direction, entry_price, exit_price, pnl, pnl_pct, status, open_ts, close_ts, close_reason, notes "
        "FROM trades ORDER BY id DESC LIMIT 5"
    ):
        print(dict(r))

    print("\n=== V3_ATTRIBUTION (window) ===")
    rows = list(
        c.execute(
            """SELECT ts_human, price, action, scenario, intended_side, trade_candidate, entered,
                      blocked, reason_text, primary_block, primary_support, net_score
               FROM v3_attribution WHERE ts BETWEEN ? AND ? ORDER BY ts LIMIT 30""",
            (START, END),
        )
    )
    print(f"count={len(c.execute('SELECT 1 FROM v3_attribution WHERE ts BETWEEN ? AND ?', (START, END)).fetchall())}")
    for r in rows[:15]:
        print(dict(r))
    if rows:
        print("...")
        for r in rows[-3:]:
            print(dict(r))

    print("\n=== PRIMARY_BLOCK distribution ===")
    for r in c.execute(
        """SELECT primary_block, COUNT(*) n FROM v3_attribution
           WHERE ts BETWEEN ? AND ? GROUP BY primary_block ORDER BY n DESC""",
        (START, END),
    ):
        print(dict(r))

    print("\n=== SAMPLE block_reason_json ===")
    r = c.execute(
        """SELECT block_reason_json, trade_reason_json, context_json FROM v3_attribution
           WHERE ts BETWEEN ? AND ? AND blocked=1 LIMIT 1""",
        (START, END),
    ).fetchone()
    if r:
        for k, v in zip(["block_reason_json", "trade_reason_json", "context_json"], r):
            if v:
                try:
                    print(k, json.dumps(json.loads(v), indent=2)[:1500])
                except Exception:
                    print(k, str(v)[:500])

    c.close()

if __name__ == "__main__":
    main()
