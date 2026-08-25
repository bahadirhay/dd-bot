import sqlite3
from core.config import cfg
c=sqlite3.connect('file:%s?mode=ro'%cfg.DB_PATH,uri=True)
bars={}
for ts,p in c.execute("SELECT ts,price FROM market_snapshots WHERE price>0 ORDER BY ts"):
    b=int(ts//900)
    if b not in bars: bars[b]={'o':p,'h':p,'l':p,'c':p}
    else:
        d=bars[b];d['h']=max(d['h'],p);d['l']=min(d['l'],p);d['c']=p
k=sorted(bars);O=[bars[x]['o'] for x in k];H=[bars[x]['h'] for x in k];L=[bars[x]['l'] for x in k];C=[bars[x]['c'] for x in k]
n=len(C);FEE=8.;SLIP=2.
print('15m bar=%d'%n)
# ATR
def atr_arr(P):
    tr=[0.0]*n
    for i in range(1,n):
        tr[i]=max(H[i]-L[i],abs(H[i]-C[i-1]),abs(L[i]-C[i-1]))
    out=[None]*n
    for i in range(P,n): out[i]=sum(tr[i-P+1:i+1])/P
    return out
# Supertrend: trend yonu (+1 yukari / -1 asagi)
def supertrend(P,mult):
    a=atr_arr(P);st=[0]*n;dirn=[0]*n;ub=lb=0
    for i in range(n):
        if a[i] is None: continue
        hl=(H[i]+L[i])/2;bu=hl+mult*a[i];bl=hl-mult*a[i]
        if i==0 or dirn[i-1]==0: dirn[i]=1;ub=bu;lb=bl;continue
        # final bands
        ub=bu if (bu<ub or C[i-1]>ub) else ub
        lb=bl if (bl>lb or C[i-1]<lb) else lb
        if dirn[i-1]==1:
            dirn[i]=-1 if C[i]<lb else 1
        else:
            dirn[i]=1 if C[i]>ub else -1
    return dirn
# Heikin Ashi
def heikin():
    hao=[O[0]];hac=[(O[0]+H[0]+L[0]+C[0])/4]
    for i in range(1,n):
        hac.append((O[i]+H[i]+L[i]+C[i])/4)
        hao.append((hao[i-1]+hac[i-1])/2)
    return hao,hac
HAO,HAC=heikin()
def ha_color(i): return 1 if HAC[i]>=HAO[i] else -1  # 1 yesil(up) -1 kirmizi(down)
# strateji: supertrend yonu (+HA teyit ops). GERCEK fiyattan dolum. flip'te don. long_only ops.
def run(P,mult,ha_confirm=False,long_only=False,SL=120,lo=0,hi=None):
    dirn=supertrend(P,mult);hi=hi or n;i=max(P,lo)+1;tr=[];pos=None;ent=0
    def close(px,sl=False):
        nonlocal pos,ent
        r=(-SL if sl else (((px-ent) if pos=='LONG' else (ent-px))/ent*1e4))-FEE-2*SLIP
        tr.append(r);pos=None
    while i<hi-1:
        if i<lo or dirn[i]==0:i+=1;continue
        want='LONG' if dirn[i]==1 else 'SHORT'
        if ha_confirm:
            if want=='LONG' and ha_color(i)!=1: want=None
            if want=='SHORT' and ha_color(i)!=-1: want=None
        if long_only and want=='SHORT': want=None
        if pos:
            adv=((H[i]-ent) if pos=='SHORT' else (ent-L[i]))/ent*1e4
            if adv>=SL: close(C[i],sl=True); i+=1; continue
            if want and want!=pos: close(C[i]); pos=want;ent=C[i]
            elif want is None and ((pos=='LONG' and dirn[i]==-1) or (pos=='SHORT' and dirn[i]==1)):
                close(C[i])
        elif want:
            pos=want;ent=C[i]
        i+=1
    if pos: close(C[min(i,n-1)])
    return tr
split=int(n*0.6)
def rep(lbl,**kw):
    al=run(**kw);tr=run(hi=split,**kw);oo=run(lo=split,**kw)
    qs=[sum(run(lo=n*q//4,hi=n*(q+1)//4,**kw)) for q in range(4)];pos=sum(1 for x in qs if x>0)
    if not al:print('%-30s islem=0'%lbl);return
    print('%-30s net%+6.0f isl=%-3d isabet%%%.0f | TRAIN%+6.0f OOS%+6.0f cey%d/4'%(
        lbl,sum(al),len(al),100*sum(1 for x in al if x>0)/len(al),sum(tr),sum(oo),pos))
print('\n=== SUPERTREND (+Heikin Ashi) GERCEK-fiyat dolum, fee8+slip2, WF ===')
rep('ST(10,3)',P=10,mult=3.0)
rep('ST(10,3)+HA teyit',P=10,mult=3.0,ha_confirm=True)
rep('ST(10,3) long-only',P=10,mult=3.0,long_only=True)
rep('ST(14,3)+HA long-only',P=14,mult=3.0,ha_confirm=True,long_only=True)
rep('ST(20,4)+HA',P=20,mult=4.0,ha_confirm=True)
print('\nKIYAS: D (MR) +1718 / al-tut?')
print('al-tut (ilk->son):',round((C[-1]-C[96])/C[96]*1e4),'bps')
