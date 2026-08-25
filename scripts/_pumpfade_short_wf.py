"""scripts/_pumpfade_short_wf.py — dump-fade LONG ile SIMETRIK pump-fade SHORT testi: gunluk >=+12.5%
pump eden coin -> ertesi gun open'in ~%3 USTUNE sell-limit (SHORT) -> gun-sonu kapat (+stop taraması).
Amac: memory'deki "pump-fade SHORT calismiyor" iddiasini AYNI 246-sembol/~520-gun/WF metoduyla
GERCEKTEN dogrula (kullanici sordu: sadece eski notu tekrarlamak yerine test et).
"""
from __future__ import annotations

import json
import os
import time
import urllib.request

CAND_N = 300
PUMP_PCT = 12.5          # gunluk >=+bu% -> pump
PUMP_CAP = 60.0           # >=+bu% ise ATLA (asiri squeeze/anomali, dump'taki -30 cap'in esdegeri genis tutuldu)
OFFSET_BPS = 300.0
MAKER_FEE = 10.0
MIN_QVOL = 25e6
MIN_DAYS = 60
DAYS = 520

CACHE = os.path.join(os.path.dirname(__file__), "_pumpfade_trades_cache.json")

# genis+ince tarama: cok siki stoplardan (erken kes) gevsege kadar
STOPS = [None, -3, -5, -8, -10, -12, -15, -20, -25, -30, -40, -50]


def _get(u: str, timeout=20):
    return json.loads(urllib.request.urlopen(u, timeout=timeout).read())


def candidates() -> list[str]:
    t = _get("https://fapi.binance.com/fapi/v1/ticker/24hr")
    rows = [(x["symbol"], float(x.get("quoteVolume", 0) or 0)) for x in t
            if str(x.get("symbol", "")).endswith("USDT")]
    rows.sort(key=lambda z: -z[1])
    return [s for s, _ in rows[:CAND_N]]


def daily(sym: str, limit: int = DAYS + 40):
    try:
        r = _get(f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1d&limit={limit}")
        return [(int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[7])) for x in r]
    except Exception:
        return None


def gen_trades(bars: list[tuple]) -> list[tuple]:
    """PUMP-FADE SHORT: gunluk pump>=+12.5% -> ertesi gun open'in %3 USTUNE sell-limit.
    Donen: (day_ts, pump_pct, limit_px(short-giris), high_px(gun-en-yuksek, MAE kaynagi), close_px, filled)"""
    out = []
    n = len(bars)
    for i in range(MIN_DAYS, n - 1):
        vols = [bars[j][5] for j in range(max(0, i - 30), i) if bars[j][5] > 0]
        if not vols or (sum(vols) / len(vols)) < MIN_QVOL:
            continue
        o_d, c_d = bars[i][1], bars[i][4]
        if o_d <= 0:
            continue
        pump = (c_d - o_d) / o_d * 100
        if pump < PUMP_PCT or pump >= PUMP_CAP:
            continue
        D1 = bars[i + 1]
        o1, h1, c1 = D1[1], D1[2], D1[4]
        if o1 <= 0:
            continue
        lim = o1 * (1 + OFFSET_BPS / 1e4)     # USTUNE sell-limit (SHORT giris)
        filled = h1 >= lim                      # gun-yuksek limit'i gecti mi (SHORT dolar)
        out.append((D1[0], pump, lim, h1, c1, filled))
    return out


def apply_stop(lim: float, high: float, close: float, stop_pct) -> float:
    """SHORT icin getiri: giris - cikis (fiyat DUSERSE kazanc). stop_pct ALEYHTE (fiyat YUKARI) esik,
    NEGATIF sayi olarak verilir (orn -15 = fiyat lehimize -%15 degil, giristen %15 aleyhimize gitti demek).
    MAE(SHORT) = (lim - high)/lim*100 (fiyat yukselirse NEGATIF -> zarar)."""
    mae = (lim - high) / lim * 100.0            # fiyat yukselirse negatif buyur (zarar)
    cret = (lim - close) / lim * 100.0            # SHORT getirisi: giris ustunde kapanirsa zarar
    if stop_pct is not None and mae <= stop_pct:
        return stop_pct
    return cret


