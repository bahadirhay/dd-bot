"""scripts/_d_trendfollow_wf.py — kullanicinin onerisi: D'nin ER/SMA-align ile "trend var, fade etme"
diye BLOKLADIGI anlarda, onun yerine trend YONUNDE (momentum) islem ac. D SHORT-blok -> LONG ac (yukari
trend), D LONG-blok -> SHORT ac (asagi trend). Cikis: trend-bitis sinyali (ER tekrar esigin altina
duser VEYA SMA-hizasi bozulur) + ATR-SL koruma + maxhold backstop. 180-gun/3-coin(ETH/BTC/SOL)/4-pencere
WF, MEVCUT-D'ye (fade) karsi kiyaslanir. Memory'de eski/farkli-tanimli bir "ters-D" testi -6865 vermisti
(regime-arch-jul2026) — bu, ER/SMA-blok anina OZEL, taze ve tam metodolojiyle tekrar.
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

COINS = ["ETHUSDT", "BTCUSDT", "SOLUSDT"]


def kl(sym, interval="15m", sec=900, days=DAYS):
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


def test_coin(sym, mode):
    """mode='fade' (mevcut D) veya 'trendfollow' (blok-anlarinda ters yon)."""
    bars = kl(sym)
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
        fade_sig = "LONG" if dev <= -DEV_T else ("SHORT" if dev >= DEV_T else None)
        if not fade_sig:
            i += 1; continue
        er = er_at(i)
        sma_v = sum(C[i - SMA_LEN:i]) / SMA_LEN
        blocked = (er >= ER_GATE) or ((fade_sig == "LONG" and px <= sma_v) or (fade_sig == "SHORT" and px >= sma_v))

        if mode == "fade":
            if blocked:
                i += 1; continue
            sig = fade_sig
        else:  # trendfollow
            if not blocked:
                i += 1; continue
            sig = "SHORT" if fade_sig == "LONG" else "LONG"  # ters yon = trend yonu

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
            if mode == "fade":
                pocj = poc_at(j)
                if pocj > 0:
                    devj = (C[j] - pocj) / pocj * 1e4
                    reverted = (devj >= 0) if sig == "LONG" else (devj <= 0)
                    if reverted and cur >= MINPROF:
                        exit_px = C[j]; reason = "poc_revert"; break
            else:  # trendfollow: trend-bitis = ER esigin altina dustu VEYA SMA-hizasi bozuldu
                erj = er_at(j)
                smaj = sum(C[j - SMA_LEN:j]) / SMA_LEN
                trend_over = (erj < ER_GATE) or ((sig == "LONG" and C[j] < smaj) or (sig == "SHORT" and C[j] > smaj))
                if trend_over and cur >= MINPROF:
                    exit_px = C[j]; reason = "trend_over"; break
        if exit_px is None:
            exit_px = C[jend]; reason = "maxhold"
        pnl = ((exit_px - entry) if sig == "LONG" else (entry - exit_px)) / entry * 1e4 - FEE
        trades.append((T[i], pnl, reason))
        i = jend + 1

    if not trades:
        print(f"  {sym:>9} [{mode:>11}]: islem YOK")
        return None

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
    print(f"  {sym:>9} [{mode:>11}]: n={len(trades):4d} | {row} | TOPLAM={tot:+8.0f}bps | "
          f"pencere={pos_w}/{n_win} | win%={wins/len(trades)*100:4.0f} | ort={tot/len(trades):+6.1f}bps/islem")
    return tot


def main():
    print(f"D-FADE vs TREND-FOLLOW (blok-anlarinda ters yon), {DAYS} gun, 3 coin, 4-pencere WF\n")
    for sym in COINS:
        test_coin(sym, "fade")
        test_coin(sym, "trendfollow")
        print()


if __name__ == "__main__":
    main()
