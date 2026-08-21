"""G'ye TREND-REJIM FILTRESI testi. Hipotez: G (funding kontraryan) guclu trende karsi kaybediyor;
gunluk-trende gore filtrelemek net'i artirir mi? Test-edilen filtreler:
  BASE       = tum G islemleri (mevcut canli)
  ALIGN      = sadece gunluk-trendle AYNI yonde contrarian (trende-karsi = ATLA)
  ER<x       = sadece Efficiency-Ratio dusukken (range) gir, yuksekken (trend) ATLA
Her biri: net, isl-basi, n, IKI-YARI. Filtre gercekten yardim ediyorsa net UP + iki-yari+ + islem
cok dusmemeli. Overfit kontrolu: coklu coin + iki-yari. EMIR YOK, sadece analiz.
"""
import json, urllib.request, time, bisect
W=120; PCT=0.15; HOLD=24; FEE=12; N=40  # N=F'nin gunluk lookback'i
COINS=["ETHUSDT","AVAXUSDT","XRPUSDT","ETCUSDT","LINKUSDT","SUIUSDT","TAOUSDT","HBARUSDT","BNBUSDT","DOGEUSDT"]
BASE="https://fapi.binance.com"
def funding(s):
    try: return [(int(x["fundingTime"])//1000,float(x["fundingRate"])) for x in json.loads(urllib.request.urlopen(f"{BASE}/fapi/v1/fundingRate?symbol={s}&limit=1000",timeout=20).read())]
    except: return None
def kl1h(s,days=220):
    out={};end=int(time.time()*1000)
    while len(out)<days*24:
        try: r=json.loads(urllib.request.urlopen(f"{BASE}/fapi/v1/klines?symbol={s}&interval=1h&limit=1500&endTime={end}",timeout=20).read())
        except: return None
        if not r: break
        for x in r: out[int(x[0])//1000]=float(x[4])
        end=r[0][0]-1
        if len(r)<1500: break
    return out
def kld(s,days=400):
    try: r=json.loads(urllib.request.urlopen(f"{BASE}/fapi/v1/klines?symbol={s}&interval=1d&limit={days}",timeout=20).read())
    except: return None
    return [(int(x[0])//1000,float(x[4])) for x in r]
def pat(kd,ks,t):
    p=bisect.bisect_right(ks,t)-1; return kd[ks[p]] if p>=0 else None

# her coin: G islemleri + o andaki gunluk-trend + ER
trades=[]  # (side, ret_bps, trend_sign, er)
for s in COINS:
    fr=funding(s); kd=kl1h(s); dd=kld(s)
    if not fr or not kd or not dd: continue
    ks=sorted(kd); dts=[t for t,_ in dd]; dpx=[p for _,p in dd]
    for k in range(W,len(fr)):
        ts,rate=fr[k]; past=sorted(r for _,r in fr[k-W:k])
        hi=past[int(W*(1-PCT))]; lo=past[int(W*PCT)]
        side=-1 if rate>=hi else (1 if rate<=lo else 0)
        if not side: continue
        p0=pat(kd,ks,ts); p1=pat(kd,ks,ts+HOLD*3600)
        if not p0 or not p1 or p0<=0: continue
        ret=side*(p1-p0)/p0*1e4
        # gunluk trend + ER at ts
        di=bisect.bisect_right(dts,ts)-1
        if di<N: continue
        c_now=dpx[di]; c_prev=dpx[di-N]
        trend=1 if c_now>c_prev else -1
        moves=sum(abs(dpx[j]-dpx[j-1]) for j in range(di-N+1,di+1))
        er=abs(c_now-c_prev)/moves if moves>0 else 0
        trades.append((side,ret,trend,er))

def stats(name,tr):
    n=len(tr)
    if n<20: print("  %-16s n=%d (az)"%(name,n)); return
    net=sum(r for _,r,_,_ in tr)-FEE*n; h=n//2
    h1=sum(r for _,r,_,_ in tr[:h])-FEE*h; h2=sum(r for _,r,_,_ in tr[h:])-FEE*(n-h)
    ok="iki-yari+" if h1>0 and h2>0 else "TUTARSIZ"
    print("  %-16s n=%4d  net=%+7.0f  isl=%+5.1f  1y=%+6.0f 2y=%+6.0f  [%s]"%(name,n,net,net/n,h1,h2,ok))

print("=== G TREND-FILTRE TESTI | %d coin, %d G-islemi ===\n"%(len(COINS),len(trades)))
stats("BASE (tum)",trades)
print("  --- ALIGN: sadece gunluk-trendle ayni yon (trende-karsi ATLA) ---")
stats("ALIGN",[t for t in trades if t[0]==t[2]])
print("  (atilan: trende-KARSI islemler; bunlar kotu mu?)")
stats("  trende-KARSI",[t for t in trades if t[0]!=t[2]])
print("  --- ER filtresi: sadece range'de (dusuk ER) gir ---")
for thr in [0.5,0.4,0.3]:
    stats("ER<%.1f"%thr,[t for t in trades if t[3]<thr])
print("  (atilan: yuksek-ER=trend; onlar kotu mu?)")
stats("  ER>=0.5 (trend)",[t for t in trades if t[3]>=0.5])
print()
print("YORUM: bir filtre BASE'i geciyorsa -> net UP + iki-yari+ + islem-sayisi makul kalmali.")
print("Trende-KARSI / yuksek-ER kotuyse -> filtre mantikli. Degilse -> reaktif his, uygulamayiz.")
