"""Trend Magic 30m paper — forward dogrulama raporu (1-2 hafta)."""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.config import cfg


def _fmt_ts(ts: float) -> str:
    if not ts:
        return "?"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def report_tmagic_paper(tf_sec: int = 1800) -> None:
    con = sqlite3.connect(cfg.DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT side, entry, exit, pnl_bps, reason, status, open_ts, close_ts, tf_sec
        FROM tmagic_paper
        WHERE tf_sec = ?
        ORDER BY open_ts ASC
        """,
        (tf_sec,),
    ).fetchall()
    con.close()

    started = {}
    sp = Path("data/tm_paper_started.json")
    if sp.exists():
        try:
            started = json.loads(sp.read_text(encoding="utf-8"))
        except Exception:
            pass

    print(f"\n=== Trend Magic PAPER | tf={tf_sec}s ({tf_sec // 60}m) ===")
    if started.get("started"):
        print(f"  Baslangic: {_fmt_ts(started['started'])}")
        days = (time.time() - started["started"]) / 86400
        print(f"  Sure: {days:.1f} gun")

    closed = [r for r in rows if r["status"] == "CLOSED" and r["pnl_bps"] is not None]
    open_ = [r for r in rows if r["status"] == "OPEN"]
    flips = [r for r in rows if r["status"] == "OPEN" and r["reason"] is None]

    if not rows:
        print("  Henuz kayit yok — bot calisiyor mu? [TM-PAPER] loglarini kontrol et.")
        return

    if closed:
        pnls = [float(r["pnl_bps"]) for r in closed]
        wins = sum(1 for x in pnls if x > 0)
        print(f"\n  Kapali islem: {len(closed)}")
        print(f"  Net PnL: {sum(pnls):+.1f} bps")
        print(f"  Isabet: {100 * wins / len(closed):.1f}%")
        print(f"  Ort/islem: {sum(pnls) / len(closed):+.1f} bps")
        print("\n  Son 10 kapali:")
        for r in closed[-10:]:
            print(
                f"    {_fmt_ts(r['open_ts'])} {r['side']:5} "
                f"{r['entry']:.2f}->{float(r['exit'] or 0):.2f} "
                f"pnl={float(r['pnl_bps']):+.1f}bps ({r['reason']})"
            )

    if open_:
        print(f"\n  Acik shadow: {len(open_)}")
        for r in open_:
            print(f"    {r['side']} entry={r['entry']:.2f} since {_fmt_ts(r['open_ts'])}")

    print(f"\n  Toplam flip kaydi (OPEN shadow): {len([r for r in rows if r['status']=='OPEN' and not r['close_ts']])}")


def report_paper_trades() -> None:
    """PAPER_MODE executor trades (v3_strategy=TM)."""
    con = sqlite3.connect(cfg.DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """
            SELECT side, entry_price, exit_price, pnl_pct, status, open_ts, close_ts, notes
            FROM trades
            WHERE notes LIKE '%STRATEGY_TM%' OR notes LIKE '%v3_strategy%TM%'
            ORDER BY open_ts DESC LIMIT 30
            """
        ).fetchall()
    except Exception:
        rows = []
    con.close()

    print("\n=== Paper executor (trades tablosu, TM) ===")
    if not rows:
        print("  Henuz TM paper trade yok.")
        return
    closed = [r for r in rows if r["status"] == "CLOSED"]
    if closed:
        pnls = [float(r["pnl_pct"] or 0) * 100 for r in closed]
        print(f"  Kapali: {len(closed)} | net={sum(pnls):+.0f} bps equiv | wr={100*sum(1 for x in pnls if x>0)/len(closed):.0f}%")


def main() -> None:
    tf = int(getattr(cfg, "V3_TREND_MAGIC_TF_SEC", 1800) or 1800)
    report_tmagic_paper(tf)
    report_paper_trades()
    print("\n  Haftalik: python scripts/tm_paper_report.py")


if __name__ == "__main__":
    main()
