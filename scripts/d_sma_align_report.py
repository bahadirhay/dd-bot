"""D SMA-hizasi FORWARD canli-olcum raporu.
Amac: "SMA filtresi canlida deger katiyor mu" sorusunu LAF degil RAKAM ile yanitlamak.
Yontem: ETH 15m klines uzerinde D'yi CANLI parametrelerle (M40/DEV85/minprofit12/ER0.5/
SL ATR-clamp300-600) yeniden oynat. Her D sinyalini SMA120 ile ALINDI vs BLOKLANDI diye
ayir; ikisine de AYNI cikis mantigini uygula. Bloklanan fade'ler ortalama KAYBEDIYORSA
filtre dogru atliyor (deger katiyor); KAZANIYORSA filtre bize para kaybettiriyor.

Canli koda SIFIR dokunus. Gun gectikce tekrar calistir -> forward pencere buyur, gercek
sayi netlesir. Deploy: 2026-07-22 (commit e319a98).
"""
import urllib.request, json, time, datetime as dt

DEPLOY_TS = dt.datetime(2026, 7, 22, 7, 0).timestamp()  # SMA-align canliya alindi
FEE = 8.0            # taker bps (canli maker ~5 -> muhafazakar). Iki set icin AYNI, kiyas etkilenmez.
M = 40; DEV = 85.0; MINPROF = 12.0; ER_GATE = 0.5; SMA_LEN = 120
SL_FLOOR, SL_CEIL, ATR_MULT, ATR_N = 300.0, 600.0, 7.5, 14
MAXHOLD = 16         # 16 x 15m = 4 saat

