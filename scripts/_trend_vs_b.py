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
print('toplam 15m bar=%d (~%.1f gun)'%(n,n*15/1440))

def zw(a,i,m):
    w=[a[j] for j in range(max(0,i-m),i) if a[j] is not None]
    if len(w)<m//2 or a[i] is None: return None
    mu=statistics.mean(w); sd=statistics.pstdev(w); return (a[i]-mu)/sd if sd>0 else None
MOM=[((C[i]-C[i-16])/C[i-16]) if i>=16 and C[i-16]>0 else None for i in range(n)]
PZ32=[zw(C,i,32) for i in range(n)]
def b_stretch(i):
    parts=[v for v in (zw(FR,i,96),zw(MOM,i,96),PZ32[i]) if v is not None]
    return sum(parts)/len(parts) if parts else None

# Trend-takip cikis: trail-stop (kar birak kossun), hard-SL, maxhold
def trend_exit(side,i,ent,SL,TRAIL,MH):
    peak=ent; j=i
    for j in range(i+1,min(i+MH+1,n)):
        adv=((H[j]-ent) if side=='SHORT' else (ent-L[j]))/ent*1e4
        if adv>=SL: return -SL-FEE,j
        if side=='LONG':
            peak=max(peak,H[j]);
            if (peak-ent)/ent*1e4>TRAIL and (peak-C[j])/ent*1e4>=TRAIL: return (C[j]-ent)/ent*1e4-FEE,j
        else:
            peak=min(peak,L[j])
            if (ent-peak)/ent*1e4>TRAIL and (C[j]-peak)/ent*1e4>=TRAIL: return (ent-C[j])/ent*1e4-FEE,j
    cur=((C[j]-ent) if side=='LONG' else (ent-C[j]))/ent*1e4
    return cur-FEE,j

# 1) DONCHIAN kirilim: N-bar en yuksegi yukari kirilinca LONG, en dusuk asagi SHORT (trendle git)
def donchian(N,SL=60,TRAIL=40,MH=48,lo=0,hi=None):
    hi=hi or n; i=max(N,96); tr=[]
    while i<hi-1:
        if i<lo: i+=1; continue
        hh=max(H[i-N:i]); ll=min(L[i-N:i]); sig=None
        if C[i]>hh: sig='LONG'
        elif C[i]<ll: sig='SHORT'
        if not sig: i+=1; continue
        res,j=trend_exit(sig,i,C[i],SL,TRAIL,MH); tr.append(res); i=j+1
    return tr

# 2) MOMENTUM-devam: son N bar getirisi +X% ustu -> LONG (devam), -X% alti -> SHORT
def mom_cont(N,X,SL=60,TRAIL=40,MH=48,lo=0,hi=None):
    hi=hi or n; i=max(N,96); tr=[]
    while i<hi-1:
        if i<lo: i+=1; continue
        if C[i-N]<=0: i+=1; continue
        r=(C[i]-C[i-N])/C[i-N]*1e4; sig=None
        if r>=X: sig='LONG'
        elif r<=-X: sig='SHORT'
        if not sig: i+=1; continue
        res,j=trend_exit(sig,i,C[i],SL,TRAIL,MH); tr.append(res); i=j+1
    return tr

# B baseline (referans)
def b_base(lo=0,hi=None):
    hi=hi or n; i=96; tr=[]
    while i<hi-1:
        if i<lo: i+=1; continue
        st=b_stretch(i)
        sig='SHORT' if (st is not None and st>=1.2) else ('LONG' if (st is not None and st<=-1.2) else None)
        if not sig: i+=1; continue
        mc=(C[i]-C[i-96])/C[i-96]*1e4 if C[i-96]>0 else 0
        if sig=='SHORT' and mc>150: i+=1; continue
        if sig=='LONG' and mc<-150: i+=1; continue
        ent=C[i]; peak=ent; phase='open'; half=None; j=i
        for j in range(i+1,min(i+33,n)):
            cur=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4
            adv=((H[j]-ent) if sig=='SHORT' else (ent-L[j]))/ent*1e4
            if adv>=60: cur=-60; j=j; tr.append(-60-FEE); break
            rev=(sig=='LONG' and PZ32[j] is not None and PZ32[j]>=0) or (sig=='SHORT' and PZ32[j] is not None and PZ32[j]<=0)
            if phase=='open':
                if (j-i)>=16 and not rev: tr.append(cur-FEE); break
                if rev: phase='runner'; half=cur; peak=C[j]
            else:
                if sig=='LONG': peak=max(peak,H[j]); retr=(peak-C[j])/ent*1e4
                else: peak=min(peak,L[j]); retr=(C[j]-peak)/ent*1e4
                if retr>=30 or (j-i)>=32: tr.append(0.5*half+0.5*cur-FEE); break
        else:
            cur=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4; tr.append(cur-FEE)
        i=j+1
    return tr

def rep(lbl,fn):
    tr=fn()
    if not tr: print('%-30s islem=0'%lbl); return
    qs=[sum(fn(lo=n*q//4,hi=n*(q+1)//4)) for q in range(4)]
    pos=sum(1 for x in qs if x>0)
    print('%-30s islem=%d net %+7.0f isabet %%%.0f cey+:%d/4'%(lbl,len(tr),sum(tr),100*sum(1 for x in tr if x>0)/len(tr),pos))

print('\n=== REFERANS ===')
rep('B (mean-revert, kanit)', b_base)
print('\n=== DONCHIAN kirilim (trend-takip, farkli ufuk) ===')
for N in (24,48,96,192):
    rep('Donchian N=%d (%.0fh)'%(N,N*0.25), lambda lo=0,hi=None,N=N: donchian(N,lo=lo,hi=hi))
print('\n=== MOMENTUM-devam (trend-takip) ===')
for N,X in ((48,100),(96,150),(96,300),(192,300)):
    rep('Mom N=%d X=%dbps'%(N,X), lambda lo=0,hi=None,N=N,X=X: mom_cont(N,X,lo=lo,hi=hi))
