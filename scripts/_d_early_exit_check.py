"""scripts/_d_early_exit_check.py — kullanicinin iddiasi: D karli pozisyonlardan ERKEN cikiyor.
Gercek d_poc_revert kapanislarini al, kapanis SONRASI 4 saat (16 bar) icinde fiyat lehte devam
etmis mi (devam etmis olsaydi ekstra ne kazanilirdi) kontrol et.
"""
import bisect
import json
import sqlite3
import time
import urllib.request

c = sqlite3.connect("file:data/bot.db?mode=ro", uri=True)
rows = c.execute(
    "SELECT direction, entry_price, exit_price, pnl_pct, close_ts FROM trades "
    "WHERE close_reason='d_poc_revert' AND close_ts > ? ORDER BY close_ts ASC",
    (time.time() - 50 * 86400,)).fetchall()
print("islem sayisi:", len(rows))


def kl(sym, days=50):
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
                out[int(x[0])] = (int(x[0]) // 1000, float(x[2]), float(x[3]), float(x[4]))
        end = r[0][0] - 1
        if len(r) < 1500:
            break
    return [out[k] for k in sorted(out)]


bars = kl("ETHUSDT", 50)
print("bar sayisi:", len(bars))
T = [b[0] for b in bars]; H = [b[1] for b in bars]; L = [b[2] for b in bars]; C = [b[3] for b in bars]

N_AFTER = 16  # 4 saat, maxhold ile ayni

results = []
for direction, entry, exitp, pnl_pct, close_ts in rows:
    idx = bisect.bisect_left(T, close_ts)
    if idx >= len(T) or idx + N_AFTER >= len(T):
        continue
    realized_bps = pnl_pct * 100
    if direction == "LONG":
        best_after = max(H[idx:idx + N_AFTER + 1])
        cont_bps = (best_after - exitp) / exitp * 1e4
        end_bps = (C[idx + N_AFTER] - exitp) / exitp * 1e4
    else:
        best_after = min(L[idx:idx + N_AFTER + 1])
        cont_bps = (exitp - best_after) / exitp * 1e4
        end_bps = (exitp - C[idx + N_AFTER]) / exitp * 1e4
    results.append((realized_bps, cont_bps, end_bps))

print(f"karsilastirma yapilan islem: {len(results)}\n")
print(f"{'gercek-kar':>10} {'sonra-en-iyi(4s)':>18} {'sonra-4s-kapanis':>18}")
tot_real = 0.0
tot_cont_end = 0.0
better = 0
for realized, cont, end in results:
    tot_real += realized
    tot_cont_end += end
    if end > 0:
        better += 1
    print(f"{realized:>10.0f} {cont:>18.0f} {end:>18.0f}")

print()
print(f"Toplam gercek kar: {tot_real:+.0f}bps")
print(f"Eger 4 saat sonraki KAPANIS fiyatina kadar tutulsaydi (EKSTRA/kayip): {tot_cont_end:+.0f}bps")
n = len(results) or 1
print(f"Kapanis-sonrasi 4 saatte LEHTE devam eden (>0) islem orani: {better}/{n} = {better/n*100:.0f}%")
