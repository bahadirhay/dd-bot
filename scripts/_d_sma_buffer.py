"""D SMA-align BUFFER taramasi - 'piyasaya duyarli' refinement.
Bulgu: SMA (buffer=0) zayif-downtrend'deki KAZANAN dip-alimlarini da kesiyor (+649 kacti).
Fikir: LONG'u sadece fiyat SMA'nin >buffer ALTINDA ise blokla (guclu downtrend); hafif altta
(choppy) IZIN ver. buffer=0 mevcut, buffer>0 daha yumusak. OFF=filtresiz. Hangisi HEM cok-ceyrek
robust HEM son-donem iyi? 5 coin x 4 ceyrek + son 60g. fee16."""
import urllib.request, json, time

M=40;DEV=85.0;MINPROF=12.0;ER_GATE=0.5;ER_WIN=20;SMA_LEN=120;FEE=16
SL_F,SL_C,AM,AN=300,600,7.5,16;MAXHOLD=16
BUFS=[None,0,150,300,500]  # None=OFF(filtresiz), 0=mevcut, digerleri yumusak

def kl(sym,days=240):
    out={};end=int(time.time()*1000);need=days*96
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
    H=[b[0] for b in bars];L=[b[1] for b in bars];C=[b[2] for b in bars];V=[b[3] for b in bars];n=len(C)
    poc=[None]*n;er=[0.]*n;sma=[None]*n;atr=[400.]*n
    for i in range(M,n):
        nu=de=0.
        for j in range(i-M,i):
            w=V[j] if V[j]>0 else 1;nu+=C[j]*w;de+=w
        poc[i]=nu/de if de>0 else None
    for i in range(n):
        if i>ER_WIN:
            net=abs(C[i]-C[i-ER_WIN]);path=sum(abs(C[i-k]-C[i-k-1]) for k in range(ER_WIN));er[i]=net/path if path>0 else 0
        if i>=SMA_LEN: sma[i]=sum(C[i-SMA_LEN+1:i+1])/SMA_LEN
        if i>=AN:
            s=sum(max(H[j]-L[j],abs(H[j]-C[j-1]),abs(L[j]-C[j-1])) for j in range(i-AN+1,i+1));atr[i]=s/AN/C[i]*1e4
    return H,L,C,poc,er,sma,atr

def run(pk,lo,hi,buf):
    H,L,C,poc,er,sma,atr=pk;n=len(C);pnls=[];i=max(M,SMA_LEN,lo)
    while i<min(hi,n-1):
        pc=poc[i]
        if not pc or sma[i] is None: i+=1;continue
        dev=(C[i]-pc)/pc*1e4
        sig="LONG" if dev<=-DEV else ("SHORT" if dev>=DEV else None)
        if not sig or er[i]>=ER_GATE: i+=1;continue
        if buf is not None:  # SMA-align + buffer: sadece belirgin ters-tarafta blokla
            th_lo=sma[i]*(1-buf/1e4); th_hi=sma[i]*(1+buf/1e4)
            if (sig=="LONG" and C[i]<=th_lo) or (sig=="SHORT" and C[i]>=th_hi): i+=1;continue
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

def lbl(b): return "OFF" if b is None else ("buf%d"%b)
if __name__=="__main__":
    print("=== D SMA-align BUFFER | 5 coin x 4 ceyrek + son60g | fee16 | net bps(poz-ceyrek) ===\n")
    agg={b:[0,0] for b in BUFS}; recent={b:0 for b in BUFS}
    for sym in ("ETHUSDT","BTCUSDT","SOLUSDT","LINKUSDT","BNBUSDT"):
        bars=kl(sym,240)
        if not bars or len(bars)<7000: print(sym,"veri az");continue
        pk=prep(bars);n=len(bars);start=max(M,SMA_LEN)+5;span=n-1-start;q=span//4
        row="  %-8s |"%sym
        for b in BUFS:
            pos=0;tot=0
            for k in range(4):
                lo=start+k*q;hi=start+(k+1)*q if k<3 else n-1
                nt=sum(run(pk,lo,hi,b));tot+=nt
                if nt>0: pos+=1
            agg[b][0]+=tot;agg[b][1]+=pos
            # son 60g
            rlo=max(start,n-5760); recent[b]+=sum(run(pk,rlo,n-1,b))
            row+=" %s %+5.0f(%d/4)|"%(lbl(b),tot,pos)
        print(row)
    print("\n=== TOPLAM (5 coin) ===")
    print("  variant | 4-ceyrek net | poz-ceyrek/20 | SON 60g net")
    for b in BUFS:
        print("  %-7s | %+8.0f    | %2d/20         | %+7.0f"%(lbl(b),agg[b][0],agg[b][1],recent[b]))
    print("\n(Aranan: HEM 4-ceyrek toplami yuksek + cok poz-ceyrek (uzun-vade robust) HEM son60g pozitif")
    print(" (mevcut rejimde de iyi). buf>0 ikisini birden buf0/OFF'tan iyi yaparsa -> daha iyi deger.)")
