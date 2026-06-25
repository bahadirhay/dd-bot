import urllib.request,json,statistics,time
COINS=['BTC','ETH','BNB','SOL','XRP','ADA','DOGE','LINK','LTC','AVAX','DOT','ATOM']
def klines(sym,limit=600):
    u='https://fapi.binance.com/fapi/v1/klines?symbol=%sUSDT&interval=1d&limit=%d'%(sym,limit)
    for _ in range(3):
        try:
            r=json.loads(urllib.request.urlopen(u,timeout=15).read())
            return [float(x[4]) for x in r]  # close
        except Exception as e:
            time.sleep(1)
    return None
data={}
for s in COINS:
    c=klines(s)
    if c and len(c)>200: data[s]=c
    print('%s: %s bar'%(s,len(c) if c else 'YOK'))
# ortak uzunluk (son N gun)
ml=min(len(v) for v in data.values())
for s in data: data[s]=data[s][-ml:]
print('\nortak gun=%d, coin=%d'%(ml,len(data)))
FEE=8.  # round-trip bps (gunluk rebalance maliyeti, pozisyon degisiminde)
def tsm(N,longshort=True,coins=None):
    coins=coins or list(data.keys())
    # gunluk portfoy getirisi: her coin momentum yonunde, esit agirlik
    daily=[]; prevpos={s:0 for s in coins}
    for i in range(N,ml-1):
        rets=[];
        for s in coins:
            C=data[s]
            if C[i-N]<=0: continue
            mom=(C[i]-C[i-N])/C[i-N]
            pos=(1 if mom>0 else (-1 if longshort else 0))
            nxt=(C[i+1]-C[i])/C[i]  # ertesi gun getiri
            r=pos*nxt
            if pos!=prevpos[s]: r-=FEE/1e4  # pozisyon degisti -> fee
            prevpos[s]=pos
            rets.append(r)
        if rets: daily.append(sum(rets)/len(rets))
    return daily
def rep(lbl,daily):
    if not daily: print('%-28s veri yok'%lbl);return
    tot=sum(daily)*100
    sharpe=(statistics.mean(daily)/statistics.pstdev(daily)*(365**0.5)) if statistics.pstdev(daily)>0 else 0
    wr=100*sum(1 for x in daily if x>0)/len(daily)
    # max drawdown
    eq=0;peak=0;mdd=0
    for x in daily:
        eq+=x;peak=max(peak,eq);mdd=min(mdd,eq-peak)
    print('%-28s toplam %+6.1f%% | Sharpe %.2f | gun-isabet %%%.0f | maxDD %.1f%%'%(lbl,tot,sharpe,wr,mdd*100))
print('\n=== CESITLENDIRILMIS TREND-TAKIP (12 coin, gunluk TSM) ===')
for N in (20,40,60,90):
    rep('DIVERSIFIED long-short N=%d'%N, tsm(N,True))
print('--- sadece long (kripto short ezilir) ---')
for N in (40,60,90):
    rep('DIVERSIFIED long-only N=%d'%N, tsm(N,False))
print('--- TEK COIN (ETH) ayni kural, cesitlendirme YOK ---')
for N in (40,60):
    rep('ETH-only long-short N=%d'%N, tsm(N,True,coins=['ETH']))
