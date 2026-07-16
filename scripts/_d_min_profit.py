"""D poc_revert cikisina MIN-KAR esigi koymak NET'i iyilestirir mi?
Sorun: cur>=0 ile cikinca brut +4bps -> fee sonrasi NET ZARAR (#319, #317).
Ama esik koyarsak pozisyon TUTULUR -> maxhold/SL'e dusebilir (belki daha kotu).
NET (fee dahil) ile, walk-forward TRAIN/OOS."""
import urllib.request, json, statistics, time

SYM = 'ETHUSDT'; ITV = '15m'
M = 40; DEV = 85.0; SL = 300.0; MAXHOLD = 200
FEE = 10.0        # round-trip taker (canli olculen ~7-10bps)


def fetch(n_pages=12):
    out = []; end = None
    for _ in range(n_pages):
        u = 'https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=%s&limit=1500' % (SYM, ITV)
        if end:
            u += '&endTime=%d' % end
        try:
            r = json.loads(urllib.request.urlopen(u, timeout=20).read())
        except Exception as e:
            print('err', e); break
        if not r:
            break
        out = r + out; end = int(r[0][0]) - 1; time.sleep(0.15)
    return [(float(x[2]), float(x[3]), float(x[4]), float(x[5])) for x in out]


bars = fetch()
H = [b[0] for b in bars]; L = [b[1] for b in bars]; C = [b[2] for b in bars]; V = [b[3] for b in bars]
n = len(C)
print('bar:', n)

_pc = {}
def poc(i):
    if i in _pc:
        return _pc[i]
    nu = de = 0.0
    for j in range(i - M, i):
        v = V[j] if V[j] > 0 else 1.0
        nu += C[j] * v; de += v
    r = nu / de if de > 0 else None
    _pc[i] = r
    return r


def sim(min_profit, lo, hi):
    """min_profit: poc_revert cikisi icin gereken MIN brut kar (bps). 0 = mevcut davranis."""
    trades = []
    i = max(M, lo)
    while i < hi - 1:
        p = poc(i)
        if not p or C[i] <= 0:
            i += 1; continue
        dev = (C[i] - p) / p * 1e4
        if abs(dev) < DEV:
            i += 1; continue
        side = -1 if dev > 0 else 1
        ent = C[i]
        j = i + 1; got = None
        while j < hi - 1 and (j - i) < MAXHOLD:
            cur = (C[j] - ent) / ent * 1e4 * side
            adv = ((ent - L[j]) if side > 0 else (H[j] - ent)) / ent * 1e4
            if adv >= SL:
                got = -SL; break
            pj = poc(j)
            if pj:
                rev = (C[j] <= pj) if side < 0 else (C[j] >= pj)
                if rev and cur >= min_profit:      # <-- esik burada
                    got = cur; break
            j += 1
        if got is None:
            got = (C[min(j, hi - 1)] - ent) / ent * 1e4 * side   # maxhold
        trades.append(got - FEE)                                  # NET
        i = j + 1
    return trades


split = int(n * 0.6)
print('\n=== D poc_revert MIN-KAR esigi (NET, fee %.0fbps dahil) ===' % FEE)
print('%-12s | %-28s | %-28s' % ('esik', 'TRAIN (net)', 'OOS (net)'))
for mp in (0, 6, 12, 20, 30, 50):
    tr = sim(mp, M, split); oo = sim(mp, split, n)
    def s(x):
        if not x:
            return 'yok'
        return 'top %+7.0f  ort %+5.1f  n=%d' % (sum(x), statistics.mean(x), len(x))
    print('%-12s | %-28s | %-28s' % ('cur>=%d' % mp, s(tr), s(oo)))
