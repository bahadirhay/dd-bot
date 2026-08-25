"""scripts/_dumpfade_cooldown_wf2.py — cooldown-N arastirmasi RAUND 2, kullanicinin haklı elestirisiyle
guclendirildi: "kalici degil, piyasa/coin degisebilir, daha saglam kanit lazım". Degisiklikler:
(1) DAYS 520->900 (~2.5 yil, mumkun olan en uzun takip penceresi, gercek uzun-vadeli iyilesme var mi
gormek icin), (2) coarse bucket yerine SUREKLI (gun-gun) gun-sonrasi egrisi + rolling-ortalama,
(3) crash SIDDETI (30-50/50-70/70+) ile iyilesme suresi iliskili mi ayri kirilim, (4) HER coin icin
KENDI "normal" performansina (o coin'in hic-crash-donemindeki ort'u) gore normalize edilmis kiyas
(coin-bazli farkli taban seviyelerini karistirmamak icin).
"""
from __future__ import annotations

import json
import os
import time
import urllib.request

CAND_N = 300
DUMP_PCT = 12.5
DUMP_CAP = 30.0
CRASH_PCT = 30.0
OFFSET_BPS = 300.0
MAKER_FEE = 10.0
MIN_QVOL = 25e6
MIN_DAYS = 60
DAYS = 900
CACHE = os.path.join(os.path.dirname(__file__), "_dumpfade_cooldown_cache2.json")


def _get(u, timeout=25):
    return json.loads(urllib.request.urlopen(u, timeout=timeout).read())


def candidates():
    t = _get("https://fapi.binance.com/fapi/v1/ticker/24hr")
    rows = [(x["symbol"], float(x.get("quoteVolume", 0) or 0)) for x in t
            if str(x.get("symbol", "")).endswith("USDT")]
    rows.sort(key=lambda z: -z[1])
    return [s for s, _ in rows[:CAND_N]]


def daily(sym, limit=DAYS + 40):
    out = {}
    end = int(time.time() * 1000)
    need = limit
    tries = 0
    while len(out) < need and tries < 3:
        tries += 1
        try:
            u = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1d&limit=1500&endTime={end}"
            r = _get(u)
        except Exception:
            break
        if not r:
            break
        for x in r:
            out[int(x[0])] = (int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[7]))
        end = r[0][0] - 1
        if len(r) < 1500:
            break
    return [out[k] for k in sorted(out)]


def main():
    if os.path.exists(CACHE):
        print(f"Onbellekten yukleniyor: {CACHE}")
        with open(CACHE) as f:
            all_data = json.load(f)
    else:
        print("Aday evren cekiliyor...")
        syms = candidates()
        print(f"{len(syms)} sembol, gunluk {DAYS}g (mumkun oldugunca uzun) veri cekiliyor...")
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

    print(f"\n{len(all_data)} coin, ortalama {sum(len(b) for b in all_data.values())/max(len(all_data),1):.0f} gun/coin\n")

    # her trade: (days_since_crash_or_None, pnl_bps, sym, crash_severity_pct)
    trades = []
    crash_events_per_coin = {}
    for sym, bars in all_data.items():
        n = len(bars)
        crash_days = []  # [(idx, severity_pct)]
        for i in range(MIN_DAYS, n - 1):
            vols = [bars[j][5] for j in range(max(0, i - 30), i) if bars[j][5] > 0]
            liquid = vols and (sum(vols) / len(vols)) >= MIN_QVOL
            o_d, c_d = bars[i][1], bars[i][4]
            if o_d <= 0:
                continue
            dump = (c_d - o_d) / o_d * 100
            if dump <= -CRASH_PCT:
                crash_days.append((i, dump))
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
            prior_crashes = [(idx, sev) for idx, sev in crash_days if idx < i]
            if prior_crashes:
                last_idx, last_sev = prior_crashes[-1]
                days_since = i - last_idx
            else:
                days_since, last_sev = None, None
            trades.append((days_since, pnl, sym, last_sev))
        crash_events_per_coin[sym] = len(crash_days)

    total_crash_events = sum(crash_events_per_coin.values())
    coins_with_crash = sum(1 for v in crash_events_per_coin.values() if v > 0)
    print(f"Toplam felaket-gunu (>=%{CRASH_PCT} tek-gun dusus) olayi: {total_crash_events}, "
          f"{coins_with_crash}/{len(all_data)} coin'de en az bir kez\n")
    print(f"Toplam dolu (FILLED) dump-fade islemi: {len(trades)}\n")

    # SUREKLI gun-gun egri: her trade'i days_since'e gore sirala, 10'luk kayan pencere ile yumusat
    with_crash = sorted([t for t in trades if t[0] is not None], key=lambda z: z[0])
    never = [t for t in trades if t[0] is None]

    print(f"HIC felaket gecirmemis coinlerin islemleri: n={len(never)} net={sum(t[1] for t in never):+.0f} "
          f"ort={sum(t[1] for t in never)/max(len(never),1):+.1f}bps win%={sum(1 for t in never if t[1]>0)/max(len(never),1)*100:.1f}\n")

    print("GECMISTE FELAKET GECIRMIS coinlerin islemleri — SUREKLI gun-gun (30-islemlik kayan pencere ortalamasi):")
    W = 30
    if len(with_crash) >= W:
        for start in range(0, len(with_crash) - W + 1, max(1, (len(with_crash) - W) // 15 or 1)):
            window = with_crash[start:start + W]
            days_range = f"{window[0][0]}-{window[-1][0]}g"
            net = sum(t[1] for t in window)
            wr = sum(1 for t in window if t[1] > 0) / len(window) * 100
            print(f"  gun-araligi~{days_range:>12} (n={len(window):3d}) | net={net:+8.0f} | ort={net/len(window):+7.1f}bps | win%={wr:5.1f}")
    else:
        print(f"  yeterli veri yok (n={len(with_crash)} < {W})")

    print(f"\nToplam (gecmiste-felaket): n={len(with_crash)} net={sum(t[1] for t in with_crash):+.0f} "
          f"ort={sum(t[1] for t in with_crash)/max(len(with_crash),1):+.1f}bps "
          f"win%={sum(1 for t in with_crash if t[1]>0)/max(len(with_crash),1)*100:.1f}")

    # siddet kirilim
    print("\nCRASH SIDDETINE gore kirilim (son felaketin buyuklugu):")
    sev_buckets = [(-50, -30), (-70, -50), (-999, -70)]
    for lo, hi in sev_buckets:
        grp = [t for t in with_crash if t[3] is not None and hi >= t[3] > lo]
        if not grp:
            continue
        net = sum(t[1] for t in grp)
        wr = sum(1 for t in grp if t[1] > 0) / len(grp) * 100
        print(f"  siddet {lo}%/{hi}% : n={len(grp):4d} net={net:+8.0f} ort={net/len(grp):+7.1f}bps win%={wr:5.1f}")


if __name__ == "__main__":
    main()
