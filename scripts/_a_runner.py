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
def eff(i,m=48):
    if i<m: return 1.0
    net=abs(C[i]-C[i-m]); tot=sum(abs(C[j]-C[j-1]) for j in range(i-m+1,i+1)); return net/tot if tot>0 else 1.0
# A girisi: S/R kenar (range filtreli, A'nin yaptigi). Cikis: ya sabit-TP ya RUNNER.
def run(exitmode,trail=40,sl=60,maxhold=24,N=24,edge=0.25,use_eff=True,lo=0,hi=None):
    hi=hi or n; i=N; tr=[]
    while i<hi-1:
        if i<lo: i+=1; continue
        hh=max(H[i-N:i]); ll=min(L[i-N:i]); rng=hh-ll
        if rng<=0: i+=1; continue
        if use_eff and eff(i)>=0.35: i+=1; continue
        pos=(C[i]-ll)/rng
        sig='LONG' if pos<=edge else ('SHORT' if pos>=1-edge else None)
        if not sig: i+=1; continue
        ent=C[i]; peak=ent; res=None
        for j in range(i+1,min(i+1+maxhold,n)):
            if sig=='LONG':
                if (ent-L[j])/ent*1e4>=sl: res=-sl; break
                if exitmode=='fix' and (H[j]-ent)/ent*1e4>=50: res=50; break
                if exitmode=='runner':
                    peak=max(peak,H[j])
                    if (peak-ent)/ent*1e4>0 and (peak-C[j])/ent*1e4>=trail: res=(C[j]-ent)/ent*1e4; break
            else:
                if (H[j]-ent)/ent*1e4>=sl: res=-sl; break
                if exitmode=='fix' and (ent-L[j])/ent*1e4>=50: res=50; break
                if exitmode=='runner':
                    peak=min(peak,L[j])
                    if (ent-peak)/ent*1e4>0 and (C[j]-peak)/ent*1e4>=trail: res=(ent-C[j])/ent*1e4; break
        if res is None:
            j=min(i+maxhold,n-1); res=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4
        tr.append(res-FEE); i=j+1
    return tr
def rep(lbl,**kw):
    tr=run(**kw); qs=[sum(run(lo=n*q//4,hi=n*(q+1)//4,**kw)) for q in range(4)]
    pos=sum(1 for x in qs if x>0)
    print('%-28s %3d islem | net %+7.0f | isabet %%%2.0f | ceyrek+:%d/4 [%s]'%(lbl,len(tr),sum(tr),100*sum(1 for x in tr if x>0)/len(tr) if tr else 0,pos,' '.join('%+.0f'%x for x in qs)))
print('=== A (S/R kenar) SABIT-TP vs RUNNER, 25 gun  [B=+873] ===')
rep('A fix-TP (eski test)',exitmode='fix')
rep('A runner trail40 sl60',exitmode='runner',trail=40,sl=60)
rep('A runner trail50 sl80',exitmode='runner',trail=50,sl=80)
rep('A runner trail60 sl100',exitmode='runner',trail=60,sl=100)
rep('A runner trail40 sl80 maxhold40',exitmode='runner',trail=40,sl=80,maxhold=40)
