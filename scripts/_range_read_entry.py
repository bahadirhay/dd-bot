"""RANGE-OKU-UCTA-GIR (kullanici cercevesi): once rejim oku (range mi?), range ise gorunur
uctan gir (swing-dip LONG / swing-tepe SHORT), aralik-disi SL, orta TP.
Kullanici tezi: 'once piyasayi oku' deger katar. Test: FILTRESIZ (her zaman uctan gir) vs
RANGE-ONLY (yalniz dusuk-ER rejimde). RANGE-ONLY belirgin iyiyse -> 'once oku' hakli.
5 coin x 4 ceyrek, fee12. Look-ahead yok (aralik gecmis barlardan)."""
import urllib.request, json, time

LB = 64          # aralik penceresi (gorunur swing hi/lo)
REGW = 96        # rejim ER penceresi (1 gun)
EDGE = 0.18      # uca yakinlik: aralik genisliginin %18'i icinde
SL_BUF = 0.12    # SL: aralik disina genisligin %12'si
FEE = 12.0; MAXHOLD = 24

def kl(sym, days=240):
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

def prep(bars):
    H=[b[0] for b in bars];L=[b[1] for b in bars];C=[b[2] for b in bars];n=len(C)
    er=[1.0]*n
    for i in range(REGW,n):
        net=abs(C[i]-C[i-REGW]);path=sum(abs(C[i-k]-C[i-k-1]) for k in range(REGW));er[i]=net/path if path>0 else 1
    return H,L,C,er

def run(pk,lo,hi,thr,range_only):
    H,L,C,er=pk;n=len(C);pnls=[];i=max(LB,REGW,lo)
    while i<min(hi,n-1):
        if range_only and er[i]>=thr:  # rejim range degil -> gir-me (KULLANICI: once oku)
            i+=1;continue
        rhi=max(H[i-LB:i]);rlo=min(L[i-LB:i]);w=rhi-rlo
        if w<=0: i+=1;continue
        near_lo = C[i]<=rlo+EDGE*w; near_hi = C[i]>=rhi-EDGE*w
        sig="LONG" if near_lo else ("SHORT" if near_hi else None)
        if not sig: i+=1;continue
        ent=C[i]
        if sig=="LONG": sl=rlo-SL_BUF*w; tp=rlo+0.5*w
        else: sl=rhi+SL_BUF*w; tp=rhi-0.5*w
        res=None;jend=i
        for j in range(i+1,min(i+MAXHOLD+1,n)):
            jend=j
            if (sig=="LONG" and L[j]<=sl) or (sig=="SHORT" and H[j]>=sl):
                res=((sl-ent) if sig=="LONG" else (ent-sl))/ent*1e4-FEE;break
            if (sig=="LONG" and H[j]>=tp) or (sig=="SHORT" and L[j]<=tp):
                res=((tp-ent) if sig=="LONG" else (ent-tp))/ent*1e4-FEE;break
            if j-i>=MAXHOLD: res=((C[j]-ent) if sig=="LONG" else (ent-C[j]))/ent*1e4-FEE;break
        if res is None: res=((C[jend]-ent) if sig=="LONG" else (ent-C[jend]))/ent*1e4-FEE
        pnls.append(res);i=jend+1
    return pnls

if __name__=="__main__":
    THR=0.30
    print("=== RANGE-oku-uctan-gir | FILTRESIZ vs RANGE-ONLY(ER<%.2f) | 5 coin x 4 ceyrek fee12 ===\n"%THR)
    agg={"FILTRESIZ":[0,0,0],"RANGE-ONLY":[0,0,0]}
    for sym in ("ETHUSDT","BTCUSDT","SOLUSDT","LINKUSDT","BNBUSDT"):
        bars=kl(sym,240)
        if not bars or len(bars)<6000: print(sym,"az");continue
        pk=prep(bars);n=len(bars);start=max(LB,REGW)+5;span=n-1-start;q=span//4
        row="  %-8s |"%sym
        for name,ro in (("FILTRESIZ",False),("RANGE-ONLY",True)):
            pos=0;tot=0;nn=0
            for k in range(4):
                loo=start+k*q;hii=start+(k+1)*q if k<3 else n-1
                d=run(pk,loo,hii,THR,ro);tot+=sum(d);nn+=len(d)
                if sum(d)>0: pos+=1
            agg[name][0]+=tot;agg[name][1]+=pos;agg[name][2]+=nn
            row+=" %s %+6.0f(%d/4,%3d) |"%(name,tot,pos,nn)
        print(row)
    print("\n=== TOPLAM ===")
    for k in ("FILTRESIZ","RANGE-ONLY"):
        print("  %-11s net=%+.0f  poz-ceyrek=%d/20  islem=%d"%(k,agg[k][0],agg[k][1],agg[k][2]))
    print("\n(RANGE-ONLY net>0 + FILTRESIZ'i geciyorsa -> 'once oku' deger katti, KULLANICI HAKLI.")
    print(" RANGE-ONLY de negatif ise -> range-uctan-giris bu veride de edge vermiyor.)")
