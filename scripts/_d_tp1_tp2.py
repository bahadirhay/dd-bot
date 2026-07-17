"""Kullanici onerisi: TP1 = poc_revert (mevcut mantik), TP2 = kalan runner.
TUM strateji NET'i uzerinde test (SL islemleri DAHIL — onceki analizim yalniz kazanan
cikislara bakmisti, bu sefer tam P&L). WF 60/40.
Kabul sarti: HEM TRAIN HEM OOS baseline'i gecmeli.
"""
import urllib.request, json, statistics, time

SYM = 'ETHUSDT'; M = 40; DEV = 85.0; SL = 300.0; MAXHOLD = 200
MIN_PROF = 12.0; FEE = 10.0


def fetch(np_=12):
    out = []; end = None
    for _ in range(np_):
        u = 'https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=15m&limit=1500' % SYM
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


def sim(pct, trail, far_tp, lo, hi):
    """pct: poc_revert'te kapatilan oran (1.0 = mevcut). Kalan (1-pct) runner:
    trail>0 -> tepeden trail bps geri cekilince cik; far_tp>0 -> o hedefe kadar tut."""
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
        j = i + 1
        booked = 0.0          # TP1 (poc_revert) ile alinan, agirlikli
        runner_on = False
        peak = 0.0
        total = None
        while j < hi - 1 and (j - i) < MAXHOLD:
            cur = (C[j] - ent) / ent * 1e4 * side
            adv = ((ent - L[j]) if side > 0 else (H[j] - ent)) / ent * 1e4
            if not runner_on:
                # faz 1: SL veya poc_revert
                if adv >= SL:
                    total = -SL; break
                pj = poc(j)
                if pj:
                    rev = (C[j] <= pj) if side < 0 else (C[j] >= pj)
                    if rev and cur >= MIN_PROF:
                        if pct >= 0.999:
                            total = cur; break
                        booked = cur * pct
                        runner_on = True; peak = cur
            else:
                # faz 2: runner (kalan 1-pct)
                fav = ((H[j] - ent) if side > 0 else (ent - L[j])) / ent * 1e4
                peak = max(peak, fav)
                if adv >= SL:
                    total = booked + (-SL) * (1 - pct); break
                if far_tp and cur >= far_tp:
                    total = booked + far_tp * (1 - pct); break
                if trail and (peak - cur) >= trail:
                    total = booked + cur * (1 - pct); break
            j += 1
        if total is None:
            cur = (C[min(j, hi - 1)] - ent) / ent * 1e4 * side
            total = booked + cur * (1 - pct) if runner_on else cur
        trades.append(total - FEE)
        i = j + 1
    return trades


split = int(n * 0.6)


def s(x):
    if not x:
        return 'yok'
    return 'top %+7.0f ort %+5.1f n=%d' % (sum(x), statistics.mean(x), len(x))


print('\n=== TP1(poc_revert) + TP2(runner) — NET bps, fee %.0f dahil ===' % FEE)
print('%-34s | %-26s | %-26s' % ('varyant', 'TRAIN', 'OOS'))
print('-' * 92)
base_tr = sim(1.0, 0, 0, M, split); base_oo = sim(1.0, 0, 0, split, n)
print('%-34s | %-26s | %-26s' % ('BASELINE %100 poc_revert', s(base_tr), s(base_oo)))
bt, bo = sum(base_tr), sum(base_oo)
print('-' * 92)
for pct in (0.5, 0.7):
    for trail in (25, 40, 60, 100):
        tr = sim(pct, trail, 0, M, split); oo = sim(pct, trail, 0, split, n)
        mark = ''
        if sum(tr) > bt and sum(oo) > bo:
            mark = '  <<< HER IKI FOLD GECTI'
        print('%-34s | %-26s | %-26s%s' % ('%%%d TP1 + runner trail%d' % (pct*100, trail), s(tr), s(oo), mark))
print('-' * 92)
for pct in (0.5, 0.7):
    for ftp in (150, 200, 300):
        tr = sim(pct, 0, ftp, M, split); oo = sim(pct, 0, ftp, split, n)
        mark = ''
        if sum(tr) > bt and sum(oo) > bo:
            mark = '  <<< HER IKI FOLD GECTI'
        print('%-34s | %-26s | %-26s%s' % ('%%%d TP1 + TP2@%dbps' % (pct*100, ftp), s(tr), s(oo), mark))
