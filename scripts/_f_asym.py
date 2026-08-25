import urllib.request,json,statistics,time
def klines(sym,limit=1000):
    u='https://fapi.binance.com/fapi/v1/klines?symbol=%sUSDT&interval=1d&limit=%d'%(sym,limit)
    for _ in range(3):
        try: return [float(x[4]) for x in json.loads(urllib.request.urlopen(u,timeout=15).read())]
        except: time.sleep(1)
    return None
FEE=8.
def mom(C,i,N): return (C[i]-C[i-N])/C[i-N] if C[i-N]>0 else 0
# asimetrik: yavaş(Ne) ve hızlı(Nx) AYNI yöndeyse o yönde; uyumsuzsa FLAT (donus riski -> cik)
def run(C,Ne,Nx,lo=0,hi=None):
    n=len(C);hi=hi or n-1;daily=[];pos=0
    for i in range(max(Ne,Nx,lo),hi):
        sd=1 if mom(C,i,Ne)>0 else -1
        fd=1 if mom(C,i,Nx)>0 else -1
        tgt=sd if sd==fd else 0
        nxt=(C[i+1]-C[i])/C[i];r=tgt*nxt
        if tgt!=pos:r-=FEE/1e4
        pos=tgt;daily.append(r)
    return daily
def stats(d):
    if not d:return 0,0,0
    tot=sum(d)*100;sh=(statistics.mean(d)/statistics.pstdev(d)*(365**0.5)) if statistics.pstdev(d)>0 else 0
    eq=pk=mdd=0
    for x in d:eq+=x;pk=max(pk,eq);mdd=min(mdd,eq-pk)
    return tot,sh,mdd*100
C=klines('ETH');n=len(C);split=int(n*0.6)
print('=== F asimetrik (yavas-giris Ne + hizli-cikis Nx) ETH gunluk, WF ===')
print('Nx=Ne -> simetrik (mevcut baseline)')
for Ne in (40,60):
    print('--- giris Ne=%d ---'%Ne)
    for Nx in (5,10,15,20,30,Ne):
        full=run(C,Ne,Nx);oo=run(C,Ne,Nx,lo=split)
        tt,sh,dd=stats(full);to,sho,_=stats(oo)
        tag=' <-simetrik' if Nx==Ne else ''
        print('  Nx=%-2d full %+6.1f%% Sh%.2f DD%.0f%% | OOS %+6.1f%% Sh%.2f%s'%(Nx,tt,sh,dd,to,sho,tag))
