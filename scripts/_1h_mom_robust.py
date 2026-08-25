import sqlite3
from core.config import cfg
c=sqlite3.connect('file:%s?mode=ro'%cfg.DB_PATH,uri=True)
rows=[(ts,p) for ts,p in c.execute("SELECT ts,price FROM market_snapshots WHERE price>0 ORDER BY ts")]
b={}
for ts,p in rows:
    x=int(ts//3600)
    if x not in b: b[x]={'h':p,'l':p,'c':p}
    else:
        d=b[x];d['h']=max(d['h'],p);d['l']=min(d['l'],p);d['c']=p
k=sorted(b);H=[b[x]['h'] for x in k];L=[b[x]['l'] for x in k];C=[b[x]['c'] for x in k]
n=len(C);FEE=8.;SLIP=2.
print('1h bar=%d (~%.0f gun)'%(n,n/24))
def run(N,THR,SL=120,TRAIL=80,lo=0,hi=None):
    hi=hi or n;i=max(N,lo)+1;tr=[]
    while i<n-1:
        if i<lo:i+=1;continue
        if C[i-N]<=0:i+=1;continue
        r=(C[i]-C[i-N])/C[i-N]*1e4;sig='LONG' if r>THR else ('SHORT' if r<-THR else None)
        if not sig:i+=1;continue
        ent=C[i];j=i;res=None;peak=ent
        for j in range(i+1,min(i+N*3,n)):
            cur=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4
            adv=((H[j]-ent) if sig=='SHORT' else (ent-L[j]))/ent*1e4
            if adv>=SL:res=-SL-FEE-2*SLIP;break
            if sig=='LONG':peak=max(peak,C[j]);rt=(peak-C[j])/ent*1e4
            else:peak=min(peak,C[j]);rt=(C[j]-peak)/ent*1e4
            if rt>=TRAIL:res=cur-FEE-2*SLIP;break
        if res is None:res=((C[min(j,n-1)]-ent) if sig=='LONG' else (ent-C[min(j,n-1)]))/ent*1e4-FEE-2*SLIP
        tr.append(res);i=j+1
    return tr
split=int(n*0.6)
def rep(N,THR):
    al=run(N,THR);tr=run(N,THR,hi=split);oo=run(N,THR,lo=split)
    qs=[sum(run(N,THR,lo=n*q//4,hi=n*(q+1)//4)) for q in range(4)];pos=sum(1 for x in qs if x>0)
    if not al:print('  N=%d THR=%d islem=0'%(N,THR));return
    print('  N=%-2d THR=%-3d net%+6.0f isl=%-3d isabet%%%.0f | TRAIN%+6.0f OOS%+6.0f cey%d/4'%(
        N,THR,sum(al),len(al),100*sum(1 for x in al if x>0)/len(al),sum(tr),sum(oo),pos))
print('\n=== 1h MOMENTUM KOMSULUK + WALK-FORWARD (fee8+slip2) ===')
for THR in (100,150,200):
    print('--- esik %d bps ---'%THR)
    for N in (12,18,24,30,36,48):
        rep(N,THR)
