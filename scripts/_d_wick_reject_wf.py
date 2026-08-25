"""scripts/_d_wick_reject_wf.py — D icin ALTERNATIF tetikleyici: mevcut D "KAPANISTA esigin
otesinde kal" ister; bu script "bar ICINDE esigi gec ama KAPANISTA geri donsun (reddedis)" tetikleyicisini
test eder. Kullanicinin onerisi: SHORT icin High esigi gecsin+Close esigin altina dussun (reddedildi ->
asagi devam beklenir); LONG icin Low esigi gecsin+Close esigin ustune ciksin. Ayni sma_align/er_trend
filtreleri + ayni exit (ATR-SL/POC-revert+min-kar/maxhold) ile MEVCUT-D'ye karsi 4-pencereli WF kiyasi.
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
    T = [b[0] for b in bars]; O = [b[1] for b in bars]; H = [b[2] for b in bars]; L = [b[3] for b in bars]
    C = [b[4] for b in bars]; V = [b[5] for b in bars]
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

    def er_at(i):
        net = abs(C[i] - C[i - ER_WIN])
        path = sum(abs(C[i - k] - C[i - k - 1]) for k in range(1, ER_WIN + 1))
        return net / path if path > 0 else 0.0

    def sim_exit(i, entry, sig):
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
        if exit_px is None:
            exit_px = C[jend]; reason = "maxhold"
        pnl = ((exit_px - entry) if sig == "LONG" else (entry - exit_px)) / entry * 1e4 - FEE
        return pnl, reason, jend

    def gen_trades(mode):
        """mode='baseline' (mevcut D: kapanis esigi gecti) veya 'wick' (bar-ici gecti+kapanis geri-dondu)."""
        out = []
        i = max(M, SMA_LEN, ER_WIN + 1, ATR_N + 1)
        while i < n:
            poc = poc_at(i)
            if poc <= 0:
                i += 1; continue
            close_dev = (C[i] - poc) / poc * 1e4
            high_dev = (H[i] - poc) / poc * 1e4
            low_dev = (L[i] - poc) / poc * 1e4

            sig = None
            if mode == "baseline":
                if close_dev <= -DEV_T:
                    sig = "LONG"
                elif close_dev >= DEV_T:
                    sig = "SHORT"
            else:  # wick-reject
                if low_dev <= -DEV_T and close_dev > -DEV_T:
                    sig = "LONG"
                elif high_dev >= DEV_T and close_dev < DEV_T:
                    sig = "SHORT"

            if not sig:
                i += 1; continue
            er = er_at(i)
            sma_v = sum(C[i - SMA_LEN:i]) / SMA_LEN
            px = C[i]
            blocked = (er >= ER_GATE) or ((sig == "LONG" and px <= sma_v) or (sig == "SHORT" and px >= sma_v))
            if blocked:
                i += 1; continue
            pnl, reason, jend = sim_exit(i, px, sig)
            out.append((T[i], pnl, reason))
            i = jend + 1
        return out

    for mode in ("baseline", "wick"):
        trades = gen_trades(mode)
        if not trades:
            print(f"{mode}: islem yok"); continue
        ts_min, ts_max = trades[0][0], trades[-1][0]
        span = ts_max - ts_min
        n_win = 4
        edges = [ts_min + span * k / n_win for k in range(n_win + 1)]

        def widx(ts):
            for k in range(n_win):
                if edges[k] <= ts <= edges[k + 1] or (k == n_win - 1 and ts > edges[k + 1]):
                    return k
            return n_win - 1

        win_stats = [[0, 0.0] for _ in range(n_win)]
        tot = 0.0; wins = 0
        for ts, pnl, reason in trades:
            k = widx(ts)
            win_stats[k][0] += 1; win_stats[k][1] += pnl
            tot += pnl
            if pnl > 0: wins += 1
        pos_w = sum(1 for cnt, net in win_stats if net > 0)
        row = " | ".join(f"({cnt:3d},{net:+7.0f})" for cnt, net in win_stats)
        label = "MEVCUT-D (kapanis-esigi)" if mode == "baseline" else "WICK-REJECT (bar-ici+geri-donus)"
        print(f"{label}")
        print(f"  n={len(trades)} | {row} | TOPLAM={tot:+.0f}bps | pencere={pos_w}/{n_win} | win%={wins/len(trades)*100:.0f} | ort={tot/len(trades):+.0f}bps/islem\n")


if __name__ == "__main__":
    main()
