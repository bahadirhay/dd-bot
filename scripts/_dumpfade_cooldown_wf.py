"""scripts/_dumpfade_cooldown_wf.py — DEXE-tipi "gercek felaket sonrasi tekrar tekrar yanlis sicrama
bahsi" sorununu COZECEK cooldown-N'yi TAHMIN degil VERIDEN bulur. Yontem: 246-sembol/~520-gun genis
veride her coin icin >= -30% (DUMP_CAP disi, "felaket") gunlerini isaretle; sonra NORMAL dump-fade
islemlerini (mevcut -12.5/-30 araligi) "en son felaketten kac gun sonra" diye grupla; her grubun
ort-pnl/win%'ini olc. Boylece N (ve N'nin coin'e/olaya gore degisip degismedigi) EMPIRIK olarak
gorulur, kullanicinin sordugu "N nasil bulunur, sabit mi degisken mi" sorusuna veriyle cevap.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request

CAND_N = 300
DUMP_PCT = 12.5
DUMP_CAP = 30.0
CRASH_PCT = 30.0   # bu esikten daha buyuk gunluk dusus = "felaket" (DUMP_CAP disinda kalan, hic islenmeyen gun)
OFFSET_BPS = 300.0
MAKER_FEE = 10.0
MIN_QVOL = 25e6
MIN_DAYS = 60
DAYS = 520
CACHE = os.path.join(os.path.dirname(__file__), "_dumpfade_cooldown_cache.json")

BUCKETS = [(0, 3), (3, 5), (5, 7), (7, 10), (10, 14), (14, 21), (21, 30), (30, 999999)]
NEVER_LABEL = "hic-felaket-yok"


def _get(u, timeout=20):
    return json.loads(urllib.request.urlopen(u, timeout=timeout).read())


def candidates():
    t = _get("https://fapi.binance.com/fapi/v1/ticker/24hr")
    rows = [(x["symbol"], float(x.get("quoteVolume", 0) or 0)) for x in t
            if str(x.get("symbol", "")).endswith("USDT")]
    rows.sort(key=lambda z: -z[1])
    return [s for s, _ in rows[:CAND_N]]


def daily(sym, limit=DAYS + 40):
    try:
        r = _get(f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1d&limit={limit}")
        return [(int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[7])) for x in r]
    except Exception:
        return None


def main():
    if os.path.exists(CACHE):
        print(f"Onbellekten yukleniyor: {CACHE}")
        with open(CACHE) as f:
            all_data = json.load(f)
    else:
        print("Aday evren cekiliyor...")
        syms = candidates()
        print(f"{len(syms)} sembol, gunluk {DAYS}g veri cekiliyor...")
        all_data = {}
        for idx, sym in enumerate(syms):
            bars = daily(sym)
            if not bars or len(bars) < MIN_DAYS + 30:
                continue
            all_data[sym] = bars
            if (idx + 1) % 50 == 0:
                print(f"  {idx+1}/{len(syms)} sembol islendi")
            time.sleep(0.05)
        with open(CACHE, "w") as f:
            json.dump(all_data, f)
        print(f"Onbellege yazildi: {CACHE}")

    print(f"\n{len(all_data)} coin uzerinde analiz ediliyor...\n")

    # her trade: (days_since_crash_or_None, pnl_bps, sym, crash_pct_if_any)
    trades = []
    for sym, bars in all_data.items():
        n = len(bars)
        last_crash_day_idx = None   # bu coin icin en son felaket gunu index'i
        last_crash_pct = None
        for i in range(MIN_DAYS, n - 1):
            vols = [bars[j][5] for j in range(max(0, i - 30), i) if bars[j][5] > 0]
            liquid = vols and (sum(vols) / len(vols)) >= MIN_QVOL
            o_d, c_d = bars[i][1], bars[i][4]
            if o_d <= 0:
                continue
            dump = (c_d - o_d) / o_d * 100
            # FELAKET gunu kaydi (likidite sarti yok - herhangi bir coin felaket gecirebilir)
            if dump <= -CRASH_PCT:
                last_crash_day_idx = i
                last_crash_pct = dump
            if not liquid:
                continue
            if dump > -DUMP_PCT or dump <= -DUMP_CAP:
                continue
            D1 = bars[i + 1]
            o1, l1, c1 = D1[1], D1[3], D1[4]
            if o1 <= 0:
                continue
            lim = o1 * (1 - OFFSET_BPS / 1e4)
            filled = l1 <= lim
            if not filled:
                continue
            pnl = (c1 - lim) / lim * 1e4 - MAKER_FEE
            days_since = (i - last_crash_day_idx) if last_crash_day_idx is not None else None
            trades.append((days_since, pnl, sym, last_crash_pct))

    print(f"Toplam dolu (FILLED) dump-fade islemi: {len(trades)}\n")

    def bucket_of(d):
        if d is None:
            return NEVER_LABEL
        for lo, hi in BUCKETS:
            if lo <= d < hi:
                return f"{lo}-{hi if hi < 999999 else '+'}g"
        return NEVER_LABEL

    stats = {}
    for days_since, pnl, sym, crash_pct in trades:
        b = bucket_of(days_since)
        stats.setdefault(b, []).append(pnl)

    order = [f"{lo}-{hi if hi < 999999 else '+'}g" for lo, hi in BUCKETS] + [NEVER_LABEL]
    print(f"{'bucket (son felaketten bu yana gun)':>38} | {'n':>5} | {'net':>9} | {'ort/islem':>10} | {'win%':>6}")
    for b in order:
        pnls = stats.get(b, [])
        if not pnls:
            print(f"{b:>38} | {'0':>5} | {'-':>9} | {'-':>10} | {'-':>6}")
            continue
        net = sum(pnls)
        wr = sum(1 for p in pnls if p > 0) / len(pnls) * 100
        print(f"{b:>38} | {len(pnls):>5} | {net:>+9.0f} | {net/len(pnls):>+10.1f} | {wr:>6.1f}")

    # coin-bazinda kirilim: "yakin-zamanda felaket gecirmis" olaylarin hangi coinlerde yogunlastigina bak
    print("\nEn cok 'yakin-felaket-sonrasi' (0-7g) islem ureten coinler (potansiyel tekrar-sorunlu):")
    from collections import Counter
    near = [t for t in trades if t[0] is not None and t[0] < 7]
    cnt = Counter(t[2] for t in near)
    for sym, c in cnt.most_common(10):
        pnls = [t[1] for t in near if t[2] == sym]
        print(f"  {sym:<14} n={c:3d} net={sum(pnls):+.0f}bps")


if __name__ == "__main__":
    main()
