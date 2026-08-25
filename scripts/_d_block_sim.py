"""scripts/_d_block_sim.py — son 8 gunde sma_align/er_trend ile BLOKLANMIS D sinyallerini,
bloklanmasaydi ne olurdu diye ayni canli exit mantigiyla (ATR-SL, POC-revert+min-kar, maxhold)
simule eder. Kullanici sordu: bloklamak mantikli mi, yoksa firsat mi kaciriliyor?
"""
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
TIME_STOP_BARS = 96     # 24 saat: bu sureye kadar donmediyse ve hala zararday-sa erken cik
TIME_STOP_MAX_LOSS = -50.0  # bu esikten daha kotudeyse zaman-stop tetiklenir (kucuk zarar ise bekle)


def kl(sym, days=8):
    limit = days * 96 + 50
    r = json.loads(urllib.request.urlopen(
        f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=15m&limit={limit}", timeout=20).read())
    return [(int(x[0]) // 1000, float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5])) for x in r]


def main():
    bars = kl("ETHUSDT", 8)
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

    print(f"GERCEKCI (tek-pozisyon, cakismasiz) simulasyon — "
          f"SL={ATR_FLOOR:.0f}-{ATR_CEIL:.0f}bps ATR, maxhold={MAXHOLD} bar, "
          f"min-kar={MINPROF:.0f}bps, fee={FEE:.0f}bps\n")

    results = []
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
        blocked = ""
        if er >= ER_GATE:
            blocked = "er_trend"
        elif (sig == "LONG" and px <= sma_v) or (sig == "SHORT" and px >= sma_v):
            blocked = "sma_align"
        if not blocked:
            i += 1; continue

        sma_dist = (px - sma_v) / sma_v * 1e4
        # BLOKLANMIS sinyal -> "ya bloklanmasaydi" diye GERCEKTEN girip cikana kadar simule et,
        # sonra bir sonraki aramaya SADECE bu islem kapandiktan sonra devam et (tek-pozisyon).
        entry = px
        sl_bps = atr_sl_bps(i, entry)
        sl = entry * (1 - sl_bps / 1e4) if sig == "LONG" else entry * (1 + sl_bps / 1e4)
        exit_px = None
        reason = "maxhold"
        jend = i
        exit_px_ts = None   # ZAMAN-STOP'LU varyant (paralel hesap, ayni giris/SL/revert kurallari + ekstra)
        reason_ts = "maxhold"
        jend_ts = i
        ts_done = False
        for j in range(i + 1, min(i + MAXHOLD + 1, n)):
            jend = j
            cur = ((C[j] - entry) if sig == "LONG" else (entry - C[j])) / entry * 1e4
            if not ts_done:
                jend_ts = j
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
            if not ts_done and (j - i) >= TIME_STOP_BARS and cur <= TIME_STOP_MAX_LOSS:
                exit_px_ts = C[j]; reason_ts = "time_stop"; ts_done = True
        if exit_px is None:
            exit_px = C[jend]; reason = "maxhold"
        if not ts_done:
            exit_px_ts, reason_ts, jend_ts = exit_px, reason, jend
        pnl = ((exit_px - entry) if sig == "LONG" else (entry - exit_px)) / entry * 1e4 - FEE
        pnl_ts = ((exit_px_ts - entry) if sig == "LONG" else (entry - exit_px_ts)) / entry * 1e4 - FEE
        hold_bars = jend - i
        results.append((T[i], sig, blocked, entry, exit_px, reason, pnl, hold_bars, er, sma_dist, dev,
                         exit_px_ts, reason_ts, pnl_ts, jend_ts - i))
        i = jend + 1   # bu islem kapanmadan yeni sinyal aranmiyor (GERCEKCI tek-pozisyon)

    tot = 0.0; tot_ts = 0.0
    wins = 0; wins_ts = 0
    for ts, sig, blocked, entry, exitp, reason, pnl, hb, er, sma_dist, dev, exitp_ts, reason_ts, pnl_ts, hb_ts in results:
        t = time.strftime("%m-%d %H:%M", time.localtime(ts))
        print(f"{t} {sig:5s} blok={blocked:10s} giris={entry:8.2f}  "
              f"[NORMAL] cikis={exitp:8.2f} sebep={reason:10s} pnl={pnl:+7.0f}bps hold={hb*15}dk  |  "
              f"[ZAMAN-STOP] cikis={exitp_ts:8.2f} sebep={reason_ts:10s} pnl={pnl_ts:+7.0f}bps hold={hb_ts*15}dk")
        tot += pnl; tot_ts += pnl_ts
        if pnl > 0:
            wins += 1
        if pnl_ts > 0:
            wins_ts += 1
    n_r = len(results) or 1
    print(f"\nNORMAL     : TOPLAM={tot:+.0f}bps, ort={tot/n_r:+.0f}bps/olay, win%={wins/n_r*100:.0f}, n={len(results)}")
    print(f"ZAMAN-STOP : TOPLAM={tot_ts:+.0f}bps, ort={tot_ts/n_r:+.0f}bps/olay, win%={wins_ts/n_r*100:.0f}, n={len(results)}  "
          f"(24s icinde donmedi + hala <-{-TIME_STOP_MAX_LOSS:.0f}bps ise erken cik)")


if __name__ == "__main__":
    main()
