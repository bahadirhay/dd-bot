"""D: MAKER-limit giris vs MARKET giris — GERCEK test (tahmin degil).
Kritik soru: limit dolmazsa isi ATLIYORUZ. Kacan islemler EN IYILER mi (ters secilim)?
-> Kacanlarin MARKET'te ne getirecegini AYRICA olc. Toplam NET kiyasla (islem sayisi farkli).
WF 60/40.
"""
import urllib.request, json, statistics, time

SYM = 'ETHUSDT'; ITV = '15m'
M = 40; DEV = 85.0; SL = 300.0; MAXHOLD = 200; MIN_PROF = 12.0
OFFSET = 5.0          # limit, sinyal fiyatindan bu kadar bps uzakta
FILL_WINDOW = 3       # bar; dolmazsa MISS (canli shadow ile ayni)
FEE_TAKER_RT = 10.0   # market giris + taker cikis
FEE_MAKER_RT = 7.0    # maker giris (~2) + taker cikis (~5)


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


def run_exit(ent, side, start_j, hi):
    """Ortak cikis mantigi (poc_revert+min_prof / SL / maxhold). Brut bps doner."""
    j = start_j
    while j < hi - 1 and (j - start_j) < MAXHOLD:
        cur = (C[j] - ent) / ent * 1e4 * side
        adv = ((ent - L[j]) if side > 0 else (H[j] - ent)) / ent * 1e4
        if adv >= SL:
            return -SL, j
        pj = poc(j)
        if pj:
            rev = (C[j] <= pj) if side < 0 else (C[j] >= pj)
            if rev and cur >= MIN_PROF:
                return cur, j
        j += 1
    return (C[min(j, hi - 1)] - ent) / ent * 1e4 * side, j


def sim(lo, hi):
    mkt = []          # market: her sinyalde
    mkr = []          # maker: yalniz dolanlar
    missed_mkt = []   # KACANLARIN market'te getirisi  <-- kritik olcum
    i = max(M, lo)
    while i < hi - 1:
        p = poc(i)
        if not p or C[i] <= 0:
            i += 1; continue
        dev = (C[i] - p) / p * 1e4
        if abs(dev) < DEV:
            i += 1; continue
        side = -1 if dev > 0 else 1
        sig = C[i]

        # --- MARKET: sinyal barinin kapanisinda gir
        g_m, j_m = run_exit(sig, side, i + 1, hi)
        mkt.append(g_m - FEE_TAKER_RT)

        # --- MAKER: limit koy, FILL_WINDOW bar bekle
        lim = sig * (1 - OFFSET / 1e4) if side > 0 else sig * (1 + OFFSET / 1e4)
        fill_j = None
        for j in range(i + 1, min(i + 1 + FILL_WINDOW, hi - 1)):
            hit = (L[j] <= lim) if side > 0 else (H[j] >= lim)
            if hit:
                fill_j = j; break
        if fill_j is not None:
            g_k, _ = run_exit(lim, side, fill_j + 1, hi)
            mkr.append(g_k - FEE_MAKER_RT)
        else:
            missed_mkt.append(g_m - FEE_TAKER_RT)   # kacirdik: market'te bunu kazanacaktik

        i = max(j_m, i + 1)
    return mkt, mkr, missed_mkt


split = int(n * 0.6)
for lbl, lo, hi in (('TRAIN', M, split), ('OOS', split, n)):
    mkt, mkr, miss = sim(lo, hi)
    fill_rate = len(mkr) / (len(mkr) + len(miss)) * 100 if (len(mkr) + len(miss)) else 0
    print('\n=== %s ===' % lbl)
    print('  MARKET (tum sinyaller): n=%-4d  TOPLAM %+8.0f bps  ort %+6.1f' % (
        len(mkt), sum(mkt), statistics.mean(mkt) if mkt else 0))
    print('  MAKER  (yalniz dolan) : n=%-4d  TOPLAM %+8.0f bps  ort %+6.1f' % (
        len(mkr), sum(mkr), statistics.mean(mkr) if mkr else 0))
    print('  FILL-RATE: %%%.1f   (kacan: %d islem)' % (fill_rate, len(miss)))
    print('  --- TERS SECILIM TESTI ---')
    if miss:
        print('  KACANLARIN market getirisi: ort %+6.1f bps  toplam %+7.0f bps' % (
            statistics.mean(miss), sum(miss)))
        print('  (kacanlar ORTALAMADAN IYI ise ters secilim var: ort_kacan %+.1f vs ort_market %+.1f)' % (
            statistics.mean(miss), statistics.mean(mkt) if mkt else 0))
    else:
        print('  kacan yok')
    print('  ==> TOPLAM FARK (maker - market): %+.0f bps' % (sum(mkr) - sum(mkt)))
