"""POC-gecisini FADE et (kirilimin TERSI): yukari-gecis->SHORT, asagi-gecis->LONG (geri doner).
Kullanici: kirilim devam etmiyorsa tersini test et. Bu D ailesi. Test: esik=0 (her gecis, churn)
vs esik>0 (gecis POC'tan X bps otesine gecince fade = daha secici, D'ye yaklasir). 3 coin x 3 pencere.
Ayrica RAW-momentum (kiyas) goster. Amac: fade yonu + esigin rolu net olsun."""
import urllib.request, json, time

FEE = 12.0; M = 40; SL_BPS = 200.0; MAXHOLD = 16
THRS = [0, 40, 85]  # gecis POC'u kac bps astiktan sonra fade (0=her gecis, 85=D-benzeri)

def kl(sym, days=75):
    out={}; end=int(time.time()*1000); need=days*96
    while len(out)<need:
        u="https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=15m&limit=1500&endTime=%d"%(sym,end)
        try: r=json.loads(urllib.request.urlopen(u,timeout=20).read())
        except Exception: return None
        if not r: break
        for x in r:
            vol=float(x[5]); out[int(x[0])]=(float(x[2]),float(x[3]),float(x[4]),vol)
        end=r[0][0]-1
        if len(r)<1500: break
    return [out[k] for k in sorted(out)]

def prep(bars):
    H=[b[0] for b in bars];L=[b[1] for b in bars];C=[b[2] for b in bars];V=[b[3] for b in bars]
    n=len(C); poc=[None]*n
    for i in range(M,n):
        nu=de=0.0
        for j in range(i-M,i):
            w=V[j] if V[j]>0 else 1.0; nu+=C[j]*w; de+=w
        poc[i]=nu/de if de>0 else None
    return H,L,C,poc

def run(pk, lo, hi, thr, momentum=False):
    H,L,C,poc=pk; n=len(C); pnls=[]; i=max(M,lo)
    while i<min(hi,n-1):
        pc=poc[i]
        if not pc: i+=1; continue
        dev=(C[i]-pc)/pc*1e4
        # POC ustunde (dev>0) + esigi asmis
        if dev>=thr and dev>0: side_fade="SHORT"
        elif dev<=-thr and dev<0: side_fade="LONG"
        else: i+=1; continue
        sig = ("LONG" if side_fade=="SHORT" else "SHORT") if momentum else side_fade
        ent=C[i]; sl=ent*(1-SL_BPS/1e4) if sig=="LONG" else ent*(1+SL_BPS/1e4); res=None; jend=i
        for j in range(i+1,min(i+MAXHOLD+1,n)):
            jend=j
            if sig=="LONG" and L[j]<=sl: res=-SL_BPS-FEE; break
            if sig=="SHORT" and H[j]>=sl: res=-SL_BPS-FEE; break
            pcj=poc[j]
            if pcj:  # FADE cikis: POC'a geri dondu
                dj=(C[j]-pcj)/pcj*1e4
                back=(sig=="LONG" and dj>=0) or (sig=="SHORT" and dj<=0)
                if back: res=((C[j]-ent) if sig=="LONG" else (ent-C[j]))/ent*1e4-FEE; break
            if (j-i)>=MAXHOLD: res=((C[j]-ent) if sig=="LONG" else (ent-C[j]))/ent*1e4-FEE; break
        if res is None: res=((C[jend]-ent) if sig=="LONG" else (ent-C[jend]))/ent*1e4-FEE
        pnls.append(res); i=jend+1
    return pnls

def net(v): return sum(v) if v else 0.0

if __name__=="__main__":
    print("=== POC FADE (kirilimin tersi) esik taramasi + momentum kiyas | 3 coin x 3 pencere ===\n")
    agg={("FADE",t):[0,0] for t in THRS}; agg[("MOM",0)]=[0,0]
    for sym in ("ETHUSDT","BTCUSDT","SOLUSDT"):
        bars=kl(sym,75)
        if not bars or len(bars)<3000: print(sym,"veri az"); continue
        pk=prep(bars); n=len(bars); start=M+5; span=n-1-start; w=span//3
        print("=== %s ==="%sym)
        print("  pencere | FADE-0     | FADE-40    | FADE-85(D) | MOM-0(kiyas)")
        for k in range(3):
            lo=start+k*w; hi=start+(k+1)*w if k<2 else n-1
            cells=[]
            for t in THRS:
                r=run(pk,lo,hi,t,False); cells.append((net(r),len(r))); agg[("FADE",t)][0]+=net(r); agg[("FADE",t)][1]+=len(r)
            mr=run(pk,lo,hi,0,True); agg[("MOM",0)][0]+=net(mr); agg[("MOM",0)][1]+=len(mr)
            print("  W%d      |%+6.0f(%3d)|%+6.0f(%3d)|%+6.0f(%3d)|%+6.0f(%3d)"%(
                k+1,cells[0][0],cells[0][1],cells[1][0],cells[1][1],cells[2][0],cells[2][1],net(mr),len(mr)))
        print()
    print("=== TOPLAM ===")
    for t in THRS: print("  FADE-%-3d net=%+.0f islem=%d"%(t,agg[("FADE",t)][0],agg[("FADE",t)][1]))
    print("  MOM-0   net=%+.0f islem=%d (momentum/kirilim = kiyas)"%(agg[("MOM",0)][0],agg[("MOM",0)][1]))
    print("\n(FADE esik yukseldikce iyilesip pozitife donerse -> D'nin esiginin (85) rolu kanit.")
    print(" FADE-0 churn'de kaybeder, FADE-85 ~ D. Momentum ayna: negatif.)")
