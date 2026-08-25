import urllib.request,json,statistics,time
def klines(sym,limit=1000):
    u='https://fapi.binance.com/fapi/v1/klines?symbol=%sUSDT&interval=1d&limit=%d'%(sym,limit)
    for _ in range(3):
        try:
            r=json.loads(urllib.request.urlopen(u,timeout=15).read())
            return [(float(x[2]),float(x[3]),float(x[4])) for x in r]  # high,low,close
        except: time.sleep(1)
    return None
FEE=8.
# exit_mode: 'flip'(SL/TP yok) | ('sl',X) felaket-SL | ('tp',X) | ('trail',X)
def tsm(bars,N,exit_mode,lo=0,hi=None):
    H=[b[0] for b in bars];L=[b[1] for b in bars];C=[b[2] for b in bars]
    n=len(C);hi=hi or n-1;daily=[];pos=0;ent=0;peak=0
    for i in range(max(N,lo),hi):
        if C[i-N]<=0:continue
        mom=(C[i]-C[i-N])/C[i-N];want=1 if mom>0 else -1
        # gun-ici koruma (acik pozisyonda)
        if pos!=0:
            cur=((C[i]-ent) if pos>0 else (ent-C[i]))/ent*1e4
            adv=((H[i]-ent) if pos<0 else (ent-L[i]))/ent*1e4
            hit=False
            if exit_mode[0]=='sl' and adv>=exit_mode[1]: pos=0;hit=True
            elif exit_mode[0]=='tp' and cur>=exit_mode[1]: pos=0;hit=True
            elif exit_mode[0]=='trail':
                if pos>0: peak=max(peak,C[i]);
                else: peak=min(peak,C[i])
                retr=((peak-C[i]) if pos>0 else (C[i]-peak))/ent*1e4
                if cur>0 and retr>=exit_mode[1]: pos=0;hit=True
            if hit: pass  # flat kaldi, asagida want'a gore tekrar girer
        # gunluk getiri (pozisyona gore)
        nxt=(C[i+1]-C[i])/C[i]
        # pozisyon: flip ya da koruma sonrasi want
        newpos=want
        r=newpos*nxt
        if newpos!=pos: r-=FEE/1e4; ent=C[i+1] if False else C[i]; peak=C[i]
        pos=newpos
        daily.append(r)
    return daily
def stats(d):
    if not d:return 0,0,0
    tot=sum(d)*100;sh=(statistics.mean(d)/statistics.pstdev(d)*(365**0.5)) if statistics.pstdev(d)>0 else 0
    eq=pk=mdd=0
    for x in d:eq+=x;pk=max(pk,eq);mdd=min(mdd,eq-pk)
    return tot,sh,mdd*100
bars=klines('ETH')
n=len(bars);split=int(n*0.6)
def rep(lbl,em):
    full=tsm(bars,40,em);oo=tsm(bars,40,em,lo=split)
    tt,sh,dd=stats(full);to,sho,ddo=stats(oo)
    print('%-26s full %+6.1f%% Sh%.2f maxDD%.0f%% | OOS %+6.1f%% Sh%.2f'%(lbl,tt,sh,dd,to,sho))
print('=== F (ETH gunluk TSM N40) koruma varyantlari, WF ===')
rep('flip-only (MEVCUT)',('flip',))
print('--- felaket-SL ---')
rep('+ SL 300bps',('sl',300));rep('+ SL 500bps',('sl',500));rep('+ SL 800bps',('sl',800))
print('--- TP1 (kullanici fikri) ---')
rep('+ TP 300bps',('tp',300));rep('+ TP 500bps',('tp',500));rep('+ TP 1000bps',('tp',1000))
print('--- trailing (tepe-trail) ---')
rep('+ trail 300bps',('trail',300));rep('+ trail 500bps',('trail',500))
