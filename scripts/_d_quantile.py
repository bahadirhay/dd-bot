import sqlite3,json,bisect,statistics
from core.config import cfg
c=sqlite3.connect('file:%s?mode=ro'%cfg.DB_PATH,uri=True)
snaps=[]
for ts,p,pj in c.execute("SELECT ts,price,payload_json FROM market_snapshots WHERE price>0 ORDER BY ts"):
    kv=None
    if pj:
        try:
            fm=json.loads(pj).get('forming_15m')
            if isinstance(fm,dict): kv=fm.get('volume')
        except: pass
    snaps.append((ts,p,kv))
print('snapshot=%d'%len(snaps))
bars={}
for ts,p,kv in snaps:
    b=int(ts//900)
    if b not in bars: bars[b]={'ts':b*900,'h':p,'l':p,'c':p,'v':kv}
    else:
        o=bars[b];o['h']=max(o['h'],p);o['l']=min(o['l'],p);o['c']=p
        if kv is not None:o['v']=kv
k=sorted(bars);H=[bars[x]['h'] for x in k];L=[bars[x]['l'] for x in k];C=[bars[x]['c'] for x in k];V=[bars[x]['v'] for x in k]
n=len(C);SL=60.;MH=16;FEE=8.;SLIP=2.
def poc(i,M=40):
    nu=de=0.
    for j in range(i-M,i):
        v=V[j] if (V[j] and V[j]>0) else 1.;nu+=C[j]*v;de+=v
    return nu/de if de>0 else None
# her bar icin dev serisi (POC sapmasi, bps)
DEV=[None]*n
for i in range(40,n):
    pc=poc(i)
    if pc: DEV[i]=(C[i]-pc)/pc*1e4
def pctl(vals,q):
    if not vals: return None
    s=sorted(vals);idx=min(len(s)-1,int(q/100*len(s)))
    return s[idx]
# threshold_fn(i)->esik(bps). fixed: sabit; quantile: son W barin |dev| q-yuzdeligi
def run_fixed(THR,lo=0,hi=None):
    return _run(lambda i: THR,lo,hi)
def run_quant(Q,W=96,lo=0,hi=None):
    def thr(i):
        w=[abs(DEV[j]) for j in range(max(40,i-W),i) if DEV[j] is not None]
        return pctl(w,Q) if len(w)>=W//2 else None
    return _run(thr,lo,hi)
def _run(thr_fn,lo,hi):
    hi=hi or n;i=max(40,lo);tr=[]
    while i<hi-1:
        if i<lo or DEV[i] is None:i+=1;continue
        th=thr_fn(i)
        if th is None:i+=1;continue
        dev=DEV[i];sig='LONG' if dev<=-th else ('SHORT' if dev>=th else None)
        if not sig:i+=1;continue
        ent=C[i];j=i;res=None
        for j in range(i+1,min(i+MH+1,n)):
            cur=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4
            adv=((H[j]-ent) if sig=='SHORT' else (ent-L[j]))/ent*1e4
            if adv>=SL:res=-SL-FEE-2*SLIP;break
            dj=DEV[j]
            if dj is not None and ((sig=='LONG' and dj>=0) or (sig=='SHORT' and dj<=0)) and cur>=0:res=cur-FEE-2*SLIP;break
            if (j-i)>=MH:res=cur-FEE-2*SLIP;break
        if res is None:res=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4-FEE-2*SLIP
        tr.append(res);i=j+1
    return tr
split=int(n*0.6)
def rep(lbl,fn):
    al=fn();tr=fn(hi=split);oo=fn(lo=split)
    qs=[sum(fn(lo=n*q//4,hi=n*(q+1)//4)) for q in range(4)];pos=sum(1 for x in qs if x>0)
    if not al:print('%-22s islem=0'%lbl);return
    # train vs oos tutarlilik: islem-basi
    tpb=sum(tr)/len(tr) if tr else 0;opb=sum(oo)/len(oo) if oo else 0
    print('%-22s net%+6.0f isl=%-4d /isl%+5.1f | TRAIN/isl%+5.1f OOS/isl%+5.1f cey%d/4'%(
        lbl,sum(al),len(al),sum(al)/len(al),tpb,opb,pos))
print('\n=== SABIT vs QUANTILE-ADAPTIF (15m, fee8+slip2, walk-forward) ===')
print('--- SABIT esik ---')
for t in (75,85,95): rep('fixed DEV%d'%t,lambda lo=0,hi=None,t=t:run_fixed(t,lo,hi))
print('--- QUANTILE-adaptif (son 24h |dev| yuzdeligi) ---')
for q in (80,85,90,93): rep('quantile P%d'%q,lambda lo=0,hi=None,q=q:run_quant(q,96,lo,hi))
print('\n(global hedef: TRAIN/isl ~ OOS/isl ise rejim-bagimsiz daha tutarli)')
