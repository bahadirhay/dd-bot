"""D'nin poc_revert cikisindan SONRA para masada kaliyor mu? (#318: 200bps kacti — sistematik mi?)
Her D cikisindan sonraki K barda: yonun DEVAMI (favorable) vs GERI DONUS (adverse) olc.
Runner'in degeri = cikis sonrasi ORTALAMA devam. ~0 ise #318 sanstir; guclu+ ise runner hakli.
"""
import urllib.request, json, statistics, time

SYM = 'ETHUSDT'; ITV = '15m'
M = 40          # POC penceresi (canli D)
DEV = 85.0      # giris esigi bps
SL = 300.0
MAXHOLD = 200
AFTER = 32      # cikis sonrasi izlenecek bar (32x15m = 8h)


def fetch(n_pages=12):
    """15m barlari geriye dogru sayfala (~1500/istek)."""
    out = []
    end = None
    for _ in range(n_pages):
        u = 'https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=%s&limit=1500' % (SYM, ITV)
        if end:
            u += '&endTime=%d' % end
        try:
            r = json.loads(urllib.request.urlopen(u, timeout=20).read())
        except Exception as e:
            print('fetch err', e); break
        if not r:
            break
        out = r + out
        end = int(r[0][0]) - 1
        time.sleep(0.15)
    # (ts, h, l, c, vol)
    return [(int(x[0]), float(x[2]), float(x[3]), float(x[4]), float(x[5])) for x in out]


bars = fetch()
print('bar:', len(bars))
H = [b[1] for b in bars]; L = [b[2] for b in bars]; C = [b[3] for b in bars]; V = [b[4] for b in bars]


def poc(i):
    nu = de = 0.0
    for j in range(i - M, i):
        v = V[j] if V[j] > 0 else 1.0
        nu += C[j] * v; de += v
    return nu / de if de > 0 else None


conts = []      # cikis sonrasi DEVAM (favorable, bps) — nihai (AFTER bar sonra)
mfes = []       # cikis sonrasi en iyi devam (max favorable)
captured = []   # D'nin aldigi (giris->cikis)
i = M
n = len(C)
while i < n - AFTER - 1:
    p = poc(i)
    if not p or C[i] <= 0:
        i += 1; continue
    dev = (C[i] - p) / p * 1e4
    if abs(dev) < DEV:
        i += 1; continue
    side = -1 if dev > 0 else 1          # dev+ -> SHORT (fade), dev- -> LONG
    ent = C[i]
    # tut: poc_revert (POC'a donus, karda) veya SL veya maxhold
    j = i + 1; exit_px = None
    while j < n - AFTER - 1 and (j - i) < MAXHOLD:
        cur = (C[j] - ent) / ent * 1e4 * side
        adv = ((ent - L[j]) if side > 0 else (H[j] - ent)) / ent * 1e4
        if adv >= SL:
            exit_px = ent * (1 - SL / 1e4 * side); break     # SL -> runner konusu degil
        pj = poc(j)
        if pj:
            # POC'a donus: fiyat POC'u gecti mi (fade tamamlandi) + karda
            rev = (C[j] <= pj) if side < 0 else (C[j] >= pj)
            if rev and cur >= 0:
                exit_px = C[j]; break
        j += 1
    if exit_px is None:
        i = j + 1; continue
    got = (exit_px - ent) / ent * 1e4 * side
    if got < 0:      # SL/zararli cikis -> runner sorusu degil (kazanan cikislari incele)
        i = j + 1; continue
    # cikis SONRASI devam (ayni yonde)
    k_end = min(j + AFTER, n - 1)
    fin = (C[k_end] - exit_px) / exit_px * 1e4 * side
    if side < 0:
        mfe = (exit_px - min(L[j + 1:k_end + 1])) / exit_px * 1e4
    else:
        mfe = (max(H[j + 1:k_end + 1]) - exit_px) / exit_px * 1e4
    captured.append(got); conts.append(fin); mfes.append(mfe)
    i = j + 1

n_ = len(conts)
print('\n=== D poc_revert (KAZANAN) cikislari: %d olay ===' % n_)
if n_:
    def st(x, lbl):
        m = statistics.mean(x); sd = statistics.pstdev(x)
        t = m / (sd / (len(x) ** 0.5)) if sd > 0 else 0
        print('%-28s ort %+7.1f bps  medyan %+7.1f  t=%+.2f' % (lbl, m, statistics.median(x), t))
    st(captured, 'D ALDIGI (giris->cikis)')
    st(conts, 'CIKIS SONRASI devam (8h)')
    st(mfes, 'CIKIS SONRASI en iyi (MFE)')
    pos = sum(1 for x in conts if x > 0)
    print('\ncikis sonrasi DEVAM eden (yon lehine): %d/%d = %%%.0f' % (pos, n_, pos / n_ * 100))
    print('(rastgele olsaydi ~%50 beklenirdi)')
    big = sum(1 for x in mfes if x >= 200)
    print('#318 gibi >=200bps devam eden: %d/%d = %%%.0f' % (big, n_, big / n_ * 100))
