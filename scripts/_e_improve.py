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
H1,L1,C1=build(3600);H4,L4,C4=build(14400)
n=len(C1);FEE=8.;SLIP=2.;N=24;THR=150.;SL=120.;TRAIL=80.
def er(C,i,M):
    if i<M:return None
    net=abs(C[i]-C[i-M]);path=sum(abs(C[j]-C[j-1]) for j in range(i-M+1,i+1));return net/path if path>0 else None
def dir4_at(ts1_idx):
    # 1h bar idx -> hangi 4h bara denk? yaklasik: 4h = 4x1h
    i4=ts1_idx//4
    if i4<6 or i4>=len(C4) or C4[i4-6]<=0:return 0
    r=(C4[i4]-C4[i4-6])/C4[i4-6]*1e4;return 1 if r>0 else (-1 if r<0 else 0)
def run(mode,er_thr=0,lo=0,hi=None):
    hi=hi or n;i=max(N,lo)+1;tr=[];slc=0
    while i<n-1:
        if i<lo:i+=1;continue
        if C1[i-N]<=0:i+=1;continue
        r=(C1[i]-C1[i-N])/C1[i-N]*1e4;sig='LONG' if r>THR else ('SHORT' if r<-THR else None)
        if not sig:i+=1;continue
        if mode=='er' or mode=='both':
            e=er(C1,i,N)
            if e is None or e<er_thr: i+=1;continue   # sadece TEMIZ trend (yuksek ER)
        if mode=='4h' or mode=='both':
            d4=dir4_at(i)
            if (sig=='LONG' and d4!=1) or (sig=='SHORT' and d4!=-1): i+=1;continue  # 4h teyit
        ent=C1[i];j=i;res=None;peak=ent
        for j in range(i+1,min(i+N*3,n)):
            cur=((C1[j]-ent) if sig=='LONG' else (ent-C1[j]))/ent*1e4
            adv=((H1[j]-ent) if sig=='SHORT' else (ent-L1[j]))/ent*1e4
            if adv>=SL:res=-SL-FEE-2*SLIP;slc+=1;break
            if sig=='LONG':peak=max(peak,C1[j]);rt=(peak-C1[j])/ent*1e4
            else:peak=min(peak,C1[j]);rt=(C1[j]-peak)/ent*1e4
            if rt>=TRAIL:res=cur-FEE-2*SLIP;break
        if res is None:res=((C1[min(j,n-1)]-ent) if sig=='LONG' else (ent-C1[min(j,n-1)]))/ent*1e4-FEE-2*SLIP
        tr.append(res);i=j+1
    return tr,slc
split=int(n*0.6)
def rep(lbl,**kw):
    al,sl=run(**kw);tr,_=run(hi=split,**kw);oo,_=run(lo=split,**kw)
    qs=[sum(run(lo=n*q//4,hi=n*(q+1)//4,**kw)[0]) for q in range(4)];pos=sum(1 for x in qs if x>0)
    if not al:print('%-26s islem=0'%lbl);return
    print('%-26s net%+6.0f isl=%-3d SL=%-2d isabet%%%.0f | TRAIN%+6.0f OOS%+6.0f cey%d/4'%(
        lbl,sum(al),len(al),sl,100*sum(1 for x in al if x>0)/len(al),sum(tr),sum(oo),pos))
print('1h=%d 4h=%d'%(n,len(C4)))
print('\n=== E gelistirme: temiz-trend filtresi (fee8+slip2, WF) ===')
rep('E mevcut (filtresiz)',mode='none')
print('--- ER filtresi (sadece temiz/guclu trend) ---')
for t in (0.30,0.40,0.50,0.60):
    rep('E + ER>=%.2f'%t,mode='er',er_thr=t)
print('--- 4h teyit (1h+4h ayni yon) ---')
rep('E + 4h teyit',mode='4h')
rep('E + ER0.40 + 4h',mode='both',er_thr=0.40)
