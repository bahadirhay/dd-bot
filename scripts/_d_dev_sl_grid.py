"""D'yi net-pozitife tasiyacak yapisal kaldirac var mi?
Hipotez: fee SABIT (10bps). Giris esigi (|dev|) buyurse HEDEF buyur -> fee oransal kucuur, R:R duzelir.
DEV x SL izgarasi, NET (fee dahil), walk-forward 60/40. Kabul sarti: HER IKI fold da baseline'i gecmeli.
"""
import urllib.request, json, statistics, time

SYM = 'ETHUSDT'; ITV = '15m'
M = 40; MAXHOLD = 200; FEE = 10.0
MIN_PROF = 12.0     # yeni min-kar kapisi (canlidaki)


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
    _pc[i] = r; return r


def sim(dev_thr, sl, lo, hi):
    trades = []
    i = max(M, lo)
    while i < hi - 1:
        p = poc(i)
        if not p or C[i] <= 0:
            i += 1; continue
        dev = (C[i] - p) / p * 1e4
        if abs(dev) < dev_thr:
            i += 1; continue
        side = -1 if dev > 0 else 1
        ent = C[i]
        j = i + 1; got = None
        while j < hi - 1 and (j - i) < MAXHOLD:
            cur = (C[j] - ent) / ent * 1e4 * side
            adv = ((ent - L[j]) if side > 0 else (H[j] - ent)) / ent * 1e4
            if adv >= sl:
                got = -sl; break
            pj = poc(j)
            if pj:
                rev = (C[j] <= pj) if side < 0 else (C[j] >= pj)
                if rev and cur >= MIN_PROF:
                    got = cur; break
            j += 1
        if got is None:
            got = (C[min(j, hi - 1)] - ent) / ent * 1e4 * side
        trades.append(got - FEE)
        i = j + 1
    return trades


split = int(n * 0.6)


def rep(x):
    if not x:
        return '      yok       '
    return 'ort%+6.1f n=%-4d' % (statistics.mean(x), len(x))


print('\n=== DEV x SL izgarasi — NET bps/islem (fee %.0f dahil), min_prof=%.0f ===' % (FEE, MIN_PROF))
print('baseline = dev85 / sl300 (canlidaki ~ATR ort)\n')
print('%-9s | %-34s | %-34s' % ('', 'TRAIN', 'OOS'))
print('%-9s | %-16s %-17s | %-16s %-17s' % ('dev esigi', 'sl=200', 'sl=300', 'sl=200', 'sl=300'))
print('-' * 84)
base_tr = base_oo = None
for dv in (85, 100, 120, 150, 200, 250):
    cells_tr = []; cells_oo = []
    for sl in (200, 300):
        cells_tr.append(sim(dv, sl, M, split))
        cells_oo.append(sim(dv, sl, split, n))
    if dv == 85:
        base_tr = statistics.mean(cells_tr[1]) if cells_tr[1] else 0
        base_oo = statistics.mean(cells_oo[1]) if cells_oo[1] else 0
    print('%-9s | %-16s %-17s | %-16s %-17s' % (
        'dev>=%d' % dv, rep(cells_tr[0]), rep(cells_tr[1]), rep(cells_oo[0]), rep(cells_oo[1])))
print('-' * 84)
print('baseline (dev85/sl300): TRAIN %+.1f | OOS %+.1f' % (base_tr, base_oo))
print('\nKABUL SARTI: hem TRAIN hem OOS baseline\'i gecmeli VE net > 0 olmali.')
