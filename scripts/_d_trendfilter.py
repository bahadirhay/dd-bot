"""D + yuksek-TF trend filtresi: karsi-trend dip-alimi/tepe-satisini atla.
#268 gibi (TREND rejiminde NEAR_SUPPORT LONG, destek kirilir) kayiplari azaltir mi,
yoksa edge'i mi keser? Faithful POC (M40,DEV85,SL90,MH16), fee8+slip2, WF."""
import sqlite3, json
from core.config import cfg

c = sqlite3.connect('file:%s?mode=ro' % cfg.DB_PATH, uri=True)
rows = c.execute("SELECT id, ts, price FROM market_snapshots WHERE price>0 ORDER BY ts").fetchall()
bars, last_id = {}, {}
for rid, ts, p in rows:
    b = int(ts // 900)
    if b not in bars:
        bars[b] = [p, p, p]
    else:
        o = bars[b]; o[0] = max(o[0], p); o[1] = min(o[1], p); o[2] = p
    last_id[b] = rid
k = sorted(bars)
ids = tuple(last_id[b] for b in k)
vol = {}
for s in range(0, len(ids), 900):
    chunk = ids[s:s + 900]
    q = "SELECT id,payload_json FROM market_snapshots WHERE id IN (%s)" % ",".join("?" * len(chunk))
    for rid, pj in c.execute(q, chunk):
        v = None
        if pj:
            try:
                fm = json.loads(pj).get('forming_15m')
                if isinstance(fm, dict):
                    v = fm.get('volume')
            except Exception:
                pass
        vol[rid] = v
H = [bars[x][0] for x in k]; L = [bars[x][1] for x in k]; C = [bars[x][2] for x in k]
V = [vol.get(last_id[x]) for x in k]
n = len(C)
SL = 90.; MH = 16; FEE = 8.; SLIP = 2.; DEV = 85.; M = 40


def poc(i):
    nu = de = 0.
    for j in range(i - M, i):
        v = V[j] if (V[j] and V[j] > 0) else 1.
        nu += C[j] * v; de += v
    return nu / de if de > 0 else None


def trend_ok(i, side, K, thr_bps):
    """side LONG: yuksek-TF asagi degilse gir. SHORT: yukari degilse gir.
    trend = (C[i]-C[i-K])/C[i-K]. thr_bps=esik (0=her ters trendde blokla)."""
    if K <= 0 or i - K < 0 or C[i - K] <= 0:
        return True
    tr = (C[i] - C[i - K]) / C[i - K] * 1e4
    if side == 'LONG':
        return tr >= -thr_bps   # asagi trend thr'den dik degilse LONG ok
    return tr <= thr_bps        # yukari trend thr'den dik degilse SHORT ok


def run(K, thr, lo=0, hi=None):
    hi = hi or n; i = max(M + 2, lo); tr = []
    while i < hi - 1:
        pc = poc(i)
        if not pc:
            i += 1; continue
        dev = (C[i] - pc) / pc * 1e4
        side = 'LONG' if dev <= -DEV else ('SHORT' if dev >= DEV else None)
        if not side:
            i += 1; continue
        if K > 0 and not trend_ok(i, side, K, thr):
            i += 1; continue   # karsi-trend -> atla
        ent = C[i]; res = None; j = i
        for j in range(i + 1, min(i + MH + 1, n)):
            cur = ((C[j] - ent) if side == 'LONG' else (ent - C[j])) / ent * 1e4
            adv = ((H[j] - ent) if side == 'SHORT' else (ent - L[j])) / ent * 1e4
            if adv >= SL:
                res = -SL - FEE - 2 * SLIP; break
            pcj = poc(j); dj = (C[j] - pcj) / pcj * 1e4 if pcj else None
            rev = dj is not None and ((side == 'LONG' and dj >= 0) or (side == 'SHORT' and dj <= 0))
            if rev and cur >= 0:
                res = cur - FEE - 2 * SLIP; break
            if (j - i) >= MH:
                res = cur - FEE - 2 * SLIP; break
        if res is None:
            cur = ((C[j] - ent) if side == 'LONG' else (ent - C[j])) / ent * 1e4
            res = cur - FEE - 2 * SLIP
        tr.append(res); i = j + 1
    return tr


split = int(n * 0.6)


def rep(lbl, K, thr):
    al = run(K, thr); oo = run(K, thr, lo=split)
    qs = [sum(run(K, thr, lo=n * q // 4, hi=n * (q + 1) // 4)) for q in range(4)]
    pos = sum(1 for x in qs if x > 0)
    hit = 100 * sum(1 for x in al if x > 0) / len(al) if al else 0
    print('%-34s net%+6.0f isl=%-3d isabet%%%.0f | OOS%+6.0f cey%d/4' % (
        lbl, sum(al), len(al), hit, sum(oo), pos))


print('\n=== D + yuksek-TF trend filtresi (faithful, fee8+slip2, WF) bar=%d ===' % n)
rep('FILTRE YOK (MEVCUT D)', 0, 0)
print('--- K=32 (8h) trend, esik=0 (her ters trend blok) ---')
rep('K32 thr0', 32, 0)
rep('K32 thr50 (sadece dik trend blok)', 32, 50)
rep('K32 thr100', 32, 100)
print('--- K=64 (16h) ---')
rep('K64 thr0', 64, 0)
rep('K64 thr100', 64, 100)
print('--- K=96 (24h) ---')
rep('K96 thr0', 96, 0)
rep('K96 thr150', 96, 150)
