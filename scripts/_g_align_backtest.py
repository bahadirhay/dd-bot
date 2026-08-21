"""G ALIGN (trend-filtreli) TAM BACKTEST: coin-basi + agregat + PERMUTASYON + iki-yari.
BASE = saf G (funding kontraryan). ALIGN = sadece gunluk-trendle ayni yon (N=40).
Soru: ALIGN gercekten kazanc mi, sans mi? Permutasyon (getiri-karistirma) + iki-yari + coin-basi.
"""
import json, urllib.request, time, bisect, random
random.seed(7)
W=120; PCT=0.15; HOLD=24; FEE=12; N=40; NPERM=2000
COINS=["ETHUSDT","AVAXUSDT","XRPUSDT","ETCUSDT","LINKUSDT","SUIUSDT","TAOUSDT","HBARUSDT"]
B="https://fapi.binance.com"
def fu(s):
    try: return [(int(x["fundingTime"])//1000,float(x["fundingRate"])) for x in json.loads(urllib.request.urlopen(f"{B}/fapi/v1/fundingRate?symbol={s}&limit=1000",timeout=20).read())]
    except: return None
def k1(s):
    o={};e=int(time.time()*1000)
    while len(o)<200*24:
        try: r=json.loads(urllib.request.urlopen(f"{B}/fapi/v1/klines?symbol={s}&interval=1h&limit=1500&endTime={e}",timeout=20).read())
        except: return None
        if not r: break
        for x in r: o[int(x[0])//1000]=float(x[4])
        e=r[0][0]-1
        if len(r)<1500: break
    return o
def kd(s):
    try: r=json.loads(urllib.request.urlopen(f"{B}/fapi/v1/klines?symbol={s}&interval=1d&limit=400",timeout=20).read())
    except: return None
    return [(int(x[0])//1000,float(x[4])) for x in r]
def pat(d,ks,t): p=bisect.bisect_right(ks,t)-1; return d[ks[p]] if p>=0 else None

base_all=[]; align_all=[]
print("=== COIN-BASI: BASE vs ALIGN (trend-filtreli, N=%d) ===\n"%N)
print("  %-6s | BASE n/net/isl        | ALIGN n/net/isl        "%"coin")
for s in COINS:
    fr=fu(s);kh=k1(s);dd=kd(s)
    if not fr or not kh or not dd: print("  %-6s veri yok"%s.replace("USDT","")); continue
    ks=sorted(kh);dts=[t for t,_ in dd];dpx=[p for _,p in dd]
    base=[]; align=[]
    for k in range(W,len(fr)):
        ts,rate=fr[k];past=sorted(r for _,r in fr[k-W:k]);hi=past[int(W*(1-PCT))];lo=past[int(W*PCT)]
        side=-1 if rate>=hi else (1 if rate<=lo else 0)
        if not side: continue
        p0=pat(kh,ks,ts);p1=pat(kh,ks,ts+HOLD*3600)
        if not p0 or not p1 or p0<=0: continue
        ret=side*(p1-p0)/p0*1e4; base.append(ret)
        di=bisect.bisect_right(dts,ts)-1
        if di>=N:
            trend=1 if dpx[di]>dpx[di-N] else -1
            if side==trend: align.append(ret)
    def nn(a): n=len(a); return (n, sum(a)-FEE*n, (sum(a)-FEE*n)/n if n else 0)
    bn,bnet,bi=nn(base); an,anet,ai=nn(align)
    base_all+=[(x) for x in base]; align_all+=[(x) for x in align]
    print("  %-6s | %3d %+7.0f %+5.1f    | %3d %+7.0f %+5.1f"%(s.replace("USDT",""),bn,bnet,bi,an,anet,ai))

def summ(name,a):
    n=len(a); net=sum(a)-FEE*n; h=n//2
    h1=sum(a[:h])-FEE*h; h2=sum(a[h:])-FEE*(n-h); w=sum(1 for x in a if x>FEE)
    return n,net,net/n if n else 0,h1,h2,w
print("\n=== AGREGAT ===")
for nm,a in [("BASE",base_all),("ALIGN",align_all)]:
    n,net,pt,h1,h2,w=summ(nm,a)
    print("  %-6s n=%4d net=%+7.0f isl=%+5.1f win=%d%% 1y=%+6.0f 2y=%+6.0f [%s]"%(
        nm,n,net,pt,100*w//n if n else 0,h1,h2,"iki-yari+" if h1>0 and h2>0 else "TUTARSIZ"))

# PERMUTASYON: ALIGN net'i sans-seviyesinin ustunde mi
a=align_all; n=len(a); obs=sum(a); ge=0
for _ in range(NPERM):
    # getirilerin isaretini korumak yerine: ALIGN zaten yon-filtreli, net getiri-toplamini test et
    sh=a[:]; random.shuffle(sh)  # (permutasyon burada zayif; asil test: getiri dagilimindan rastgele n cek)
    # dogru permutasyon: tum BASE getirilerinden rastgele n-tane cekip net karsilastir
    samp=random.sample(base_all,min(n,len(base_all)))
    if sum(samp)-FEE*len(samp)>=obs-FEE*n: ge+=1
print("\n=== PERMUTASYON (ALIGN net vs BASE'den rastgele n-islem) ===")
print("  ALIGN gercekten trend-secimi mi, yoksa rastgele n-islem de ayni mi:")
print("  p = %.4f  (dusuk=ALIGN'in trend-secimi GERCEK katki)"%(ge/NPERM))
print("\nYORUM: ALIGN net>>BASE + iki-yari+ + p<0.05 -> trend-filtresi GERCEK kazanc.")
