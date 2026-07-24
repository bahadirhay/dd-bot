"""D (POC+SMA-align) GENIS-EVREN 4-ceyrek WF: kac coin GERCEKTEN robust?
Kullanici hakli: 3 coin yetersiz orneklem. ~25 likit coin (ETH-eko altlar dahil), her biri
4 BAGIMSIZ ceyrek, fee16 (gercekci-orta). Robust = >=3/4 ceyrek pozitif. Kac tane cikacak?
Hizli: POC onceden-hesaplanir (rolling)."""
import urllib.request, json, time

M = 40; DEV = 85.0; MINPROF = 12.0; ER_GATE = 0.5; SMA_LEN = 120
SL_FLOOR, SL_CEIL, ATR_MULT, ATR_N = 300.0, 600.0, 7.5, 16
MAXHOLD = 16; FEE = 16.0

COINS = ["ETHUSDT","BTCUSDT","SOLUSDT","BNBUSDT","XRPUSDT","DOGEUSDT","AVAXUSDT","LINKUSDT",
         "ADAUSDT","SUIUSDT","APTUSDT","INJUSDT","ARBUSDT","OPUSDT","LTCUSDT","DOTUSDT",
         "NEARUSDT","ATOMUSDT","FILUSDT","UNIUSDT","AAVEUSDT","TIAUSDT","SEIUSDT","TONUSDT","PEPEUSDT"]

def kl(sym, days=200):
    out = {}; end = int(time.time() * 1000); need = days * 96
    while len(out) < need:
        u = ("https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=15m&limit=1500&endTime=%d" % (sym, end))
        try: r = json.loads(urllib.request.urlopen(u, timeout=20).read())
        except Exception: return None
        if not r: break
        for x in r:
            out[int(x[0])] = (float(x[2]), float(x[3]), float(x[4]), float(x[5]))
        end = r[0][0] - 1
        if len(r) < 1500: break
    return [out[k] for k in sorted(out)]

def prep(bars):
    H=[b[0] for b in bars];L=[b[1] for b in bars];C=[b[2] for b in bars];V=[b[3] for b in bars]
    n=len(C)
    # rolling POC (hacim-agirlikli, son M bar)
    poc=[None]*n
    for i in range(M,n):
        nu=de=0.0
        for j in range(i-M,i):
            v=V[j] if V[j]>0 else 1.0; nu+=C[j]*v; de+=v
        poc[i]=nu/de if de>0 else None
    # rolling ER(20), SMA(120), ATR(16)
    er=[0.0]*n; sma=[None]*n; atr=[400.0]*n
    for i in range(n):
        if i>20:
            net=abs(C[i]-C[i-20]); path=sum(abs(C[i-k]-C[i-k-1]) for k in range(20))
            er[i]=net/path if path>0 else 0.0
        if i>=SMA_LEN: sma[i]=sum(C[i-SMA_LEN+1:i+1])/SMA_LEN
        if i>=ATR_N:
            s=sum(max(H[j]-L[j],abs(H[j]-C[j-1]),abs(L[j]-C[j-1])) for j in range(i-ATR_N+1,i+1))
            atr[i]=(s/ATR_N)/C[i]*1e4 if C[i]>0 else 400.0
    return H,L,C,poc,er,sma,atr

def scan(pk, lo, hi):
    H,L,C,poc,er,sma,atr=pk; n=len(C); trades=[]; i=max(M,SMA_LEN,96,lo)
    while i<min(hi,n-1):
        pc=poc[i]
        if not pc or sma[i] is None: i+=1; continue
        dev=(C[i]-pc)/pc*1e4
        sig="LONG" if dev<=-DEV else ("SHORT" if dev>=DEV else None)
        if not sig or er[i]>=ER_GATE: i+=1; continue
        if (sig=="LONG" and C[i]<=sma[i]) or (sig=="SHORT" and C[i]>=sma[i]): i+=1; continue
        ent=C[i]; sl=min(max(ATR_MULT*atr[i],SL_FLOOR),SL_CEIL); res=None; jend=i
        for j in range(i+1,min(i+MAXHOLD+1,n)):
            jend=j
            cur=((C[j]-ent) if sig=="LONG" else (ent-C[j]))/ent*1e4
            adv=((H[j]-ent) if sig=="SHORT" else (ent-L[j]))/ent*1e4
            if adv>=sl: res=-sl-FEE; break
            pcj=poc[j]
            if pcj:
                dj=(C[j]-pcj)/pcj*1e4
                if ((sig=="LONG" and dj>=0) or (sig=="SHORT" and dj<=0)) and cur>=MINPROF: res=cur-FEE; break
            if (j-i)>=MAXHOLD: res=cur-FEE; break
        if res is None: res=((C[jend]-ent) if sig=="LONG" else (ent-C[jend]))/ent*1e4-FEE
        trades.append(res); i=jend+1
    return trades

if __name__=="__main__":
    print("=== D GENIS-EVREN 4-ceyrek WF | ~200 gun 15m | fee%d | robust=>=3/4 ceyrek+ ===\n"%int(FEE))
    print("  %-9s | isl/ay | Q1   Q2   Q3   Q4  | net   | poz/4"%"coin")
    robust=[]
    for sym in COINS:
        bars=kl(sym,200)
        if not bars or len(bars)<7000: print("  %-9s | veri az"%sym); continue
        pk=prep(bars); n=len(bars); start=max(M,SMA_LEN,96)+5; span=n-1-start; q=span//4
        qn=[]; days=n*15/1440.0; tot=0; alln=0
        for k in range(4):
            lo=start+k*q; hi=start+(k+1)*q if k<3 else n-1
            t=scan(pk,lo,hi); qn.append(sum(t)); tot+=sum(t); alln+=len(t)
        pos=sum(1 for x in qn if x>0)
        freq=alln/days*30
        flag="OK" if pos>=3 else ""
        if pos>=3: robust.append((sym,freq,tot,pos))
        print("  %-9s | %5.1f  |%+5.0f %+5.0f %+5.0f %+5.0f |%+6.0f | %d/4 %s"%(sym,freq,qn[0],qn[1],qn[2],qn[3],tot,pos,flag))
    print("\n=== ROBUST (>=3/4 ceyrek pozitif, fee%d) ==="%int(FEE))
    for sym,freq,tot,pos in sorted(robust,key=lambda x:-x[2]):
        print("  %-9s freq=%.1f/ay net=%+.0f (%d/4)"%(sym,freq,tot,pos))
    print("\nTOPLAM robust coin: %d / %d test edildi"%(len(robust),len(COINS)))
