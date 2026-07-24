"""Makro-trend LONG filtresi COK-REJIM proxy testi.
Gercek B/V3 longlarini klines'tan uretemem -> proxy: 4h 'dip-al' longlari (destek/dip touch).
Filtre: gunluk makro-trend DOWN iken long ALMA. Soru: bu filtre HEM ayida (zarari keser)
HEM bogada (zarar vermez) calisir mi -> genellenir mi, yoksa tek-donem tautolojisi mi?
3 coin x 4 pencere (uzun gecmis = farkli rejimler). NOFILTER vs MACROFILTER kiyas.
"""
import urllib.request, json, time, bisect, datetime as dt

FEE = 12.0
LOOK = 20        # 4h destek penceresi
TOUCH = 1.002    # dibe %0.2 yakin = dip-al long
SL_PCT, TP_PCT = 0.03, 0.05   # 300/500 bps
MAXHOLD = 60     # 4h bar (~10 gun)

def kl(sym, interval, want):
    per = {"4h": 6, "1d": 1}[interval]
    out = {}; end = int(time.time() * 1000)
    while len(out) < want:
        u = ("https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=%s&limit=1500&endTime=%d" % (sym, interval, end))
        try: r = json.loads(urllib.request.urlopen(u, timeout=20).read())
        except Exception: break
        if not r: break
        for x in r:
            out[int(x[0])] = (int(x[0]) // 1000, float(x[2]), float(x[3]), float(x[4]))  # ts,H,L,C
        end = r[0][0] - 1
        if len(r) < 1500: break
    return [out[k] for k in sorted(out)]

def macro_map(daily):
    """her gunluk ts -> DOWN/UP/NEUTRAL (SMA50/SMA20)."""
    C = [b[3] for b in daily]; ts = [b[0] for b in daily]; out = {}
    for i in range(50, len(C)):
        s20 = sum(C[i-20:i]) / 20; s50 = sum(C[i-50:i]) / 50; px = C[i]
        out[ts[i]] = 'DOWN' if (px < s50 and s20 < s50) else ('UP' if (px > s50 and s20 > s50) else 'NEUTRAL')
    return sorted(out), out

def sim(H, L, C, i):
    ent = C[i]; sl = ent * (1 - SL_PCT); tp = ent * (1 + TP_PCT); n = len(C)
    for j in range(i + 1, min(i + MAXHOLD + 1, n)):
        if L[j] <= sl: return -SL_PCT * 1e4 - FEE
        if H[j] >= tp: return TP_PCT * 1e4 - FEE
    return (C[min(i + MAXHOLD, n - 1)] - ent) / ent * 1e4 - FEE

def run(bars, dts, dmap, lo, hi, macro_filter):
    H = [b[1] for b in bars]; L = [b[2] for b in bars]; C = [b[3] for b in bars]; T = [b[0] for b in bars]
    n = len(C); pnls = []
    for i in range(max(LOOK, lo), min(hi, n - 1)):
        if L[i] > min(L[i - LOOK:i]) * TOUCH:   # dip/destek touch degil
            continue
        if macro_filter:
            p = bisect.bisect_right(dts, T[i]) - 1
            reg = dmap.get(dts[p]) if p >= 0 else None
            if reg == 'DOWN':
                continue   # makro dususte long ALMA
        pnls.append(sim(H, L, C, i))
    return pnls

def net(v): return sum(v) if v else 0.0

if __name__ == "__main__":
    print("=== Makro-LONG filtresi | 4h dip-al proxy | 3 coin x 4 pencere (cok-rejim) ===")
    print("NOFILTER = tum dip-longlar | MACRO = gunluk-DOWN'da long alma\n")
    agg = {"NOFILTER": 0, "MACRO": 0}; wins = 0; tot = 0
    for sym in ("ETHUSDT", "BTCUSDT", "SOLUSDT"):
        b4 = kl(sym, "4h", 3000); dd = kl(sym, "1d", 800)
        if len(b4) < 1500 or len(dd) < 200:
            print(sym, "veri az"); continue
        dts, dmap = macro_map(dd)
        n = len(b4); start = LOOK + 5; span = n - 1 - start; w = span // 4
        # pencere rejimini etiketle (ortalama makro)
        print("=== %s (4h bar=%d) ===" % (sym, n))
        print("  pencere |  NOFILTER | MACRO-filtre | fark")
        for k in range(4):
            lo = start + k * w; hi = start + (k + 1) * w if k < 3 else n - 1
            nf = net(run(b4, dts, dmap, lo, hi, False))
            mf = net(run(b4, dts, dmap, lo, hi, True))
            d = mf - nf
            print("  W%d      | %+8.0f | %+8.0f | %+7.0f" % (k + 1, nf, mf, d))
            agg["NOFILTER"] += nf; agg["MACRO"] += mf
            if mf >= nf: wins += 1
            tot += 1
        print()
    print("=== TOPLAM ===")
    print("  NOFILTER: %+.0f bps" % agg["NOFILTER"])
    print("  MACRO   : %+.0f bps  (fark %+.0f)" % (agg["MACRO"], agg["MACRO"] - agg["NOFILTER"]))
    print("  MACRO >= NOFILTER: %d/%d pencere" % (wins, tot))
    print("\n(MACRO cogu pencerede >= NOFILTER + toplam iyi + bogada ZARAR vermiyorsa -> genellenir, EKLE.")
    print(" Bazi rejimde MACRO daha kotuyse -> tautoloji/tek-donem, dikkatli ol.)")
