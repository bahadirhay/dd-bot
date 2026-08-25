import sqlite3,json
from core.config import cfg
c=sqlite3.connect('file:%s?mode=ro'%cfg.DB_PATH,uri=True)
# 1h bar: close + funding + oi + cvd_raw (bar kapanistaki son)
b={}
for ts,p,pj in c.execute("SELECT ts,price,payload_json FROM market_snapshots WHERE price>0 ORDER BY ts"):
    x=int(ts//3600);fr=oi=cv=None
    if pj:
        try:
            d=json.loads(pj);fr=d.get('funding_rate');oi=d.get('oi');cv=d.get('cvd_raw')
        except:pass
    if x not in b: b[x]={'h':p,'l':p,'c':p,'fr':fr,'oi':oi,'cv':cv}
    else:
        o=b[x];o['h']=max(o['h'],p);o['l']=min(o['l'],p);o['c']=p
        if fr is not None:o['fr']=fr
        if oi is not None:o['oi']=oi
        if cv is not None:o['cv']=cv
k=sorted(b);H=[b[x]['h'] for x in k];L=[b[x]['l'] for x in k];C=[b[x]['c'] for x in k];FR=[b[x]['fr'] for x in k];OI=[b[x]['oi'] for x in k];CV=[b[x]['cv'] for x in k]
n=len(C);FEE=8.;SLIP=2.;N=24;THR=150.;SL=120.;TRAIL=80.
def run(mode,lo=0,hi=None):
    hi=hi or n;i=max(N,lo)+1;tr=[]
    while i<n-1:
        if i<lo:i+=1;continue
        if C[i-N]<=0:i+=1;continue
        r=(C[i]-C[i-N])/C[i-N]*1e4;sig='LONG' if r>THR else ('SHORT' if r<-THR else None)
        if not sig:i+=1;continue
        # teyit filtreleri
        ok=True
        if mode in ('oi','all'):
            # OI yukseliyor mu (son 6 bar) = konviksiyon
            if OI[i] is not None and OI[i-6] is not None:
                ok = ok and (OI[i]>OI[i-6])  # OI artisi = trend devam konviksiyonu
            else: ok=False
        if mode in ('cvd','all'):
            # CVD yonu sinyalle uyumlu mu (son 6 bar delta)
            if CV[i] is not None and CV[i-6] is not None:
                cd=CV[i]-CV[i-6]
                ok = ok and ((sig=='LONG' and cd>0) or (sig=='SHORT' and cd<0))
            else: ok=False
        if mode in ('fund','all'):
            # funding asiri DEGIL (asiri=donus riski); |funding| dusuk
            if FR[i] is not None:
                ok = ok and (abs(FR[i])<0.0003)
            else: ok=False
        if not ok: i+=1;continue
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
def rep(lbl,mode):
    al=run(mode);tr=run(mode,hi=split);oo=run(mode,lo=split)
    qs=[sum(run(mode,lo=n*q//4,hi=n*(q+1)//4)) for q in range(4)];pos=sum(1 for x in qs if x>0)
    if not al:print('%-22s islem=0'%lbl);return
    print('%-22s net%+6.0f isl=%-3d isabet%%%.0f | TRAIN%+6.0f OOS%+6.0f cey%d/4'%(
        lbl,sum(al),len(al),100*sum(1 for x in al if x>0)/len(al),sum(tr),sum(oo),pos))
print('1h bar=%d (fr/oi/cv dolu: %d/%d/%d)'%(n,sum(x is not None for x in FR),sum(x is not None for x in OI),sum(x is not None for x in CV)))
print('\n=== E + order-flow teyit (fee8+slip2, WF) ===')
rep('E mevcut (teyitsiz)','none')
rep('E + OI-yukseliyor','oi')
rep('E + CVD-uyumlu','cvd')
rep('E + funding-asiri-degil','fund')
rep('E + HEPSI (oi+cvd+fund)','all')
