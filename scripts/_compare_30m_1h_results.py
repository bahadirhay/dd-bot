"""30m / 1h — sonuc karsilastirmasi: D vs Trend Magic HA (fee8+slip2)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json
import sqlite3

import numpy as np

from core.config import cfg
from scripts._trend_magic_ha_bt import (
    Params,
    build_ohlc,
    heikin_ashi,
    load_ticks,
    run_backtest,
)

FEE, SLIP, SL, MH = 8.0, 2.0, 60.0, 16
SPLIT = 0.6


def load_snaps():
    c = sqlite3.connect(f"file:{cfg.DB_PATH}?mode=ro", uri=True)
    snaps = []
    for ts, p, pj in c.execute(
        "SELECT ts, price, payload_json FROM market_snapshots WHERE price>0 ORDER BY ts"
    ):
        kv = None
        if pj:
            try:
                fm = json.loads(pj).get("forming_15m")
                if isinstance(fm, dict):
                    kv = fm.get("volume")
            except Exception:
                pass
        snaps.append((float(ts), float(p), kv))
    c.close()
    return snaps


def build_bars(snaps, tf_sec):
    bars = {}
    for ts, p, kv in snaps:
        b = int(ts // tf_sec)
        if b not in bars:
            bars[b] = {"ts": b * tf_sec, "h": p, "l": p, "c": p, "v": kv}
        else:
            o = bars[b]
            o["h"] = max(o["h"], p)
            o["l"] = min(o["l"], p)
            o["c"] = p
            if kv is not None:
                o["v"] = kv
    k = sorted(bars)
    return [
        (bars[x]["ts"], bars[x]["h"], bars[x]["l"], bars[x]["c"], bars[x]["v"])
        for x in k
    ]


def poc_at(bars, i, M=40):
    nu = de = 0.0
    for j in range(i - M, i):
        v = bars[j][4]
        v = v if (v and v > 0) else 1.0
        nu += bars[j][3] * v
        de += v
    return nu / de if de > 0 else None


def run_d(bars, dev=85, lo=0, hi=None):
    n = len(bars)
    hi = hi or n
    i = max(40, lo)
    tr = []
    while i < hi - 1:
        pc = poc_at(bars, i)
        if not pc:
            i += 1
            continue
        _, h, l, c, _ = bars[i]
        dev_bps = (c - pc) / pc * 1e4
        sig = "LONG" if dev_bps <= -dev else ("SHORT" if dev_bps >= dev else None)
        if not sig:
            i += 1
            continue
        ent = c
        j = i
        res = None
        for j in range(i + 1, min(i + MH + 1, n)):
            _, hj, lj, cj, _ = bars[j]
            cur = ((cj - ent) if sig == "LONG" else (ent - cj)) / ent * 1e4
            adv = ((hj - ent) if sig == "SHORT" else (ent - lj)) / ent * 1e4
            if adv >= SL:
                res = -SL - FEE - 2 * SLIP
                break
            pcj = poc_at(bars, j)
            if pcj:
                dj = (cj - pcj) / pcj * 1e4
                if ((sig == "LONG" and dj >= 0) or (sig == "SHORT" and dj <= 0)) and cur >= 0:
                    res = cur - FEE - 2 * SLIP
                    break
            if (j - i) >= MH:
                res = cur - FEE - 2 * SLIP
                break
        if res is None:
            cj = bars[min(j, n - 1)][3]
            res = ((cj - ent) if sig == "LONG" else (ent - cj)) / ent * 1e4 - FEE - 2 * SLIP
        tr.append(res)
        i = j + 1
    return tr


def stats(tr):
    if not tr:
        return dict(net=0, n=0, wr=0, avg=0)
    return dict(
        net=sum(tr),
        n=len(tr),
        wr=100 * sum(1 for x in tr if x > 0) / len(tr),
        avg=sum(tr) / len(tr),
    )


def split_stats(tr, split_i):
    return stats(tr[:split_i]), stats(tr[split_i:])


def tm_stats(trades, split_ts, bar_ts):
    def pack(sub):
        if not sub:
            return dict(net=0, n=0, wr=0, avg=0)
        pnls = [t.pnl_bps * t.qty for t in sub]
        return dict(
            net=sum(pnls),
            n=len(sub),
            wr=100 * sum(1 for x in pnls if x > 0) / len(pnls),
            avg=sum(pnls) / len(pnls),
        )

    tr = [t for t in trades if bar_ts[t.entry_i] < split_ts]
    oo = [t for t in trades if bar_ts[t.entry_i] >= split_ts]
    return pack(trades), pack(tr), pack(oo)


def print_row(name, s):
    print(
        f"  {name:22} net={s['net']:+8.0f} bps | isl={s['n']:3d} | "
        f"isabet={s['wr']:5.1f}% | avg={s['avg']:+6.1f} bps"
    )


def main():
    snaps = load_snaps()
    ts, px = load_ticks(str(cfg.DB_PATH))
    bh = (px[-1] - px[0]) / px[0] * 1e4

    print("=== SONUC TABLOSU (May-Jul 2026, fee8+slip2) ===\n")
    print(f"  Buy&Hold referans: {bh:+.0f} bps\n")

    for tf_sec, label in ((1800, "30m"), (3600, "1h")):
        bars = build_bars(snaps, tf_sec)
        n = len(bars)
        split_i = int(n * SPLIT)
        split_ts = bars[split_i][0]

        d_all = run_d(bars)
        d_tr = run_d(bars, hi=split_i)
        d_oo = run_d(bars, lo=split_i)

        real = build_ohlc(ts, px, tf_sec)
        ha = heikin_ashi(real["open"], real["high"], real["low"], real["close"])
        tm_trades = run_backtest(real, ha, Params(), fee_bps=FEE, slip_bps=SLIP)
        tm_all, tm_tr, tm_oo = tm_stats(tm_trades, split_ts, real["ts"])

        print(f"--- {label} ---")
        print_row("D (POC revert)", stats(d_all))
        print_row("  TRAIN", stats(d_tr))
        print_row("  OOS", stats(d_oo))
        print_row("Trend Magic HA", tm_all)
        print_row("  TRAIN", tm_tr)
        print_row("  OOS", tm_oo)

        winner = "Trend Magic" if tm_all["net"] > stats(d_all)["net"] else "D"
        print(f"  -> Kazanan (net): {winner} ({max(tm_all['net'], stats(d_all)['net']):+.0f} bps)\n")


if __name__ == "__main__":
    main()
