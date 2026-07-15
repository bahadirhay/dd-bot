"""F per-coin stop analizi: her coin icin SL'yi 4 BAGIMSIZ OOS ceyreginde sina.
Robust = coginda flip-only'i gecmeli (tek pencere sansi degil). ETH ayri, digerleri ayri."""
import urllib.request, json, statistics, time

FEE = 8.0

def klines(sym, limit=1000):
    u = 'https://fapi.binance.com/fapi/v1/klines?symbol=%sUSDT&interval=1d&limit=%d' % (sym, limit)
    for _ in range(3):
        try:
            r = json.loads(urllib.request.urlopen(u, timeout=15).read())
            return [(float(x[2]), float(x[3]), float(x[4])) for x in r]
        except Exception:
            time.sleep(1)
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

# Her coin: N=40 baslangicini gecince kalan bar'i 4 disjoint ceyrege bol; her ceyrekte
# flip-only vs SL varyantlarini ayri kos (bagimsiz pencere).
SLS = [0, 300, 500, 700]
for sym in ('ETH', 'BTC', 'SOL', 'BNB', 'XRP'):
    bars = klines(sym)
    if not bars:
        print(sym, 'yok'); continue
    n = len(bars)
    start = 40
    span = n - 1 - start
    q = span // 4
    print('\n=== %s ===' % sym)
    print('  ceyrek |    flip |  SL300  |  SL500  |  SL700   (net%%, ())=maxDD)')
    wins = {300: 0, 500: 0, 700: 0}
    for k in range(4):
        lo = start + k * q
        hi = start + (k + 1) * q if k < 3 else n - 1
        cells = []
        base = net(run(bars, 40, 0, lo, hi))
        for sl in SLS:
            d = run(bars, 40, sl, lo, hi)
            cells.append((sl, net(d), maxdd(d)))
            if sl and net(d) > base:
                wins[sl] += 1
        s = '  Q%d    ' % (k + 1)
        for sl, nt, dd in cells:
            s += ' |%+6.0f(%+4.0f)' % (nt, dd)
        print(s)
    print('  SL flip-only\'i gecen ceyrek sayisi (4 uzerinden): SL300=%d SL500=%d SL700=%d'
          % (wins[300], wins[500], wins[700]))
