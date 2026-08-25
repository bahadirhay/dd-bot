import sqlite3
from core.config import cfg
c=sqlite3.connect('file:%s?mode=ro'%cfg.DB_PATH,uri=True)
rows=[(ts,p) for ts,p in c.execute("SELECT ts,price FROM market_snapshots WHERE price>0 ORDER BY ts")]
def build(sec):
    b={}
    for ts,p in rows:
        x=int(ts//sec)
        if x not in b:b[x]={'h':p,'l':p,'c':p}
        else:
            d=b[x];d['h']=max(d['h'],p);d['l']=min(d['l'],p);d['c']=p
    k=sorted(b);return [b[x]['h'] for x in k],[b[x]['l'] for x in k],[b[x]['c'] for x in k]
FEE=8.;SLIP=2.;M=40;DEV=85.;SL=90.;MH=16
def run(H,L,C,lo=0,hi=None):
    n=len(C);hi=hi or n;i=max(M,lo);tr=[]
    def poc(idx): return sum(C[idx-M:idx])/M
    while i<hi-1:
        if i<lo:i+=1;continue
        pc=poc(i);dev=(C[i]-pc)/pc*1e4;sig='LONG' if dev<=-DEV else ('SHORT' if dev>=DEV else None)
        if not sig:i+=1;continue
        ent=C[i];j=i;res=None
        for j in range(i+1,min(i+MH+1,n)):
            cur=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4;adv=((H[j]-ent) if sig=='SHORT' else (ent-L[j]))/ent*1e4
            if adv>=SL:res=-SL-FEE-2*SLIP;break
            dj=(C[j]-poc(j))/poc(j)*1e4
            if ((sig=='LONG' and dj>=0) or (sig=='SHORT' and dj<=0)) and cur>=0:res=cur-FEE-2*SLIP;break
            if (j-i)>=MH:res=cur-FEE-2*SLIP;break
        if res is None:res=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4-FEE-2*SLIP
        tr.append(res);i=j+1
    return tr
print('=== D (POC esit-agirlik) ZAMAN DILIMI kiyasi, fee8+slip2, WF ===')
for nm,sec in (('5m',300),('10m',600),('15m',900),('30m',1800)):
    H,L,C=build(sec);n=len(C);split=int(n*0.6)
    al=run(H,L,C);tr=run(H,L,C,hi=split);oo=run(H,L,C,lo=split)
    pertr=sum(al)/len(al) if al else 0
    print('  %-4s bar=%-5d isl=%-4d net%+6.0f /isl%+5.1f isabet%%%.0f | TRAIN%+6.0f OOS%+6.0f'%(
        nm,n,len(al),sum(al),pertr,100*sum(1 for x in al if x>0)/len(al) if al else 0,sum(tr),sum(oo)))
