import sqlite3,json,statistics
from core.config import cfg
c=sqlite3.connect('file:%s?mode=ro'%cfg.DB_PATH,uri=True)
bars={}
for ts,p,pj in c.execute("SELECT ts,price,payload_json FROM market_snapshots WHERE price>0 ORDER BY ts"):
    b=int(ts//900); fr=None
    if pj:
        try: fr=json.loads(pj).get('funding_rate')
        except Exception: pass
    if b not in bars: bars[b]={'h':p,'l':p,'c':p,'fr':fr}
    else:
        d=bars[b]; d['h']=max(d['h'],p); d['l']=min(d['l'],p); d['c']=p
        if fr is not None: d['fr']=fr
keys=sorted(bars)
H=[bars[k]['h'] for k in keys]; L=[bars[k]['l'] for k in keys]; C=[bars[k]['c'] for k in keys]; FR=[bars[k]['fr'] for k in keys]
n=len(keys); FEE=3.0; SL=60.0; MH=16
def zw(a,i,m):
    w=[a[j] for j in range(max(0,i-m),i) if a[j] is not None]
    if len(w)<m//2 or a[i] is None: return None
    mu=statistics.mean(w); sd=statistics.pstdev(w); return (a[i]-mu)/sd if sd>0 else None
MOM=[((C[i]-C[i-16])/C[i-16]) if i>=16 and C[i-16]>0 else None for i in range(n)]
PZ=[zw(C,i,32) for i in range(n)]
def stretch(i):
    parts=[v for v in (zw(FR,i,96),zw(MOM,i,96),PZ[i]) if v is not None]
    return sum(parts)/len(parts) if parts else None

def run(coh,lo=0,hi=None):
    hi=hi or n; i=96; tr=[]; churn=0
    while i<hi-1:
        if i<lo: i+=1; continue
        st=stretch(i)
        sig='SHORT' if (st is not None and st>=1.2) else ('LONG' if (st is not None and st<=-1.2) else None)
        if not sig: i+=1; continue
        mc=(C[i]-C[i-96])/C[i-96]*1e4 if C[i-96]>0 else 0
        if sig=='SHORT' and mc>150: i+=1; continue
        if sig=='LONG' and mc<-150: i+=1; continue
        if coh>0 and PZ[i] is not None:   # GIRIS-TUTARLILIK: fiyat-z yonu teyit etsin
            if sig=='LONG' and PZ[i]>-coh: i+=1; continue
            if sig=='SHORT' and PZ[i]<coh: i+=1; continue
        ent=C[i]; j=i; res=None
        for j in range(i+1,min(i+MH+1,n)):
            cur=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4
            adv=((H[j]-ent) if sig=='SHORT' else (ent-L[j]))/ent*1e4
            if adv>=SL: res=-SL-FEE; break
            rev=(sig=='LONG' and PZ[j] is not None and PZ[j]>=0) or (sig=='SHORT' and PZ[j] is not None and PZ[j]<=0)
            if (j-i)>=MH and not rev: res=cur-FEE; break
            if rev and cur>=0:
                res=cur-FEE
                if (j-i)<=1: churn+=1
                break
        if res is None:
            cur=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4; res=cur-FEE
        tr.append(res); i=j+1
    return tr,churn

def rep(lbl,coh):
    tr,ch=run(coh)
    qs=[sum(run(coh,lo=n*q//4,hi=n*(q+1)//4)[0]) for q in range(4)]; pos=sum(1 for x in qs if x>0)
    split=int(n*0.6); oos=sum(run(coh,lo=split)[0])
    print('%-22s isl=%-3d net %+6.0f isabet %%%.0f OOS %+6.0f cey%d/4 | churn(<=1bar)=%d'%(
        lbl,len(tr),sum(tr),100*sum(1 for x in tr if x>0)/len(tr),oos,pos,ch))

print('=== B giris-tutarlilik kapisi (fiyat-z teyidi) — churn vs net ===')
for lbl,coh in (('MEVCUT (kapi yok)',0),('coh 0.3',0.3),('coh 0.5',0.5),('coh 0.8',0.8),('coh 1.0',1.0)):
    rep(lbl,coh)
