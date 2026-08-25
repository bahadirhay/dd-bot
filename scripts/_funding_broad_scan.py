"""G (funding kontraryan) GENIS TARAMA: ~35 likit perp + permutasyon + fee12. Amac: (1) yeni aday
coin, (2) G yaygin-gercek mi yoksa 4-coin sansi mi (HIT-RATE). Sans-seviyesi %5; belirgin ustuyse
G yaygin edge. Likidite: curated likit evren (egzotik/illikit YOK - poc_atr tuzagi). Rolling esik,
look-ahead yok, 1000 permutasyon, fee12."""
import urllib.request, json, time, bisect, random
random.seed(7)
W=120; HOLD=24; PCT=0.15; FEE=12.0; NPERM=1000

UNIVERSE=["ETHUSDT","BTCUSDT","SOLUSDT","BNBUSDT","XRPUSDT","DOGEUSDT","AVAXUSDT","LINKUSDT",
"ADAUSDT","DOTUSDT","LTCUSDT","NEARUSDT","ATOMUSDT","UNIUSDT","AAVEUSDT","INJUSDT","SUIUSDT",
"APTUSDT","ARBUSDT","OPUSDT","FILUSDT","TIAUSDT","SEIUSDT","TONUSDT","TRXUSDT","BCHUSDT",
"ETCUSDT","XLMUSDT","ICPUSDT","RENDERUSDT","FETUSDT","WLDUSDT","ORDIUSDT","GALAUSDT","IMXUSDT",
"HBARUSDT","VETUSDT","MKRUSDT","LDOUSDT","GRTUSDT","RUNEUSDT","ALGOUSDT","EGLDUSDT","SANDUSDT",
"MANAUSDT","AXSUSDT","THETAUSDT","EOSUSDT","ARUSDT","JUPUSDT","JTOUSDT","PENDLEUSDT","DYDXUSDT",
"GMXUSDT","SNXUSDT","CRVUSDT","COMPUSDT","1INCHUSDT","ENSUSDT","STXUSDT","KAVAUSDT","CFXUSDT",
"QNTUSDT","FLOWUSDT","CHZUSDT","ENAUSDT","JASMYUSDT","PEOPLEUSDT","ARKMUSDT","BOMEUSDT","WUSDT"]

def funding(sym,limit=1000):
    u="https://fapi.binance.com/fapi/v1/fundingRate?symbol=%s&limit=%d"%(sym,limit)
    for _ in range(2):
        try: return [(int(x["fundingTime"])//1000,float(x["fundingRate"])) for x in json.loads(urllib.request.urlopen(u,timeout=20).read())]
        except: time.sleep(1)
    return None
def kl1h(sym,days=210):
    out={};end=int(time.time()*1000);need=days*24
    while len(out)<need:
        u="https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=1h&limit=1500&endTime=%d"%(sym,end)
        try: r=json.loads(urllib.request.urlopen(u,timeout=20).read())
        except: return None
        if not r: break
        for x in r: out[int(x[0])//1000]=float(x[4])
        end=r[0][0]-1
        if len(r)<1500: break
    return out
def pat(kd,ks,ts):
    p=bisect.bisect_right(ks,ts)-1; return kd[ks[p]] if p>=0 else None

def analyze(sym):
    fr=funding(sym); kd=kl1h(sym)
    if not fr or not kd or len(fr)<W+30: return None
    ks=sorted(kd); sides=[]; raws=[]
    for k in range(W,len(fr)):
        ts,rate=fr[k]; past=sorted(r for _,r in fr[k-W:k])
        hi=past[int(W*(1-PCT))]; lo=past[int(W*PCT)]
        side=-1 if rate>=hi else (1 if rate<=lo else 0)
        if not side: continue
        p0=pat(kd,ks,ts); p1=pat(kd,ks,ts+HOLD*3600)
        if not p0 or not p1 or p0<=0: continue
        sides.append(side); raws.append((p1-p0)/p0*1e4)
    n=len(sides)
    if n<25: return None
    net=sum(sides[i]*raws[i] for i in range(n))-FEE*n
    obs=sum(sides[i]*raws[i] for i in range(n))
    ge=0
    for _ in range(NPERM):
        rr=raws[:]; random.shuffle(rr)
        if sum(sides[i]*rr[i] for i in range(n))>=obs: ge+=1
    return n, net, ge/NPERM

if __name__=="__main__":
    print("=== G GENIS TARAMA | %d likit coin | rolling+permutasyon fee12 ===\n"%len(UNIVERSE))
    results=[]
    for sym in UNIVERSE:
        r=analyze(sym)
        if r is None: print("  %-9s veri az/yok"%sym.replace("USDT","")); continue
        n,net,p=r; results.append((sym,n,net,p))
        flag=" <-- GECER" if (p<0.05 and net>0) else ""
        print("  %-9s n=%3d net=%+6.0f isl-basi=%+5.1f p=%.3f%s"%(sym.replace("USDT",""),n,net,net/n,p,flag))
    print()
    tested=len(results); passed=[r for r in results if r[3]<0.05 and r[2]>0]
    posnet=[r for r in results if r[2]>0]
    print("=== OZET ===")
    print("  test edilen: %d | net-pozitif: %d (%%%d) | permutasyon p<0.05 GECEN: %d (%%%d)"%(
        tested,len(posnet),100*len(posnet)//max(tested,1),len(passed),100*len(passed)//max(tested,1)))
    print("  sans-seviyesi ~%%5. Gecen-oran %%%d."%(100*len(passed)//max(tested,1)))
    print("  GECEN coinler:", ", ".join(r[0].replace("USDT","") for r in sorted(passed,key=lambda x:x[3])))
    print()
    print("(gecen-oran %5'ten BELIRGIN yuksek (>=%20) -> G yaygin-gercek + yeni adaylar.")
    print(" ~%5 ise -> 4-coin sansti, temkinli ol.)")
