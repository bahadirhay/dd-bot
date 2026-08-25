import sqlite3,statistics
from core.config import cfg
c=sqlite3.connect('file:%s?mode=ro'%cfg.DB_PATH,uri=True)
bars={}
for ts,p in c.execute('SELECT ts,price FROM market_snapshots WHERE price>0 ORDER BY ts'):
    b=int(ts//900)
    if b not in bars: bars[b]=[p,p,p]
    else:
        o=bars[b]; o[0]=max(o[0],p);o[1]=min(o[1],p);o[2]=p
keys=sorted(bars); H=[bars[k][0] for k in keys]; L=[bars[k][1] for k in keys]; C=[bars[k][2] for k in keys]
n=len(keys); FEE=3.0
def zsc(i,m=24):
    if i<m: return None
    w=C[i-m:i]; mu=statistics.mean(w); sd=statistics.pstdev(w); return (C[i]-mu)/sd if sd>0 else None
def macro(i):
    return (C[i]-C[i-96])/C[i-96]*1e4 if i>96 and C[i-96]>0 else 0.0
def at_low_bounce(i,N=24,K=2):
    # gercek destek: son N-bar dibine yakin + son K barda yeni dip yok (bounce-confirm)
    if i<N+2: return False
    ll=min(L[i-N:i]); near = (C[i]-ll)/C[i]*1e4 <= 50
    recentlow=min(L[i-K:i+1]); prevlow=min(L[i-K-2:i-K]); held = recentlow>=prevlow-0.0003*recentlow
    return near and held
def run(mode,k=2.0,sl=60,maxhold=16,lo=0,hi=None):
    # mode: 'strict' (makro), 'relax' (makro VEYA dip-bounce)
    hi=hi or n; i=96; tr=[]; longs=[]
    while i<hi-1:
        if i<lo: i+=1; continue
        z=zsc(i)
        if z is None: i+=1; continue
        sig='LONG' if z<=-k else ('SHORT' if z>=k else None)
        if not sig: i+=1; continue
        mc=macro(i)
        if sig=='SHORT' and mc>150: i+=1; continue
        if sig=='LONG':
            ok = mc>=-150
            if not ok and mode=='relax': ok = at_low_bounce(i)
            if not ok: i+=1; continue
        ent=C[i]; res=None
        for j in range(i+1,min(i+1+maxhold,n)):
            if sig=='SHORT' and (H[j]-ent)/ent*1e4>=sl: res=-sl;break
            if sig=='LONG' and (ent-L[j])/ent*1e4>=sl: res=-sl;break
            zj=zsc(j)
            if zj is not None and ((sig=='SHORT' and zj<=0) or (sig=='LONG' and zj>=0)):
                res=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4;break
        if res is None:
            j=min(i+maxhold,n-1); res=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4
        tr.append(res-FEE)
        if sig=='LONG': longs.append(res-FEE)
        i=j+1
    return tr,longs
def rep(lbl,mode):
    tr,lg=run(mode); qs=[sum(run(mode,lo=n*q//4,hi=n*(q+1)//4)[0]) for q in range(4)]
    pos=sum(1 for x in qs if x>0)
    lnet=sum(lg); lwr=100*sum(1 for x in lg if x>0)/len(lg) if lg else 0
    print('%-8s TUM %3d net %+7.0f wr%%%2.0f cey+:%d/4 [%s] | LONG %d net %+.0f wr%%%2.0f'%(
        lbl,len(tr),sum(tr),100*sum(1 for x in tr if x>0)/len(tr) if tr else 0,pos,' '.join('%+.0f'%x for x in qs),len(lg),lnet,lwr))
print('=== KATI vs GEVSEK makro (z-score MR), 25 gun ===')
rep('STRICT',mode='strict')
rep('RELAX',mode='relax')
