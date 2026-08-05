"""Funding kontraryan SIKI dogrulama: ROLLING yuzdelik esik (look-ahead YOK) + PERMUTASYON p-degeri.
Rolling: her funding aninda esik SADECE gecmis W-donemden. Permutasyon: funding-sinyalleri sabit,
getirileri karistir -> gercek eslesme sansi geciyor mu (drift+split kontrollu). p<0.05 = gercek sinyal.
24h tutus. Cok-coin + agregat p. fee6."""
import urllib.request, json, time, bisect, random

FEE=6.0; HOLD=24; W=120; PCT=0.15; NPERM=2000
random.seed(42)

def funding(sym,limit=1000):
    u="https://fapi.binance.com/fapi/v1/fundingRate?symbol=%s&limit=%d"%(sym,limit)
    try: return [(int(x["fundingTime"])//1000,float(x["fundingRate"])) for x in json.loads(urllib.request.urlopen(u,timeout=20).read())]
    except: return None
def kl1h(sym,days=340):
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

def signals(sym):
    fr=funding(sym); kd=kl1h(sym)
    if not fr or not kd: return None
    ks=sorted(kd); out=[]
    for k in range(W,len(fr)):
        ts,rate=fr[k]
        past=sorted(r for _,r in fr[k-W:k])   # ROLLING: sadece gecmis
        hi=past[int(W*(1-PCT))]; lo=past[int(W*PCT)]
        side=0
        if rate>=hi: side=-1
        elif rate<=lo: side=1
        if not side: continue
        p0=pat(kd,ks,ts); p1=pat(kd,ks,ts+HOLD*3600)
        if not p0 or not p1 or p0<=0: continue
        raw=(p1-p0)/p0*1e4    # yonsel-olmayan getiri
        out.append((side,raw))
    return out

def perm_p(sigs):
    if len(sigs)<20: return None
    sides=[s for s,_ in sigs]; raws=[r for _,r in sigs]
    obs=sum(s*r for s,r in sigs)
    ge=0
    for _ in range(NPERM):
        rr=raws[:]; random.shuffle(rr)
        if sum(sides[i]*rr[i] for i in range(len(sides)))>=obs: ge+=1
    return obs, ge/NPERM

if __name__=="__main__":
    print("=== FUNDING kontraryan | ROLLING esik (look-ahead yok) + PERMUTASYON | 24h ===\n")
    COINS=["ETHUSDT","BTCUSDT","SOLUSDT","BNBUSDT","XRPUSDT","DOGEUSDT","AVAXUSDT","LINKUSDT"]
    all_sig=[]
    print("  coin | n | net(bps,fee dahil) | isl-basi | p-deger")
    for sym in COINS:
        s=signals(sym)
        if not s: print("  %-8s veri yok"%sym);continue
        all_sig+=s
        net=sum(side*raw for side,raw in s)-FEE*len(s)
        pp=perm_p(s)
        pstr="%.3f"%pp[1] if pp else "n/az"
        flag=" <-- GERCEK" if (pp and pp[1]<0.05 and net>0) else ""
        print("  %-8s %3d | %+7.0f | %+.1f | p=%s%s"%(sym.replace("USDT",""),len(s),net,net/len(s),pstr,flag))
    print()
    net_all=sum(side*raw for side,raw in all_sig)-FEE*len(all_sig)
    pp=perm_p(all_sig)
    print("  >>> HEP: n=%d net=%+.0f isl-basi=%+.1f  PERMUTASYON p=%.4f"%(len(all_sig),net_all,net_all/len(all_sig),pp[1] if pp else -1))
    print()
    print("(agregat p<0.05 + coğu coin p<0.05 + isl-basi fee-ustu -> funding-konumlanma GERCEK, forward-shadow'a gec.")
    print(" p>0.10 ise -> look-ahead cikinca sinyal zayifladi, sansti.)")
