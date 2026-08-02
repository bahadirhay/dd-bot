"""F (gunluk TSM) vs TERS-F. Kullanici: F dibi shortladi, orada LONG mantikli degil mi?
= ters-F. Ama F trendi yakalar (2400->1594 dususu short'la kazandi); tersi tum trendde kaybeder,
sadece donum noktasinda kazanir. Test: F (want=sign(mom)) vs REVERSE (want=-sign(mom)),
5 coin x 4 ceyrek WF. Ters-F pozitif/iyiyse kullanici hakli; F'den kotuyse F'nin yonu dogru."""
import urllib.request, json, time

FEE = 8.0; N = 40

def klines(sym, limit=1000):
    u = 'https://fapi.binance.com/fapi/v1/klines?symbol=%sUSDT&interval=1d&limit=%d' % (sym, limit)
    for _ in range(3):
        try: return [float(x[4]) for x in json.loads(urllib.request.urlopen(u, timeout=15).read())]
        except Exception: time.sleep(1)
    return None

def run(C, lo, hi, reverse):
    pos = 0; ent = 0.0; daily = []
    for i in range(max(N, lo), hi):
        if C[i - N] <= 0: continue
        want = 1 if C[i] > C[i - N] else -1
        if reverse: want = -want
        fee = FEE / 1e4 if want != pos else 0.0
        if want != pos: ent = C[i]
        daily.append(want * (C[i + 1] - C[i]) / C[i] - fee)
        pos = want
    return daily

def net(d): return sum(d) * 100 if d else 0.0

if __name__ == "__main__":
    print("=== F (gunluk TSM N40) vs TERS-F | 5 coin x 4 ceyrek | net%% ===\n")
    print("  coin | ceyrek |     F      |   TERS-F")
    tot_f = tot_r = 0
    for sym in ('ETH', 'BTC', 'SOL', 'BNB', 'XRP'):
        C = klines(sym)
        if not C: print("  %s veri yok" % sym); continue
        n = len(C); start = N; span = n - 1 - start; q = span // 4
        cf = cr = 0
        for k in range(4):
            lo = start + k * q; hi = start + (k + 1) * q if k < 3 else n - 1
            f = net(run(C, lo, hi, False)); r = net(run(C, lo, hi, True))
            cf += f; cr += r
            print("  %-4s |  Q%d    | %+8.0f  | %+8.0f" % (sym if k == 0 else '', k + 1, f, r))
        print("  %-4s | TOPLAM | %+8.0f  | %+8.0f" % (sym, cf, cr))
        print("       |        |")
        tot_f += cf; tot_r += cr
    print("=== GENEL TOPLAM ===")
    print("  F      : %+.0f%%" % tot_f)
    print("  TERS-F : %+.0f%%" % tot_r)
    print("\n(F belirgin > TERS-F ise -> F'nin yonu dogru, dipteki short trend-yakalamanin bedeli.")
    print(" TERS-F daha iyiyse -> kullanici hakli, yonu cevir. Ama fee + trend-kaybi bekle.)")
