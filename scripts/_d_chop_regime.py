"""REJIM-KOSULLU D: chop tespit -> SMA'siz raw-D (fade, chop'ta calisir); trend -> SMA-D.
Kullanici: bu (choppy) rejimde calisan yapi kur. Chop = uzun-pencere ER dusuk (range/salinim).
Test: SMA-hep vs RAW-hep vs KOSULLU(chop->raw, trend->SMA). KOSULLU ikisini de gecer + pozitifse
kullaniciyi hakli -> rejim-yapi calisir. Gecmezse -> rejim-tespit yine guvenilmez (0/9 gibi).
5 coin x 4 ceyrek + son60g. fee16."""
import urllib.request, json, time

M=40;DEV=85.0;MINPROF=12.0;ER_GATE=0.5;ER_WIN=20;SMA_LEN=120;FEE=16
SL_F,SL_C,AM,AN=300,600,7.5,16;MAXHOLD=16
REGW=192; THRS=[0.25,0.35]  # uzun-pencere ER; dusuk=chop

def kl(sym,days=240):
    out={};end=int(time.time()*1000);need=days*96
    while len(out)<need:
        u="https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=15m&limit=1500&endTime=%d"%(sym,end)
        try: r=json.loads(urllib.request.urlopen(u,timeout=20).read())
        except: return None
        if not r: break
        for x in r: out[int(x[0])]=(float(x[2]),float(x[3]),float(x[4]),float(x[5]))
        end=r[0][0]-1
        if len(r)<1500: break
    return [out[k] for k in sorted(out)]

def prep(bars):
    H=[b[0] for b in bars];L=[b[1] for b in bars];C=[b[2] for b in bars];V=[b[3] for b in bars];n=len(C)
    poc=[None]*n;er=[0.]*n;sma=[None]*n;atr=[400.]*n;ler=[0.]*n
    for i in range(M,n):
        nu=de=0.
        for j in range(i-M,i):
            w=V[j] if V[j]>0 else 1;nu+=C[j]*w;de+=w
        poc[i]=nu/de if de>0 else None
    for i in range(n):
        if i>ER_WIN:
            net=abs(C[i]-C[i-ER_WIN]);path=sum(abs(C[i-k]-C[i-k-1]) for k in range(ER_WIN));er[i]=net/path if path>0 else 0
        if i>REGW:
            net=abs(C[i]-C[i-REGW]);path=sum(abs(C[i-k]-C[i-k-1]) for k in range(REGW));ler[i]=net/path if path>0 else 0
        if i>=SMA_LEN: sma[i]=sum(C[i-SMA_LEN+1:i+1])/SMA_LEN
        if i>=AN:
            s=sum(max(H[j]-L[j],abs(H[j]-C[j-1]),abs(L[j]-C[j-1])) for j in range(i-AN+1,i+1));atr[i]=s/AN/C[i]*1e4
    return H,L,C,poc,er,sma,atr,ler

def run(pk,lo,hi,mode,thr=0):
    """mode: 'sma','raw','cond'. cond: chop(ler<thr)->raw, trend->sma."""
    H,L,C,poc,er,sma,atr,ler=pk;n=len(C);pnls=[];i=max(M,SMA_LEN,REGW,lo)
    while i<min(hi,n-1):
        pc=poc[i]
        if not pc or sma[i] is None: i+=1;continue
        dev=(C[i]-pc)/pc*1e4
        sig="LONG" if dev<=-DEV else ("SHORT" if dev>=DEV else None)
        if not sig or er[i]>=ER_GATE: i+=1;continue
        use_sma = (mode=="sma") or (mode=="cond" and ler[i]>=thr)  # trend'de SMA uygula
        if use_sma:
            if (sig=="LONG" and C[i]<=sma[i]) or (sig=="SHORT" and C[i]>=sma[i]): i+=1;continue
        ent=C[i];sl=min(max(AM*atr[i],SL_F),SL_C);res=None;jend=i
        for j in range(i+1,min(i+MAXHOLD+1,n)):
            jend=j;cur=((C[j]-ent) if sig=="LONG" else (ent-C[j]))/ent*1e4;adv=((H[j]-ent) if sig=="SHORT" else (ent-L[j]))/ent*1e4
            if adv>=sl: res=-sl-FEE;break
            pcj=poc[j]
            if pcj:
                dj=(C[j]-pcj)/pcj*1e4
                if ((sig=="LONG" and dj>=0) or (sig=="SHORT" and dj<=0)) and cur>=MINPROF: res=cur-FEE;break
            if j-i>=MAXHOLD: res=cur-FEE;break
        if res is None: res=((C[jend]-ent) if sig=="LONG" else (ent-C[jend]))/ent*1e4-FEE
        pnls.append(res);i=jend+1
    return pnls

if __name__=="__main__":
    print("=== REJIM-KOSULLU D | SMA-hep vs RAW-hep vs KOSULLU(chop->raw) | 5 coin x 4 ceyrek ===\n")
    modes=[("SMA","sma",0),("RAW","raw",0)]+[("COND%.2f"%t,"cond",t) for t in THRS]
    agg={m[0]:[0,0] for m in modes}; rec={m[0]:0 for m in modes}
    for sym in ("ETHUSDT","BTCUSDT","SOLUSDT","LINKUSDT","BNBUSDT"):
        bars=kl(sym,240)
        if not bars or len(bars)<7000: print(sym,"az");continue
        pk=prep(bars);n=len(bars);start=max(M,SMA_LEN,REGW)+5;span=n-1-start;q=span//4
        row="  %-8s |"%sym
        for name,md,thr in modes:
            pos=0;tot=0
            for k in range(4):
                lo=start+k*q;hi=start+(k+1)*q if k<3 else n-1
                nt=sum(run(pk,lo,hi,md,thr));tot+=nt
                if nt>0: pos+=1
            agg[name][0]+=tot;agg[name][1]+=pos
            rec[name]+=sum(run(pk,max(start,n-5760),n-1,md,thr))
            row+=" %s %+5.0f(%d)|"%(name,tot,pos)
        print(row)
    print("\n=== TOPLAM (5 coin) ===")
    print("  mode     | 4-ceyrek net | poz-ceyrek/20 | son60g")
    for name,_,_ in modes:
        print("  %-8s | %+8.0f    | %2d/20         | %+7.0f"%(name,agg[name][0],agg[name][1],rec[name]))
    print("\n(COND, HEM SMA'yi HEM RAW'i gecer + poz-ceyrek yuksek + son60g iyi ise -> rejim-yapi CALISIR.")
    print(" COND ikisinin arasinda/altinda ise -> rejim-tespit yine tutmadi, SMA-hep en iyi.)")
