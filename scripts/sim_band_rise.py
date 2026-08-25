#!/usr/bin/env python3
"""
1549-1642 trade band yukselisi — yeni V3 yapisi offline replay.

Kullanim:
  python scripts/sim_band_rise.py
  python scripts/sim_band_rise.py --start 2026-06-06 --end 2026-06-07
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import deque
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import logging

from core.config import cfg
from core.state import state
from dashboard.binance_chart import fetch_15m_klines, fetch_1h_klines, fetch_1m_klines
from engine.bars_1m import set_bars_1m
from engine import structure as struct_mod
from engine.structure import add_bar_15m, add_bar_1h


def _enrich_bar(b: dict) -> dict:
    c = dict(b)
    vol = float(c.get("volume", 0) or 0)
    # Yaklasik delta: yukari mum = alim agirlikli
    o = float(c.get("open", 0) or 0)
    cl = float(c.get("close", 0) or 0)
    buy_frac = 0.58 if cl >= o else 0.42
    c.setdefault("buy_vol", vol * buy_frac)
    c["sell_vol"] = vol - c["buy_vol"]
    c["delta"] = c["buy_vol"] - c["sell_vol"]
    return c


def _seed_ticks_from_1m(bars_1m: list[dict]) -> None:
    ticks = []
    for b in bars_1m[-40:]:
        d = float(b.get("delta", 0) or 0)
        px = float(b.get("close", 0) or 0)
        qty = abs(d) if abs(d) > 0 else float(b.get("volume", 0) or 0) * 0.01
        ticks.append({"price": px, "qty": qty, "delta": d, "ts": b.get("ts")})
    state.ticks = deque(ticks, maxlen=5000)


def _reset_struct() -> None:
    struct_mod._bars_15m = deque(maxlen=500)
    struct_mod._bars_1h = deque(maxlen=150)
    state.v3_levels = {}
    state.v3_scenario = {}
    state.v3_entry_signal = {}
    state.in_position = False
    state.v3_trade_channel_traversed = False
    state.v3_cvd = {}
    state.v3_market_state = {}


def _load_history(bars_15m: list[dict], bars_1h: list[dict], bars_1m: list[dict], upto: int) -> None:
    _reset_struct()
    for b in bars_15m[: upto + 1]:
        add_bar_15m(_enrich_bar(b))
    for b in bars_1h:
        if b["ts"] <= bars_15m[upto]["ts"]:
            add_bar_1h(_enrich_bar(b))
    b1 = [b for b in bars_1m if b["ts"] <= bars_15m[upto]["ts"]]
    set_bars_1m(b1[-200:])
    _seed_ticks_from_1m(b1)
    px = float(bars_15m[upto]["close"])
    state.price = px
    state.mark_price = px


def _pin_trade_band(s: float, r: float, macro_r: float = 2046.0) -> None:
    from engine.levels_v3 import _trade_channel_traversed, price_zone_for_band
    from engine.v3_common import bars_15m

    px = float(state.price or 0)
    snap = state.v3_levels or {}
    active = dict(snap.get("active") or {})
    active["support"] = {"price": s, "strength": "STRONG", "score": 8}
    active["resistance"] = {"price": r, "strength": "MEDIUM", "score": 6}
    active["trade_band"] = True
    active["range_valid"] = True
    active["zone"] = price_zone_for_band(s, r, px)
    bars = bars_15m(48)
    traverse = _trade_channel_traversed(bars, s, r)
    active["channel_traversed"] = traverse
    state.v3_trade_channel_traversed = traverse
    active["macro_support"] = s
    active["macro_resistance"] = macro_r
    snap["active"] = active
    state.v3_levels = snap


def _find_leg(bars: list[dict], s_tgt: float = 1555.0, r_tgt: float = 1630.0) -> tuple[int, int] | None:
    for i, b in enumerate(bars):
        if b["low"] > s_tgt:
            continue
        for j in range(i, len(bars)):
            if bars[j]["high"] >= r_tgt:
                return i, j
        break
    return None


def _replay_step(
    bars_15m: list[dict],
    bars_1h: list[dict],
    bars_1m: list[dict],
    idx: int,
    *,
    pin_s: float,
    pin_r: float,
) -> dict:
    from engine.cvd_v3 import update_cvd_snapshot
    from engine.decision_v3 import _update_decision_probabilistic
    from engine.entry_v3 import update_entry
    from engine.levels_v3 import get_levels_snapshot, update_levels
    from engine.market_state_v3 import update_market_state
    from engine.scenario_v3 import get_scenario_snapshot, update_scenario
    from engine.structure_v3 import get_structure_snapshot
    from engine.trade_thesis_v3 import build_trade_theses

    _load_history(bars_15m, bars_1h, bars_1m, idx)
    bar = bars_15m[idx]
    px = float(state.price)
    update_levels()
    _pin_trade_band(pin_s, pin_r)
    ms = update_market_state(px)
    update_scenario()
    signal = update_entry()
    levels = get_levels_snapshot(px) or {}
    levels["market_state"] = ms
    scenario = get_scenario_snapshot(px) or {}
    structure = get_structure_snapshot()
    zone = str(levels.get("zone") or "MID_RANGE")
    scn_name = str(scenario.get("name") or "WAIT")
    breakout_side = ""
    if scn_name.startswith("BREAKOUT_"):
        breakout_side = "BUY" if "BUY" in scn_name else "SELL"
    cvd = update_cvd_snapshot(zone=zone, breakout_side=breakout_side)
    s1h = (structure.get("1h") or {})
    range_locked = bool(s1h.get("range_locked")) or bool(getattr(state, "v3_range_locked", False))
    dec = _update_decision_probabilistic(
        px=px,
        levels=levels,
        structure=structure,
        scenario=scenario,
        cvd=cvd,
        signal=signal,
        range_locked=range_locked,
        flow_tag="sim",
        flow_force=True,
    )
    theses = build_trade_theses(
        levels=levels,
        scenario=scenario,
        px=px,
        cvd=cvd,
    )
    sel = theses.get("selected")
    short_t = theses.get("short")
    ds = dec.get("direction_scores") or {}

    return {
        "ts": bar["ts"],
        "px": px,
        "low": bar["low"],
        "high": bar["high"],
        "zone": levels.get("zone"),
        "s": levels.get("active_support"),
        "r": levels.get("active_resistance"),
        "traverse": levels.get("channel_traversed"),
        "scn": scenario.get("name"),
        "scn_detail": (scenario.get("detail") or "")[:80],
        "action": dec.get("action"),
        "reason": (dec.get("reason") or "")[:120],
        "reject": dec.get("reject_reason"),
        "pS": ds.get("prob_short_pct"),
        "pL": ds.get("prob_long_pct"),
        "cvd": cvd.get("direction"),
        "cvd_ok": cvd.get("confirmed"),
        "entry_valid": (dec.get("entry") or {}).get("valid"),
        "entry_rr": (dec.get("entry") or {}).get("rr"),
        "thesis_sel": getattr(sel, "direction", None) if sel else None,
        "thesis_state": getattr(sel, "state", None) if sel else None,
        "short_state": getattr(short_t, "state", None) if short_t else None,
        "long_state": getattr(theses.get("long"), "state", None) if theses.get("long") else None,
        "thesis_reason": theses.get("reason", "")[:100],
        "verdict": ((levels.get("market_state") or {}).get("trade_verdict") or {}).get("verdict"),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--warmup", type=int, default=350, help="Ilk N 15m mum isinma")
    ap.add_argument("--s-touch", type=float, default=1555.0)
    ap.add_argument("--r-touch", type=float, default=1630.0)
    ap.add_argument("--pin-s", type=float, default=1549.87)
    ap.add_argument("--pin-r", type=float, default=1642.0)
    args = ap.parse_args()

    logging.getLogger().setLevel(logging.ERROR)

    print("=== V3 BAND RISE SIM (trade band + tez/entry kapilari) ===\n")
    print(
        f"config: TRADE_BAND={getattr(cfg, 'V3_TRADE_BAND_ENABLED', False)} "
        f"CHANNEL_ENTRY={getattr(cfg, 'V3_CHANNEL_ENTRY_ENABLED', True)} "
        f"TRAVERSE_TP={getattr(cfg, 'V3_CHANNEL_TRAVERSE_TP_OK', True)} "
        f"THESIS={getattr(cfg, 'V3_THESIS_DECISION_ENABLED', True)}"
    )

    bars_15m = [_enrich_bar(b) for b in fetch_15m_klines(500)]
    bars_1h = [_enrich_bar(b) for b in fetch_1h_klines(150)]
    bars_1m = [_enrich_bar(b) for b in fetch_1m_klines(500)]
    if len(bars_15m) < 50:
        raise SystemExit("Yetersiz 15m veri")

    leg = _find_leg(bars_15m, args.s_touch, args.r_touch)
    if not leg:
        raise SystemExit("1549-1642 yukselis bacagi bulunamadi")
    i0, i1 = leg
    start_i = max(args.warmup, i0 - 5)
    t0 = datetime.fromtimestamp(bars_15m[i0]["ts"], tz=timezone.utc)
    t1 = datetime.fromtimestamp(bars_15m[i1]["ts"], tz=timezone.utc)
    print(f"Pin band: S={args.pin_s:.2f} R={args.pin_r:.2f} (macro R=2046)")
    print(f"Veri: {len(bars_15m)} x 15m | bacak: idx {i0}-{i1}")
    print(f"  destek dokunma ~{t0} low={bars_15m[i0]['low']:.2f}")
    print(f"  direnc yaklasma ~{t1} high={bars_15m[i1]['high']:.2f}\n")

    rows: list[dict] = []
    prev_zone = None
    trades: list[dict] = []

    for idx in range(start_i, i1 + 1):
        try:
            r = _replay_step(
                bars_15m, bars_1h, bars_1m, idx, pin_s=args.pin_s, pin_r=args.pin_r
            )
        except Exception as e:
            ts = datetime.fromtimestamp(bars_15m[idx]["ts"], tz=timezone.utc).strftime("%m-%d %H:%M")
            print(f"HATA @{ts}: {e}")
            continue
        rows.append(r)
        if r["action"] in ("SHORT", "LONG") and r.get("entry_valid"):
            trades.append(r)
        zone_chg = r["zone"] != prev_zone
        prev_zone = r["zone"]
        interesting = (
            zone_chg
            or r["zone"] == "NEAR_RESISTANCE"
            or r["scn"] in ("RANGE_SELL", "RANGE_BUY")
            or r["action"] in ("SHORT", "LONG")
            or (r.get("pS") or 0) >= 65
        )
        if interesting:
            ts = datetime.fromtimestamp(r["ts"], tz=timezone.utc).strftime("%m-%d %H:%M")
            print(
                f"{ts} px={r['px']:.1f} [{r['low']:.0f}-{r['high']:.0f}] "
                f"zone={r['zone']} S={r['s']:.0f} R={r['r']:.0f} "
                f"traverse={'Y' if r['traverse'] else 'N'} "
                f"scn={r['scn']} cvd={r['cvd']}/{1 if r['cvd_ok'] else 0} "
                f"pS={r['pS']}% thesis={r['thesis_sel'] or '-'}/{r['short_state'] or '-'} "
                f"-> {r['action']} entry={r['entry_valid']} RR={r['entry_rr']}"
            )
            if r["scn_detail"]:
                print(f"         scn: {r['scn_detail']}")
            if r["thesis_reason"] and r["zone"] == "NEAR_RESISTANCE":
                print(f"         tez: {r['thesis_reason']}")
            if r["reason"] and r["action"] == "WAIT":
                print(f"         wait: {r['reason'][:100]}")

    print("\n=== OZET ===")
    zones = [r["zone"] for r in rows]
    near_r = sum(1 for z in zones if z == "NEAR_RESISTANCE")
    near_s = sum(1 for z in zones if z == "NEAR_SUPPORT")
    mid = sum(1 for z in zones if z == "MID_RANGE")
    range_sell = sum(1 for r in rows if r["scn"] == "RANGE_SELL")
    short_valid = sum(1 for r in rows if r.get("short_state") == "VALID")
    long_valid = sum(1 for r in rows if r.get("long_state") == "VALID")
    range_buy = sum(1 for r in rows if r["scn"] == "RANGE_BUY")
    traverse_y = sum(1 for r in rows if r.get("traverse"))
    print(f"Adim: {len(rows)} | MID={mid} NEAR_S={near_s} NEAR_R={near_r}")
    print(
        f"RANGE_BUY: {range_buy} | RANGE_SELL: {range_sell} | "
        f"LONG tez VALID: {long_valid} | SHORT tez VALID: {short_valid} | traverse: {traverse_y}"
    )
    long_trades = [t for t in trades if t["action"] == "LONG"]
    short_trades = [t for t in trades if t["action"] == "SHORT"]
    print(f"Karar LONG (entry valid): {len(long_trades)} | SHORT: {len(short_trades)}")
    for t in long_trades + short_trades:
        ts = datetime.fromtimestamp(t["ts"], tz=timezone.utc).strftime("%m-%d %H:%M")
        print(
            f"  TRADE @{ts} px={t['px']:.1f} {t['action']} RR={t['entry_rr']} "
            f"scn={t['scn']} zone={t['zone']}"
        )
    if not trades:
        # En yakin NEAR_R anlari
        near_rows = [r for r in rows if r["zone"] == "NEAR_RESISTANCE"]
        if near_rows:
            best = max(near_rows, key=lambda x: x["px"])
            ts = datetime.fromtimestamp(best["ts"], tz=timezone.utc).strftime("%m-%d %H:%M")
            print(f"\nEn yuksek NEAR_RESISTANCE: @{ts} px={best['px']:.1f}")
            print(f"  scn={best['scn']} thesis={best['short_state']} wait={best['reason'][:90]}")
        else:
            print("\nNEAR_RESISTANCE hic olusmadi — fiyat band kenarina ulasmadi (sim band S/R)")


if __name__ == "__main__":
    main()
