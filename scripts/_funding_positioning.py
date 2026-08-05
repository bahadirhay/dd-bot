"""KONUMLANMA okuması: funding kontraryan (fiyat-DISI edge). Kalabalik asiri-long (yuksek funding)
-> SHORT; asiri-short (negatif funding) -> LONG. Memory: p=0.033 dogrulanmis. Fiyatta degil,
konumlanmada -> etkinlik tuzagina girmeyebilir. Test: funding-asiri sonrasi kontraryan ileri-getiri
+ net (fee sonrasi), esik taramasi, WF. Cok-coin. Funding 8 saatte bir (dusuk frekans, normal)."""
import urllib.request, json, time

FEE=6.0  # funding dusuk-frekans, tek round-trip; maker ~5-6

def funding(sym, limit=1000):
    u="https://fapi.binance.com/fapi/v1/fundingRate?symbol=%s&limit=%d"%(sym,limit)
    try: return [(int(x["fundingTime"])//1000, float(x["fundingRate"])) for x in json.loads(urllib.request.urlopen(u,timeout=20).read())]
    except: return None

def kl_1h(sym, days=340):
    out={};end=int(time.time()*1000);need=days*24
    while len(out)<need:
        u="https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=1h&limit=1500&endTime=%d"%(sym,end)
        try: r=json.loads(urllib.request.urlopen(u,timeout=20).read())
        except: return None
        if not r: break
        for x in r: out[int(x[0])//1000]=float(x[4])
        end=r[0][0]-1
        if len(r)<1500: break
    return out  # {sec: close}

def price_at(kd, ts):
    import bisect
    ks=sorted(kd); p=bisect.bisect_right(ks,ts)-1
    return kd[ks[p]] if p>=0 else None

def test(sym, thr, hold_h):
    fr=funding(sym); kd=kl_1h(sym)
    if not fr or not kd: return None
    ks=sorted(kd)
    trades=[]
    for ts,rate in fr:
        if abs(rate)<thr: continue
        side = -1 if rate>0 else 1   # yuksek funding -> SHORT(-1); negatif -> LONG(+1)
        p0=price_at(kd,ts); p1=price_at(kd,ts+hold_h*3600)
        if not p0 or not p1 or p0<=0: continue
        ret=side*(p1-p0)/p0*1e4-FEE
        trades.append((ts,ret))
    return trades

if __name__=="__main__":
    print("=== FUNDING KONTRARYAN (konumlanma okuma) | esik + tutus taramasi | cok-coin ===")
    print("yuksek funding->SHORT, negatif->LONG. net bps (fee6). WF: ilk-yari/son-yari.\n")
    COINS=["ETHUSDT","BTCUSDT","SOLUSDT","BNBUSDT","XRPUSDT"]
    for thr in (0.0002, 0.0004):   # 0.02%, 0.04% funding esigi
        for hold in (8, 24):
            print("--- esik=%.2f%% tutus=%dh ---"%(thr*100,hold))
            tot_all=[]
            for sym in COINS:
                tr=test(sym,thr,hold)
                if not tr: continue
                v=[r for _,r in tr]; n=len(v)
                if n<10: print("  %-8s n=%d (az)"%(sym,n)); continue
                # WF: ilk yari / son yari
                mid=n//2; h1=sum(v[:mid]); h2=sum(v[mid:])
                tot_all+=v
                rob='OK' if (h1>0 and h2>0) else ''
                print('  %-8s n=%3d net=%+6.0f islem-basi=%+.1f win%%%d | H1=%+.0f H2=%+.0f %s'%(sym,n,sum(v),sum(v)/n,100*sum(1 for x in v if x>0)//n,h1,h2,rob))
            if tot_all: print('  >>> HEP: n=%d net=%+.0f islem-basi=%+.1f'%(len(tot_all),sum(tot_all),sum(tot_all)/len(tot_all)))
            print()
    print("(net>0 + islem-basi fee-ustu + H1&H2 ikisi de + (OK) coğu coinde -> konumlanma edge GERCEK.")
    print(" negatif/tek-yari ise -> funding de fiyat gibi etkin, kalabalık-okuma da tutmuyor.)")
