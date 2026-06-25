import urllib.request,json,statistics,time
def klines(sym,limit=1000):
    u='https://fapi.binance.com/fapi/v1/klines?symbol=%sUSDT&interval=1d&limit=%d'%(sym,limit)
    for _ in range(3):
        try: return [float(x[4]) for x in json.loads(urllib.request.urlopen(u,timeout=15).read())]
        except: time.sleep(1)
    return None
FEE=8.
def tsm(C,N,longshort=True,lo=0,hi=None):
    n=len(C);hi=hi or n-1;daily=[];prev=0
    for i in range(max(N,lo),hi):
        if C[i-N]<=0:continue
        mom=(C[i]-C[i-N])/C[i-N];pos=(1 if mom>0 else (-1 if longshort else 0))
        nxt=(C[i+1]-C[i])/C[i];r=pos*nxt
        if pos!=prev:r-=FEE/1e4
        prev=pos;daily.append(r)
    return daily
def stats(daily):
    if not daily:return 0,0,0
    tot=sum(daily)*100;sh=(statistics.mean(daily)/statistics.pstdev(daily)*(365**0.5)) if statistics.pstdev(daily)>0 else 0
    eq=peak=mdd=0
    for x in daily:eq+=x;peak=max(peak,eq);mdd=min(mdd,eq-peak)
    return tot,sh,mdd*100
for sym in ('ETH','BTC','SOL','BNB'):
    C=klines(sym)
    if not C:print(sym,'YOK');continue
    n=len(C);split=int(n*0.6)
    print('\n=== %s GUNLUK TSM (%d gun) WALK-FORWARD ===' % (sym,n))
    for N in (30,40,50,60,75,90):
        full=tsm(C,N,True)
        tr=tsm(C,N,True,hi=split);oo=tsm(C,N,True,lo=split)
        tt,sh,dd=stats(full);_,sht,_=stats(tr);to,sho,ddo=stats(oo)
        print('  N=%-2d full %+6.1f%% Sh%.2f DD%.0f%% | TRAIN Sh%.2f | OOS %+6.1f%% Sh%.2f'%(N,tt,sh,dd,sht,to,sho))
