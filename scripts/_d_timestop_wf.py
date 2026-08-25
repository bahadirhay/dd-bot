"""scripts/_d_timestop_wf.py — D'nin GERCEK aldigi islemler (bloklanmayan, fiilen acilan) uzerinde
zaman-stop (24s icinde POC-donusu olmadi + hala zararda ise erken cik) fikrini BUYUK veri + WF ile
test eder. Amac: 7-orneklik kucuk testte (-337->-237) gorulen iyilesmenin gercek D islem gecmisinde
de tutarli olup olmadigini dogrulamak (kullanicinin istegi: -337 tipi kaybi azaltacak bir sey yap,
ama kucuk-orneklem-uydurma tuzagina dusme).
"""
from __future__ import annotations

import json
import time
import urllib.request

M = 40
SMA_LEN = 120
DEV_T = 85.0
ER_WIN = 20
ER_GATE = 0.5
ATR_N = 14
ATR_MULT = 7.5
ATR_FLOOR = 300.0
ATR_CEIL = 600.0
MAXHOLD = 200
MINPROF = 12.0
FEE = 16.0
DAYS = 180

TIME_STOP_VARIANTS = [None, 48, 96, 144]   # bar sayisi (15m*N): None=kapali, 48=12s, 96=24s, 144=36s
TIME_STOP_MAX_LOSS = -50.0


