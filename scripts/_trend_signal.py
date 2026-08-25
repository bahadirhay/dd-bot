import sqlite3,json
from core.config import cfg
c=sqlite3.connect('file:%s?mode=ro'%cfg.DB_PATH,uri=True)
# 15m bar: close + botun trend etiketi (structure_15m) + regime (bar kapanistaki son deger)
bars={}
for ts,p,pj in c.execute("SELECT ts,price,payload_json FROM market_snapshots WHERE price>0 ORDER BY ts"):
    b=int(ts//900);st=rg=None
    if pj:
        try:
            d=json.loads(pj);st=d.get('structure_15m');rg=d.get('regime')
        except:pass
    if b not in bars: bars[b]={'h':p,'l':p,'c':p,'st':st,'rg':rg}
    else:
        o=bars[b];o['h']=max(o['h'],p);o['l']=min(o['l'],p);o['c']=p
        if st is not None:o['st']=st
        if rg is not None:o['rg']=rg
k=sorted(bars);H=[bars[x]['h'] for x in k];L=[bars[x]['l'] for x in k];C=[bars[x]['c'] for x in k];ST=[bars[x]['st'] for x in k];RG=[bars[x]['rg'] for x in k]
n=len(C);FEE=8.;SLIP=2.
print('15m bar=%d'%n)
# TREND-TAKIP: botun structure_15m yonunde gir, yon donunce cik (veya SL).
def run(SL,only_trend_regime=False,lo=0,hi=None):
    hi=hi or n;i=max(2,lo);tr=[]
    while i<hi-1:
        if i<lo:i+=1;continue
        st=ST[i]
        sig='LONG' if st=='UP' else ('SHORT' if st=='DOWN' else None)
        if not sig:i+=1;continue
        if only_trend_regime and RG[i]!='TREND':i+=1;continue
        ent=C[i];j=i;res=None
        for j in range(i+1,n):
            cur=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4
            adv=((H[j]-ent) if sig=='SHORT' else (ent-L[j]))/ent*1e4
            if adv>=SL:res=-SL-FEE-2*SLIP;break
            # trend yon degisti -> cik
            stj=ST[j]
            if (sig=='LONG' and stj=='DOWN') or (sig=='SHORT' and stj=='UP'):
                res=cur-FEE-2*SLIP;break
        if res is None:
            res=((C[min(j,n-1)]-ent) if sig=='LONG' else (ent-C[min(j,n-1)]))/ent*1e4-FEE-2*SLIP
        tr.append(res);i=j+1
    return tr
split=int(n*0.6)
def rep(lbl,**kw):
    al=run(lo=0,hi=None,**kw);tr=run(hi=split,**kw);oo=run(lo=split,**kw)
    qs=[sum(run(lo=n*q//4,hi=n*(q+1)//4,**kw)) for q in range(4)];pos=sum(1 for x in qs if x>0)
    if not al:print('%-32s islem=0'%lbl);return
    print('%-32s net%+6.0f isl=%-4d /isl%+5.1f isabet%%%.0f | TRAIN%+6.0f OOS%+6.0f cey%d/4'%(
        lbl,sum(al),len(al),sum(al)/len(al),100*sum(1 for x in al if x>0)/len(al),sum(tr),sum(oo),pos))
print('\n=== BOTUN KENDI TREND YONUYLE ISLEM (fee8+slip2, WF) ===')
print('(structure_15m: UP->long DOWN->short, yon donunce cik)')
rep('trend-takip SL60',SL=60)
rep('trend-takip SL90',SL=90)
rep('trend-takip SL120',SL=120)
rep('trend-takip SL90 (sadece regime=TREND)',SL=90,only_trend_regime=True)
print('\nKARSILASTIRMA: D (POC mean-revert) +1762..+1863')
