"""F (gunluk TSM N40) DD-koruma testi - DOGRU mantik.
Stop yiyince: flat kal, momentum sinyali DONENE kadar ayni yone tekrar girme
(eski script ayni gun geri giriyordu -> stop etkisizdi). WF 60/40.
"""
import urllib.request, json, statistics, time

FEE = 8.0  # bps round-trip

def klines(sym, limit=1000):
    u = 'https://fapi.binance.com/fapi/v1/klines?symbol=%sUSDT&interval=1d&limit=%d' % (sym, limit)
    for _ in range(3):
        try:
            r = json.loads(urllib.request.urlopen(u, timeout=15).read())
            return [(float(x[2]), float(x[3]), float(x[4])) for x in r]  # high,low,close
        except Exception:
            time.sleep(1)
    return None


def run(bars, N=40, sl=0, trail=0, lo=0, hi=None):
    H = [b[0] for b in bars]; L = [b[1] for b in bars]; C = [b[2] for b in bars]
    n = len(C); hi = hi or n - 1
    pos = 0; ent = 0.0; peak = 0.0; blocked = 0
    daily = []
    for i in range(max(N, lo), hi):
        if C[i - N] <= 0:
            continue
        want = 1 if C[i] > C[i - N] else -1
        if blocked and want != blocked:
            blocked = 0                      # sinyal dondu -> blok kalk
        target = 0 if blocked == want else want
        # pozisyon acilis/flip -> entry ve fee
        opening = target != 0 and target != pos
        if opening:
            ent = C[i]; peak = C[i]
        fee = FEE / 1e4 if target != pos else 0.0
        if target == 0:
            if fee:
                daily.append(-fee)           # kapanis maliyeti
            pos = 0
            continue
        # --- pozisyon tut (i -> i+1) + gun-ici koruma ---
        if sl:
            adv = ((ent - L[i + 1]) if target > 0 else (H[i + 1] - ent)) / ent * 1e4
            if adv >= sl:
                daily.append(-sl / 1e4 - fee)  # stop'ta cik
                blocked = target; pos = 0       # bu yonu blokla
                continue
        if trail:
            peak = max(peak, C[i]) if target > 0 else min(peak, C[i])
            retr = ((peak - C[i + 1]) if target > 0 else (C[i + 1] - peak)) / ent * 1e4
            cur = ((C[i + 1] - ent) if target > 0 else (ent - C[i + 1])) / ent * 1e4
            if cur > 0 and retr >= trail:
                r = target * (C[i + 1] - C[i]) / C[i]
                daily.append(r - fee)
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


bars = klines('ETH')
n = len(bars); split = int(n * 0.6)
print('ETH gunluk bar:', n, ' split@', split)

def rep(lbl, **kw):
    full = run(bars, 40, **kw)
    oo = run(bars, 40, lo=split, **kw)
    tt, sh, dd = stats(full); to, sho, ddo = stats(oo)
    print('%-24s full %+7.1f%% Sh%.2f maxDD%+6.0f%% | OOS %+7.1f%% Sh%.2f maxDD%+6.0f%%'
          % (lbl, tt, sh, dd, to, sho, ddo))

print('=== F koruma varyantlari (dogru: stop->flip-e-kadar-blok), WF ===')
rep('flip-only (MEVCUT)')
print('--- felaket-SL (entry-den) ---')
for x in (300, 500, 800, 1200):
    rep('SL %dbps' % x, sl=x)
print('--- trailing (tepe-trail, karda) ---')
for x in (300, 500, 800):
    rep('trail %dbps' % x, trail=x)
