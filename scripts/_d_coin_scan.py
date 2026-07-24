"""D (POC+SMA-align) COK-COIN tarama: hangi coin daha SIK girer VE kar ed3r?
Kullanici: ETH 2 gundur girmiyor (dusuk oynaklik). Daha hizli coinlerde D daha sik tetiklenir.
AMA sik != karli. Her coin: frekans (islem/ay) + 2-yari WF net (ikisi + = robust). Disiplin:
in-sample kazanan forward ister; naif coin-search data-mining. Canli D parametreleri.
"""
import urllib.request, json, time

FEE = 12.0
M = 40; DEV = 85.0; MINPROF = 12.0; ER_GATE = 0.5; SMA_LEN = 120
SL_FLOOR, SL_CEIL, ATR_MULT, ATR_N = 300.0, 600.0, 7.5, 14
MAXHOLD = 16

COINS = ["ETHUSDT", "BTCUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT",
         "AVAXUSDT", "LINKUSDT", "SUIUSDT", "APTUSDT", "INJUSDT", "WIFUSDT"]

def kl(sym, days=75):
    out = {}; end = int(time.time() * 1000); need = days * 96
    while len(out) < need:
        u = ("https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=15m&limit=1500&endTime=%d" % (sym, end))
        try: r = json.loads(urllib.request.urlopen(u, timeout=20).read())
        except Exception: return None
        if not r: break
        for x in r:
            out[int(x[0])] = (float(x[2]), float(x[3]), float(x[4]), float(x[5]))
        end = r[0][0] - 1
        if len(r) < 1500: break
    return [out[k] for k in sorted(out)]

def poc(C, V, i):
    nu = de = 0.0
    for j in range(i - M, i):
        v = V[j] if V[j] > 0 else 1.0; nu += C[j] * v; de += v
    return nu / de if de > 0 else None

def er(C, i, n=20):
    if i <= n: return 0.0
    net = abs(C[i] - C[i - n]); path = sum(abs(C[i - j] - C[i - j - 1]) for j in range(n))
    return net / path if path > 0 else 0.0

def atrb(H, L, C, i):
    if i < ATR_N: return 400.0
    s = sum(max(H[j] - L[j], abs(H[j] - C[j - 1]), abs(L[j] - C[j - 1])) for j in range(i - ATR_N + 1, i + 1))
    return (s / ATR_N) / C[i] * 1e4 if C[i] > 0 else 400.0

def scan(bars, lo, hi):
    H = [b[0] for b in bars]; L = [b[1] for b in bars]; C = [b[2] for b in bars]; V = [b[3] for b in bars]
    n = len(C); trades = []; i = max(M, SMA_LEN, 96, lo)
    while i < min(hi, n - 1):
        pc = poc(C, V, i)
        if not pc: i += 1; continue
        dev = (C[i] - pc) / pc * 1e4
        sig = "LONG" if dev <= -DEV else ("SHORT" if dev >= DEV else None)
        if not sig or er(C, i) >= ER_GATE: i += 1; continue
        sma = sum(C[i - SMA_LEN + 1:i + 1]) / SMA_LEN
        if (sig == "LONG" and C[i] <= sma) or (sig == "SHORT" and C[i] >= sma):
            i += 1; continue  # SMA-align blok
        ent = C[i]; sl = min(max(ATR_MULT * atrb(H, L, C, i), SL_FLOOR), SL_CEIL); res = None; jend = i
        for j in range(i + 1, min(i + MAXHOLD + 1, n)):
            jend = j
            cur = ((C[j] - ent) if sig == "LONG" else (ent - C[j])) / ent * 1e4
            adv = ((H[j] - ent) if sig == "SHORT" else (ent - L[j])) / ent * 1e4
            if adv >= sl: res = -sl - FEE; break
            pcj = poc(C, V, j)
            if pcj:
                dj = (C[j] - pcj) / pcj * 1e4
                if ((sig == "LONG" and dj >= 0) or (sig == "SHORT" and dj <= 0)) and cur >= MINPROF:
                    res = cur - FEE; break
            if (j - i) >= MAXHOLD: res = cur - FEE; break
        if res is None: res = ((C[jend] - ent) if sig == "LONG" else (ent - C[jend])) / ent * 1e4 - FEE
        trades.append(res); i = jend + 1
    return trades

if __name__ == "__main__":
    print("=== D (POC+SMA-align) COK-COIN tarama | ~75 gun 15m | canli parametreler ===")
    print("frekans=islem/ay | net=toplam bps | H1/H2=iki yari (ikisi+ = robust)\n")
    print("  %-9s | isl | ay/isl | net bps | ort  | win | H1    H2   | ATR%%" % "coin")
    rows = []
    for sym in COINS:
        bars = kl(sym, 75)
        if not bars or len(bars) < 3000:
            print("  %-9s | veri yok" % sym); continue
        n = len(bars); days = n * 15 / 1440.0; mid = n // 2
        allt = scan(bars, 0, n - 1)
        h1 = scan(bars, 0, mid); h2 = scan(bars, mid, n - 1)
        C = [b[2] for b in bars]; H = [b[0] for b in bars]; L = [b[1] for b in bars]
        atr_pct = sum((H[i] - L[i]) / C[i] for i in range(len(C)) if C[i] > 0) / len(C) * 100
        if allt:
            freq = len(allt) / days * 30
            win = 100 * sum(1 for x in allt if x > 0) / len(allt)
            robust = "OK" if (sum(h1) > 0 and sum(h2) > 0) else ""
            rows.append((freq, sym, len(allt), freq, sum(allt), sum(allt)/len(allt), win, sum(h1), sum(h2), atr_pct, robust))
        else:
            print("  %-9s | 0 sinyal (cok sakin)" % sym)
    rows.sort(reverse=True)  # frekans yuksek uste
    for freq, sym, n_, f, net, avg, win, hh1, hh2, atr, rob in rows:
        print("  %-9s | %3d | %5.1f  | %+7.0f | %+4.0f | %3.0f | %+5.0f %+5.0f | %.2f %s"
              % (sym, n_, f, net, avg, win, hh1, hh2, atr, rob))
    print("\n(ARANAN: yuksek frekans + net pozitif + H1&H2 ikisi de + (OK=robust). Sadece frekans YETMEZ.")
    print(" OK olmayan yuksek-frekans = cok isler ama edge yok/tek-yari = TUZAK. Forward sart.)")
