import sqlite3,statistics
from core.config import cfg
c=sqlite3.connect('file:%s?mode=ro'%cfg.DB_PATH,uri=True)
rows=[(ts,p) for ts,p in c.execute("SELECT ts,price FROM market_snapshots WHERE price>0 ORDER BY ts")]

def build(sec):
    bars={}
    for ts,p in rows:
        b=int(ts//sec)
        if b not in bars: bars[b]=[p,p,p]
        else:
            o=bars[b]; o[0]=max(o[0],p);o[1]=min(o[1],p);o[2]=p
    keys=sorted(bars); return [bars[k][0] for k in keys],[bars[k][1] for k in keys],[bars[k][2] for k in keys]

def zscore_arr(C,M):
    out=[None]*len(C)
    for i in range(M,len(C)):
        win=C[i-M:i]; mu=statistics.mean(win); sd=statistics.pstdev(win)
        out[i]=(C[i]-mu)/sd if sd>0 else None
    return out

FEE=3.0
def bt(H,L,C,Z,M,K,SL,TRAIL,MH,RUNNER=True,macro=150,lo=0,hi=None):
    n=len(C); hi=hi or n; i=max(M,96); tr=[]
    while i<hi-1:
        if i<lo: i+=1; continue
        z=Z[i]
        if z is None: i+=1; continue
        sig='SHORT' if z>=K else ('LONG' if z<=-K else None)
        if not sig: i+=1; continue
        if macro>0 and i>=96 and C[i-96]>0:
            mc=(C[i]-C[i-96])/C[i-96]*1e4
            if (sig=='SHORT' and mc>macro) or (sig=='LONG' and mc<-macro): i+=1; continue
        ent=C[i]; phase='open'; half=None; peak=ent; j=i
        for j in range(i+1,min(i+MH*2+1,n)):
            cur=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4
            adv=((H[j]-ent) if sig=='SHORT' else (ent-L[j]))/ent*1e4
            if adv>=SL: tr.append(-SL-FEE); break
            rev=(sig=='LONG' and Z[j] is not None and Z[j]>=0) or (sig=='SHORT' and Z[j] is not None and Z[j]<=0)
            if phase=='open':
                if (j-i)>=MH and not rev: tr.append(cur-FEE); break
                if rev:
                    if RUNNER: phase='runner'; half=cur; peak=C[j]
                    else: tr.append(cur-FEE); break
            else:
                if sig=='LONG': peak=max(peak,H[j]); retr=(peak-C[j])/ent*1e4
                else: peak=min(peak,L[j]); retr=(C[j]-peak)/ent*1e4
                if retr>=TRAIL or (j-i)>=MH*2: tr.append(0.5*half+0.5*cur-FEE); break
        else:
            cur=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4; tr.append(cur-FEE)
        i=j+1
    return tr

def summ(tr):
    if not tr: return 'isl=0'
    net=sum(tr); worst=min(tr); hit=100*sum(1 for x in tr if x>0)/len(tr)
    return 'isl=%-4d net %+6.0f isabet %%%.0f worst %+.0f'%(len(tr),net,hit,worst)

H5,L5,C5=build(300)
print('5m bar=%d'%len(C5))
print('\n=== 5m MR: parametre taramasi (fee dahil, runner) ===')
best=None
for M in (48,72,96):
    for K in (1.5,2.0,2.5):
        for SL in (60,90):
            Z=zscore_arr(C5,M)
            tr=bt(H5,L5,C5,Z,M,K,SL,30,16)
            net=sum(tr)
            print('  M=%-3d K=%.1f SL=%2d: %s'%(M,K,SL,summ(tr)))
            n=len(C5)
            qs=[sum(bt(H5,L5,C5,Z,M,K,SL,30,16,lo=n*q//4,hi=n*(q+1)//4)) for q in range(4)]
            pos=sum(1 for x in qs if x>0)
            if best is None or net>best[0]: best=(net,M,K,SL,pos)

print('\n=== EN IYI 5m HUCRE WALK-FORWARD ===')
net,M,K,SL,pos=best
print('en yuksek net: M=%d K=%.1f SL=%d net=%+0.f cey+:%d/4'%(M,K,SL,net,pos))
Z=zscore_arr(C5,M); n=len(C5); split=int(n*0.6)
print('  TRAIN(%%60): %s'%summ(bt(H5,L5,C5,Z,M,K,SL,30,16,hi=split)))
print('  OOS  (%%40): %s'%summ(bt(H5,L5,C5,Z,M,K,SL,30,16,lo=split)))
for q in range(4):
    print('  ceyrek %d: %s'%(q+1,summ(bt(H5,L5,C5,Z,M,K,SL,30,16,lo=n*q//4,hi=n*(q+1)//4))))
