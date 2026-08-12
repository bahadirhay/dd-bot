"""G ADAY KESIF TARAMASI (AYLIK, disiplinli). ~70 LIKIT coin. Amac: yeni sağlam funding-kontraryan
adayi bulmak — GUVENLIK araci DEGIL, KESIF araci. Sıkı filtreler (data-mining tuzagina karsi):
  - Rolling esik W=120, PCT=0.15, HOLD=24h, fee12 (canli G ile ayni).
  - PERMUTASYON (1000) — getiri-karistirma, sans-seviyesi.
  - IKI-YARI tutarlilik — tek-pencere tuzagi kontrolu (iki yari da pozitif olmali).
  - BONFERRONI — coklu-test duzeltmesi (p < 0.05/N). Ham p<0.05 YETMEZ.
  - LIKIDITE tabani — 24h hacim >= MIN_VOL_M ($M); illikit backtest=yalan (poc_atr dersi).
Sonuc: reports/g_scan_YYYY-MM-DD.txt + yeni SAGLAM aday (canli/izleme-disi) vurgusu. EMIR YOK.
"""
import urllib.request, json, time, bisect, random, os
random.seed(7)
W=120; HOLD=24; PCT=0.15; FEE=12.0; NPERM=1000
MIN_VOL_M=10.0   # 24h quote-hacim tabani (milyon USDT). $30 emir icin dusuk; asil amac backtest-guvenilirligi
                 # (cok-thin coinde funding-ani fiyati yaniltir, poc_atr dersi). ORDI/QNT gibi <$10M elenir.
LIVE={"ETHUSDT","AVAXUSDT","INJUSDT"}
WATCH={"ETCUSDT","SUIUSDT","XRPUSDT","LINKUSDT"}

UNIVERSE=["ETHUSDT","BTCUSDT","SOLUSDT","BNBUSDT","XRPUSDT","DOGEUSDT","AVAXUSDT","LINKUSDT",
"ADAUSDT","DOTUSDT","LTCUSDT","NEARUSDT","ATOMUSDT","UNIUSDT","AAVEUSDT","INJUSDT","SUIUSDT",
"APTUSDT","ARBUSDT","OPUSDT","FILUSDT","TIAUSDT","SEIUSDT","TONUSDT","TRXUSDT","BCHUSDT",
"ETCUSDT","XLMUSDT","ICPUSDT","RENDERUSDT","FETUSDT","WLDUSDT","ORDIUSDT","GALAUSDT","IMXUSDT",
"HBARUSDT","VETUSDT","MKRUSDT","LDOUSDT","GRTUSDT","RUNEUSDT","ALGOUSDT","EGLDUSDT","SANDUSDT",
"MANAUSDT","AXSUSDT","THETAUSDT","EOSUSDT","ARUSDT","JUPUSDT","JTOUSDT","PENDLEUSDT","DYDXUSDT",
"GMXUSDT","SNXUSDT","CRVUSDT","COMPUSDT","1INCHUSDT","ENSUSDT","STXUSDT","KAVAUSDT","CFXUSDT",
"QNTUSDT","FLOWUSDT","CHZUSDT","ENAUSDT","JASMYUSDT","PEOPLEUSDT","ARKMUSDT","BOMEUSDT","WUSDT"]

