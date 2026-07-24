"""Cok-TF konfluens LONG testi: 15m+1h+4h hepsi YUKARI iken long.
Kullanici fikri: cok zaman diliminde ayni yon -> daha guvenilir. Ama TF'ler ayni fiyattan
turer (korele); daha cok onay = daha cok GECIKME. Test: konfluens-long POZITIF+robust mu,
yoksa tek-TF'e ek deger katmiyor mu (memory: katmiyor). 3 coin x 3 pencere, gercek-mum.
"""
import urllib.request, json, time

FEE = 12.0
SL_PCT, TP_PCT = 0.03, 0.05
MAXHOLD = 40   # 15m bar

def kl(sym, days=75):
    out = {}; end = int(time.time() * 1000); need = days * 96
    while len(out) < need:
        u = ("https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=15m&limit=1500&endTime=%d" % (sym, end))
        try: r = json.loads(urllib.request.urlopen(u, timeout=20).read())
        except Exception: break
        if not r: break
        for x in r:
            out[int(x[0])] = (float(x[2]), float(x[3]), float(x[4]))  # H,L,C
        end = r[0][0] - 1
        if len(r) < 1500: break
    return [out[k] for k in sorted(out)]

def ema(v, n):
    k = 2 / (n + 1); e = [v[0]]
    for x in v[1:]: e.append(e[-1] + k * (x - e[-1]))
    return e

def tf_trend(C, step, emalen=9):
    """step: 1=15m, 4=1h, 16=4h. O TF'de close>EMA ve EMA yukselen -> +1."""
    idx = list(range(0, len(C), step))
    sub = [C[i] for i in idx]
    e = ema(sub, emalen)
    up = [0] * len(sub)
    for j in range(1, len(sub)):
        if sub[j] > e[j] and e[j] > e[j - 1]: up[j] = 1
        elif sub[j] < e[j] and e[j] < e[j - 1]: up[j] = -1
    # 15m index'e forward-fill
    out = [0] * len(C)
    for j, i in enumerate(idx):
        for ii in range(i, min(i + step, len(C))): out[ii] = up[j]
    return out

def sim(H, L, C, i):
    ent = C[i]; sl = ent * (1 - SL_PCT); tp = ent * (1 + TP_PCT); n = len(C)
    for j in range(i + 1, min(i + MAXHOLD + 1, n)):
        if L[j] <= sl: return -SL_PCT * 1e4 - FEE
        if H[j] >= tp: return TP_PCT * 1e4 - FEE
    return (C[min(i + MAXHOLD, n - 1)] - ent) / ent * 1e4 - FEE

def run(bars, t15, t1h, t4h, lo, hi, mode):
    H = [b[0] for b in bars]; L = [b[1] for b in bars]; C = [b[2] for b in bars]; n = len(C)
    pnls = []
    for i in range(max(20, lo), min(hi, n - 1)):
        if mode == "single":   # sadece 15m yukari (giris tetigi: yeni yukari)
            trig = t15[i] == 1 and t15[i - 1] != 1
        else:                   # konfluens: 15m+1h+4h hepsi yukari (yeni tam-hiza)
            allup = t15[i] == 1 and t1h[i] == 1 and t4h[i] == 1
            prevall = t15[i - 1] == 1 and t1h[i - 1] == 1 and t4h[i - 1] == 1
            trig = allup and not prevall
        if trig: pnls.append(sim(H, L, C, i))
    return pnls

def net(v): return sum(v) if v else 0.0

if __name__ == "__main__":
    print("=== Cok-TF konfluens LONG | 15m dip yerine TREND-hiza | 3 coin x 3 pencere ===")
    print("SINGLE = sadece 15m yukari | MTF = 15m+1h+4h hepsi yukari\n")
    agg = {"SINGLE": [0, 0], "MTF": [0, 0]}  # [net, islem]
    for sym in ("ETHUSDT", "BTCUSDT", "SOLUSDT"):
        bars = kl(sym, 75)
        if len(bars) < 3000: print(sym, "veri az"); continue
        C = [b[2] for b in bars]
        t15 = tf_trend(C, 1); t1h = tf_trend(C, 4); t4h = tf_trend(C, 16)
        n = len(bars); start = 60; span = n - 1 - start; w = span // 3
        print("=== %s (bar=%d) ===" % (sym, n))
        print("  pencere |   SINGLE (n)  |    MTF (n)")
        for k in range(3):
            lo = start + k * w; hi = start + (k + 1) * w if k < 2 else n - 1
            s = run(bars, t15, t1h, t4h, lo, hi, "single")
            m = run(bars, t15, t1h, t4h, lo, hi, "mtf")
            print("  W%d      | %+7.0f (%3d) | %+7.0f (%3d)" % (k + 1, net(s), len(s), net(m), len(m)))
            agg["SINGLE"][0] += net(s); agg["SINGLE"][1] += len(s)
            agg["MTF"][0] += net(m); agg["MTF"][1] += len(m)
        print()
    print("=== TOPLAM ===")
    for k in ("SINGLE", "MTF"):
        print("  %-7s net=%+.0f bps  islem=%d" % (k, agg[k][0], agg[k][1]))
    print("\n(MTF net belirgin POZITIF + SINGLE'i gecerse -> konfluens deger katti.")
    print(" MTF de negatif / SINGLE'dan iyi degilse -> cok-TF ek bilgi yok (ayni fiyat, gecikme).)")
