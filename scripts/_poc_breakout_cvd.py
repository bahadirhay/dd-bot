"""POC-kirilim + CVD + Rejim onayli momentum. Kullanici fikri: POC kirilinca, CVD ve rejim
onaylarsa, fiyat ustundeyse LONG altindaysa SHORT (kirilim yonu = momentum).
UYARI: POC-momentum = ters-D (test edildi -6865). CVD/rejim tek tek katki vermedi. Ama tam
kombinasyon denenmedi -> RAW vs +CVD vs +CVD+REGIME. Onaylar deger katiyor mu? 3 coin x 3 pencere."""
import urllib.request, json, time

FEE = 12.0; M = 40; SL_BPS = 200.0; MAXHOLD = 24
CVD_WIN = 6; ER_WIN = 20; ER_THR = 0.40  # rejim: trend (ER yuksek) = kirilim-dostu

def kl(sym, days=75):
    out = {}; end = int(time.time() * 1000); need = days * 96
    while len(out) < need:
        u = "https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=15m&limit=1500&endTime=%d" % (sym, end)
        try: r = json.loads(urllib.request.urlopen(u, timeout=20).read())
        except Exception: return None
        if not r: break
        for x in r:
            vol = float(x[5]); buy = float(x[9])  # taker buy base
            out[int(x[0])] = (float(x[2]), float(x[3]), float(x[4]), vol, 2 * buy - vol)  # H,L,C,vol,delta
        end = r[0][0] - 1
        if len(r) < 1500: break
    return [out[k] for k in sorted(out)]

def prep(bars):
    H=[b[0] for b in bars];L=[b[1] for b in bars];C=[b[2] for b in bars];V=[b[3] for b in bars];D=[b[4] for b in bars]
    n=len(C); poc=[None]*n; er=[0.0]*n; cvd=[0.0]*n
    for i in range(M,n):
        nu=de=0.0
        for j in range(i-M,i):
            w=V[j] if V[j]>0 else 1.0; nu+=C[j]*w; de+=w
        poc[i]=nu/de if de>0 else None
    for i in range(n):
        if i>=CVD_WIN: cvd[i]=sum(D[i-CVD_WIN+1:i+1])   # son K bar net taker-delta
        if i>ER_WIN:
            net=abs(C[i]-C[i-ER_WIN]); path=sum(abs(C[i-k]-C[i-k-1]) for k in range(ER_WIN))
            er[i]=net/path if path>0 else 0.0
    return H,L,C,poc,er,cvd

def run(pk, lo, hi, use_cvd, use_reg):
    H,L,C,poc,er,cvd=pk; n=len(C); pnls=[]; i=max(M,ER_WIN,lo)
    while i<min(hi,n-1):
        pc=poc[i]; pcp=poc[i-1]
        if not pc or not pcp: i+=1; continue
        up = C[i]>pc and C[i-1]<=pcp      # POC yukari kirilim -> LONG (momentum)
        dn = C[i]<pc and C[i-1]>=pcp      # POC asagi kirilim -> SHORT
        sig = "LONG" if up else ("SHORT" if dn else None)
        if not sig: i+=1; continue
        if use_cvd:  # CVD kirilim yonunu onaylamali
            if (sig=="LONG" and cvd[i]<=0) or (sig=="SHORT" and cvd[i]>=0): i+=1; continue
        if use_reg:  # rejim: trend (ER>=esik) olmali (kirilim trendde tutar)
            if er[i]<ER_THR: i+=1; continue
        ent=C[i]; sl=ent*(1-SL_BPS/1e4) if sig=="LONG" else ent*(1+SL_BPS/1e4)
        res=None; jend=i
        for j in range(i+1,min(i+MAXHOLD+1,n)):
            jend=j
            if sig=="LONG" and L[j]<=sl: res=-SL_BPS-FEE; break
            if sig=="SHORT" and H[j]>=sl: res=SL_BPS*-1-FEE; break  # -SL
            pcj=poc[j]
            if pcj and ((sig=="LONG" and C[j]<pcj) or (sig=="SHORT" and C[j]>pcj)):  # POC'a geri kesti = cik
                res=((C[j]-ent) if sig=="LONG" else (ent-C[j]))/ent*1e4-FEE; break
            if (j-i)>=MAXHOLD: res=((C[j]-ent) if sig=="LONG" else (ent-C[j]))/ent*1e4-FEE; break
        if res is None: res=((C[jend]-ent) if sig=="LONG" else (ent-C[jend]))/ent*1e4-FEE
        pnls.append(res); i=jend+1
    return pnls

def net(v): return sum(v) if v else 0.0

if __name__=="__main__":
    print("=== POC-kirilim momentum: RAW vs +CVD vs +CVD+REGIME | 3 coin x 3 pencere ===\n")
    agg={"RAW":[0,0],"+CVD":[0,0],"+CVD+REG":[0,0]}
    for sym in ("ETHUSDT","BTCUSDT","SOLUSDT"):
        bars=kl(sym,75)
        if not bars or len(bars)<3000: print(sym,"veri az"); continue
        pk=prep(bars); n=len(bars); start=max(M,ER_WIN)+5; span=n-1-start; w=span//3
        print("=== %s (bar=%d) ==="%(sym,n))
        print("  pencere |   RAW      |   +CVD     |  +CVD+REG")
        for k in range(3):
            lo=start+k*w; hi=start+(k+1)*w if k<2 else n-1
            r=run(pk,lo,hi,False,False); cv=run(pk,lo,hi,True,False); cr=run(pk,lo,hi,True,True)
            print("  W%d      |%+7.0f(%3d)|%+7.0f(%3d)|%+7.0f(%3d)"%(k+1,net(r),len(r),net(cv),len(cv),net(cr),len(cr)))
            agg["RAW"][0]+=net(r);agg["RAW"][1]+=len(r)
            agg["+CVD"][0]+=net(cv);agg["+CVD"][1]+=len(cv)
            agg["+CVD+REG"][0]+=net(cr);agg["+CVD+REG"][1]+=len(cr)
        print()
    print("=== TOPLAM ===")
    for k in ("RAW","+CVD","+CVD+REG"):
        print("  %-9s net=%+.0f bps  islem=%d"%(k,agg[k][0],agg[k][1]))
    print("\n(Herhangi biri belirgin POZITIF + robust ise -> POC-kirilim yasiyor. Onaylar RAW'i")
    print(" belirgin iyilestiriyorsa CVD/rejim deger katti. Hepsi negatifse -> ters-D dogrulandi.)")
