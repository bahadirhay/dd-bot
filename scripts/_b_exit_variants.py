import sqlite3,json,statistics
from core.config import cfg
c=sqlite3.connect('file:%s?mode=ro'%cfg.DB_PATH,uri=True)
bars={}
for ts,p,pj in c.execute("SELECT ts,price,payload_json FROM market_snapshots WHERE price>0 ORDER BY ts"):
    b=int(ts//900); fr=None
    if pj:
        try: fr=json.loads(pj).get('funding_rate')
        except Exception: pass
    if b not in bars: bars[b]=[p,p,p,fr]
    else:
        o=bars[b]; o[0]=max(o[0],p);o[1]=min(o[1],p);o[2]=p
        if fr is not None:o[3]=fr
keys=sorted(bars); H=[bars[k][0] for k in keys]; L=[bars[k][1] for k in keys]; C=[bars[k][2] for k in keys]; FR=[bars[k][3] for k in keys]
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
def sig_at(i):
    st=stretch(i)
    if st is None: return None
    s='SHORT' if st>=1.2 else ('LONG' if st<=-1.2 else None)
    if not s: return None
    mc=(C[i]-C[i-96])/C[i-96]*1e4 if C[i-96]>0 else 0
    if s=='SHORT' and mc>150: return None
    if s=='LONG' and mc<-150: return None
    return s

# mode: 'full' = mean-revert'te %100 kapat; 'runner' = %50 + trail; trail bps param
def run(mode,trail=30,lo=0,hi=None):
    hi=hi or n; i=96; tr=[]; mfe_cap=[]
    while i<hi-1:
        if i<lo: i+=1; continue
        sig=sig_at(i)
        if not sig: i+=1; continue
        ent=C[i]; phase='open'; half=None; peak=ent; j=i; mfe=0; res=None
        for j in range(i+1,min(i+MH*2+1,n)):
            cur=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4
            fav=((H[j]-ent) if sig=='LONG' else (ent-L[j]))/ent*1e4  # en iyi lehe (bar ici)
            mfe=max(mfe,fav)
            adv=((H[j]-ent) if sig=='SHORT' else (ent-L[j]))/ent*1e4
            if adv>=SL: res=-SL-FEE; break
            rev=(sig=='LONG' and PZ[j] is not None and PZ[j]>=0) or (sig=='SHORT' and PZ[j] is not None and PZ[j]<=0)
            if phase=='open':
                if (j-i)>=MH and not rev: res=cur-FEE; break
                if rev:
                    if mode=='full': res=cur-FEE; break
                    phase='runner'; half=cur; peak=C[j]
            else:
                if sig=='LONG': peak=max(peak,H[j]); retr=(peak-C[j])/ent*1e4
                else: peak=min(peak,L[j]); retr=(C[j]-peak)/ent*1e4
                if retr>=trail or (j-i)>=MH*2: res=0.5*half+0.5*cur-FEE; break
        if res is None:
            cur=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4; res=cur-FEE
        tr.append(res)
        if mfe>5: mfe_cap.append(max(0,(res+FEE))/mfe)  # yakalanan kar / tepe kar
        i=j+1
    return tr,mfe_cap

def rep(lbl,**kw):
    tr,cap=run(**kw)
    qs=[sum(run(lo=n*q//4,hi=n*(q+1)//4,**kw)[0]) for q in range(4)]
    pos=sum(1 for x in qs if x>0)
    avgcap=100*statistics.mean(cap) if cap else 0
    worst=min(tr) if tr else 0
    print('%-26s isl=%d net %+6.0f isabet %%%.0f cey+:%d/4 worst%+.0f | tepe-yakalama %%%.0f'%(
        lbl,len(tr),sum(tr),100*sum(1 for x in tr if x>0)/len(tr),pos,worst,avgcap))

print('=== B cikis varyantlari: net + kar-geri-verme (tepe-yakalama %) ===')
rep('MEVCUT %50+runner trail30', mode='runner', trail=30)
rep('runner trail 20 (sikica)', mode='runner', trail=20)
rep('runner trail 15', mode='runner', trail=15)
rep('runner trail 25', mode='runner', trail=25)
rep('FULL: mean-revert %100 kapat', mode='full')
