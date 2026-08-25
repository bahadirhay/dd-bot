"""poc_atr (D + ATR-normalize esik/SL) DISIPLINLI dogrulama: 4-ceyrek WF + slippage duyarliligi.
+4996/485 verideki en iyiydi AMA egzotik coinler (RAVE/SNDK/1000PEPE) illikit -> gercek slippage
brutal. Test: her coin 4-ceyrek, fee 12(iyimser)/25/40(egzotik-gercekci). LIKIT vs EGZOTIK ayri.
Kural: dev_thr=max(2.36*ATR,40), SL=max(2.5*ATR,30), giris fade, cikis POC-donus."""
import urllib.request, json, time

M=40; ATR_P=14; DEV_MULT=2.36; SL_MULT=2.5; DEV_FLOOR=40.0; SL_FLOOR=30.0; MAXHOLD=16
LIKIT=["ETHUSDT","BTCUSDT","SOLUSDT","AVAXUSDT","ARBUSDT","INJUSDT","LINKUSDT"]
EGZOTIK=["GLMUSDT","SYNUSDT","PYTHUSDT","RAVEUSDT","1000PEPEUSDT","SNDKUSDT"]

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
    poc=[None]*n;atr=[0.]*n
    for i in range(M,n):
        nu=de=0.
        for j in range(i-M,i):
            w=V[j] if V[j]>0 else 1;nu+=C[j]*w;de+=w
        poc[i]=nu/de if de>0 else None
    for i in range(ATR_P,n):
        s=sum(max(H[j]-L[j],abs(H[j]-C[j-1]),abs(L[j]-C[j-1])) for j in range(i-ATR_P+1,i+1))
        atr[i]=s/ATR_P/C[i]*1e4 if C[i]>0 else 0
    return H,L,C,poc,atr

def run(pk,lo,hi,fee):
    H,L,C,poc,atr=pk;n=len(C);pnls=[];i=max(M,ATR_P,lo)
    while i<min(hi,n-1):
        pc=poc[i]
        if not pc or atr[i]<=0: i+=1;continue
        dev=(C[i]-pc)/pc*1e4; dev_thr=max(DEV_MULT*atr[i],DEV_FLOOR); sl_bps=max(SL_MULT*atr[i],SL_FLOOR)
        sig="LONG" if dev<=-dev_thr else ("SHORT" if dev>=dev_thr else None)
        if not sig: i+=1;continue
        ent=C[i];sl=ent*(1-sl_bps/1e4) if sig=="LONG" else ent*(1+sl_bps/1e4);res=None;jend=i
        for j in range(i+1,min(i+MAXHOLD+1,n)):
            jend=j;cur=((C[j]-ent) if sig=="LONG" else (ent-C[j]))/ent*1e4
            if (sig=="LONG" and L[j]<=sl) or (sig=="SHORT" and H[j]>=sl): res=-sl_bps-fee;break
            pcj=poc[j];dj=(C[j]-pcj)/pcj*1e4 if pcj else 0
            if ((sig=="LONG" and dj>=0) or (sig=="SHORT" and dj<=0)) and cur>=0: res=cur-fee;break
            if j-i>=MAXHOLD: res=cur-fee;break
        if res is None: res=((C[jend]-ent) if sig=="LONG" else (ent-C[jend]))/ent*1e4-fee
        pnls.append(res);i=jend+1
    return pnls

def wf(pk,fees=(12,25,40)):
    C=pk[2];n=len(C);start=max(M,ATR_P)+5;span=n-1-start;q=span//4
    out={}
    for fee in fees:
        pos=0;tot=0;nn=0
        for k in range(4):
            lo=start+k*q;hi=start+(k+1)*q if k<3 else n-1
            d=run(pk,lo,hi,fee);tot+=sum(d);nn+=len(d)
            if sum(d)>0: pos+=1
        out[fee]=(tot,pos,nn)
    return out

if __name__=="__main__":
    print("=== poc_atr 4-ceyrek WF + slippage | fee12/25/40 | net bps(poz-ceyrek) ===\n")
    for grup,coins in (("LIKIT",LIKIT),("EGZOTIK",EGZOTIK)):
        print("--- %s ---"%grup)
        print("  coin        | fee12          | fee25          | fee40")
        for sym in coins:
            bars=kl(sym,240)
            if not bars or len(bars)<4000: print("  %-11s | veri az (%s bar)"%(sym,len(bars) if bars else 0));continue
            r=wf(prep(bars))
            print("  %-11s | %+6.0f(%d/4,%3d) | %+6.0f(%d/4) | %+6.0f(%d/4)"%(sym,
                r[12][0],r[12][1],r[12][2], r[25][0],r[25][1], r[40][0],r[40][1]))
        print()
    print("(LIKIT fee25'te 3-4/4 pozitif ise -> gercek aday. EGZOTIK fee40'ta cokerse -> slippage")
    print(" yer, +4996 illuzyon. Likit + robust olan alt-kume varsa poc_atr'nin GERCEK edge'i odur.)")
