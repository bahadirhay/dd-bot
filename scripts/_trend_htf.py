import sqlite3
from core.config import cfg
c=sqlite3.connect('file:%s?mode=ro'%cfg.DB_PATH,uri=True)
rows=[(ts,p) for ts,p in c.execute("SELECT ts,price FROM market_snapshots WHERE price>0 ORDER BY ts")]
def build(sec):
    b={}
    for ts,p in rows:
        x=int(ts//sec)
        if x not in b: b[x]={'h':p,'l':p,'c':p}
        else:
            d=b[x];d['h']=max(d['h'],p);d['l']=min(d['l'],p);d['c']=p
    k=sorted(b);return [b[x]['h'] for x in k],[b[x]['l'] for x in k],[b[x]['c'] for x in k]
FEE=8.;SLIP=2.
# tek-TF donchian/momentum trend-takip
def trend_tf(sec,N,kind,SL=120,TRAIL=80,lo=0,hi=None):
    H,L,C=build(sec);n=len(C);hi=hi or n;i=max(N,lo)+1;tr=[]
    while i<n-1:
        if i<lo:i+=1;continue
        if kind=='donch':
            hh=max(H[i-N:i]);ll=min(L[i-N:i]);sig='LONG' if C[i]>hh else ('SHORT' if C[i]<ll else None)
        else:
            r=(C[i]-C[i-N])/C[i-N]*1e4 if C[i-N]>0 else 0;sig='LONG' if r>150 else ('SHORT' if r<-150 else None)
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
def rep(lbl,sec,N,kind,**kw):
    n=len(build(sec)[2]);split=int(n*0.6)
    al=trend_tf(sec,N,kind,**kw);tr=trend_tf(sec,N,kind,hi=split,**kw);oo=trend_tf(sec,N,kind,lo=split,**kw)
    qs=[sum(trend_tf(sec,N,kind,lo=n*q//4,hi=n*(q+1)//4,**kw)) for q in range(4)];pos=sum(1 for x in qs if x>0)
    if not al:print('%-26s islem=0'%lbl);return
    print('%-26s net%+6.0f isl=%-3d isabet%%%.0f | TRAIN%+6.0f OOS%+6.0f cey%d/4'%(
        lbl,sum(al),len(al),100*sum(1 for x in al if x>0)/len(al),sum(tr),sum(oo),pos))
print('15m bar=%d 1h=%d 4h=%d'%(len(build(900)[2]),len(build(3600)[2]),len(build(14400)[2])))
print('\n=== 1h TREND ===')
rep('1h donchian N=10',3600,10,'donch')
rep('1h donchian N=20',3600,20,'donch')
rep('1h momentum N=24',3600,24,'mom')
print('\n=== 4h TREND ===')
rep('4h donchian N=5',14400,5,'donch')
rep('4h donchian N=10',14400,10,'donch')
rep('4h momentum N=6',14400,6,'mom')
print('\nKIYAS D(MR) +1718')
