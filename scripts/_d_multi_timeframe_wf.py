"""scripts/_d_multi_timeframe_wf.py — MEVCUT D tasarimini (POC+SMA-align+ER-gate+ATR-SL+POC-revert)
3m/5m/15m(kiyas)/30m/1h zaman dilimlerinde AYNI bar-sayisi parametreleriyle (M=40,SMA=120,ER=20,
ATR=14,MAXHOLD=200,DEV=85bps) test eder. Memory'deki eski 1h/4h testleri (sr-timeframe-jul2026)
SMA-align/ER-gate EKLENMEDEN yapilmisti -> bu GUNCEL tasarimla taze tekrar.
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
DAYS = 120

TF_SEC = {"3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600}


def kl(sym, interval, sec, days=DAYS):
    out = {}
    end = int(time.time() * 1000)
    need = int(days * 86400 / sec)
    while len(out) < need:
        u = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval={interval}&limit=1500&endTime={end}"
        r = json.loads(urllib.request.urlopen(u, timeout=20).read())
        if not r:
            break
        for x in r:
            if int(x[6]) < int(time.time() * 1000):
                out[int(x[0])] = (int(x[0]) // 1000, float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5]))
        end = r[0][0] - 1
        if len(r) < 1500:
            break
        time.sleep(0.05)
    return [out[k] for k in sorted(out)]


def test_tf(label, interval, sec):
    bars = kl("ETHUSDT", interval, sec)
    n = len(bars)
    T = [b[0] for b in bars]; H = [b[2] for b in bars]; L = [b[3] for b in bars]; C = [b[4] for b in bars]; V = [b[5] for b in bars]

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

    trades = []
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
        er = er_at(i)
        sma_v = sum(C[i - SMA_LEN:i]) / SMA_LEN
        blocked = (er >= ER_GATE) or ((sig == "LONG" and px <= sma_v) or (sig == "SHORT" and px >= sma_v))
        if blocked:
            i += 1; continue
        entry = px
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
        trades.append((T[i], pnl, reason))
        i = jend + 1

    if not trades:
        print(f"{label:>4}: bar={n:6d}  islem YOK\n")
        return

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
    print(f"{label:>4}: bar={n:6d} n={len(trades):4d} | {row} | TOPLAM={tot:+8.0f}bps | "
          f"pencere={pos_w}/{n_win} | win%={wins/len(trades)*100:4.0f} | ort={tot/len(trades):+6.1f}bps/islem")


def main():
    print(f"MEVCUT-D tasarimi, {DAYS} gun, farkli zaman dilimlerinde (ayni bar-sayisi parametreleri: "
          f"M={M} SMA={SMA_LEN} ER={ER_WIN} ATR={ATR_N} MAXHOLD={MAXHOLD} DEV={DEV_T}bps)\n")
    for label, interval in (("3m", "3m"), ("5m", "5m"), ("15m", "15m"), ("30m", "30m"), ("1h", "1h")):
        test_tf(label, interval, TF_SEC[label])


if __name__ == "__main__":
    main()
