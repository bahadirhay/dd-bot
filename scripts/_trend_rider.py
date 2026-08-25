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
def run(N,trail,sl=80,maxhold=40,trend_min=None,lo=0,hi=None):
    hi=hi or n; i=N; tr=[]
    while i<hi-1:
        if i<lo: i+=1; continue
        hh=max(H[i-N:i]); ll=min(L[i-N:i])
        sig='LONG' if C[i]>hh else ('SHORT' if C[i]<ll else None)
        if not sig: i+=1; continue
        if trend_min is not None and eff(i)<trend_min: i+=1; continue  # yalniz trend rejimi
        ent=C[i]; peak=ent; res=None
        for j in range(i+1,min(i+1+maxhold,n)):
            if sig=='LONG':
                peak=max(peak,H[j])
                if (ent-L[j])/ent*1e4>=sl: res=-sl; break
                if (peak-C[j])/ent*1e4>=trail and (peak-ent)/ent*1e4>0: res=(C[j]-ent)/ent*1e4; break
            else:
                peak=min(peak,L[j])
                if (H[j]-ent)/ent*1e4>=sl: res=-sl; break
                if (C[j]-peak)/ent*1e4>=trail and (ent-peak)/ent*1e4>0: res=(ent-C[j])/ent*1e4; break
        if res is None:
            j=min(i+maxhold,n-1); res=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4
        tr.append(res-FEE); i=j+1
    return tr
def rep(lbl,**kw):
    tr=run(**kw); qs=[sum(run(lo=n*q//4,hi=n*(q+1)//4,**kw)) for q in range(4)]
    pos=sum(1 for x in qs if x>0)
    print('%-30s %3d islem | net %+7.0f | isabet %%%2.0f | ceyrek+:%d/4 [%s]'%(lbl,len(tr),sum(tr),100*sum(1 for x in tr if x>0)/len(tr) if tr else 0,pos,' '.join('%+.0f'%x for x in qs)))
print('=== TREND-RIDER (Donchian kirilim + trailing), 25 gun  [B=+873 ref] ===')
rep('N24 trail40 sl80',N=24,trail=40,sl=80)
rep('N24 trail50 sl80',N=24,trail=50,sl=80)
rep('N24 trail40 +trend>0.40',N=24,trail=40,sl=80,trend_min=0.40)
rep('N16 trail40 +trend>0.40',N=16,trail=40,sl=80,trend_min=0.40)
rep('N32 trail50 +trend>0.45',N=32,trail=50,sl=80,trend_min=0.45)
rep('N24 trail30 sl60',N=24,trail=30,sl=60)
