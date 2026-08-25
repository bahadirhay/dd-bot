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
T=1.2; SL=60; MH=16; mac=150; TRAIL=30
def bt(tp_cap):
    # tp_cap = runner ust siniri (bps). None=uzak/serbest (backtest orijinali). 120=cap.
    i=96; tr=[]
    while i<n-1:
        st=stretch(i)
        if st is None: i+=1; continue
        sig='SHORT' if st>=T else ('LONG' if st<=-T else None)
        if not sig: i+=1; continue
        if i>96 and C[i-96]>0:
            mc=(C[i]-C[i-96])/C[i-96]*1e4
            if (sig=='SHORT' and mc>mac) or (sig=='LONG' and mc<-mac): i+=1; continue
        ent=C[i]; rev_j=None
        for j in range(i+1,min(i+1+MH,n)):
            if sig=='SHORT' and (H[j]-ent)/ent*1e4>=SL: tr.append(-SL-FEE); break
            if sig=='LONG' and (ent-L[j])/ent*1e4>=SL: tr.append(-SL-FEE); break
            if PZ[j] is not None and ((sig=='SHORT' and PZ[j]<=0) or (sig=='LONG' and PZ[j]>=0)):
                rev_j=j; break
        else:
            j=min(i+MH,n-1); r=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4; tr.append(r-FEE); i=j+1; continue
        if rev_j is None: i=j+1; continue
        rev_px=C[rev_j]; base=((rev_px-ent) if sig=='LONG' else (ent-rev_px))/ent*1e4
        half=base*0.5
        # runner %50: peak-trail; tp_cap varsa cap'te kapanir
        peak=rev_px; rj=rev_j
        for j in range(rev_j+1,min(rev_j+1+MH,n)):
            fav=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4
            if tp_cap is not None and fav>=tp_cap: rj=j; run=tp_cap; break
            if sig=='LONG':
                peak=max(peak,H[j])
                if (peak-C[j])/ent*1e4>=TRAIL: rj=j; break
            else:
                peak=min(peak,L[j])
                if (C[j]-peak)/ent*1e4>=TRAIL: rj=j; break
            rj=j
        else:
            pass
        run=((C[rj]-ent) if sig=='LONG' else (ent-C[rj]))/ent*1e4 if (tp_cap is None or 'run' not in dir()) else run
        run=((C[rj]-ent) if sig=='LONG' else (ent-C[rj]))/ent*1e4
        if tp_cap is not None:
            fav_rj=((C[rj]-ent) if sig=='LONG' else (ent-C[rj]))/ent*1e4
            if fav_rj>=tp_cap: run=tp_cap
        tr.append(half+run*0.5-FEE); i=rj+1
    return tr
for lbl,cap in [('UZAK (cap yok, =backtest orijinal)',None),('TP2 cap=120 (eski canli)',120),('cap=90',90)]:
    t=bt(cap); print('%-36s islem=%d net %+.0f isabet %%%.0f'%(lbl,len(t),sum(t),100*sum(1 for x in t if x>0)/len(t) if t else 0))