def klines_paged(sym="ETHUSDT", days=45):
    """Son `days` gunluk 15m barlari sayfalayarak cek (Binance limit=1500/istek)."""
    out = {}; end = int(time.time() * 1000)
    need = days * 96
    while len(out) < need:
        u = ("https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=15m&limit=1500&endTime=%d"
             % (sym, end))
        try:
            r = json.loads(urllib.request.urlopen(u, timeout=20).read())
        except Exception as e:
            print("klines cek hata:", e); break
        if not r:
            break
        for x in r:
            out[int(x[0])] = (int(x[0]) // 1000, float(x[2]), float(x[3]), float(x[4]), float(x[5]))
        end = r[0][0] - 1
        if len(r) < 1500:
            break
    ks = sorted(out)
    return [out[k] for k in ks]  # (ts, high, low, close, vol)

def poc(C, V, i):
    nu = de = 0.0
    for j in range(i - M, i):
        v = V[j] if (V[j] and V[j] > 0) else 1.0
        nu += C[j] * v; de += v
    return nu / de if de > 0 else None

def er(C, i, n=20):
    if i <= n: return 0.0
    net = abs(C[i] - C[i - n])
    path = sum(abs(C[i - j] - C[i - j - 1]) for j in range(n))
    return net / path if path > 0 else 0.0

def atr_bps(H, L, C, i):
    if i < ATR_N: return 400.0
    s = 0.0
    for j in range(i - ATR_N + 1, i + 1):
        tr = max(H[j] - L[j], abs(H[j] - C[j - 1]), abs(L[j] - C[j - 1]))
        s += tr
    a = s / ATR_N
    return a / C[i] * 1e4 if C[i] > 0 else 400.0

def sl_bps(H, L, C, i):
    return min(max(ATR_MULT * atr_bps(H, L, C, i), SL_FLOOR), SL_CEIL)

def sim_signal(H, L, C, V, i, sig):
    """Bir D sinyalinin sonucunu (bps net) hesapla — taken/blocked ayni cikis."""
    ent = C[i]; sl = sl_bps(H, L, C, i); n = len(C)
    for j in range(i + 1, min(i + MAXHOLD + 1, n)):
        cur = ((C[j] - ent) if sig == "LONG" else (ent - C[j])) / ent * 1e4
        adv = ((H[j] - ent) if sig == "SHORT" else (ent - L[j])) / ent * 1e4
        if adv >= sl:
            return -sl - FEE, j
        pcj = poc(C, V, j)
        if pcj:
            dj = (C[j] - pcj) / pcj * 1e4
            rev = (sig == "LONG" and dj >= 0) or (sig == "SHORT" and dj <= 0)
            if rev and cur >= MINPROF:
                return cur - FEE, j
        if (j - i) >= MAXHOLD:
            return cur - FEE, j
    last = ((C[-1] - ent) if sig == "LONG" else (ent - C[-1])) / ent * 1e4
    return last - FEE, n - 1

def scan(bars, ts_lo=0, ts_hi=None):
    T = [b[0] for b in bars]; H = [b[1] for b in bars]; L = [b[2] for b in bars]
    C = [b[3] for b in bars]; V = [b[4] for b in bars]
    n = len(C); ts_hi = ts_hi or (T[-1] + 1)
    taken = []; blocked = []
    i = max(M, SMA_LEN, 96)
    while i < n - 1:
        if not (ts_lo <= T[i] < ts_hi):
            i += 1; continue
        pc = poc(C, V, i)
        if not pc:
            i += 1; continue
        dev = (C[i] - pc) / pc * 1e4
        sig = "LONG" if dev <= -DEV else ("SHORT" if dev >= DEV else None)
        if not sig or er(C, i) >= ER_GATE:
            i += 1; continue
        sma = sum(C[i - SMA_LEN + 1:i + 1]) / SMA_LEN
        aligned = (sig == "LONG" and C[i] > sma) or (sig == "SHORT" and C[i] < sma)
        res, jend = sim_signal(H, L, C, V, i, sig)
        (taken if aligned else blocked).append(res)
        i = jend + 1
    return taken, blocked

def rep(lbl, tr):
    if not tr:
        print("  %-26s islem=0" % lbl); return
    net = sum(tr); win = 100 * sum(1 for x in tr if x > 0) / len(tr)
    print("  %-26s isl=%-3d net=%+7.0fbps ort=%+6.1fbps isabet=%%%.0f"
          % (lbl, len(tr), net, net / len(tr), win))

if __name__ == "__main__":
    bars = klines_paged("ETHUSDT", days=45)
    if not bars:
        print("veri yok"); raise SystemExit
    d0 = dt.datetime.fromtimestamp(bars[0][0]).strftime("%m-%d")
    d1 = dt.datetime.fromtimestamp(bars[-1][0]).strftime("%m-%d %H:%M")
    print("ETH 15m bar=%d  (%s -> %s)\n" % (len(bars), d0, d1))

    print("=== 1) FORWARD: SMA-align DEPLOY sonrasi (canli-olcum, buyuyen) ===")
    tk, bl = scan(bars, ts_lo=DEPLOY_TS)
    print("  deploy: %s" % dt.datetime.fromtimestamp(DEPLOY_TS).strftime("%Y-%m-%d %H:%M"))
    rep("ALINAN (SMA gecti)", tk)
    rep("BLOKLANAN (skip)", bl)
    if bl:
        print("  -> BLOKLANAN ort <0 ise filtre DOGRU atliyor (deger katti).")
    else:
        print("  -> Henuz forward veri yetersiz (deploy yeni). Gunler gecince tekrar calistir.")

    print("\n=== 2) BAGLAM: son 45 gun (deploy ONCESI dahil, backtest-baglami, KANIT DEGIL) ===")
    tk2, bl2 = scan(bars, ts_lo=0, ts_hi=DEPLOY_TS)
    rep("ALINAN (SMA gecti)", tk2)
    rep("BLOKLANAN (skip)", bl2)
    if tk2 and bl2:
        print("  fark (alinan_ort - bloklanan_ort) = %+.1f bps  (+ = filtre iyi ayiriyor)"
              % (sum(tk2) / len(tk2) - sum(bl2) / len(bl2)))
