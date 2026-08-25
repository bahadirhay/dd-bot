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
n=len(keys); FEE=3.0; SL=60.0; TRAIL=30.0; MH=16
def zw(a,i,m):
    w=[a[j] for j in range(max(0,i-m),i) if a[j] is not None]
    if len(w)<m//2 or a[i] is None: return None
    mu=statistics.mean(w); sd=statistics.pstdev(w); return (a[i]-mu)/sd if sd>0 else None
# B kompozit
MOM=[((C[i]-C[i-16])/C[i-16]) if i>=16 and C[i-16]>0 else None for i in range(n)]
PZ32=[zw(C,i,32) for i in range(n)]
def b_stretch(i):
    parts=[v for v in (zw(FR,i,96),zw(MOM,i,96),PZ32[i]) if v is not None]
    return sum(parts)/len(parts) if parts else None
# C pure price-z M=24
PZ24=[zw(C,i,24) for i in range(n)]

def runner_exit(sig,i,ent):
    # mean-revert (PZ24 cross 0) -> %50 + runner trail; hard-SL; maxhold
    phase='open'; half=None; peak=ent; res=None; j=i
    for j in range(i+1,min(i+MH*2+1,n)):
        cur=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4
        adv=((H[j]-ent) if sig=='SHORT' else (ent-L[j]))/ent*1e4
        if adv>=SL: return -SL-FEE,j
        if phase=='open':
            rev=(sig=='LONG' and PZ24[j] is not None and PZ24[j]>=0) or (sig=='SHORT' and PZ24[j] is not None and PZ24[j]<=0)
            if (j-i)>=MH and not rev: return cur-FEE,j
            if rev:
                phase='runner'; half=cur; peak=C[j]
        else:
            if sig=='LONG': peak=max(peak,H[j]); retr=(peak-C[j])/ent*1e4
            else: peak=min(peak,L[j]); retr=(C[j]-peak)/ent*1e4
            if retr>=TRAIL or (j-i)>=MH*2:
                blended=0.5*half+0.5*cur-FEE; return blended,j
    cur=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4
    return cur-FEE,j

def run(kind,short_mac=150,long_mac=150,K=2.0,lo=0,hi=None):
    hi=hi or n; i=96; tr=[]; det={'LONG':[], 'SHORT':[]}
    while i<hi-1:
        if i<lo: i+=1; continue
        if kind=='B':
            st=b_stretch(i)
            sig='SHORT' if (st is not None and st>=1.2) else ('LONG' if (st is not None and st<=-1.2) else None)
        else: # C
            z=PZ24[i]
            sig='SHORT' if (z is not None and z>=K) else ('LONG' if (z is not None and z<=-K) else None)
        if not sig: i+=1; continue
        mc=(C[i]-C[i-96])/C[i-96]*1e4 if C[i-96]>0 else 0
        if sig=='SHORT' and mc>short_mac: i+=1; continue
        if sig=='LONG' and mc<-long_mac: i+=1; continue
        ent=C[i]; res,j=runner_exit(sig,i,ent)
        tr.append(res); det[sig].append(res); i=j+1
    return tr,det

def rep(lbl,**kw):
    tr,det=run(**kw)
    qs=[sum(run(lo=n*q//4,hi=n*(q+1)//4,**kw)[0]) for q in range(4)]
    pos=sum(1 for x in qs if x>0)
    ls=det['LONG']; ss=det['SHORT']
    print('%-32s isl=%d net %+7.0f isabet %%%.0f cey+:%d/4 | LONG(%d) %+.0f / SHORT(%d) %+.0f'%(
        lbl,len(tr),sum(tr),100*sum(1 for x in tr if x>0)/len(tr) if tr else 0,pos,
        len(ls),sum(ls),len(ss),sum(ss)))

print('=== B vs C ve hibrit varyantlar (25 gun, SL60/trail30/mh16) ===')
rep('B (kompozit, makro150)',kind='B')
rep('C (saf fiyat-z, makro150)',kind='C')
rep('C + short-makro 80 (rally-short kis)',kind='C',short_mac=80)
rep('C + short-makro 50',kind='C',short_mac=50)
rep('C + short-makro 0 (SHORT YOK, sadece long)',kind='C',short_mac=-99999)
rep('C K=1.5 (daha cok sinyal)',kind='C',K=1.5)
rep('C K=2.5 (daha secici)',kind='C',K=2.5)
