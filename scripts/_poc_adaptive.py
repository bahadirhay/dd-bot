import sqlite3,json,statistics
from core.config import cfg
c=sqlite3.connect('file:%s?mode=ro'%cfg.DB_PATH,uri=True)
bars={}
for ts,p,pj in c.execute("SELECT ts,price,payload_json FROM market_snapshots WHERE price>0 ORDER BY ts"):
    b=int(ts//900); vol=None
    if pj:
        try:
            d=json.loads(pj); bv=d.get('buy_vol_5m'); sv=d.get('sell_vol_5m')
            if bv is not None and sv is not None: vol=float(bv)+float(sv)
        except Exception: pass
    if b not in bars: bars[b]=[p,p,p,p,vol]
    else:
        o=bars[b]; o[1]=max(o[1],p);o[2]=min(o[2],p);o[3]=p
        if vol is not None: o[4]=vol
keys=sorted(bars)
H=[bars[k][1] for k in keys]; L=[bars[k][2] for k in keys]; C=[bars[k][3] for k in keys]; VOL=[bars[k][4] for k in keys]
n=len(keys); FEE=3.0
print('15m bar=%d'%n)

def poc(i,M):
    num=den=0.0
    for j in range(i-M,i):
        v=VOL[j] if (VOL[j] and VOL[j]>0) else 1.0
        num+=C[j]*v; den+=v
    return num/den if den>0 else None

def sd_close(i,M):  # son M kapanisin std (bps cinsinden) = volatilite olcusu
    w=C[i-M:i]; m=statistics.mean(w)
    return statistics.pstdev(w)/m*1e4 if m>0 else 0

# mode: 'fix' DEV bps sabit | 'adap' sapma/volatilite (K sigma)
def run(mode,M=40,DEV=50,K=1.5,SL=60,MH=16,min_prof=0,lo=0,hi=None):
    hi=hi or n; i=max(M,96); tr=[]
    while i<hi-1:
        if i<lo: i+=1; continue
        pc=poc(i,M)
        if pc is None or pc<=0: i+=1; continue
        dev=(C[i]-pc)/pc*1e4
        if mode=='fix':
            sig='LONG' if dev<=-DEV else ('SHORT' if dev>=DEV else None)
        else:
            vol=sd_close(i,M)
            if vol<=0: i+=1; continue
            zz=dev/vol  # volatiliteye normalize sapma
            sig='LONG' if zz<=-K else ('SHORT' if zz>=K else None)
        if not sig: i+=1; continue
        ent=C[i]; j=i; res=None
        for j in range(i+1,min(i+MH+1,n)):
            cur=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4
            adv=((H[j]-ent) if sig=='SHORT' else (ent-L[j]))/ent*1e4
            if adv>=SL: res=-SL-FEE; break
            pcj=poc(j,M); devj=(C[j]-pcj)/pcj*1e4 if (pcj and pcj>0) else None
            reverted = devj is not None and ((sig=='LONG' and devj>=0) or (sig=='SHORT' and devj<=0))
            if (j-i)>=MH and not reverted: res=cur-FEE; break
            if reverted and cur>=min_prof: res=cur-FEE; break
        if res is None:
            cur=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4; res=cur-FEE
        tr.append(res); i=j+1
    return tr

def rep(lbl,**kw):
    tr=run(**kw)
    if not tr: print('%-30s islem=0'%lbl); return
    qs=[sum(run(lo=n*q//4,hi=n*(q+1)//4,**kw)) for q in range(4)]; pos=sum(1 for x in qs if x>0)
    split=int(n*0.6); oos=sum(run(lo=split,**kw))
    print('%-30s isl=%-3d net %+6.0f isabet %%%.0f OOS %+6.0f cey+:%d/4'%(
        lbl,len(tr),sum(tr),100*sum(1 for x in tr if x>0)/len(tr),oos,pos))

print('\n=== SABIT bps (mevcut D) vs ADAPTIF (sapma/volatilite, K sigma) ===')
print('--- SABIT ---')
rep('D-fix DEV50 (mevcut)', mode='fix', DEV=50)
rep('D-fix DEV65', mode='fix', DEV=65)
print('--- ADAPTIF (volatiliteye normalize) ---')
rep('D-adap K1.2', mode='adap', K=1.2)
rep('D-adap K1.5', mode='adap', K=1.5)
rep('D-adap K2.0', mode='adap', K=2.0)
rep('D-adap K2.5', mode='adap', K=2.5)
