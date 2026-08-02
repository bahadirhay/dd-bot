"""D neden gunlerdir islem acmiyor? Son ~14 gun ETH 15m: her barda D karari + HANGI KAPI bloklar.
Ayristirma: (1) sinyal yok |dev|<85, (2) ER-trend blok, (3) SMA-hiza blok, (4) GECER (islem).
Boylece '85 mi, ER mi, SMA mi' net gorunur. Canli parametreler."""
import urllib.request, json, time, datetime as dt

M=40; DEV=85.0; ER_GATE=0.5; ER_WIN=20; SMA_LEN=120

def kl(sym, limit=1400):
    out={}; end=int(time.time()*1000)
    while len(out)<limit:
        u="https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=15m&limit=1500&endTime=%d"%(sym,end)
        try: r=json.loads(urllib.request.urlopen(u,timeout=15).read())
        except Exception: return None
        if not r: break
        for x in r: out[int(x[0])]=(int(x[0])//1000,float(x[2]),float(x[3]),float(x[4]),float(x[5]))
        end=r[0][0]-1
        if len(r)<1500: break
    return [out[k] for k in sorted(out)]

bars=kl("ETHUSDT",1400)
if not bars: print("veri yok"); raise SystemExit
T=[b[0] for b in bars];C=[b[3] for b in bars];V=[b[4] for b in bars]
n=len(C)
# son 14 gun = 14*96 = 1344 bar
lo=max(M,SMA_LEN,ER_WIN+1, n-1344)
cat={"sinyal_yok":0,"ER_blok":0,"SMA_blok":0,"GECER":0}
passes=[]; maxdev=0; blocked_sma=[]
for i in range(lo,n):
    nu=de=0.0
    for j in range(i-M,i):
        w=V[j] if V[j]>0 else 1; nu+=C[j]*w; de+=w
    poc=nu/de; dev=(C[i]-poc)/poc*1e4
    maxdev=max(maxdev,abs(dev))
    if abs(dev)<DEV: cat["sinyal_yok"]+=1; continue
    sig="LONG" if dev<=-DEV else "SHORT"
    net=abs(C[i]-C[i-ER_WIN]); path=sum(abs(C[i-k]-C[i-k-1]) for k in range(ER_WIN)); er=net/path if path>0 else 0
    if er>=ER_GATE: cat["ER_blok"]+=1; continue
    sma=sum(C[i-SMA_LEN+1:i+1])/SMA_LEN
    if (sig=="LONG" and C[i]<=sma) or (sig=="SHORT" and C[i]>=sma):
        cat["SMA_blok"]+=1
        blocked_sma.append((T[i],sig,round(dev,0),round(er,2))); continue
    cat["GECER"]+=1
    passes.append((T[i],sig,round(dev,0),round(er,2)))

gun=(T[-1]-T[lo])/86400
print("=== D KARAR AYRISTIRMA | son %.1f gun ETH 15m (%d bar) ==="%(gun,n-lo))
print("guncel dev aralik: max |dev| bu donemde = %.0f bps (esik 85)\n"%maxdev)
tot=n-lo
for k,v in cat.items(): print("  %-12s: %4d bar  (%%%.0f)"%(k,v,100*v/tot))
print()
print("=== TESHIS ===")
if cat["GECER"]==0 and cat["SMA_blok"]==0 and cat["ER_blok"]==0:
    print("  -> Hic sinyal olmadi (|dev| hic 85'e ulasmadi). Sorun: DUSUK OYNAKLIK, esik degil.")
elif cat["SMA_blok"]>0:
    print("  -> SMA-hiza %d sinyali BLOKLADI:"%cat["SMA_blok"])
    for t,s,d,e in blocked_sma[-8:]: print("       %s  %s dev=%+.0f ER=%.2f"%(dt.datetime.fromtimestamp(t).strftime('%m-%d %H:%M'),s,d,e))
print()
if passes:
    print("=== GECEN (islem acardi) %d adet, son 8: ==="%len(passes))
    for t,s,d,e in passes[-8:]: print("   %s  %s dev=%+.0f ER=%.2f"%(dt.datetime.fromtimestamp(t).strftime('%m-%d %H:%M'),s,d,e))
else:
    print("=== HIC islem acmayacakti (tum sinyaller bloklu ya da sinyal yok) ===")
print()
# ozet oran
sinyal_var = cat["ER_blok"]+cat["SMA_blok"]+cat["GECER"]
print("OZET: %d barda sinyal cikti; %d ER-blok, %d SMA-blok, %d GECER."%(sinyal_var,cat["ER_blok"],cat["SMA_blok"],cat["GECER"]))
if sinyal_var>0:
    print("  SMA'nin payi: sinyallerin %%%.0f'ini SMA bloklad."%(100*cat["SMA_blok"]/sinyal_var))
