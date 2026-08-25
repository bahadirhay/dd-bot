import sqlite3,json,statistics
from core.config import cfg
c=sqlite3.connect('file:%s?mode=ro'%cfg.DB_PATH,uri=True)
bars={}
for ts,p,pj in c.execute("SELECT ts,price,payload_json FROM market_snapshots WHERE price>0 ORDER BY ts"):
    b=int(ts//900)
    if b not in bars: bars[b]=[p,p,p]
    else:
        o=bars[b]; o[0]=max(o[0],p);o[1]=min(o[1],p);o[2]=p
keys=sorted(bars); H=[bars[k][0] for k in keys]; L=[bars[k][1] for k in keys]; C=[bars[k][2] for k in keys]
n=len(keys); FEE=3.0

def trend_exit(side,i,ent,SL,TRAIL,MH):
    peak=ent; j=i
    for j in range(i+1,min(i+MH+1,n)):
        adv=((H[j]-ent) if side=='SHORT' else (ent-L[j]))/ent*1e4
        if adv>=SL: return -SL-FEE,j
        if side=='LONG':
            peak=max(peak,H[j])
            if (peak-ent)/ent*1e4>TRAIL and (peak-C[j])/ent*1e4>=TRAIL: return (C[j]-ent)/ent*1e4-FEE,j
        else:
            peak=min(peak,L[j])
            if (ent-peak)/ent*1e4>TRAIL and (C[j]-peak)/ent*1e4>=TRAIL: return (ent-C[j])/ent*1e4-FEE,j
    cur=((C[j]-ent) if side=='LONG' else (ent-C[j]))/ent*1e4
    return cur-FEE,j

def mom(N,X,SL=60,TRAIL=40,MH=48,lo=0,hi=None):
    hi=hi or n; i=max(N,96); L_=[];S_=[]
    while i<hi-1:
        if i<lo: i+=1; continue
        if C[i-N]<=0: i+=1; continue
        r=(C[i]-C[i-N])/C[i-N]*1e4; sig=None
        if r>=X: sig='LONG'
        elif r<=-X: sig='SHORT'
        if not sig: i+=1; continue
        res,j=trend_exit(sig,i,C[i],SL,TRAIL,MH)
        (L_ if sig=='LONG' else S_).append(res); i=j+1
    return L_,S_

def stats(L_,S_):
    tr=L_+S_
    if not tr: return 'islem=0'
    net=sum(tr); worst=min(tr); hit=100*sum(1 for x in tr if x>0)/len(tr)
    return 'isl=%d net %+6.0f isabet %%%.0f worst %+.0f | LONG(%d) %+.0f / SHORT(%d) %+.0f'%(
        len(tr),net,hit,worst,len(L_),sum(L_),len(S_),sum(S_))

print('=== Momentum-devam: parametre komsulugu (overfit kontrolu) ===')
for N in (64,96,128):
    for X in (200,250,300,350):
        L_,S_=mom(N,X); print('  N=%3d X=%3d: %s'%(N,X,stats(L_,S_)))

print('\n=== WALK-FORWARD (ilk %60 train referans, son %40 OUT-OF-SAMPLE) ===')
split=int(n*0.6)
for N,X in ((96,300),(96,250),(64,300),(128,300)):
    Lt,St=mom(N,X,hi=split); Lo,So=mom(N,X,lo=split)
    print('  N=%d X=%d  TRAIN[%s]'%(N,X,stats(Lt,St)))
    print('           OOS  [%s]'%stats(Lo,So))

print('\n=== CEYREK BAZINDA (N=96 X=300) ===')
for q in range(4):
    L_,S_=mom(96,300,lo=n*q//4,hi=n*(q+1)//4)
    print('  ceyrek %d: %s'%(q+1,stats(L_,S_)))
