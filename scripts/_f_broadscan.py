"""scripts/_f_broadscan.py — Strateji F'yi 500+ coin'den likit bir alt-kumede (top ~150 hacim)
GENIS taramaya cikarir. Ayni kanitlanmis yontem (_f_percoin.py: 4 BAGIMSIZ ceyrek, flip-only vs
SL300/500/700, N=40 gunluk momentum) — 9-coin elle-secim yerine sistematik tarama. Amac: ETH'ye
benzer ROBUST (SL cogu ceyrekte flip-only'i geciyor + net pozitif + DD makul) coinleri bulmak.
"""
import json
import time
import urllib.request

FEE = 8.0
CAND_N = 150
MIN_DAYS = 400  # en az ~400 gunluk gecmisi olmayan coin atlanir (4 ceyrek icin yeterli olsun)


def candidates():
    t = json.loads(urllib.request.urlopen("https://fapi.binance.com/fapi/v1/ticker/24hr", timeout=15).read())
    rows = [(x["symbol"], float(x.get("quoteVolume", 0) or 0)) for x in t
            if str(x.get("symbol", "")).endswith("USDT")]
    rows.sort(key=lambda z: -z[1])
    return [s for s, _ in rows[:CAND_N]]


def klines(sym, limit=1000):
    u = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1d&limit={limit}"
    for _ in range(2):
        try:
            r = json.loads(urllib.request.urlopen(u, timeout=15).read())
            return [(float(x[2]), float(x[3]), float(x[4])) for x in r]
        except Exception:
            time.sleep(0.5)
    return None


def run(bars, N=40, sl_bps=0, lo=0, hi=None):
    H = [b[0] for b in bars]; L = [b[1] for b in bars]; C = [b[2] for b in bars]
    n = len(C); hi = hi or n - 1
    pos = 0; ent = 0.0; blocked = 0
    daily = []
    for i in range(max(N, lo), hi):
        if C[i - N] <= 0:
            continue
        want = 1 if C[i] > C[i - N] else -1
        if blocked and want != blocked:
            blocked = 0
        target = 0 if blocked == want else want
        fee = FEE / 1e4 if target != pos else 0.0
        if target != 0 and target != pos:
            ent = C[i]
        if target == 0:
            if fee:
                daily.append(-fee)
            pos = 0
            continue
        if sl_bps:
            adv = ((ent - L[i + 1]) if target > 0 else (H[i + 1] - ent)) / ent * 1e4
            if adv >= sl_bps:
                daily.append(-sl_bps / 1e4 - fee)
                blocked = target; pos = 0
                continue
        daily.append(target * (C[i + 1] - C[i]) / C[i] - fee)
        pos = target
    return daily


def net(d):
    return sum(d) * 100 if d else 0.0


def maxdd(d):
    eq = pk = mdd = 0
    for x in d:
        eq += x; pk = max(pk, eq); mdd = min(mdd, eq - pk)
    return mdd * 100


def score_coin(sym, bars):
    n = len(bars)
    start = 40
    span = n - 1 - start
    if span < 200:
        return None
    q = span // 4
    quarters = []
    for k in range(4):
        lo = start + k * q
        hi = start + (k + 1) * q if k < 3 else n - 1
        base_d = run(bars, 40, 0, lo, hi)
        base = net(base_d)
        sl300_d = run(bars, 40, 300, lo, hi)
        sl300 = net(sl300_d)
        quarters.append((base, maxdd(base_d), sl300, maxdd(sl300_d)))
    sl_wins = sum(1 for b, _, s, _ in quarters if s > b)
    flip_all_pos = sum(1 for b, _, _, _ in quarters if b > 0)
    sl_all_pos = sum(1 for _, _, s, _ in quarters if s > 0)
    sl_net_total = sum(s for _, _, s, _ in quarters)
    flip_net_total = sum(b for b, _, _, _ in quarters)
    avg_dd_sl = sum(d for _, _, _, d in quarters) / 4
    return {
        "sym": sym, "quarters": quarters, "sl_wins": sl_wins, "flip_pos": flip_all_pos,
        "sl_pos": sl_all_pos, "sl_net": sl_net_total, "flip_net": flip_net_total, "avg_dd_sl": avg_dd_sl,
    }


def main():
    print("Aday evren cekiliyor (top %d hacim)..." % CAND_N)
    syms = candidates()
    print(f"{len(syms)} sembol, gunluk ~1000g veri cekiliyor (biraz surer)...\n")
    results = []
    for idx, sym in enumerate(syms):
        bars = klines(sym)
        if not bars or len(bars) < MIN_DAYS:
            continue
        r = score_coin(sym, bars)
        if r:
            results.append(r)
        if (idx + 1) % 30 == 0:
            print(f"  {idx+1}/{len(syms)} islendi, {len(results)} coin skorlandi")

    print(f"\nToplam skorlanan coin: {len(results)}\n")

    # ROBUST kriteri: SL300 >=3/4 ceyrek flip'i geciyor VE SL300 net toplam pozitif VE SL300 >=3/4 ceyrek pozitif
    robust = [r for r in results if r["sl_wins"] >= 3 and r["sl_net"] > 0 and r["sl_pos"] >= 3]
    robust.sort(key=lambda r: -r["sl_net"])

    print(f"=== ROBUST ADAYLAR (SL300 >=3/4 ceyrek flip'i geciyor + net pozitif + >=3/4 ceyrek pozitif): {len(robust)} coin ===\n")
    print(f"{'sembol':>12} | {'SL-kazanma':>10} | {'SL-net':>8} | {'flip-net':>8} | {'SL-pozitif-ceyrek':>17} | {'ort-DD':>7}")
    for r in robust:
        print(f"{r['sym']:>12} | {r['sl_wins']:>10}/4 | {r['sl_net']:>+8.0f} | {r['flip_net']:>+8.0f} | "
              f"{r['sl_pos']:>17}/4 | {r['avg_dd_sl']:>+7.0f}")

    print(f"\n=== Tum sonuclar SL-net'e gore siralanmis (ilk 25) ===")
    all_sorted = sorted(results, key=lambda r: -r["sl_net"])
    print(f"{'sembol':>12} | {'SL-kazanma':>10} | {'SL-net':>8} | {'flip-net':>8} | {'ort-DD':>7}")
    for r in all_sorted[:25]:
        print(f"{r['sym']:>12} | {r['sl_wins']:>10}/4 | {r['sl_net']:>+8.0f} | {r['flip_net']:>+8.0f} | {r['avg_dd_sl']:>+7.0f}")


if __name__ == "__main__":
    main()
