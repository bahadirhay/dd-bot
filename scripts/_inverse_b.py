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
n=len(keys); FEE=3.0
def zw(a,i,m):
    w=[a[j] for j in range(max(0,i-m),i) if a[j] is not None]
    if len(w)<m//2 or a[i] is None: return None
    mu=statistics.mean(w); sd=statistics.pstdev(w); return (a[i]-mu)/sd if sd>0 else None
MOM=[((C[i]-C[i-16])/C[i-16]) if i>=16 and C[i-16]>0 else None for i in range(n)]
PZ=[zw(C,i,32) for i in range(n)]
def stretch(i):
    parts=[v for v in (zw(FR,i,96),zw(MOM,i,96),PZ[i]) if v is not None]
    return sum(parts)/len(parts) if parts else None
def bt(inverse,exit_trend,lo=0,hi=None):
    hi=hi or n; i=96; tr=[]
    while i<hi-1:
        if i<lo: i+=1; continue
        st=stretch(i)
        if st is None: i+=1; continue
        base='SHORT' if st>=1.2 else ('LONG' if st<=-1.2 else None)
        if not base: i+=1; continue
        sig = ({'SHORT':'LONG','LONG':'SHORT'}[base]) if inverse else base
        mc=(C[i]-C[i-96])/C[i-96]*1e4 if C[i-96]>0 else 0
        # makro (normal B yonunde); ters icin de ayni makro mantigi
        if not inverse:
            if base=='SHORT' and mc>150: i+=1; continue
            if base=='LONG' and mc<-150: i+=1; continue
        ent=C[i]; res=None; peak=ent
        for j in range(i+1,min(i+17,n)):
            if sig=='SHORT' and (H[j]-ent)/ent*1e4>=60: res=-60;break
            if sig=='LONG' and (ent-L[j])/ent*1e4>=60: res=-60;break
            if exit_trend:  # momentum cikis: trail (devam et)
                if sig=='LONG':
                    peak=max(peak,H[j])
                    if (peak-ent)/ent*1e4>0 and (peak-C[j])/ent*1e4>=30: res=(C[j]-ent)/ent*1e4;break
                else:
                    peak=min(peak,L[j])
                    if (ent-peak)/ent*1e4>0 and (C[j]-peak)/ent*1e4>=30: res=(ent-C[j])/ent*1e4;break
            else:  # mean-revert cikis
                if PZ[j] is not None and ((sig=='SHORT' and PZ[j]<=0) or (sig=='LONG' and PZ[j]>=0)):
                    res=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4;break
        if res is None:
            j=min(i+16,n-1); res=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4
        tr.append(res-FEE); i=j+1
    return tr
def rep(lbl,**kw):
    tr=bt(**kw); print('%-34s islem=%d net %+7.0f isabet %%%.0f'%(lbl,len(tr),sum(tr),100*sum(1 for x in tr if x>0)/len(tr) if tr else 0))
print('=== B vs TERS-B (25 gun) ===')
rep('B (normal, mean-revert cikis)',inverse=False,exit_trend=False)
rep('TERS-B (momentum, mean cikis)',inverse=True,exit_trend=False)
rep('TERS-B (momentum, trend/trail cikis)',inverse=True,exit_trend=True)
