"""Ters-cikis (reverse-exit) tanimi testi.
Soru: Range-SHORT'u ters sinyalde erken kapatmak, SL/hedefe kadar tutmaktan iyi mi?
Ve 'ters' tanimi ne olmali — 15m UP mi, 5m+15m ikisi UP mi (mevcut kod), yoksa hic mi?

DURUST SINIR: v3'un gercek s5m/s15m structure etiketlerini klines'tan birebir uretemem.
Proxy: her TF'de yon = close>EMA(EMA_N) ve EMA yukselen => UP. Bu ILKEYI olcer (erken-cikis
degerli mi, siki mi gevsek mi tanim), canli esigi degil.

Kurgu: AYNI SHORT girisleri (range-tepesi fade, #330 tarzi SL+350bps/hedef-880bps), sadece
CIKIS kurali degisir -> temiz kiyas. 3 coin x 3 bagimsiz pencere.
"""
import urllib.request, json, time

FEE = 8.0
SL_BPS = 350.0        # #330: entry 1888 -> SL 1954 ~ +3.5%
TP_BPS = 880.0        # #330: entry 1888 -> hedef 1722 ~ -8.8%
RES_LOOK = 20         # 15m direnc penceresi
TOUCH = 0.999         # direnci %0.1 yakininda touch = fade short
EMA_N = 9
MAXHOLD = 200         # 15m bar (guvenlik ust siniri)

def klines_paged(sym, interval, days):
    per = {"5m": 288, "15m": 96}[interval]
    out = {}; end = int(time.time() * 1000); need = days * per
    while len(out) < need:
        u = ("https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=%s&limit=1500&endTime=%d"
             % (sym, interval, end))
        try:
            r = json.loads(urllib.request.urlopen(u, timeout=20).read())
        except Exception:
            break
        if not r: break
        for x in r:
            out[int(x[0])] = (int(x[0]) // 1000, float(x[2]), float(x[3]), float(x[4]))
        end = r[0][0] - 1
        if len(r) < 1500: break
    return [out[k] for k in sorted(out)]  # (ts,H,L,C)

def ema(vals, n):
    k = 2 / (n + 1); e = [vals[0]]
    for v in vals[1:]:
        e.append(e[-1] + k * (v - e[-1]))
    return e

def dir_series(bars):
    """UP(+1)/DOWN(-1) proxy: close>EMA ve EMA yukselen -> UP."""
    C = [b[3] for b in bars]; e = ema(C, EMA_N); d = [0] * len(C)
    for i in range(1, len(C)):
        if C[i] > e[i] and e[i] > e[i - 1]: d[i] = 1
        elif C[i] < e[i] and e[i] < e[i - 1]: d[i] = -1
    return d

def build_5m_dir_lookup(bars5):
    d5 = dir_series(bars5); return {bars5[i][0]: d5[i] for i in range(len(bars5))}

def dir5_at(lk5, ts, keys5):
    """ts (15m bar) icin en yakin GECMIS 5m yonu."""
    import bisect
    p = bisect.bisect_right(keys5, ts) - 1
    return lk5[keys5[p]] if p >= 0 else 0

def run(bars15, d15, lk5, keys5, lo, hi, exit_mode):
    """exit_mode: 'hold' | '15m' | '5m15m'. Ayni girisler, farkli cikis."""
    T = [b[0] for b in bars15]; H = [b[1] for b in bars15]; L = [b[2] for b in bars15]; C = [b[3] for b in bars15]
    n = len(C); trades = []; i = max(RES_LOOK, EMA_N, lo)
    while i < min(hi, n - 1):
        res = max(H[i - RES_LOOK:i])
        if H[i] < res * TOUCH:   # direnci touch etmedi -> giris yok
            i += 1; continue
        ent = C[i]; sl = ent * (1 + SL_BPS / 1e4); tp = ent * (1 - TP_BPS / 1e4)
        pnl = None; jend = i
        for j in range(i + 1, min(i + MAXHOLD + 1, n)):
            jend = j
            if H[j] >= sl: pnl = -SL_BPS - FEE; break            # SL
            if L[j] <= tp: pnl = TP_BPS - FEE; break             # hedef
            if exit_mode != "hold":                              # ters-cikis kontrol
                up15 = d15[j] == 1
                up5 = dir5_at(lk5, T[j], keys5) == 1
                rev = up15 if exit_mode == "15m" else (up15 and up5)
                if rev:
                    cur = (ent - C[j]) / ent * 1e4               # SHORT anlik
                    pnl = cur - FEE; break
        if pnl is None:
            pnl = (ent - C[jend]) / ent * 1e4 - FEE
        trades.append((pnl, jend - i))
        i = jend + 1
    return trades

def stats(trades):
    if not trades: return (0, 0, 0, 0, 0)
    p = [t[0] for t in trades]; net = sum(p)
    win = 100 * sum(1 for x in p if x > 0) / len(p)
    eq = pk = mdd = 0
    for x in p:
        eq += x; pk = max(pk, eq); mdd = min(mdd, eq - pk)
    avghold = sum(t[1] for t in trades) / len(trades)
    return (len(p), net, win, mdd, avghold)

if __name__ == "__main__":
    print("=== Ters-cikis tanimi | ayni range-SHORT girisleri, 3 cikis | 3 coin x 3 pencere ===")
    print("HOLD=SL/hedefe kadar tut | 15m=15m UP'ta kapat | 5m15m=ikisi UP'ta kapat (kod)\n")
    agg = {"hold": [0, 0], "15m": [0, 0], "5m15m": [0, 0]}  # [net toplam, dd toplam]
    for sym in ("ETHUSDT", "BTCUSDT", "SOLUSDT"):
        b15 = klines_paged(sym, "15m", 60); b5 = klines_paged(sym, "5m", 60)
        if len(b15) < 3000 or len(b5) < 3000:
            print("%s veri az, atla" % sym); continue
        d15 = dir_series(b15); lk5 = build_5m_dir_lookup(b5); keys5 = sorted(lk5)
        n = len(b15); start = max(RES_LOOK, EMA_N) + 5; span = n - 1 - start; w = span // 3
        print("=== %s (15m bar=%d) ===" % (sym, n))
        print("  pencere | mod    | isl | net(bps) | win%% | maxDD | ort-tutus(bar)")
        for k in range(3):
            lo = start + k * w; hi = start + (k + 1) * w if k < 2 else n - 1
            for mode in ("hold", "15m", "5m15m"):
                nn, net, win, mdd, ah = stats(run(b15, d15, lk5, keys5, lo, hi, mode))
                print("  W%d      | %-6s | %3d | %+8.0f | %3.0f  | %+5.0f | %.1f"
                      % (k + 1, mode, nn, net, win, mdd, ah))
                agg[mode][0] += net; agg[mode][1] += mdd
            print()
    print("=== TOPLAM (3 coin x 3 pencere) ===")
    print("  mod    | net toplam | dd toplam")
    for mode in ("hold", "15m", "5m15m"):
        print("  %-6s | %+9.0f | %+8.0f" % (mode, agg[mode][0], agg[mode][1]))
    print("\n(net yuksek + dd kucuk = iyi. HOLD'u gecen ters-cikis varsa erken-kapatma degerli;")
    print(" hicbiri gecmezse SL/hedefe kadar tutmak dogru -> kod zaten oyle.)")