def kl(sym, days=DAYS):
    out = {}
    end = int(time.time() * 1000)
    need = days * 96
    while len(out) < need:
        u = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=15m&limit=1500&endTime={end}"
        r = json.loads(urllib.request.urlopen(u, timeout=20).read())
        if not r:
            break
        for x in r:
            if int(x[6]) < int(time.time() * 1000):
                out[int(x[0])] = (int(x[0]) // 1000, float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5]))
        end = r[0][0] - 1
        if len(r) < 1500:
            break
    return [out[k] for k in sorted(out)]


def main():
    print(f"ETHUSDT {DAYS} gunluk 15m veri cekiliyor...")
    bars = kl("ETHUSDT", DAYS)
    print(f"{len(bars)} bar cekildi\n")
    T = [b[0] for b in bars]; H = [b[2] for b in bars]; L = [b[3] for b in bars]; C = [b[4] for b in bars]; V = [b[5] for b in bars]
    n = len(C)

    def poc_at(i):
        num = den = 0.0
        for j in range(i - M, i):
            w = V[j] if V[j] > 0 else 1.0
            num += C[j] * w; den += w
        return num / den if den > 0 else 0.0

    def atr_sl_bps(i, px):
        if i < ATR_N + 1:
            return 300.0
        trs = []
        for k in range(i - ATR_N + 1, i + 1):
            trs.append(max(H[k] - L[k], abs(H[k] - C[k - 1]), abs(L[k] - C[k - 1])))
        atr_bps = (sum(trs) / ATR_N) / px * 1e4
        return max(ATR_FLOOR, min(ATR_CEIL, ATR_MULT * atr_bps))

    def gen_real_trades():
        """D'nin GERCEKTE (bloklanmadan) actigi islemler — sma_align + er_trend GECEN sinyaller."""
        out = []
        i = max(M, SMA_LEN, ER_WIN + 1, ATR_N + 1)
        while i < n:
            poc = poc_at(i)
            if poc <= 0:
                i += 1; continue
            px = C[i]
            dev = (px - poc) / poc * 1e4
            sig = "LONG" if dev <= -DEV_T else ("SHORT" if dev >= DEV_T else None)
            if not sig:
                i += 1; continue
            net = abs(C[i] - C[i - ER_WIN])
            path = sum(abs(C[i - k] - C[i - k - 1]) for k in range(1, ER_WIN + 1))
            er = net / path if path > 0 else 0
            sma_v = sum(C[i - SMA_LEN:i]) / SMA_LEN
            blocked = (er >= ER_GATE) or ((sig == "LONG" and px <= sma_v) or (sig == "SHORT" and px >= sma_v))
            if blocked:
                i += 1; continue
            out.append((i, T[i], px, sig))
            # bu sinyal GERCEK islem sayilir -> exit simule edip bir sonrakini exit sonrasindan ara
            entry = px
            sl_bps = atr_sl_bps(i, entry)
            sl = entry * (1 - sl_bps / 1e4) if sig == "LONG" else entry * (1 + sl_bps / 1e4)
            jend = i
            for j in range(i + 1, min(i + MAXHOLD + 1, n)):
                jend = j
                cur = ((C[j] - entry) if sig == "LONG" else (entry - C[j])) / entry * 1e4
                if sig == "LONG" and L[j] <= sl:
                    break
                if sig == "SHORT" and H[j] >= sl:
                    break
                pocj = poc_at(j)
                if pocj > 0:
                    devj = (C[j] - pocj) / pocj * 1e4
                    reverted = (devj >= 0) if sig == "LONG" else (devj <= 0)
                    if reverted and cur >= MINPROF:
                        break
            i = jend + 1
        return out

    trades = gen_real_trades()
    print(f"D'nin GERCEK actigi (bloklanmamis) islem sayisi: {len(trades)}\n")

    def sim_variant(time_stop_bars):
        results = []
        for (i, ts, entry, sig) in trades:
            sl_bps = atr_sl_bps(i, entry)
            sl = entry * (1 - sl_bps / 1e4) if sig == "LONG" else entry * (1 + sl_bps / 1e4)
            exit_px = None; reason = "maxhold"; jend = i
            for j in range(i + 1, min(i + MAXHOLD + 1, n)):
                jend = j
                cur = ((C[j] - entry) if sig == "LONG" else (entry - C[j])) / entry * 1e4
                if sig == "LONG" and L[j] <= sl:
                    exit_px = sl; reason = "sl"; break
                if sig == "SHORT" and H[j] >= sl:
                    exit_px = sl; reason = "sl"; break
                pocj = poc_at(j)
                if pocj > 0:
                    devj = (C[j] - pocj) / pocj * 1e4
                    reverted = (devj >= 0) if sig == "LONG" else (devj <= 0)
                    if reverted and cur >= MINPROF:
                        exit_px = C[j]; reason = "poc_revert"; break
                if time_stop_bars and (j - i) >= time_stop_bars and cur <= TIME_STOP_MAX_LOSS:
                    exit_px = C[j]; reason = "time_stop"; break
            if exit_px is None:
                exit_px = C[jend]; reason = "maxhold"
            pnl = ((exit_px - entry) if sig == "LONG" else (entry - exit_px)) / entry * 1e4 - FEE
            results.append((ts, pnl, reason))
        return results

    ts_min, ts_max = trades[0][1], trades[-1][1]
    span = ts_max - ts_min
    n_win = 4
    edges = [ts_min + span * k / n_win for k in range(n_win + 1)]

    def widx(ts):
        for k in range(n_win):
            if edges[k] <= ts <= edges[k + 1] or (k == n_win - 1 and ts > edges[k + 1]):
                return k
        return n_win - 1

    print(f"{'varyant':>10} | " + " | ".join(f"Q{k+1}" for k in range(n_win)) + " |    TUM-net | win-pencere | win% | en-kotu-tek-islem")
    for variant in TIME_STOP_VARIANTS:
        res = sim_variant(variant)
        win_stats = [[0, 0.0] for _ in range(n_win)]
        tot = 0.0; wins = 0; worst = 0.0
        for ts, pnl, reason in res:
            k = widx(ts)
            win_stats[k][0] += 1; win_stats[k][1] += pnl
            tot += pnl
            if pnl > 0: wins += 1
            worst = min(worst, pnl)
        pos_w = sum(1 for cnt, net in win_stats if net > 0)
        label = "kapali" if variant is None else f"{variant*15//60}s"
        row = " | ".join(f"{net:+7.0f}" for cnt, net in win_stats)
        print(f"{label:>10} | {row} | {tot:+10.0f} |   {pos_w}/{n_win}       | {wins/len(res)*100:4.0f} | {worst:+.0f}bps")


if __name__ == "__main__":
    main()
