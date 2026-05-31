#!/usr/bin/env python3
"""V3 seviye skorlari — REST mumlari ile belirli ana replay."""
from __future__ import annotations

import json
import sys
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import state as st
from core.config import cfg
from dashboard.binance_chart import fetch_15m_klines, fetch_1h_klines, fetch_1m_klines
from engine.bars_1m import set_bars_1m
from engine.levels_v3 import level_trade_ready, update_levels
from engine.scenario_v3 import update_scenario
from engine.decision_v3 import update_decision
from engine.v3_common import bars_15m
from engine import structure as struct_mod
from engine.structure import add_bar_15m, add_bar_1h


def parse_when_tr(s: str) -> float:
    """TR yerel saat (UTC+3) -> unix ts."""
    s = s.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            local = datetime.strptime(s, fmt)
            utc = local - timedelta(hours=3)
            return utc.replace(tzinfo=timezone.utc).timestamp()
            break
        except ValueError:
            continue
    raise SystemExit(f"Tarih parse edilemedi: {s}")


def load_bars_until(target_ts: float) -> None:
    bars_15m_all = fetch_15m_klines(96)
    bars_1h_all = fetch_1h_klines(48)
    bars_1m_all = fetch_1m_klines(480)

    bars_15m = [b for b in bars_15m_all if b["ts"] <= target_ts]
    bars_1h = [b for b in bars_1h_all if b["ts"] <= target_ts]
    bars_1m = [b for b in bars_1m_all if b["ts"] <= target_ts]

    struct_mod._bars_15m = deque(maxlen=200)
    struct_mod._bars_1h = deque(maxlen=100)
    for b in bars_15m:
        c = dict(b)
        c.setdefault("buy_vol", c.get("volume", 0) * 0.5)
        c["sell_vol"] = c.get("volume", 0) - c["buy_vol"]
        c["delta"] = c["buy_vol"] - c["sell_vol"]
        add_bar_15m(c)
    for b in bars_1h:
        c = dict(b)
        c.setdefault("buy_vol", c.get("volume", 0) * 0.5)
        add_bar_1h(c)
    set_bars_1m(bars_1m[-200:])

    if bars_15m:
        st.price = float(bars_15m[-1]["close"])
        st.mark_price = st.price


def main():
    when = sys.argv[1] if len(sys.argv) > 1 else "2026-05-30 15:20"
    target_ts = parse_when_tr(when)
    utc_label = datetime.fromtimestamp(target_ts, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M UTC"
    )
    print(f"=== V3 REPLAY @ {when} TR ({utc_label}) ===\n")

    load_bars_until(target_ts)
    print(f"Fiyat (son 15m close): {st.price:.2f}")
    print(f"15m mum sayisi: {len(struct_mod._bars_15m)}")
    print()

    snap = update_levels()
    active = snap.get("active") or {}
    sup = active.get("support") or {}
    res = active.get("resistance") or {}

    print("=== AKTIF BANT ===")
    print(
        f"Destek:  {sup.get('price')}  skor={sup.get('score')}  guc={sup.get('strength')}"
    )
    print(
        f"Direnc:  {res.get('price')}  skor={res.get('score')}  guc={res.get('strength')}"
    )
    print(
        f"zone={active.get('zone')}  locked={active.get('locked')}  "
        f"range_valid={active.get('range_valid')}"
    )
    print()

    merged = snap.get("levels") or []
    for label, price, kind in (
        ("Destek ~2012.96", 2012.96, "support"),
        ("Direnc ~2029.50", 2029.50, "resistance"),
    ):
        hits = [
            l
            for l in merged
            if str(l.get("kind")) == kind
            and abs(float(l.get("price", 0) or 0) - price) < 2.0
        ]
        print(f"=== MERGED {label} ({len(hits)} aday) ===")
        for m in sorted(hits, key=lambda x: -int(x.get("score", 0) or 0)):
            print(
                f"  {m.get('price')} skor={m.get('score')} guc={m.get('strength')} "
                f"tf={m.get('timeframe')} touch={m.get('touch_count')}"
            )
        print()

    min_range = int(getattr(cfg, "V3_MIN_RANGE_SCORE", 10))
    min_med = int(getattr(cfg, "V3_LEVEL_SCORE_MEDIUM", 4))
    s_score = int(sup.get("score", 0) or 0)
    r_score = int(res.get("score", 0) or 0)
    levels_snap = {
        "support": sup,
        "resistance": res,
        "active_support": float(sup.get("price", 0) or 0),
        "active_resistance": float(res.get("price", 0) or 0),
        "range_valid": active.get("range_valid"),
        "zone": active.get("zone"),
    }

    print("=== KALITE KAPILARI ===")
    print(f"V3_MIN_RANGE_SCORE={min_range}  V3_LEVEL_SCORE_MEDIUM={min_med}")
    print(f"  destek skor {s_score} >= {min_med}?  {s_score >= min_med}")
    print(f"  direnc skor {r_score} >= {min_med}?  {r_score >= min_med}")
    bars15 = bars_15m(80)
    ready_b, detail_b = level_trade_ready(bars15, levels_snap, "BUY")
    ready_s, detail_s = level_trade_ready(bars15, levels_snap, "SELL")
    print(f"  scenario RANGE_BUY:  {'OK — ' + detail_b if ready_b else detail_b}")
    print(f"  scenario RANGE_SELL: {'OK — ' + detail_s if ready_s else detail_s}")
    print()

    scn = update_scenario()
    dec = update_decision()
    print("=== SENARYO ===")
    print(f"  {scn.get('name')}: {scn.get('detail')}")
    print()
    print("=== KARAR ===")
    print(f"  {dec.get('reason')}")
    entry = dec.get("entry") or {}
    print(
        f"  giris_valid={entry.get('valid')}  SL={entry.get('sl')}  "
        f"TP2={entry.get('tp2')}  RR={entry.get('rr')}  preview={entry.get('preview')}"
    )


if __name__ == "__main__":
    main()
