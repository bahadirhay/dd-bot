#!/usr/bin/env python3
"""Destek seviyesi skor/strength diagnostigi."""
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import cfg
from engine.levels_v3 import level_trade_ready, update_levels
from engine.decision_v3 import update_decision
from engine.v3_common import bars_15m


def main():
    target_price = float(sys.argv[1]) if len(sys.argv) > 1 else 2012.96
    when = sys.argv[2] if len(sys.argv) > 2 else ""

    snap = update_levels()
    active = snap.get("active") or {}
    sup = active.get("support") or {}
    res = active.get("resistance") or {}
    merged = snap.get("levels") or []

    print("=== CANLI AKTIF BANT ===")
    print(
        f"Destek: {sup.get('price')} skor={sup.get('score')} guc={sup.get('strength')}"
    )
    print(
        f"Direnc: {res.get('price')} skor={res.get('score')} guc={res.get('strength')}"
    )
    print(
        f"Fiyat: {snap.get('price')} zone={active.get('zone')} "
        f"locked={active.get('locked')} range_valid={active.get('range_valid')}"
    )
    print()

    matches = [
        l
        for l in merged
        if abs(float(l.get("price", 0) or 0) - target_price) < 1.5
        and str(l.get("kind")) == "support"
    ]
    print(f"=== MERGED destek ~{target_price} ({len(matches)} aday) ===")
    for m in sorted(matches, key=lambda x: -int(x.get("score", 0) or 0)):
        print(
            f"  {m.get('price')} skor={m.get('score')} guc={m.get('strength')} "
            f"tf={m.get('timeframe')} shelf={m.get('shelf_bars')} "
            f"touch={m.get('touch_count')} fb={m.get('failed_break_count')}"
        )

    persist = json.loads((ROOT / "data" / "v3_active_levels.json").read_text(encoding="utf-8"))
    ps = persist.get("support") or {}
    pr = persist.get("resistance") or {}
    print()
    print("=== PERSIST ===")
    print(f"  support  {ps.get('price')} skor={ps.get('score')} guc={ps.get('strength')}")
    print(f"  resist   {pr.get('price')} skor={pr.get('score')} guc={pr.get('strength')}")

    print()
    print("=== KALITE KAPILARI ===")
    levels_snap = {
        "support": sup,
        "resistance": res,
        "active_support": float(sup.get("price", 0) or 0),
        "active_resistance": float(res.get("price", 0) or 0),
        "range_valid": active.get("range_valid"),
        "zone": active.get("zone"),
    }
    min_range = int(getattr(cfg, "V3_MIN_RANGE_SCORE", 10))
    min_med = int(getattr(cfg, "V3_LEVEL_SCORE_MEDIUM", 4))
    s_score = int(sup.get("score", 0) or 0)
    r_score = int(res.get("score", 0) or 0)
    print(f"  V3_MIN_RANGE_SCORE={min_range}  V3_LEVEL_SCORE_MEDIUM={min_med}")
    bars15 = bars_15m(80)
    ready_b, detail_b = level_trade_ready(bars15, levels_snap, "BUY")
    ready_s, detail_s = level_trade_ready(bars15, levels_snap, "SELL")
    print(f"  scenario BUY:  {'OK' if ready_b else detail_b}")
    print(f"  scenario SELL: {'OK' if ready_s else detail_s}")
    print(
        f"  decision support_ok={s_score >= min_med} "
        f"resistance_ok={r_score >= min_med}"
    )

    dec = update_decision()
    print()
    print("=== SON V3 KARAR ===")
    print(dec.get("reason", "—"))
    entry = dec.get("entry") or {}
    scn = dec.get("scenario") or {}
    print(
        f"  senaryo={scn.get('name')} giris_valid={entry.get('valid')} "
        f"SL={entry.get('sl')} RR={entry.get('rr')}"
    )

    if when:
        print()
        print(f"=== DB (journal, {when}) ===")
        dt = datetime.strptime(when, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        # TR giris -> UTC (UTC+3)
        from datetime import timedelta

        dt_utc = dt - timedelta(hours=3)
        target_ts = dt_utc.timestamp()
        db = sqlite3.connect(ROOT / "data" / "bot.db")
        rows = db.execute(
            """
            SELECT ts_human, kind, price, note FROM market_snapshots
            WHERE ts BETWEEN ? AND ? ORDER BY abs(ts - ?) LIMIT 3
            """,
            (target_ts - 900, target_ts + 900, target_ts),
        ).fetchall()
        for r in rows:
            print(f"  {r}")


if __name__ == "__main__":
    main()
