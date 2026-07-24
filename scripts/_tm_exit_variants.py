"""TM giris + farkli CIKIS testi (kullanici hipotezi: giris/yon dogru, sorun cikista).
TM entry'leri (HA CCI+ATR sinyali) alinir; cikis varyantlari GERCEK-mum uzerinde denenir:
  FLIP   = mevcut (ters flip'e kadar tut)
  TP_X   = +X bps hedefe ulasinca cik (D-tarzi erken kar-al)
  TRAIL_X= tepe-kardan X bps geri cekilince cik (kilit)
Martingale YOK. fee12. 3 coin x 3 bagimsiz pencere. FLIP'i gecen + net pozitif = hipotez dogru.
"""
import sys, urllib.request, json, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.trend_magic_v3 import compute_series, TrendMagicParams

FEE = 12.0  # 8 fee + 2x2 slip (run_backtest ile ayni)

def klines(sym, days=60):
    per = 96; out = {}; end = int(time.time() * 1000); need = days * per
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
    """TM flip noktalari -> (i, side)."""
    ser = compute_series(bars, TrendMagicParams())
    if not ser.get("ready"): return []
    td = ser["trend_dir"]; out = []
    for i in range(1, len(td)):
        if td[i] == 1 and td[i - 1] == -1: out.append((i, "LONG"))
        elif td[i] == -1 and td[i - 1] == 1: out.append((i, "SHORT"))
    return out, td

def sim(bars, td, i, side, mode, param):
    """Bir TM giris icin cikis PnL (bps, net fee)."""
    H = [b["high"] for b in bars]; L = [b["low"] for b in bars]; C = [b["close"] for b in bars]
    ent = C[i]; n = len(C); peak = 0.0
    for j in range(i + 1, n):
        fav = ((H[j] - ent) if side == "LONG" else (ent - L[j])) / ent * 1e4   # lehte azami
        cur = ((C[j] - ent) if side == "LONG" else (ent - C[j])) / ent * 1e4
        flip = (side == "LONG" and td[j] == -1) or (side == "SHORT" and td[j] == 1)
        if mode == "tp" and fav >= param:
            return param - FEE
        if mode == "trail":
            peak = max(peak, fav)
            if peak >= param and (peak - cur) >= param * 0.5:   # tepe-kardan yariladi -> cik
                return cur - FEE
        if flip:
            return cur - FEE
    return ((C[-1] - ent) if side == "LONG" else (ent - C[-1])) / ent * 1e4 - FEE

def net(sym_bars, td, ents, lo, hi, mode, param):
    pnls = [sim(sym_bars, td, i, s, mode, param) for (i, s) in ents if lo <= i < hi]
    return (len(pnls), sum(pnls), 100 * sum(1 for x in pnls if x > 0) / len(pnls) if pnls else 0)

if __name__ == "__main__":
    variants = [("FLIP", "flip", 0), ("TP200", "tp", 200), ("TP300", "tp", 300),
                ("TP500", "tp", 500), ("TRAIL200", "trail", 200)]
    print("=== TM giris + farkli cikis | gercek-mum, martingale YOK, fee12 | 3 coin x 3 pencere ===\n")
    agg = {v[0]: 0 for v in variants}
    for sym in ("ETHUSDT", "BTCUSDT", "SOLUSDT"):
        bars = klines(sym, 60)
        if len(bars) < 3000: print(sym, "veri az"); continue
        res = entries(bars)
        if not res: print(sym, "TM hazir degil"); continue
        ents, td = res
        n = len(bars); start = 60; span = n - 1 - start; w = span // 3
        print("=== %s (bar=%d, TM giris=%d) ===" % (sym, n, len(ents)))
        hdr = "  pencere " + "".join("| %-9s" % v[0] for v in variants)
        print(hdr)
        for k in range(3):
            lo = start + k * w; hi = start + (k + 1) * w if k < 2 else n - 1
            row = "  W%d     " % (k + 1)
            for name, mode, param in variants:
                nn, nt, win = net(bars, td, ents, lo, hi, mode, param)
                row += "|%+6.0f(%2d)" % (nt, nn)
                agg[name] += nt
            print(row)
        print()
    print("=== TOPLAM net (3 coin x 3 pencere) ===")
    for name, _, _ in variants:
        print("  %-9s %+7.0f bps" % (name, agg[name]))
    print("\n(FLIP baz. Bir varyant HEM FLIP'i gecer HEM net pozitifse -> D-tarzi cikis TM'yi kurtardi.")
    print(" Hepsi negatif / FLIP en iyi ise -> giris/yon zayif, cikis degistirmek kurtarmiyor.)")
