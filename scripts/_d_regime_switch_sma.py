"""D SMA-align REJIM-KOSULLU switch testi.
Soru: SMA filtresini rejime gore ac/kapa (trend->ac, range->kapa) 3 coin x 3 bagimsiz
pencerede HEM 'hep-acik' HEM 'hep-kapali'yi gecer mi? Gecmezse switch = overfit (hafiza:
regime-cluster/regime-arch defalarca coktu). Rejim metrigi = uzun-pencere efficiency-ratio
(ER yuksek=trend). Disiplin: birkac esik dene, plato + coin-tutarlilik ara.
"""
import urllib.request, json, time

FEE = 8.0
M = 40; DEV = 85.0; MINPROF = 12.0; ER_GATE = 0.5; SMA_LEN = 120
SL_FLOOR, SL_CEIL, ATR_MULT, ATR_N = 300.0, 600.0, 7.5, 14
MAXHOLD = 16
REGW = 96                     # rejim ER penceresi (1 gun = 96 x 15m)
REG_THRS = [0.30, 0.40, 0.50]  # trend esikleri (plato ariyoruz)

def klines_paged(sym, days=150):
    out = {}; end = int(time.time() * 1000); need = days * 96
    while len(out) < need:
        u = ("https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=15m&limit=1500&endTime=%d"
             % (sym, end))
        try:
            r = json.loads(urllib.request.urlopen(u, timeout=20).read())
        except Exception:
            break
        if not r: break
        for x in r:
            out[int(x[0])] = (float(x[2]), float(x[3]), float(x[4]), float(x[5]))
        end = r[0][0] - 1
        if len(r) < 1500: break
    return [out[k] for k in sorted(out)]  # (H,L,C,V)

def poc(C, V, i):
    nu = de = 0.0
    for j in range(i - M, i):
        v = V[j] if (V[j] and V[j] > 0) else 1.0
        nu += C[j] * v; de += v
    return nu / de if de > 0 else None

def er(C, i, n=20):
    if i <= n: return 0.0
    net = abs(C[i] - C[i - n]); path = sum(abs(C[i - j] - C[i - j - 1]) for j in range(n))
    return net / path if path > 0 else 0.0

def atr_bps(H, L, C, i):
    if i < ATR_N: return 400.0
    s = sum(max(H[j] - L[j], abs(H[j] - C[j - 1]), abs(L[j] - C[j - 1])) for j in range(i - ATR_N + 1, i + 1))
    a = s / ATR_N
    return a / C[i] * 1e4 if C[i] > 0 else 400.0

def sl_bps(H, L, C, i):
    return min(max(ATR_MULT * atr_bps(H, L, C, i), SL_FLOOR), SL_CEIL)

def sim(H, L, C, V, i, sig):
    ent = C[i]; sl = sl_bps(H, L, C, i); n = len(C)
    for j in range(i + 1, min(i + MAXHOLD + 1, n)):
        cur = ((C[j] - ent) if sig == "LONG" else (ent - C[j])) / ent * 1e4
        adv = ((H[j] - ent) if sig == "SHORT" else (ent - L[j])) / ent * 1e4
        if adv >= sl: return -sl - FEE, j
        pcj = poc(C, V, j)
        if pcj:
            dj = (C[j] - pcj) / pcj * 1e4
            if ((sig == "LONG" and dj >= 0) or (sig == "SHORT" and dj <= 0)) and cur >= MINPROF:
                return cur - FEE, j
        if (j - i) >= MAXHOLD: return cur - FEE, j
    return ((C[-1] - ent) if sig == "LONG" else (ent - C[-1])) / ent * 1e4 - FEE, n - 1

def policies(bars, lo, hi, reg_thr):
    """Ayni sinyaller uzerinde 3 politika neti dondur: OFF, ON, COND(reg_thr)."""
    H = [b[0] for b in bars]; L = [b[1] for b in bars]; C = [b[2] for b in bars]; V = [b[3] for b in bars]
    n = len(C); off = []; on = []; cond = []
    i = max(M, SMA_LEN, REGW, lo)
    while i < min(hi, n - 1):
        pc = poc(C, V, i)
        if not pc: i += 1; continue
        dev = (C[i] - pc) / pc * 1e4
        sig = "LONG" if dev <= -DEV else ("SHORT" if dev >= DEV else None)
        if not sig or er(C, i) >= ER_GATE: i += 1; continue
        sma = sum(C[i - SMA_LEN + 1:i + 1]) / SMA_LEN
        aligned = (sig == "LONG" and C[i] > sma) or (sig == "SHORT" and C[i] < sma)
        reg_er = er(C, i, REGW)              # uzun-pencere trend gucu
        trending = reg_er >= reg_thr
        res, jend = sim(H, L, C, V, i, sig)
        off.append(res)                                   # hep al
        if aligned: on.append(res)                        # hep filtrele
        if (trending and aligned) or (not trending):      # trend->filtre, range->al
            cond.append(res)
        i = jend + 1
    return off, on, cond

def net(d): return sum(d) if d else 0.0

if __name__ == "__main__":
    coins = ["ETHUSDT", "BTCUSDT", "SOLUSDT"]
    print("=== D SMA-align REJIM-KOSULLU switch | 3 coin x 3 bagimsiz pencere ===")
    print("net bps. COND kazanmak icin HEM OFF HEM ON'u gecmeli (yoksa switch gereksiz).\n")
    win_cond = {t: 0 for t in REG_THRS}; tot = 0
    for sym in coins:
        bars = klines_paged(sym, 150)
        if len(bars) < 5000:
            print("%s veri az (%d), atla" % (sym, len(bars))); continue
        n = len(bars); start = max(M, SMA_LEN, REGW) + 5; span = n - 1 - start; w = span // 3
        print("=== %s (bar=%d) ===" % (sym, n))
        print("  pencere |   OFF   |   ON    | " + " | ".join("COND%.2f" % t for t in REG_THRS))
        for k in range(3):
            lo = start + k * w; hi = start + (k + 1) * w if k < 2 else n - 1
            off, on, _ = policies(bars, lo, hi, REG_THRS[0])
            noff, non = net(off), net(on)
            row = "  W%d      |%+7.0f |%+7.0f" % (k + 1, noff, non)
            best_base = max(noff, non)
            for t in REG_THRS:
                _, _, cond = policies(bars, lo, hi, t)
                nc = net(cond)
                row += " |%+7.0f" % nc
                if nc > best_base: win_cond[t] += 1
            tot += 1
            print(row)
        print()
    print("=== OZET: COND(rejim-switch) HEM OFF HEM ON'u kac pencerede gecti (%d uzerinden) ===" % tot)
    for t in REG_THRS:
        print("  esik %.2f: %d/%d pencere" % (t, win_cond[t], tot))
    print("\n(coğunlukta gecerse + coin-tutarli + esik-plato = rejim-switch degerli.")
    print(" gecmezse: switch overfit, EN IYI SABIT politikayi (OFF ya da ON) sec.)")
