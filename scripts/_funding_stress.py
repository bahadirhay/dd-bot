"""G (funding kontraryan) STRES TESTI - hayal kirikligindan once kir: fee-duyarlilik + 4-ceyrek WF
+ parametre platosu + son-rejim. Rolling esik (look-ahead yok). ETH/AVAX ana, XRP/LINK izleme.
Gecerse gercekten saglam; fee12'de coker ya da tek-ceyrek/tek-param ise -> hayal kurma."""
import urllib.request, json, time, bisect

W=120; HOLD=24; PCT=0.15
def funding(sym,limit=1000):
    u="https://fapi.binance.com/fapi/v1/fundingRate?symbol=%s&limit=%d"%(sym,limit)
    try: return [(int(x["fundingTime"])//1000,float(x["fundingRate"])) for x in json.loads(urllib.request.urlopen(u,timeout=20).read())]
    except: return None
def kl1h(sym,days=345):
    out={};end=int(time.time()*1000);need=days*24
    while len(out)<need:
        u="https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=1h&limit=1500&endTime=%d"%(sym,end)
        try: r=json.loads(urllib.request.urlopen(u,timeout=20).read())
        except: return None
        if not r: break
        for x in r: out[int(x[0])//1000]=float(x[4])
        end=r[0][0]-1
        if len(r)<1500: break
    return out
def pat(kd,ks,ts):
    p=bisect.bisect_right(ks,ts)-1; return kd[ks[p]] if p>=0 else None

def sigs(fr,kd,pct,hold):
    ks=sorted(kd); out=[]
    for k in range(W,len(fr)):
        ts,rate=fr[k]; past=sorted(r for _,r in fr[k-W:k])
        hi=past[int(W*(1-pct))]; lo=past[int(W*pct)]
        side=-1 if rate>=hi else (1 if rate<=lo else 0)
        if not side: continue
        p0=pat(kd,ks,ts); p1=pat(kd,ks,ts+hold*3600)
        if not p0 or not p1 or p0<=0: continue
        out.append((ts,side*(p1-p0)/p0*1e4))  # (ts, yonsel-getiri, fee-siz)
    return out

DATA={}
def load(sym):
    if sym in DATA: return DATA[sym]
    fr=funding(sym); kd=kl1h(sym); DATA[sym]=(fr,kd) if (fr and kd) else (None,None); return DATA[sym]

if __name__=="__main__":
    COINS=["ETHUSDT","AVAXUSDT","XRPUSDT","LINKUSDT"]
    print("=== TEST 1: FEE-DUYARLILIK + 4-CEYREK WF (rolling esik) ===")
    print("  coin | fee6: net(poz/4) | fee12 | fee20\n")
    for sym in COINS:
        fr,kd=load(sym)
        if not fr: print("  %-8s veri yok"%sym);continue
        s=sigs(fr,kd,PCT,HOLD); ts=[x[0] for x in s]; rr=[x[1] for x in s]
        n=len(s); q=n//4
        line="  %-8s |"%sym.replace("USDT","")
        for fee in (6,12,20):
            pos=0;tot=0
            for k in range(4):
                seg=rr[k*q:(k+1)*q if k<3 else n]
                net=sum(seg)-fee*len(seg); tot+=net
                if net>0: pos+=1
            line+=" %+6.0f(%d/4) |"%(tot,pos)
        print(line)
    print("\n=== TEST 2: PARAMETRE PLATOSU (ETH+AVAX, fee12) ===")
    print("  pct\\hold |  16h  |  24h  |  48h")
    for pct in (0.10,0.15,0.20):
        row="   %.2f    |"%pct
        for hold in (16,24,48):
            tot=0
            for sym in ("ETHUSDT","AVAXUSDT"):
                fr,kd=load(sym)
                if not fr: continue
                s=sigs(fr,kd,pct,hold); tot+=sum(r for _,r in s)-12*len(s)
            row+=" %+5.0f |"%tot
        print(row)
    print("\n=== TEST 3: SON REJIM (ETH+AVAX, fee12, son 90g vs oncesi) ===")
    for sym in ("ETHUSDT","AVAXUSDT"):
        fr,kd=load(sym)
        if not fr: continue
        s=sigs(fr,kd,PCT,HOLD); now=time.time()
        rec=[r for t,r in s if t>now-90*86400]; old=[r for t,r in s if t<=now-90*86400]
        print("  %-8s son90g: n=%d net=%+.0f | oncesi: n=%d net=%+.0f"%(sym.replace("USDT",""),len(rec),sum(rec)-12*len(rec),len(old),sum(old)-12*len(old)))
    print("\n(fee12'de 4-ceyrek coğu +, plato genis (civar param +), son90g + ise -> G SAGLAM.")
    print(" fee12'de coker / tek-ceyrek / tek-param / son90g - ise -> hayal kurma, forward bekle.)")
