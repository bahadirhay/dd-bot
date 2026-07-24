"""D (POC+SMA-align) INJ/DOGE 4-CEYREK WF + slippage duyarliligi.
Adim 1 (backtest): INJ 2-yari cazip cikti; simdi 4 BAGIMSIZ ceyrek + fee 12(iyimser)/20(gercekci-alt).
Robust = coğu ceyrek + HEM fee12 HEM fee20'de pozitif. Geçerse -> shadow adimina. ETH baz."""
import urllib.request, json, time

M = 40; DEV = 85.0; MINPROF = 12.0; ER_GATE = 0.5; SMA_LEN = 120
SL_FLOOR, SL_CEIL, ATR_MULT, ATR_N = 300.0, 600.0, 7.5, 14
MAXHOLD = 16

def kl(sym, days=240):
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

def scan(bars, lo, hi, fee):
    H = [b[0] for b in bars]; L = [b[1] for b in bars]; C = [b[2] for b in bars]; V = [b[3] for b in bars]
    n = len(C); trades = []; i = max(M, SMA_LEN, 96, lo)
    while i < min(hi, n - 1):
        pc = poc(C, V, i)
        if not pc: i += 1; continue
        dev = (C[i] - pc) / pc * 1e4
        sig = "LONG" if dev <= -DEV else ("SHORT" if dev >= DEV else None)
        if not sig or er(C, i) >= ER_GATE: i += 1; continue
        sma = sum(C[i - SMA_LEN + 1:i + 1]) / SMA_LEN
        if (sig == "LONG" and C[i] <= sma) or (sig == "SHORT" and C[i] >= sma): i += 1; continue
        ent = C[i]; sl = min(max(ATR_MULT * atrb(H, L, C, i), SL_FLOOR), SL_CEIL); res = None; jend = i
        for j in range(i + 1, min(i + MAXHOLD + 1, n)):
            jend = j
            cur = ((C[j] - ent) if sig == "LONG" else (ent - C[j])) / ent * 1e4
            adv = ((H[j] - ent) if sig == "SHORT" else (ent - L[j])) / ent * 1e4
            if adv >= sl: res = -sl - fee; break
            pcj = poc(C, V, j)
            if pcj:
                dj = (C[j] - pcj) / pcj * 1e4
                if ((sig == "LONG" and dj >= 0) or (sig == "SHORT" and dj <= 0)) and cur >= MINPROF:
                    res = cur - fee; break
            if (j - i) >= MAXHOLD: res = cur - fee; break
        if res is None: res = ((C[jend] - ent) if sig == "LONG" else (ent - C[jend])) / ent * 1e4 - fee
        trades.append(res); i = jend + 1
    return trades

if __name__ == "__main__":
    print("=== D INJ/DOGE 4-ceyrek WF + slippage | fee12=iyimser fee20=gercekci-alt ===\n")
    for sym in ("INJUSDT", "DOGEUSDT", "ETHUSDT"):
        bars = kl(sym, 240)
        if not bars or len(bars) < 6000:
            print("%s: veri az (%s bar)\n" % (sym, len(bars) if bars else 0)); continue
        n = len(bars); start = max(M, SMA_LEN, 96) + 5; span = n - 1 - start; q = span // 4
        print("=== %s (bar=%d, ~%d gun) ===" % (sym, n, int(n * 15 / 1440)))
        print("  ceyrek | fee12 net(isl) | fee20 net(isl)")
        pos12 = pos20 = 0
        for k in range(4):
            lo = start + k * q; hi = start + (k + 1) * q if k < 3 else n - 1
            t12 = scan(bars, lo, hi, 12.0); t20 = scan(bars, lo, hi, 20.0)
            n12, n20 = sum(t12), sum(t20)
            if n12 > 0: pos12 += 1
            if n20 > 0: pos20 += 1
            print("  Q%d     | %+7.0f (%3d)  | %+7.0f (%3d)" % (k + 1, n12, len(t12), n20, len(t20)))
        print("  --> pozitif ceyrek: fee12=%d/4  fee20=%d/4" % (pos12, pos20))
        print()
    print("(ROBUST = fee20'de bile 3-4/4 ceyrek pozitif. O zaman shadow adimina gec.")
    print(" fee20'de cokerse = edge slippage'a dayanmiyor, alt-coin execution riski gercek.)")
