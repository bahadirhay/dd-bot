"""Rejim clustering: davranis + gercek order-flow feature -> GMM -> cluster basina D perf + permutation."""
import sqlite3, json, numpy as np
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import StandardScaler

c = sqlite3.connect('file:data/bot.db?mode=ro', uri=True)
rows = c.execute("SELECT id, ts, price FROM market_snapshots WHERE price>0 ORDER BY ts").fetchall()
bars, last_id = {}, {}
for rid, ts, p in rows:
    b = int(ts // 900)
    if b not in bars: bars[b] = [p, p, p, 0]
    o = bars[b]; o[0] = max(o[0], p); o[1] = min(o[1], p); o[2] = p
    last_id[b] = rid
k = sorted(bars)
ids = tuple(last_id[b] for b in k)
flow = {}
for s in range(0, len(ids), 800):
    ch = ids[s:s+800]
    q = "SELECT id,payload_json FROM market_snapshots WHERE id IN (%s)" % ",".join("?"*len(ch))
    for rid, pj in c.execute(q, ch):
        f = {}
        if pj:
            try:
                d = json.loads(pj)
                f['cvd'] = d.get('cvd_5m') or (d.get('intra_15m') or {}).get('cvd_5m')
                f['taker'] = d.get('taker_ratio')
                f['oi'] = 1.0 if d.get('oi_rising') else 0.0
                f['funding'] = d.get('funding_rate')
                fm = d.get('forming_15m') or {}
                f['delta'] = fm.get('delta_sum'); f['vol'] = fm.get('volume')
                bp = (d.get('intra_15m') or {}).get('buy_pressure_5m'); sp = (d.get('intra_15m') or {}).get('sell_pressure_5m')
                if bp is not None and sp is not None and (bp+sp) != 0: f['imb'] = (bp-sp)/(bp+sp)
            except Exception: pass
        flow[rid] = f
H = np.array([bars[x][0] for x in k]); L = np.array([bars[x][1] for x in k]); C = np.array([bars[x][2] for x in k])
FL = [flow.get(last_id[x], {}) for x in k]
V = np.array([(fl.get('vol') or 0) for fl in FL]); n = len(C)
ret = np.diff(C)/C[:-1]; W = 96

def beh(i):
    r = ret[i-W:i]
    if np.std(r) == 0: return None
    ac1 = np.corrcoef(r[:-1], r[1:])[0,1]
    er = abs(C[i]-C[i-W])/np.sum(np.abs(np.diff(C[i-W:i+1])))
    vc = np.corrcoef(np.abs(r[:-1]), np.abs(r[1:]))[0,1] if np.std(np.abs(r))>0 else 0
    bins = np.clip((r/np.std(r)*2).astype(int), -3, 3); _, cnt = np.unique(bins, return_counts=True); p = cnt/cnt.sum()
    ent = -np.sum(p*np.log(p))
    return [ac1, er, vc, ent]

# feature matrisi: davranis + akis (bar bazli akis ort son W)
def flowfeat(i):
    seg = FL[i-W:i]
    def avg(kk):
        vs = [s.get(kk) for s in seg if s.get(kk) is not None]
        return np.mean(vs) if vs else np.nan
    return [avg('cvd'), avg('taker'), avg('oi'), avg('funding'), avg('imb')]

X = []; idx = []
for i in range(W+1, n):
    bb = beh(i)
    if bb is None or not all(np.isfinite(bb)): continue
    ff = flowfeat(i)
    X.append(bb + ff); idx.append(i)
X = np.array(X); idx = np.array(idx)
# akis NaN -> kolon medyani
for j in range(X.shape[1]):
    m = np.nanmedian(X[:, j]); X[np.isnan(X[:, j]), j] = m
split = int(n*0.6); tr = idx < split
sc = StandardScaler().fit(X[tr]); Z = sc.transform(X)
# GMM K secimi (BIC) train'de
best = None
for K in (3, 4, 5):
    g = GaussianMixture(K, covariance_type='full', random_state=1, n_init=3).fit(Z[tr])
    bic = g.bic(Z[tr])
    if best is None or bic < best[1]: best = (K, bic, g)
K, _, gmm = best
lab = gmm.predict(Z)
bar2cl = {int(idx[q]): int(lab[q]) for q in range(len(idx))}

# D backtest bu barlarda
DEV=85.; SL=90.; MHOLD=16; FEE=8.; SLIP=2.
def poc(i):
    s = C[i-40:i]; v = np.where(V[i-40:i] > 0, V[i-40:i], 1.); return float((s*v).sum()/v.sum())
trades = []; i = 42
while i < n-1:
    pc = poc(i); d = (C[i]-pc)/pc*1e4; side = 'LONG' if d <= -DEV else ('SHORT' if d >= DEV else None)
    if not side: i += 1; continue
    ent = C[i]; res = None; j = i
    for j in range(i+1, min(i+MHOLD+1, n)):
        cur = ((C[j]-ent) if side=='LONG' else (ent-C[j]))/ent*1e4; adv = ((H[j]-ent) if side=='SHORT' else (ent-L[j]))/ent*1e4
        if adv >= SL: res = -SL-FEE-2*SLIP; break
        pcj = poc(j) if j >= 42 else None; dj = (C[j]-pcj)/pcj*1e4 if pcj else None
        if dj is not None and ((side=='LONG' and dj>=0) or (side=='SHORT' and dj<=0)) and cur>=0: res = cur-FEE-2*SLIP; break
        if (j-i) >= MHOLD: res = cur-FEE-2*SLIP; break
    if res is None:
        cur = ((C[j]-ent) if side=='LONG' else (ent-C[j]))/ent*1e4; res = cur-FEE-2*SLIP
    if i in bar2cl: trades.append((i, bar2cl[i], res))
    i = j+1

print("=== GMM K=%d (BIC), feature: [ac1,ER,volclust,entropy | cvd,taker,oi,funding,imbalance] ===" % K)
print("bar=%d, D islem=%d, gun~%.0f" % (n, len(trades), n*15/1440))
print("cluster | TRAIN net win%% n | OOS net win%% n")
def pf(sub):
    if not sub: return (0,0,0)
    a = np.array([x[2] for x in sub]); return (a.sum(), 100*np.mean(a>0), len(a))
for cl in range(K):
    t = [x for x in trades if x[1]==cl and x[0]<split]; o = [x for x in trades if x[1]==cl and x[0]>=split]
    tn,tw,tc = pf(t); on,ow,oc = pf(o)
    print("  %d     | %+6.0f %3.0f %3d | %+6.0f %3.0f %3d" % (cl, tn, tw, tc, on, ow, oc))

# PERMUTATION: cluster-etiketlerini karistir, cluster-ortalama-getiri yayilimini (max-min) kiyasla
rr = np.array([x[2] for x in trades]); cl_arr = np.array([x[1] for x in trades])
def spread(labels):
    ms = [rr[labels==cl].mean() for cl in range(K) if (labels==cl).sum()>3]
    return max(ms)-min(ms) if len(ms)>1 else 0
actual = spread(cl_arr)
rng = np.random.RandomState(0); perm = [spread(rng.permutation(cl_arr)) for _ in range(2000)]
pval = np.mean([p >= actual for p in perm])
print()
print("PERMUTATION: cluster'lar arasi ort-getiri yayilimi=%.0f bps | rastgele-p=%.3f" % (actual, pval))
print("  (p<0.05 -> cluster ayrimi tesadufi degil; p buyuk -> spurious/gurultu)")
