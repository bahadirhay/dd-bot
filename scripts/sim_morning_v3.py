"""
Sabah okumasi + katman bandi ile V3 karar simulasyonu (offline).
Kullanim: python scripts/sim_morning_v3.py
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.config import cfg
from core.state import state


def _mk_bar(ts: float, o: float, h: float, l: float, c: float, vol: float = 1000.0) -> dict:
    return {"ts": ts, "open": o, "high": h, "low": l, "close": c, "volume": vol}


def build_story_bars(n: int = 96, price_end: float = 1972.0) -> list[dict]:
    """1983-1992 yukari, 1992-1960 dusus, 1960-1979 zayif, 1969-1972 sikisma."""
    bars = []
    t0 = time.time() - n * 900
    prices = []
    # ramp up
    for i in range(20):
        p = 1983 + (1992 - 1983) * (i / 19)
        prices.append(p)
    # impulse down
    for i in range(25):
        p = 1992 - (1992 - 1960) * (i / 24)
        prices.append(p)
    # weak bounce to ~1979
    for i in range(15):
        p = 1960 + (1979 - 1960) * (i / 14)
        prices.append(p)
    # compression drift to price_end
    rest = n - len(prices)
    for i in range(max(0, rest)):
        p = 1979 - (1979 - price_end) * (i / max(rest - 1, 1))
        prices.append(p)
    while len(prices) < n:
        prices.append(price_end)
    prices = prices[-n:]
    for i, p in enumerate(prices):
        w = 2.5
        bars.append(
            _mk_bar(
                t0 + i * 900,
                p - 1,
                p + w,
                p - w,
                p + (0.3 if i % 2 else -0.2),
            )
        )
    bars[-1]["close"] = price_end
    bars[-1]["low"] = min(bars[-1]["low"], price_end - 3)
    return bars


def run_sim(price: float, label: str) -> dict:
    import engine.structure as structure_mod
    from engine.market_state_v3 import update_market_state
    from engine.decision_v3 import update_decision
    from engine.levels_v3 import get_levels_snapshot, update_levels
    from engine.scenario_v3 import get_scenario_snapshot

    bars = build_story_bars(96, price)
    _orig = structure_mod.get_bars_15m
    structure_mod.get_bars_15m = lambda limit=96: bars[-limit:] if limit else bars

    state.price = price
    state.mark_price = price
    state.in_position = False
    state.v3_cvd = {"direction": "BEAR", "confirmed": False, "cumulative": -5000}

  # Katmanlar (sabah okumasi)
    zones = [
        {"low": 1988, "high": 1993, "kind": "supply", "strength": 85, "tag": "supply_major"},
        {"low": 1976, "high": 1982, "kind": "supply", "strength": 65, "tag": "supply_mid"},
        {"low": 1964, "high": 1970, "kind": "demand", "strength": 72, "tag": "demand_weak"},
        {"low": 1954, "high": 1960, "kind": "demand", "strength": 55, "tag": "demand_liq"},
    ]

    try:
        ms = update_market_state(price, zones=zones, bars15=bars)
        update_levels()
        snap = update_decision()
    finally:
        structure_mod.get_bars_15m = _orig
    levels = get_levels_snapshot(price)
    scenario = get_scenario_snapshot(price)

    collapse = ms.get("collapse") or {}
    tv = ms.get("trade_verdict") or {}
    ef = ms.get("execution_filter") or {}
    opp = ef.get("opportunity") or {}

    return {
        "label": label,
        "price": price,
        "zone": levels.get("zone"),
        "active_s": levels.get("active_support"),
        "active_r": levels.get("active_resistance"),
        "scenario": scenario.get("name"),
        "scenario_detail": (scenario.get("detail") or "")[:120],
        "action": snap.get("action"),
        "wait_reason": snap.get("reason", "")[:200],
        "collapse_mode": collapse.get("mode"),
        "collapse_score": collapse.get("state_score"),
        "dominant": collapse.get("dominant_bias"),
        "allow_trade": collapse.get("allow_trade"),
        "event_long": collapse.get("event_confirms_long"),
        "event_short": collapse.get("event_confirms_short"),
        "verdict": tv.get("verdict"),
        "verdict_entry": tv.get("allow_entry"),
        "tier": opp.get("tier"),
        "setup_strength": opp.get("half_life_score"),
        "effective": opp.get("effective_lifetime"),
        "structure_trend": (ms.get("structure") or {}).get("trend"),
        "structure_summary": (ms.get("structure") or {}).get("summary", "")[:80],
        "entry_valid": (snap.get("entry") or {}).get("valid"),
        "entry_rr": (snap.get("entry") or {}).get("rr"),
    }


def main() -> None:
    print("=" * 60)
    print("V3 KARAR SIMULASYONU — sabah katman okumasi")
    print(f"STRATEGY_V3={cfg.STRATEGY_V3_ENABLED}")
    print("=" * 60)

    cases = [
        (1972.0, "Sikisma — demand_weak icinde"),
        (1968.0, "Destek alti sweep"),
        (1978.0, "Zayif bounce / mid"),
        (1989.0, "Supply major — direnç"),
        (1962.0, "Liquidity hunt"),
    ]

    for px, lbl in cases:
        r = run_sim(px, lbl)
        print(f"\n--- {lbl} @ {px:.0f} ---")
        print(f"  Band: S={r['active_s']:.0f} R={r['active_r']:.0f} zone={r['zone']}")
        print(f"  Structure: {r['structure_trend']} | {r['structure_summary']}")
        print(
            f"  Collapse: {r['collapse_mode']} skor={r['collapse_score']} "
            f"baskın={r['dominant']} trade={r['allow_trade']}"
        )
        print(f"  Events: long={r['event_long']} short={r['event_short']}")
        print(
            f"  Exec: tier={r['tier']} setup={r['setup_strength']} eff={r['effective']} "
            f"→ verdict={r['verdict']} entry={r['verdict_entry']}"
        )
        print(f"  Scenario: {r['scenario']}")
        if r["scenario_detail"]:
            print(f"    {r['scenario_detail']}")
        print(f"  DECISION: {r['action']} | entry valid={r['entry_valid']} RR={r['entry_rr']}")
        if r["action"] == "WAIT":
            print(f"    WAIT: {r['wait_reason']}")


if __name__ == "__main__":
    main()
