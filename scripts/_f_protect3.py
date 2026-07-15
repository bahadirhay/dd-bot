"""F (gunluk TSM N40) DD-koruma: ATR-carpani stop vs sabit-bps. 5 coin WF 60/40.
ATR gunluk (14), stop = entry -/+ k*ATR. Vol-rejimine gore nefes alir."""
import urllib.request, json, statistics, time

FEE = 8.0

def klines(sym, limit=1000):
    u = 'https://fapi.binance.com/fapi/v1/klines?symbol=%sUSDT&interval=1d&limit=%d' % (sym, limit)
    for _ in range(3):
        try:
            r = json.loads(urllib.request.urlopen(u, timeout=15).read())
            return [(float(x[2]), float(x[3]), float(x[4])) for x in r]  # h,l,c
        except Exception:
            time.sleep(1)
    return None

def atr_series(bars, n=14):
    H = [b[0] for b in bars]; L = [b[1] for b in bars]; C = [b[2] for b in bars]
    tr = [H[0] - L[0]]
    for i in range(1, len(bars)):
        tr.append(max(H[i] - L[i], abs(H[i] - C[i-1]), abs(L[i] - C[i-1])))
    atr = [0.0] * len(bars)
    for i in range(len(bars)):
        lo = max(0, i - n + 1)
        atr[i] = sum(tr[lo:i+1]) / (i - lo + 1)
    return atr

def run(bars, N=40, sl_bps=0, atr_mult=0, atr_n=14, lo=0, hi=None):
    H = [b[0] for b in bars]; L = [b[1] for b in bars]; C = [b[2] for b in bars]
    ATR = atr_series(bars, atr_n) if atr_mult else None
    n = len(C); hi = hi or n - 1
    pos = 0; ent = 0.0; blocked = 0; ent_i = 0
    daily = []
    for i in range(max(N, lo), hi):
        if C[i - N] <= 0:
            continue
        want = 1 if C[i] > C[i - N] else -1
        if blocked and want != blocked:
            blocked = 0
        target = 0 if blocked == want else want
        opening = target != 0 and target != pos
        if opening:
            ent = C[i]; ent_i = i
        fee = FEE / 1e4 if target != pos else 0.0
        if target == 0:
            if fee:
                daily.append(-fee)
            pos = 0
            continue
        # stop mesafesi (bps): sabit ya da ATR-carpani (entry gunundeki ATR)
        stop_bps = sl_bps
        if atr_mult:
            a = ATR[ent_i]
            stop_bps = (atr_mult * a / ent * 1e4) if ent > 0 else 0
        if stop_bps:
            adv = ((ent - L[i + 1]) if target > 0 else (H[i + 1] - ent)) / ent * 1e4
            if adv >= stop_bps:
                daily.append(-stop_bps / 1e4 - fee)
                blocked = target; pos = 0
                continue
        r = target * (C[i + 1] - C[i]) / C[i]
        daily.append(r - fee)
        pos = target
    return daily

def stats(d):
    if not d:
        return 0, 0, 0
    tot = sum(d) * 100
    sh = (statistics.mean(d) / statistics.pstdev(d) * (365 ** 0.5)) if statistics.pstdev(d) > 0 else 0
    eq = pk = mdd = 0
    for x in d:
        eq += x; pk = max(pk, eq); mdd = min(mdd, eq - pk)
    return tot, sh, mdd * 100

VARIANTS = [
    ('flip-only', {}),
    ('SL 500bps (sabit)', {'sl_bps': 500}),
    ('2xATR', {'atr_mult': 2}),
    ('3xATR', {'atr_mult': 3}),
    ('4xATR', {'atr_mult': 4}),
    ('5xATR', {'atr_mult': 5}),
    ('6xATR', {'atr_mult': 6}),
]

for sym in ('ETH', 'BTC', 'SOL', 'BNB', 'XRP'):
    bars = klines(sym)
    if not bars:
        print(sym, 'no data'); continue
    n = len(bars); split = int(n * 0.6)
    # ort ATR% bilgi
    A = atr_series(bars, 14); avg_atr_pct = sum(A[i]/bars[i][2]*100 for i in range(len(bars)) if bars[i][2]>0)/len(bars)
    print('=== %s (n=%d, ort ATR%%~%.1f) ===' % (sym, n, avg_atr_pct))
    for lbl, kw in VARIANTS:
        full = run(bars, 40, lo=0, **kw); oo = run(bars, 40, lo=split, **kw)
        tt, sh, dd = stats(full); to, sho, ddo = stats(oo)
        print('  %-18s full %+7.1f%% Sh%.2f DD%+5.0f%% | OOS %+7.1f%% Sh%.2f DD%+5.0f%%'
              % (lbl, tt, sh, dd, to, sho, ddo))
