"""Kullanicinin TAM istegi: POC ALTINDA SHORT (ustunde LONG) = momentum, cikis = D'nin cikisi
birebir (poc-revert + min-profit 12 + ATR-SL clamp 300-600 + maxhold 16). 3 coin x 3 pencere.
Kiyas: D-FADE (dogru yon: ustunde SHORT/altinda LONG, ayni cikis). Boylece 'yon' etkisi izole.
NOT: poc-revert cikisi bir MEAN-REVERSION cikisi; momentum girisine takinca uyumsuz - test gosterir."""
import urllib.request, json, time

FEE = 12.0; M = 40; MINPROF = 12.0; MAXHOLD = 16
SL_FLOOR, SL_CEIL, ATR_MULT, ATR_N = 300.0, 600.0, 7.5, 16
DEVT = 85.0  # giris esigi (D ile ayni; hem momentum hem fade icin adil)

def kl(sym, days=75):
    out={}; end=int(time.time()*1000); need=days*96
    while len(out)<need:
        u="https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=15m&limit=1500&endTime=%d"%(sym,end)
        try: r=json.loads(urllib.request.urlopen(u,timeout=20).read())
        except Exception: return None
        if not r: break
        for x in r: out[int(x[0])]=(float(x[2]),float(x[3]),float(x[4]),float(x[5]))
        end=r[0][0]-1
        if len(r)<1500: break
    return [out[k] for k in sorted(out)]

def prep(bars):
    H=[b[0] for b in bars];L=[b[1] for b in bars];C=[b[2] for b in bars];V=[b[3] for b in bars]
    n=len(C); poc=[None]*n; atr=[400.0]*n
    for i in range(M,n):
        nu=de=0.0
        for j in range(i-M,i):
            w=V[j] if V[j]>0 else 1.0; nu+=C[j]*w; de+=w
        poc[i]=nu/de if de>0 else None
    for i in range(ATR_N,n):
        s=sum(max(H[j]-L[j],abs(H[j]-C[j-1]),abs(L[j]-C[j-1])) for j in range(i-ATR_N+1,i+1))
        atr[i]=(s/ATR_N)/C[i]*1e4 if C[i]>0 else 400.0
    return H,L,C,poc,atr

def run(pk, lo, hi, momentum):
    """momentum=True: POC altinda SHORT/ustunde LONG (kullanici). False: D-fade (tersi)."""
    H,L,C,poc,atr=pk; n=len(C); pnls=[]; i=max(M,ATR_N,lo)
    while i<min(hi,n-1):
        pc=poc[i]
        if not pc: i+=1; continue
        dev=(C[i]-pc)/pc*1e4
        if abs(dev)<DEVT: i+=1; continue
        below = dev<0
        # momentum: altinda SHORT / ustunde LONG ; fade: tersi
        if momentum: sig = "SHORT" if below else "LONG"
        else: sig = "LONG" if below else "SHORT"
        ent=C[i]; sl=min(max(ATR_MULT*atr[i],SL_FLOOR),SL_CEIL); res=None; jend=i
        for j in range(i+1,min(i+MAXHOLD+1,n)):
            jend=j
            cur=((C[j]-ent) if sig=="LONG" else (ent-C[j]))/ent*1e4
            adv=((H[j]-ent) if sig=="SHORT" else (ent-L[j]))/ent*1e4
            if adv>=sl: res=-sl-FEE; break
            pcj=poc[j]
            if pcj:  # D poc-revert cikisi: dev POC'a geri dondu + min-kar
                dj=(C[j]-pcj)/pcj*1e4
                if ((sig=="LONG" and dj>=0) or (sig=="SHORT" and dj<=0)) and cur>=MINPROF: res=cur-FEE; break
            if (j-i)>=MAXHOLD: res=cur-FEE; break
        if res is None: res=((C[jend]-ent) if sig=="LONG" else (ent-C[jend]))/ent*1e4-FEE
        pnls.append(res); i=jend+1
    return pnls

def net(v): return sum(v) if v else 0.0

if __name__=="__main__":
    print("=== POC altinda SHORT (kullanici, momentum) vs D-FADE | ayni D-cikisi | 3 coin x 3 pencere ===\n")
    agg={"MOMENTUM(altSHORT)":[0,0],"D-FADE(altLONG)":[0,0]}
    for sym in ("ETHUSDT","BTCUSDT","SOLUSDT"):
        bars=kl(sym,75)
        if not bars or len(bars)<3000: print(sym,"veri az"); continue
        pk=prep(bars); n=len(bars); start=max(M,ATR_N)+5; span=n-1-start; w=span//3
        print("=== %s ==="%sym)
        print("  pencere | MOMENTUM(alt=SHORT) | D-FADE(alt=LONG)")
        for k in range(3):
            lo=start+k*w; hi=start+(k+1)*w if k<2 else n-1
            m=run(pk,lo,hi,True); f=run(pk,lo,hi,False)
            print("  W%d      |  %+7.0f (%3d)     |  %+7.0f (%3d)"%(k+1,net(m),len(m),net(f),len(f)))
            agg["MOMENTUM(altSHORT)"][0]+=net(m);agg["MOMENTUM(altSHORT)"][1]+=len(m)
            agg["D-FADE(altLONG)"][0]+=net(f);agg["D-FADE(altLONG)"][1]+=len(f)
        print()
    print("=== TOPLAM (ayni giris esigi 85, ayni D-cikisi, sadece YON farkli) ===")
    for k,v in agg.items(): print("  %-20s net=%+.0f bps  islem=%d"%(k,v[0],v[1]))
    print("\n(Ayni sinyaller, ayni cikis, sadece yon ters. MOMENTUM(alt=SHORT) kullanicinin fikri.")
    print(" Negatifse ve D-FADE pozitifse -> 'altinda short' yon olarak yanlis, kanit.)")
