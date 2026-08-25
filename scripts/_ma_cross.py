import sqlite3
from core.config import cfg
c=sqlite3.connect('file:%s?mode=ro'%cfg.DB_PATH,uri=True)
bars={}
for ts,p in c.execute("SELECT ts,price FROM market_snapshots WHERE price>0 ORDER BY ts"):
    b=int(ts//900)
    if b not in bars: bars[b]=[p,p,p]
    else: bars[b][1]=max(bars[b][1],p);bars[b][2]=p
k=sorted(bars);H=[bars[x][1] for x in k];L=[bars[x][2-1] for x in k];C=[bars[x][2] for x in k]
# duzelt L
L=[bars[x][1] for x in k]  # gecici
# temiz O/H/L/C
H=[];L=[];C=[]
for x in k:
    o=bars[x];H.append(o[1]);L.append(min(o[0],o[1],o[2]) if False else o[1]);C.append(o[2])
# bars[x]=[ilk(max baslangic? hayir), max, son] -> aslinda [open?,high,close]; L yok. Yeniden kur dogru:
bars={}
for ts,p in c.execute("SELECT ts,price FROM market_snapshots WHERE price>0 ORDER BY ts"):
    b=int(ts//900)
    if b not in bars: bars[b]={'h':p,'l':p,'c':p}
    else:
        d=bars[b];d['h']=max(d['h'],p);d['l']=min(d['l'],p);d['c']=p
k=sorted(bars);H=[bars[x]['h'] for x in k];L=[bars[x]['l'] for x in k];C=[bars[x]['c'] for x in k]
n=len(C);FEE=8.;SLIP=2.
print('15m bar=%d'%n)
def sma(i,m): return sum(C[i-m+1:i+1])/m if i>=m-1 else None
# MA-cross: fast>slow -> LONG, fast<slow -> SHORT. Cross'ta flip (her zaman piyasada). SL opsiyonel.
def run(fast,slow,use_sl=True,SL=120.,lo=0,hi=None):
    hi=hi or n;i=max(slow,lo)+1;tr=[];pos=None;ent=0;ent_i=0
    def close(j,reason_sl=False):
        nonlocal pos,ent
        px=C[j]
        if reason_sl:
            r=-SL-FEE-2*SLIP
        else:
            r=((px-ent) if pos=='LONG' else (ent-px))/ent*1e4-FEE-2*SLIP
        tr.append(r);pos=None
    while i<hi-1:
        if i<lo:i+=1;continue
        f=sma(i,fast);s=sma(i,slow)
        if f is None or s is None:i+=1;continue
        want='LONG' if f>s else 'SHORT'
        if pos is None:
            pos=want;ent=C[i];ent_i=i;i+=1;continue
        # SL kontrol
        if use_sl:
            adv=((H[i]-ent) if pos=='SHORT' else (ent-L[i]))/ent*1e4
            if adv>=SL: close(i,reason_sl=True); i+=1; continue
        # cross -> flip
        if want!=pos:
            close(i); pos=want;ent=C[i];ent_i=i
        i+=1
    if pos is not None: close(min(i,n-1))
    return tr
split=int(n*0.6)
def rep(lbl,**kw):
    al=run(**kw);tr=run(hi=split,**kw);oo=run(lo=split,**kw)
    qs=[sum(run(lo=n*q//4,hi=n*(q+1)//4,**kw)) for q in range(4)];pos=sum(1 for x in qs if x>0)
    if not al:print('%-22s islem=0'%lbl);return
    gp=sum(x for x in al if x>0);gl=sum(x for x in al if x<=0)
    print('%-22s net%+6.0f isl=%-4d isabet%%%.0f | KAZANC%+6.0f/KAYIP%+6.0f | TRAIN%+6.0f OOS%+6.0f cey%d/4'%(
        lbl,sum(al),len(al),100*sum(1 for x in al if x>0)/len(al),gp,gl,sum(tr),sum(oo),pos))
print('\n=== 2-MA CROSS (trend-takip) fee8+slip2, WF ===')
print('(OOS = son %40 = SU ANKI dusus trendi dahil)')
for fa,sl in ((9,21),(10,30),(20,50),(20,100),(50,200)):
    rep('MA %d/%d (SL120)'%(fa,sl),fast=fa,slow=sl,use_sl=True,SL=120)
print('--- SL yok (saf flip) ---')
for fa,sl in ((10,30),(20,50)):
    rep('MA %d/%d (SL yok)'%(fa,sl),fast=fa,slow=sl,use_sl=False)
print('\nKIYAS: D (MR) +1590..+1718')
