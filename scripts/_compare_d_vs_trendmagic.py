"""D vs Trend Magic HA — ayni DB, fee8+slip2."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlite3, json, bisect
from core.config import cfg

# --- load ticks (shared) ---
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
    snaps.append((ts, p, kv))
c.close()
print(f"snapshot={len(snaps)}")


def build_15m_ohlc():
    bars = {}
    for ts, p, kv in snaps:
        b = int(ts // 900)
        if b not in bars:
            bars[b] = {"ts": b * 900, "h": p, "l": p, "c": p, "v": kv}
        else:
            o = bars[b]
            o["h"] = max(o["h"], p)
            o["l"] = min(o["l"], p)
            o["c"] = p
            if kv is not None:
                o["v"] = kv
    k = sorted(bars)
    return [(bars[x]["ts"], bars[x]["h"], bars[x]["l"], bars[x]["c"]) for x in k], k, [bars[x]["c"] for x in k], [bars[x]["v"] for x in k]


B15, k15, C15, V15 = build_15m_ohlc()
n = len(B15)
split = int(n * 0.6)
FEE, SLIP, SL = 8.0, 2.0, 60.0
MH = 16


def poc(i, M=40):
    nu = de = 0.0
    for j in range(i - M, i):
        v = V15[j] if (V15[j] and V15[j] > 0) else 1.0
        nu += C15[j] * v
        de += v
    return nu / de if de > 0 else None


def run_d(DEV=85, lo=0, hi=None):
    hi = hi or n
    i = max(40, lo)
    tr = []
    while i < hi - 1:
        pc = poc(i)
        if not pc:
            i += 1
            continue
        cl = B15[i][3]
        dev = (cl - pc) / pc * 1e4
        sig = "LONG" if dev <= -DEV else ("SHORT" if dev >= DEV else None)
        if not sig:
            i += 1
            continue
        ent = cl
        j = i
        res = None
        for j in range(i + 1, min(i + MH + 1, n)):
            _, hj, lj, clj = B15[j]
            cur = ((clj - ent) if sig == "LONG" else (ent - clj)) / ent * 1e4
            adv = ((hj - ent) if sig == "SHORT" else (ent - lj)) / ent * 1e4
            if adv >= SL:
                res = -SL - FEE - 2 * SLIP
                break
            pcj = poc(j)
            dj = (clj - pcj) / pcj * 1e4 if pcj else None
            if dj is not None and ((sig == "LONG" and dj >= 0) or (sig == "SHORT" and dj <= 0)) and cur >= 0:
                res = cur - FEE - 2 * SLIP
                break
            if (j - i) >= MH:
                res = cur - FEE - 2 * SLIP
                break
        if res is None:
            clj = B15[min(j, n - 1)][3]
            res = ((clj - ent) if sig == "LONG" else (ent - clj)) / ent * 1e4 - FEE - 2 * SLIP
        tr.append(res)
        i = j + 1
    return tr


def rep(name, tr):
    if not tr:
        print(f"{name:28} islem=0")
        return
    print(
        f"{name:28} net={sum(tr):+7.0f} bps | isl={len(tr):4d} | "
        f"isabet={100*sum(1 for x in tr if x>0)/len(tr):5.1f}% | avg={sum(tr)/len(tr):+.1f}"
    )


# D @15m canonical (DEV85, fee8+slip2)
for dev in (50, 85, 100):
    al = run_d(dev)
    tr = run_d(dev, hi=split)
    oo = run_d(dev, lo=split)
    print(f"\n=== D POC revert DEV={dev} @15m ===")
    rep("ALL", al)
    rep("TRAIN 60%", tr)
    rep("OOS 40%", oo)

# Trend Magic from existing script
from scripts._trend_magic_ha_bt import (
    load_ticks, build_ohlc, heikin_ashi, run_backtest, Params,
)

ts, px = load_ticks(str(cfg.DB_PATH))
real = build_ohlc(ts, px, 900)
ha = heikin_ashi(real["open"], real["high"], real["low"], real["close"])
tm = run_backtest(real, ha, Params(), fee_bps=FEE, slip_bps=SLIP)
tm_tr = [t for t in tm if real["ts"][t.entry_i] < real["ts"][split]]
tm_oo = [t for t in tm if real["ts"][t.entry_i] >= real["ts"][split]]

def rep_tm(name, trades):
    if not trades:
        print(f"{name:28} islem=0")
        return
    pnls = [t.pnl_bps * t.qty for t in trades]
    print(
        f"{name:28} net={sum(pnls):+7.0f} bps | isl={len(trades):4d} | "
        f"isabet={100*sum(1 for x in pnls if x>0)/len(pnls):5.1f}% | avg={sum(pnls)/len(pnls):+.1f}"
    )

print("\n=== Trend Magic HA @15m (Pine default) ===")
rep_tm("ALL", tm)
rep_tm("TRAIN 60%", tm_tr)
rep_tm("OOS 40%", tm_oo)

bh = (real["close"][-1] - real["close"][0]) / real["close"][0] * 1e4
print(f"\nBuy&Hold: {bh:+.0f} bps")
