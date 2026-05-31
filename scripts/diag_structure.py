"""Diagnose why 15m/1h structure is UNCLEAR vs visual downtrend."""
from dashboard.binance_chart import fetch_15m_klines, fetch_1h_klines
from engine.structure import _detect_swings, _determine_structure
from engine.structure_analyzer import update_structure, STRUCT_NEUTRAL
from core.config import cfg


def analyze(bars, lb, name):
    highs, lows = _detect_swings(bars, lb)
    det = _determine_structure(highs, lows)
    sh = [h["price"] for h in highs]
    sl = [l["price"] for l in lows]
    close = bars[-1]["close"]
    snap = update_structure(sh, sl, close, STRUCT_NEUTRAL)
    last_h = highs[-3:] if len(highs) >= 3 else highs
    last_l = lows[-3:] if len(lows) >= 3 else lows
    print(f"=== {name} lb={lb} bars={len(bars)} close={close:.2f} ===")
    print(f"  swings: {len(highs)} highs, {len(lows)} lows")
    print(f"  _determine_structure: {det}")
    print(f"  analyzer bias: {snap.bias}")
    if last_h:
        print(f"  last highs: {[round(x['price'], 1) for x in last_h]}")
    if last_l:
        print(f"  last lows: {[round(x['price'], 1) for x in last_l]}")
    if len(last_h) >= 2:
        h_dn = all(
            last_h[i]["price"] < last_h[i - 1]["price"]
            for i in range(1, len(last_h))
        )
        l_dn = all(
            last_l[i]["price"] < last_l[i - 1]["price"]
            for i in range(1, len(last_l))
        )
        print(f"  monotonic lower highs? {h_dn}  lower lows? {l_dn}")


if __name__ == "__main__":
    b15 = fetch_15m_klines(96)
    b1h = fetch_1h_klines(48)
    analyze(b15, cfg.SWING_LB_15M, "15m")
    analyze(b1h, cfg.SWING_LB_1H, "1h")
