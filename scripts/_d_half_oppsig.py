"""D cikis varyanti: %100 POC-kapat vs %50 POC + %50 ters-sinyale-kadar kostur.
Kullanici fikri: kari elinde tut, runner'i ters D sinyali gelene dek tasi.
Faithful: volume-agirlikli POC (M40), DEV85, SL90, MH16, fee8+slip2, WF."""
import sqlite3, json
from core.config import cfg

c = sqlite3.connect('file:%s?mode=ro' % cfg.DB_PATH, uri=True)
# 1) OHLC hizli (ts, price, id) — json yok
rows = c.execute("SELECT id, ts, price FROM market_snapshots WHERE price>0 ORDER BY ts").fetchall()
bars = {}
last_id = {}
for rid, ts, p in rows:
    b = int(ts // 900)
    if b not in bars:
        bars[b] = [p, p, p]  # h,l,c
    else:
        o = bars[b]; o[0] = max(o[0], p); o[1] = min(o[1], p); o[2] = p
    last_id[b] = rid
k = sorted(bars)
# 2) volume: sadece bar-sonu satirin payload'undan
ids = tuple(last_id[b] for b in k)
vol = {}
CH = 900
for s in range(0, len(ids), CH):
    chunk = ids[s:s + CH]
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


def sig_at(i):
    pc = poc(i)
    if not pc:
        return None
    dev = (C[i] - pc) / pc * 1e4
    return 'LONG' if dev <= -DEV else ('SHORT' if dev >= DEV else None)


# mode: 'poc'  -> %100 POC'ta kapat (MEVCUT)
#       'opp'  -> %50 POC'ta kapat, %50 ters sinyale kadar tut (kullanici fikri)
#       'opp_trail',T -> %50 POC + %50 ters-sinyal VEYA trail-T (hangisi once)
def run(mode, lo=0, hi=None):
    hi = hi or n; i = max(M + 2, lo); tr = []
    while i < hi - 1:
        sig = sig_at(i)
        if not sig:
            i += 1; continue
        ent = C[i]; half = 0.; phase = 'open'; peak = ent; res = None; j = i
        cap = i + MH * 6  # runner ust siniri (acik kalmasin)
        for j in range(i + 1, min(cap + 1, n)):
            cur = ((C[j] - ent) if sig == 'LONG' else (ent - C[j])) / ent * 1e4
            adv = ((H[j] - ent) if sig == 'SHORT' else (ent - L[j])) / ent * 1e4
            if adv >= SL:  # SL: acik fazda tum, runner fazda kalan %50
                res = (0.5 * half + 0.5 * (-SL)) if phase == 'runner' else -SL
                res -= FEE + 2 * SLIP; break
            pcj = poc(j); dj = (C[j] - pcj) / pcj * 1e4 if pcj else None
            rev = dj is not None and ((sig == 'LONG' and dj >= 0) or (sig == 'SHORT' and dj <= 0))
            if phase == 'open':
                if rev and cur >= 0:
                    if mode[0] == 'poc':
                        res = cur - FEE - 2 * SLIP; break
                    else:  # %50 al, %50 runner
                        phase = 'runner'; half = cur; peak = C[j]
                if phase == 'open' and (j - i) >= MH:
                    res = cur - FEE - 2 * SLIP; break
            else:  # runner: ters sinyal (veya trail) bekle
                osig = sig_at(j)
                opp = (osig is not None and osig != sig)
                done = opp
                if mode[0] == 'opp_trail':
                    T = mode[1]
                    if sig == 'LONG':
                        peak = max(peak, C[j]); retr = (peak - C[j]) / ent * 1e4
                    else:
                        peak = min(peak, C[j]); retr = (C[j] - peak) / ent * 1e4
                    done = done or retr >= T
                if done or (j - i) >= cap - i:
                    res = 0.5 * half + 0.5 * cur - FEE - 2 * SLIP; break
        if res is None:
            cur = ((C[j] - ent) if sig == 'LONG' else (ent - C[j])) / ent * 1e4
            res = (0.5 * half + 0.5 * cur if phase == 'runner' else cur) - FEE - 2 * SLIP
        tr.append(res); i = j + 1
    return tr


split = int(n * 0.6)


def rep(lbl, m):
    al = run(m); tr = run(m, hi=split); oo = run(m, lo=split)
    qs = [sum(run(m, lo=n * q // 4, hi=n * (q + 1) // 4)) for q in range(4)]
    pos = sum(1 for x in qs if x > 0)
    hit = 100 * sum(1 for x in al if x > 0) / len(al) if al else 0
    print('%-30s net%+6.0f isl=%-3d isabet%%%.0f | TRAIN%+6.0f OOS%+6.0f cey%d/4' % (
        lbl, sum(al), len(al), hit, sum(tr), sum(oo), pos))


print('\n=== D cikis: %100 POC vs %50 POC + %50 ters-sinyal (faithful, fee8+slip2, WF) ===')
print('bar=%d (36 gun)' % n)
rep('%100 POC kapat (MEVCUT)', ('poc',))
rep('%50 POC + %50 ters-sinyale dek', ('opp',))
rep('%50 POC + %50 (ters VEYA trail40)', ('opp_trail', 40))
rep('%50 POC + %50 (ters VEYA trail60)', ('opp_trail', 60))
