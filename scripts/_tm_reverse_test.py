"""TM TERS test: kullanici fikri 'kaybedeni ters cevir'. TM sinyalinin TAM TERSINI al
(TM LONG derse SHORT gir), ayni tutus/cikis. Fee her iki yonde de maliyet -> ters cevirmek
kaybi otomatik kara cevirmez. NORMAL vs REVERSE yan yana. 3 coin x 3 pencere, gercek-mum."""
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

def entries(bars):
    ser = tm.compute_series(bars, TrendMagicParams())
    if not ser.get("ready"): return None
    td = ser["trend_dir"]; out = []
    for i in range(1, len(td)):
        if td[i] == 1 and td[i - 1] == -1: out.append((i, "LONG"))
        elif td[i] == -1 and td[i - 1] == 1: out.append((i, "SHORT"))
    return out, td

def sim(bars, td, i, tm_side, trade_side, mode, param):
    """flip-zamanlamasi TM'nin (tm_side), pozisyon trade_side (ters icin karsi)."""
    H = [b["high"] for b in bars]; L = [b["low"] for b in bars]; C = [b["close"] for b in bars]
    ent = C[i]; n = len(C); peak = 0.0
    for j in range(i + 1, n):
        fav = ((H[j] - ent) if trade_side == "LONG" else (ent - L[j])) / ent * 1e4
        cur = ((C[j] - ent) if trade_side == "LONG" else (ent - C[j])) / ent * 1e4
        flip = (tm_side == "LONG" and td[j] == -1) or (tm_side == "SHORT" and td[j] == 1)
        if mode == "tp" and fav >= param: return param - FEE
        if mode == "trail":
            peak = max(peak, fav)
            if peak >= param and (peak - cur) >= param * 0.5: return cur - FEE
        if flip: return cur - FEE
    return ((C[-1] - ent) if trade_side == "LONG" else (ent - C[-1])) / ent * 1e4 - FEE

if __name__ == "__main__":
    variants = [("FLIP", "flip", 0), ("TP300", "tp", 300)]
    print("=== TM NORMAL vs TERS | gercek-mum, fee12 | 3 coin x 3 pencere | net bps ===\n")
    agg = {("normal", v[0]): 0 for v in variants}
    agg.update({("reverse", v[0]): 0 for v in variants})
    tot = 0
    for sym in ("ETHUSDT", "BTCUSDT", "SOLUSDT"):
        bars = klines(sym, 60)
        if len(bars) < 3000: continue
        res = entries(bars)
        if not res: continue
        ents, td = res; tot += len(ents)
        n = len(bars); start = 60; span = n - 1 - start; w = span // 3
        for k in range(3):
            lo = start + k * w; hi = start + (k + 1) * w if k < 2 else n - 1
            wents = [(i, s) for (i, s) in ents if lo <= i < hi]
            for name, m, param in variants:
                for (i, s) in wents:
                    opp = "SHORT" if s == "LONG" else "LONG"
                    agg[("normal", name)] += sim(bars, td, i, s, s, m, param)
                    agg[("reverse", name)] += sim(bars, td, i, s, opp, m, param)
    print("toplam TM giris=%d\n" % tot)
    print("  varyant  |   NORMAL   |   TERS")
    for name, _, _ in variants:
        print("  %-8s | %+8.0f  | %+8.0f" % (name, agg[("normal", name)], agg[("reverse", name)]))
    print("\n(TERS net belirgin POZITIF ise -> ters cevirmek kurtardi.")
    print(" TERS de negatif/marjinal ise -> kayip fee/churn'den, yon edge'i yok, ters de olu.)")
