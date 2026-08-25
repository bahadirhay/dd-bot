"""scripts/_d_funding_confluence_wf.py — YENI HIPOTEZ: D (POC mean-reversion) + funding (pozisyonlanma,
bkz memory positioning-funding-jul2026: TEK bagimsiz-dogrulanmis ikinci edge) BIRLESIMI. Bu oturumda
denenen her sey (wick-reject, zaman-stop, entry-stop) fiyat-turevi oldugu icin basarisiz oldu — memory'nin
kendi dersi: "indikatorler ayni fiyattan turer, kombine yeni bilgi katmaz" (indicator-combos-jul2026).
Funding GERCEKTEN bagimsiz bir veri kaynagi (piyasa pozisyonlanmasi, fiyat serisinden turemez) -> D ile
birlesimi GENUINELY yeni bir hipotez. Test: D SHORT sinyali SADECE funding da yuksekse (kalabalik-long,
"asiri optimist" -> asagi donus daha guclu) alinsin; D LONG sadece funding dusuk/negatifse (kalabalik-short)
alinsin. Ayni sma_align/er_trend + ATR-SL/POC-revert+min-kar exit, 180-gun/4-pencere WF, ETHUSDT.
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


def funding_hist(sym, days=DAYS):
    """Binance funding-rate gecmisi (8s periyot). (ts_ms, rate) listesi, kronolojik."""
    out = []
    start = int((time.time() - days * 86400) * 1000)
    while True:
        u = f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={sym}&startTime={start}&limit=1000"
        r = json.loads(urllib.request.urlopen(u, timeout=20).read())
        if not r:
            break
        for x in r:
            out.append((int(x["fundingTime"]), float(x["fundingRate"])))
        if len(r) < 1000:
            break
        start = int(r[-1]["fundingTime"]) + 1
    out.sort()
    return out


def main():
    print(f"ETHUSDT {DAYS} gunluk 15m veri + funding gecmisi cekiliyor...")
    bars = kl("ETHUSDT", DAYS)
    fr = funding_hist("ETHUSDT", DAYS)
    print(f"{len(bars)} bar, {len(fr)} funding-kaydi cekildi\n")
    T = [b[0] for b in bars]; H = [b[2] for b in bars]; L = [b[3] for b in bars]; C = [b[4] for b in bars]; V = [b[5] for b in bars]
    n = len(C)
    fr_ts = [x[0] // 1000 for x in fr]
    fr_val = [x[1] for x in fr]

    # her bar zamanina en son GECERLI funding orani (lookahead yok, sadece GECMIS funding kullanilir)
    def funding_at(ts):
        # ikili arama yerine basit lineer (kucuk n, kabul edilebilir)
        val = fr_val[0] if fr_val else 0.0
        for k, fts in enumerate(fr_ts):
            if fts <= ts:
                val = fr_val[k]
            else:
                break
        return val

    all_funding_sorted = sorted(fr_val)

    def pctile(p):
        if not all_funding_sorted:
            return 0.0
        idx = min(int(len(all_funding_sorted) * p), len(all_funding_sorted) - 1)
        return all_funding_sorted[idx]

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

    def gen_trades(funding_gate_pct):
        """funding_gate_pct None ise filtre yok (baseline D). Degilse: SHORT icin funding >= ust-p yuzdelik,
        LONG icin funding <= alt-p yuzdelik sart."""
        hi_thr = pctile(1 - funding_gate_pct) if funding_gate_pct else None
        lo_thr = pctile(funding_gate_pct) if funding_gate_pct else None
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
            er = er_at(i)
            sma_v = sum(C[i - SMA_LEN:i]) / SMA_LEN
            blocked = (er >= ER_GATE) or ((sig == "LONG" and px <= sma_v) or (sig == "SHORT" and px >= sma_v))
            if blocked:
                i += 1; continue
            if funding_gate_pct:
                f = funding_at(T[i])
                if sig == "SHORT" and f < hi_thr:
                    i += 1; continue
                if sig == "LONG" and f > lo_thr:
                    i += 1; continue
            pnl, reason, jend = sim_exit(i, px, sig)
            out.append((T[i], pnl, reason))
            i = jend + 1
        return out

    variants = [("MEVCUT-D (funding-filtresiz)", None),
                ("D+funding ust/alt-%50 (gevsek)", 0.50),
                ("D+funding ust/alt-%30 (orta)", 0.30),
                ("D+funding ust/alt-%15 (siki)", 0.15)]

    for label, gate in variants:
        trades = gen_trades(gate)
        if not trades:
            print(f"{label}: islem yok (n=0)\n"); continue
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
        print(f"{label}")
        print(f"  n={len(trades)} | {row} | TOPLAM={tot:+.0f}bps | pencere={pos_w}/{n_win} | "
              f"win%={wins/len(trades)*100:.0f} | ort={tot/len(trades):+.0f}bps/islem\n")


if __name__ == "__main__":
    main()
