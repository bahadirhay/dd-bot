import sqlite3,json,statistics
from core.config import cfg
c=sqlite3.connect('file:%s?mode=ro'%cfg.DB_PATH,uri=True)
bars={}
for ts,p,pj in c.execute("SELECT ts,price,payload_json FROM market_snapshots WHERE price>0 ORDER BY ts"):
    b=int(ts//900);fr=None
    if pj:
        try:fr=json.loads(pj).get('funding_rate')
        except:pass
    if b not in bars: bars[b]={'h':p,'l':p,'c':p,'fr':fr}
    else:
        o=bars[b];o['h']=max(o['h'],p);o['l']=min(o['l'],p);o['c']=p
        if fr is not None:o['fr']=fr
k=sorted(bars);H=[bars[x]['h'] for x in k];L=[bars[x]['l'] for x in k];C=[bars[x]['c'] for x in k];FR=[bars[x]['fr'] for x in k]
n=len(C);SL=60.;MH=16;FEE=8.;SLIP=2.
def zw(a,i,m):
    w=[a[j] for j in range(max(0,i-m),i) if a[j] is not None]
    if len(w)<m//2 or a[i] is None: return None
    mu=statistics.mean(w);sd=statistics.pstdev(w);return (a[i]-mu)/sd if sd>0 else None
MOM=[((C[i]-C[i-16])/C[i-16]) if i>=16 and C[i-16]>0 else None for i in range(n)];PZ=[zw(C,i,32) for i in range(n)]
def stretch(i):
    p=[v for v in (zw(FR,i,96),zw(MOM,i,96),PZ[i]) if v is not None];return sum(p)/len(p) if p else None
def er(i,N=20):
    if i<N:return None
    net=abs(C[i]-C[i-N]);path=sum(abs(C[j]-C[j-1]) for j in range(i-N+1,i+1));return net/path if path>0 else None
def run(er_gate=0,T=1.2,coh=0.5,lo=0,hi=None):
    hi=hi or n;i=96;tr=[]
    while i<hi-1:
        if i<lo:i+=1;continue
        st=stretch(i);sig='SHORT' if (st and st>=T) else ('LONG' if (st and st<=-T) else None)
        if not sig:i+=1;continue
        mc=(C[i]-C[i-96])/C[i-96]*1e4 if C[i-96]>0 else 0
        if (sig=='SHORT' and mc>150) or (sig=='LONG' and mc<-150):i+=1;continue
        if coh>0 and PZ[i] is not None and ((sig=='LONG' and PZ[i]>-coh) or (sig=='SHORT' and PZ[i]<coh)):i+=1;continue
        if er_gate>0:
            e=er(i)
            if e is not None and e>=er_gate:i+=1;continue
        ent=C[i];j=i;res=None
        for j in range(i+1,min(i+MH+1,n)):
            cur=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4;adv=((H[j]-ent) if sig=='SHORT' else (ent-L[j]))/ent*1e4
            if adv>=SL:res=-SL-FEE-2*SLIP;break
            rev=(sig=='LONG' and PZ[j] is not None and PZ[j]>=0) or (sig=='SHORT' and PZ[j] is not None and PZ[j]<=0)
            if rev and cur>=0:res=cur-FEE-2*SLIP;break
            if (j-i)>=MH:res=cur-FEE-2*SLIP;break
        if res is None:res=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4-FEE-2*SLIP
        tr.append(res);i=j+1
    return tr
split=int(n*0.6)
def rep(lbl,**kw):
    al=run(**kw);tr=run(hi=split,**kw);oo=run(lo=split,**kw)
    qs=[sum(run(lo=n*q//4,hi=n*(q+1)//4,**kw)) for q in range(4)];pos=sum(1 for x in qs if x>0)
    if not al:print('%-26s islem=0'%lbl);return
    gp=sum(x for x in al if x>0);gl=sum(x for x in al if x<=0)
    print('%-26s net%+6.0f isl=%-3d | KAZ%+6.0f/KAYIP%+6.0f | TRAIN%+6.0f OOS%+6.0f cey%d/4'%(
        lbl,sum(al),len(al),gp,gl,sum(tr),sum(oo),pos))
print('\n=== B gelistirme (gercek fee8+slip2, WF) ===')
rep('B mevcut (coh0.5)',er_gate=0)
rep('B + ER0.50 (trendde dur)',er_gate=0.50)
rep('B + ER0.45',er_gate=0.45)
rep('B + ER0.55',er_gate=0.55)
rep('B esik T1.0 (daha cok islem)',T=1.0,er_gate=0.50)
print('\nKIYAS: D + ER0.50 ~+1718')