def main() -> None:
    if os.path.exists(CACHE):
        print(f"Onbellekten yukleniyor: {CACHE}")
        with open(CACHE, "r") as f:
            all_trades = [tuple(x) for x in json.load(f)]
        print(f"{len(all_trades)} dolu islem (onbellek)\n")
    else:
        print("Aday evren cekiliyor...")
        syms = candidates()
        print(f"{len(syms)} sembol, gunluk {DAYS}g veri cekiliyor (biraz surer)...")
        all_trades = []
        ok = 0
        for idx, sym in enumerate(syms):
            bars = daily(sym)
            if not bars or len(bars) < MIN_DAYS + 30:
                continue
            trs = gen_trades(bars)
            for (ts, pump, lim, high, close, filled) in trs:
                if filled:
                    all_trades.append((ts, sym, pump, lim, high, close))
            ok += 1
            if (idx + 1) % 50 == 0:
                print(f"  {idx+1}/{len(syms)} sembol islendi, su ana kadar {len(all_trades)} dolu islem")
            time.sleep(0.05)
        print(f"\nTOPLAM: {ok} sembol basarili, {len(all_trades)} dolu (FILLED) PUMP-FADE SHORT islemi\n")
        with open(CACHE, "w") as f:
            json.dump(all_trades, f)
        print(f"Onbellege yazildi: {CACHE}\n")
    if not all_trades:
        print("Islem yok, cikiliyor.")
        return

    # MAE dagilimi (stopsuz, aleyhte-hareket) — hangi esiklerin anlamli oldugunu gorelim
    maes = sorted(((lim - high) / lim * 100.0) for ts, sym, pump, lim, high, close in all_trades)
    n = len(maes)
    def pct(p): return maes[min(int(n * p), n - 1)]
    print("MAE (aleyhte-hareket) dagilimi (negatif=zarara gitti):")
    print(f"  p10={pct(0.10):.1f}%  p25={pct(0.25):.1f}%  p50(medyan)={pct(0.50):.1f}%  "
          f"p75={pct(0.75):.1f}%  p90={pct(0.90):.1f}%  en-kotu={maes[0]:.1f}%  en-iyi={maes[-1]:.1f}%\n")

    all_trades.sort(key=lambda z: z[0])
    ts_min, ts_max = all_trades[0][0], all_trades[-1][0]
    span = ts_max - ts_min
    n_win = 4
    edges = [ts_min + span * k / n_win for k in range(n_win + 1)]

    def window_idx(ts):
        for k in range(n_win):
            if edges[k] <= ts <= edges[k + 1] or (k == n_win - 1 and ts > edges[k + 1]):
                return k
        return n_win - 1

    print(f"{'stop':>6} | " + " | ".join(f"Q{k+1}(n,net)" for k in range(n_win)) + " |   TUM-net | win-pencere | win%  | en-kotu")
    best_stop, best_net = None, float("-inf")
    for stop in STOPS:
        win_stats = [[0, 0.0] for _ in range(n_win)]
        total_net = 0.0
        worst = 0.0
        wins = 0
        for ts, sym, pump, lim, high, close in all_trades:
            ret = apply_stop(lim, high, close, stop)
            bps = ret * 100 - MAKER_FEE
            worst = min(worst, bps)
            total_net += bps
            if bps > 0:
                wins += 1
            k = window_idx(ts)
            win_stats[k][0] += 1
            win_stats[k][1] += bps
        pos_windows = sum(1 for cnt, net in win_stats if net > 0)
        wr = wins / len(all_trades) * 100
        row = " | ".join(f"({cnt:4d},{net:+8.0f})" for cnt, net in win_stats)
        label = "stopsuz" if stop is None else f"{stop:.0f}%"
        print(f"{label:>6} | {row} | {total_net:+9.0f} |   {pos_windows}/{n_win}       | {wr:5.1f} | {worst:+.0f}bps")
        if total_net > best_net:
            best_net, best_stop = total_net, label

    print(f"\nEN IYI (yine de yorumla): stop={best_stop}, net={best_net:+.0f}bps "
          f"({len(all_trades)} islem, ortalama={best_net/len(all_trades):+.1f}bps/islem)")


if __name__ == "__main__":
    main()