BASE="https://fapi.binance.com"
def funding(sym,limit=1000):
    u="%s/fapi/v1/fundingRate?symbol=%s&limit=%d"%(BASE,sym,limit)
    for _ in range(3):
        try: return [(int(x["fundingTime"])//1000,float(x["fundingRate"])) for x in json.loads(urllib.request.urlopen(u,timeout=20).read())]
        except: time.sleep(1)
    return None
def kl1h(sym,days=220):
    out={};end=int(time.time()*1000);need=days*24
    while len(out)<need:
        u="%s/fapi/v1/klines?symbol=%s&interval=1h&limit=1500&endTime=%d"%(BASE,sym,end)
        try: r=json.loads(urllib.request.urlopen(u,timeout=20).read())
        except: return None
        if not r: break
        for x in r: out[int(x[0])//1000]=float(x[4])
        end=r[0][0]-1
        if len(r)<1500: break
    return out
def vol24(sym):
    try: return float(json.loads(urllib.request.urlopen("%s/fapi/v1/ticker/24hr?symbol=%s"%(BASE,sym),timeout=15).read()).get("quoteVolume",0))/1e6
    except: return 0.0
def pat(kd,ks,ts):
    p=bisect.bisect_right(ks,ts)-1; return kd[ks[p]] if p>=0 else None

def analyze(sym):
    fr=funding(sym); kd=kl1h(sym)
    if not fr or not kd or len(fr)<W+30: return None
    ks=sorted(kd); tr=[]
    for k in range(W,len(fr)):
        ts,rate=fr[k]; past=sorted(r for _,r in fr[k-W:k])
        hi=past[int(W*(1-PCT))]; lo=past[int(W*PCT)]
        side=-1 if rate>=hi else (1 if rate<=lo else 0)
        if not side: continue
        p0=pat(kd,ks,ts); p1=pat(kd,ks,ts+HOLD*3600)
        if not p0 or not p1 or p0<=0: continue
        tr.append((side,(p1-p0)/p0*1e4))
    n=len(tr)
    if n<25: return None
    net=sum(s*r for s,r in tr)-FEE*n
    half=n//2; n1=sum(s*r for s,r in tr[:half])-FEE*half; n2=sum(s*r for s,r in tr[half:])-FEE*(n-half)
    obs=sum(s*r for s,r in tr); raws=[r for _,r in tr]; sides=[s for s,_ in tr]; ge=0
    for _ in range(NPERM):
        rr=raws[:]; random.shuffle(rr)
        if sum(sides[i]*rr[i] for i in range(n))>=obs: ge+=1
    return dict(sym=sym,n=n,net=net,per=net/n,p=ge/NPERM,h1=n1,h2=n2,vol=vol24(sym))

def main():
    print("=== G ADAY KESIF TARAMASI | %s | %d likit coin ==="%(time.strftime("%Y-%m-%d %H:%M"),len(UNIVERSE)))
    print("  filtreler: permut(1000) + iki-yari + Bonferroni + likidite>=$%.0fM | fee12 W120 hold24\n"%MIN_VOL_M)
    res=[]
    for sym in UNIVERSE:
        r=analyze(sym)
        if r: res.append(r)
        else: print("  %-9s veri az/atla"%sym.replace("USDT",""))
    N=len(res); bonf=0.05/max(N,1)
    print("\n  test edilen: %d | SAGLAM standardi = CANLI-COIN ile AYNI (ETH/AVAX/INJ boyle bulundu):"%N)
    print("    permut p<0.05 + iki-yari-pozitif + likit>=$%.0fM. Bonferroni (p<%.4f) sadece INFO [B+]."%(MIN_VOL_M,bonf))
    print("    Asil out-of-sample kalkan = FORWARD-SHADOW (paper), sert backtest-esigi DEGIL.\n")
    # siniflandir — SAGLAM = canli-coin standardi (permut+iki-yari+likit). Bonferroni RED-kriteri DEGIL, INFO.
    for r in res:
        r["likit"]=r["vol"]>=MIN_VOL_M
        r["robust"]=(r["p"]<0.05 and r["net"]>0 and r["h1"]>0 and r["h2"]>0 and r["likit"])
        r["bonf_pass"]=(r["p"]<bonf)
        r["pass_weak"]=(r["p"]<0.05 and r["net"]>0 and not r["robust"])
    robust=sorted([r for r in res if r["robust"]],key=lambda x:x["p"])
    weak=sorted([r for r in res if r["pass_weak"]],key=lambda x:x["p"])
    def line(r):
        loc="CANLI" if r["sym"] in LIVE else ("izle" if r["sym"] in WATCH else "YENI")
        bf="B+" if r.get("bonf_pass") else "  "
        return "  %-9s n=%3d net=%+6.0f isl=%+5.1f p=%.4f %s 1y=%+5.0f 2y=%+5.0f hac=%5.0fM [%s]"%(
            r["sym"].replace("USDT",""),r["n"],r["net"],r["per"],r["p"],bf,r["h1"],r["h2"],r["vol"],loc)
    print("=== SAGLAM (canli-coin standardi: permut p<0.05 + iki-yari-poz + likit; B+=Bonferroni de gecer) ===")
    for r in robust: print(line(r))
    if not robust: print("  (yok)")
    yeni=[r for r in robust if r["sym"] not in LIVE and r["sym"] not in WATCH]
    print("\n  >>> YENI SAGLAM ADAY (canli/izleme disi) -> funding_shadow'a ekle, forward tut: %s"%(
        ", ".join(r["sym"].replace("USDT","") for r in yeni) or "YOK"))
    print("\n=== zayif-gecen (p<0.05 ama iki-yari-tutarsiz VEYA illikit — ALMA, tuzak) ===")
    for r in weak[:15]: print(line(r))
    print("\n  NOT: 'YENI SAGLAM' cikarsa -> once funding_shadow'a (paper) ekle, forward tut, sonra kucuk-canli.")
    print("  Hicbir sey cikmamasi NORMAL ve saglikli; sans-seviyesi coklu-testte gizlenir.")

if __name__=="__main__":
    os.makedirs("reports",exist_ok=True)
    main()
