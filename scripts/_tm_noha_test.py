"""TM HA'SIZ test: heikin_ashi_series'i identity ile degistir -> TM gercek mumlarda hesaplanir.
Soru: HA'yi kaldirinca (gecikmeyi/yanilsamayi) TM edge kazanir mi? HA'li vs HA'siz yan yana.
Kullanici hipotezi: HA sorunsa, gercek mumda giris duzeler. 3 coin x 3 pencere, gercek-mum fill.
"""
import sys, urllib.request, json, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine.trend_magic_v3 as tm
from engine.trend_magic_v3 import TrendMagicParams

FEE = 12.0

def klines(sym, days=60):
    out = {}; end = int(time.time() * 1000); need = days * 96
    while len(out) < need:
        u = ("https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=15m&limit=1500&endTime=%d" % (sym, end))
        try: r = json.loads(urllib.request.urlopen(u, timeout=20).read())
        except Exception: break
        if not r: break
        for x in r:
            out[int(x[0])] = {"ts": int(x[0]) // 1000, "open": float(x[1]), "high": float(x[2]),
                              "low": float(x[3]), "close": float(x[4]), "volume": float(x[5])}
        end = r[0][0] - 1
        if len(r) < 1500: break
    return [out[k] for k in sorted(out)]

_ORIG_HA = tm.heikin_ashi_series
def _identity(bars):
    return [{"open": float(b["open"]), "high": float(b["high"]), "low": float(b["low"]),
             "close": float(b["close"]), "ts": b.get("ts", 0)} for b in bars if float(b["close"]) > 0]

def entries(bars):
    ser = tm.compute_series(bars, TrendMagicParams())
    if not ser.get("ready"): return None
    td = ser["trend_dir"]; out = []
    for i in range(1, len(td)):
        if td[i] == 1 and td[i - 1] == -1: out.append((i, "LONG"))
        elif td[i] == -1 and td[i - 1] == 1: out.append((i, "SHORT"))
    return out, td

def sim(bars, td, i, side, mode, param):
    H = [b["high"] for b in bars]; L = [b["low"] for b in bars]; C = [b["close"] for b in bars]
    ent = C[i]; n = len(C); peak = 0.0
    for j in range(i + 1, n):
        fav = ((H[j] - ent) if side == "LONG" else (ent - L[j])) / ent * 1e4
        cur = ((C[j] - ent) if side == "LONG" else (ent - C[j])) / ent * 1e4
        flip = (side == "LONG" and td[j] == -1) or (side == "SHORT" and td[j] == 1)
        if mode == "tp" and fav >= param: return param - FEE
        if mode == "trail":
            peak = max(peak, fav)
            if peak >= param and (peak - cur) >= param * 0.5: return cur - FEE
        if flip: return cur - FEE
    return ((C[-1] - ent) if side == "LONG" else (ent - C[-1])) / ent * 1e4 - FEE

def totals(mode_ha):
    """mode_ha: 'HA' veya 'NOHA'. Toplam net + giris sayisi dondur (variant basi)."""
    tm.heikin_ashi_series = _ORIG_HA if mode_ha == "HA" else _identity
    variants = [("FLIP", "flip", 0), ("TP300", "tp", 300), ("TRAIL200", "trail", 200)]
    agg = {v[0]: 0 for v in variants}; tot_ent = 0
    for sym in ("ETHUSDT", "BTCUSDT", "SOLUSDT"):
        bars = klines(sym, 60)
        if len(bars) < 3000: continue
        res = entries(bars)
        if not res: continue
        ents, td = res; tot_ent += len(ents)
        n = len(bars); start = 60; span = n - 1 - start; w = span // 3
        for k in range(3):
            lo = start + k * w; hi = start + (k + 1) * w if k < 2 else n - 1
            wents = [(i, s) for (i, s) in ents if lo <= i < hi]
            for name, m, param in variants:
                agg[name] += sum(sim(bars, td, i, s, m, param) for (i, s) in wents)
    return agg, tot_ent

if __name__ == "__main__":
    print("=== TM: HA'li vs HA'siz (gercek mum) | 3 coin x 3 pencere | net bps ===\n")
    for md in ("HA", "NOHA"):
        agg, ne = totals(md)
        lbl = "HA (mevcut)" if md == "HA" else "HA'SIZ (gercek mum)"
        print("--- %s | toplam TM giris=%d ---" % (lbl, ne))
        for k, v in agg.items():
            print("   %-9s %+7.0f bps" % (k, v))
        print()
    print("(HA'siz FLIP ya da varyanti POZITIF + HA'dan iyi ise -> HA sucluydu, kurtardik.")
    print(" Ikisi de negatif ise -> sorun HA degil, TM sinyalinin kendisi edge'siz.)")
