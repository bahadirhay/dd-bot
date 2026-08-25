import sqlite3
from core.config import cfg
c=sqlite3.connect('file:%s?mode=ro'%cfg.DB_PATH,uri=True)
rows=[(ts,p) for ts,p in c.execute("SELECT ts,price FROM market_snapshots WHERE price>0 ORDER BY ts")]
def build(sec):
    b={}
    for ts,p in rows:
        x=int(ts//sec)
        if x not in b:b[x]={'h':p,'l':p,'c':p}
        else:
            d=b[x];d['h']=max(d['h'],p);d['l']=min(d['l'],p);d['c']=p
    k=sorted(b);return [b[x]['h'] for x in k],[b[x]['l'] for x in k],[b[x]['c'] for x in k]
H,L,C=build(900)  # 15m
H1,L1,C1=build(3600)  # 1h trend bias
n=len(C);FEE=8.;SLIP=2.
def trend1h(ts15_idx):
    i1=ts15_idx//4
    if i1<24 or i1>=len(C1) or C1[i1-24]<=0: return 0
    r=(C1[i1]-C1[i1-24])/C1[i1-24]*1e4
    return 1 if r>150 else (-1 if r<-150 else 0)
# pullback: downtrend'de fiyat son P barin DIBINDEN PB bps yukari teptiyse -> SHORT (rally sat)
#           uptrend'de fiyat son P barin TEPESINDEN PB bps asagi cektiyse -> LONG (dip al)
def run(P,PB,SL=120,TRAIL=80,lo=0,hi=None):
    hi=hi or n;i=max(P,lo)+1;tr=[]
    while i<n-1:
        if i<lo:i+=1;continue
        t=trend1h(i)
        if t==0:i+=1;continue
        sig=None
        if t==-1:  # downtrend -> rally'yi bekle, sat
            lo_=min(L[i-P:i]); bounce=(C[i]-lo_)/lo_*1e4
            if bounce>=PB: sig='SHORT'
        else:  # uptrend -> dip'i bekle, al
            hi_=max(H[i-P:i]); dipb=(hi_-C[i])/hi_*1e4
            if dipb>=PB: sig='LONG'
        if not sig:i+=1;continue
        ent=C[i];j=i;res=None;peak=ent
        for j in range(i+1,min(i+P*3,n)):
            cur=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4
            adv=((H[j]-ent) if sig=='SHORT' else (ent-L[j]))/ent*1e4
            if adv>=SL:res=-SL-FEE-2*SLIP;break
            if sig=='LONG':peak=max(peak,C[j]);rt=(peak-C[j])/ent*1e4
            else:peak=min(peak,C[j]);rt=(C[j]-peak)/ent*1e4
            if rt>=TRAIL:res=cur-FEE-2*SLIP;break
            if trend1h(j)==0 or (sig=='SHORT' and trend1h(j)==1) or (sig=='LONG' and trend1h(j)==-1):
                res=cur-FEE-2*SLIP;break  # trend bozuldu -> cik
        if res is None:res=((C[min(j,n-1)]-ent) if sig=='LONG' else (ent-C[min(j,n-1)]))/ent*1e4-FEE-2*SLIP
        tr.append(res);i=j+1
    return tr
split=int(n*0.6)
def rep(lbl,**kw):
    al=run(**kw);tr=run(hi=split,**kw);oo=run(lo=split,**kw)
    qs=[sum(run(lo=n*q//4,hi=n*(q+1)//4,**kw)) for q in range(4)];pos=sum(1 for x in qs if x>0)
    if not al:print('%-26s islem=0'%lbl);return
    print('%-26s net%+6.0f isl=%-3d isabet%%%.0f | TRAIN%+6.0f OOS%+6.0f cey%d/4'%(
        lbl,sum(al),len(al),100*sum(1 for x in al if x>0)/len(al),sum(tr),sum(oo),pos))
print('=== PULLBACK trend-takip (1h-trend + 15m pullback giris) fee8+slip2 WF ===')
print('(E rally/dip BEKLER sonra trendle gider; E dibi shortlamaz)')
rep('pullback P8 PB30',P=8,PB=30)
rep('pullback P8 PB50',P=8,PB=50)
rep('pullback P12 PB40',P=12,PB=40)
rep('pullback P16 PB60',P=16,PB=60)
rep('pullback P8 PB80',P=8,PB=80)
print('\nKIYAS: E(momentum-chase) canli -658, D(MR) +1.261')
