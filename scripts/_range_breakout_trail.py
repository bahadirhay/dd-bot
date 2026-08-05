"""TERSI: RANGE KIRILIM + TRAILING (kullanici: fade kaybediyorsa kirilim kazanir).
Fade (uctan ters) -6.5 bps ileri-getiri -> kirilim +6.5 gross. Tek basina fee-alti AMA kirilim
POZITIF-CARPIK (cogu ufak-zarar, birkaci BUYUK kosar). TRAILING stop kuyrugu yakalar.
Entry: fiyat LB-bar yuksegini kirarsa LONG, dibini kirarsa SHORT. Cikis: trailing (tepeden geri
cekilme) + maxhold. Erken cikan POC-momentum'dan FARKLI. 5 coin x 4 ceyrek, trailing taramasi."""
import urllib.request, json, time

LB=64; FEE=12.0; MAXHOLD=96   # kirilim koşabilir -> uzun maxhold
TRAILS=[150, 300, 500]        # tepe-kardan geri cekilme (bps) -> cik

def kl(sym,days=240):
    out={};end=int(time.time()*1000);need=days*96
    while len(out)<need:
        u="https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=15m&limit=1500&endTime=%d"%(sym,end)
        try: r=json.loads(urllib.request.urlopen(u,timeout=20).read())
        except: return None
        if not r: break
        for x in r: out[int(x[0])]=(float(x[2]),float(x[3]),float(x[4]))
        end=r[0][0]-1
        if len(r)<1500: break
    return [out[k] for k in sorted(out)]

def run(bars,lo,hi,trail):
    H=[b[0] for b in bars];L=[b[1] for b in bars];C=[b[2] for b in bars];n=len(C)
    pnls=[];i=max(LB,lo)
    while i<min(hi,n-1):
        rhi=max(H[i-LB:i]);rlo=min(L[i-LB:i])
        sig="LONG" if C[i]>rhi else ("SHORT" if C[i]<rlo else None)
        if not sig: i+=1;continue
        ent=C[i];peak=0.0;res=None;jend=i
        for j in range(i+1,min(i+MAXHOLD+1,n)):
            jend=j
            fav=((H[j]-ent) if sig=="LONG" else (ent-L[j]))/ent*1e4  # lehte azami
            cur=((C[j]-ent) if sig=="LONG" else (ent-C[j]))/ent*1e4
            peak=max(peak,fav)
            if peak-cur>=trail:   # tepe-kardan trail kadar geri cekildi -> cik
                res=cur-FEE;break
            if j-i>=MAXHOLD: res=cur-FEE;break
        if res is None: res=((C[jend]-ent) if sig=="LONG" else (ent-C[jend]))/ent*1e4-FEE
        pnls.append(res);i=jend+1
    return pnls

if __name__=="__main__":
    print("=== RANGE KIRILIM + TRAILING | 5 coin x 4 ceyrek | trail taramasi fee12 ===\n")
    agg={t:[0,0,0] for t in TRAILS}
    for sym in ("ETHUSDT","BTCUSDT","SOLUSDT","LINKUSDT","BNBUSDT"):
        bars=kl(sym,240)
        if not bars or len(bars)<6000: print(sym,"az");continue
        n=len(bars);start=LB+5;span=n-1-start;q=span//4
        row="  %-8s |"%sym
        for t in TRAILS:
            pos=0;tot=0;nn=0
            for k in range(4):
                loo=start+k*q;hii=start+(k+1)*q if k<3 else n-1
                d=run(bars,loo,hii,t);tot+=sum(d);nn+=len(d)
                if sum(d)>0: pos+=1
            agg[t][0]+=tot;agg[t][1]+=pos;agg[t][2]+=nn
            row+=" trail%d %+6.0f(%d/4) |"%(t,tot,pos)
        print(row)
    print("\n=== TOPLAM ===")
    for t in TRAILS:
        print("  trail%-4d net=%+.0f  poz-ceyrek=%d/20  islem=%d  islem-basi=%+.1f"%(t,agg[t][0],agg[t][1],agg[t][2],agg[t][0]/max(agg[t][2],1)))
    print("\n(Bir trail net>0 + coğu ceyrek + ise -> kirilim+trailing GERCEK, KULLANICI HAKLI (fade tersi).")
    print(" Hepsi negatif ise -> kirilim de fee'yi asamiyor, pozitif-carpik bile yetmiyor.)")
