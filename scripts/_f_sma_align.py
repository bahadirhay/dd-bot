"""F (gunluk TSM) + SMA-teyit filtresi testi.
Hipotez: TSM LONG isterken close>SMA_M, SHORT isterken close<SMA_M degilse gir-me (flat).
D'de SMA-hizasi counter-trend fade'i engelleyip negatiften pozitife cevirmisti. F ZATEN
trend-takip -> SMA ayni fiyattan turer, YENI BILGI olmayabilir (bkz indicator-combos hafizasi).
Bu yuzden DISIPLINLI test: 5 coin x 4 BAGIMSIZ ceyrek, SMA {50,100,150,200} plato ariyoruz.
Kazanan = coginda + coinde flip-only'i GECMELI (tek-pencere sansi degil)."""
import urllib.request, json, time

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

def run(bars, N=40, sma_m=0, lo=0, hi=None):
    """sma_m=0 -> filtresiz (duz flip-only F). sma_m>0 -> SMA-teyit filtresi."""
    C = [b[2] for b in bars]
    n = len(C); hi = hi or n - 1
    pos = 0; ent = 0.0
    daily = []
    for i in range(max(N, sma_m, lo), hi):
        if C[i - N] <= 0:
            continue
        want = 1 if C[i] > C[i - N] else -1
        # SMA-teyit: trend sinyali SMA ile ayni yonde degilse FLAT
        if sma_m:
            sma = sum(C[i - sma_m + 1:i + 1]) / sma_m
            if (want == 1 and C[i] <= sma) or (want == -1 and C[i] >= sma):
                want = 0
        target = want
        fee = FEE / 1e4 if target != pos else 0.0
        if target != 0 and target != pos:
            ent = C[i]
        if target == 0:
            if fee:
                daily.append(-fee)
            pos = 0
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

SMAS = [50, 100, 150, 200]
print('=== F (gunluk TSM N40) + SMA-teyit | 5 coin x 4 bagimsiz ceyrek ===')
print('deger: net%% (parantez maxDD%%). Kazanan ceyrek = flip-only net\'ini gecen.\n')
tot_win = {m: 0 for m in SMAS}; tot_q = 0
for sym in ('ETH', 'BTC', 'SOL', 'BNB', 'XRP'):
    bars = klines(sym)
    if not bars:
        print(sym, 'yok'); continue
    n = len(bars); start = 200  # en uzun SMA'ya yer birak
    span = n - 1 - start; q = span // 4
    print('=== %s (bar=%d) ===' % (sym, n))
    print('  ceyrek |   flip-only |   SMA50   |  SMA100   |  SMA150   |  SMA200')
    for k in range(4):
        lo = start + k * q
        hi = start + (k + 1) * q if k < 3 else n - 1
        base = net(run(bars, 40, 0, lo, hi))
        s = '  Q%d     |%+7.0f(%+4.0f)' % (k + 1, base, maxdd(run(bars, 40, 0, lo, hi)))
        for m in SMAS:
            d = run(bars, 40, m, lo, hi); nt = net(d)
            s += ' |%+6.0f(%+4.0f)' % (nt, maxdd(d))
            if nt > base:
                tot_win[m] += 1
        tot_q += 1
        print(s)
    print()
tot_q_per = tot_q  # 20 (5 coin x 4)
print('=== OZET: SMA-teyit flip-only\'i KAC ceyrekte gecti (20 uzerinden) ===')
for m in SMAS:
    print('  SMA%-4d: %2d/20 ceyrek flip-only\'i gecti' % (m, tot_win[m]))
print('\n(>=14/20 + coinler-arasi tutarli + plato = robust. <=10/20 veya tek-coin = ELE.)')
