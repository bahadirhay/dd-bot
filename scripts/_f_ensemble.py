"""F whipsaw DUZELTME: tek-N40 vs ENSEMBLE (N20/40/60/90 ortalama pozisyon).
Kullanici: F dibi shortluyor, duzelt. Whipsaw yok EDILEMEZ (donusu tahmin gerekir) ama
YUMUSATILABILIR: tek keskin flip (+1->-1) yerine coklu-lookback ortalamasi kademeli gecis
verir (+1->+0.5->0->-0.5->-1). Donum noktasinda tam-ters degil, yari pozisyon -> DD/whipsaw azalir.
Ayrica per-coin SL (ETH/SOL SL300) kiyasi. 5 coin x 4 ceyrek, net%% + maxDD."""
import urllib.request, json, time

FEE = 8.0; NS = [20, 40, 60, 90]

def klines(sym, limit=1000):
    u = 'https://fapi.binance.com/fapi/v1/klines?symbol=%sUSDT&interval=1d&limit=%d' % (sym, limit)
    for _ in range(3):
        try: return [(float(x[2]), float(x[3]), float(x[4])) for x in json.loads(urllib.request.urlopen(u, timeout=15).read())]
        except Exception: time.sleep(1)
    return None

def run_single(bars, lo, hi, N, sl_bps=0):
    C=[b[2] for b in bars];H=[b[0] for b in bars];L=[b[1] for b in bars]
    pos=0;ent=0.0;daily=[];blocked=0
    for i in range(max(N,lo),hi):
        if C[i-N]<=0: continue
        want=1 if C[i]>C[i-N] else -1
        if blocked and want!=blocked: blocked=0
        target=0 if blocked==want else want
        fee=FEE/1e4 if target!=pos else 0.0
        if target!=0 and target!=pos: ent=C[i]
        if target==0:
            if fee: daily.append(-fee)
            pos=0; continue
        if sl_bps:
            adv=((ent-L[i+1]) if target>0 else (H[i+1]-ent))/ent*1e4
            if adv>=sl_bps: daily.append(-sl_bps/1e4-fee); blocked=target; pos=0; continue
        daily.append(target*(C[i+1]-C[i])/C[i]-fee); pos=target
    return daily

def run_ensemble(bars, lo, hi):
    """Pozisyon = ortalama(sign(mom_N)) -> kesirli. Fee = turnover*|dpos|."""
    C=[b[2] for b in bars]; pos=0.0; daily=[]
    for i in range(max(NS)+lo if lo==0 else max(max(NS),lo),hi):
        votes=[]
        for N in NS:
            if i-N>=0 and C[i-N]>0: votes.append(1 if C[i]>C[i-N] else -1)
        if not votes: continue
        target=sum(votes)/len(votes)   # -1..+1 kesirli
        fee=FEE/1e4*abs(target-pos)
        daily.append(target*(C[i+1]-C[i])/C[i]-fee); pos=target
    return daily

def net(d): return sum(d)*100 if d else 0.0
def mdd(d):
    eq=pk=m=0
    for x in d: eq+=x;pk=max(pk,eq);m=min(m,eq-pk)
    return m*100

if __name__=="__main__":
    print("=== F whipsaw duzeltme | tek-N40 vs ENSEMBLE vs N40+SL300 | 5 coin x 4 ceyrek ===")
    print("deger: net%% (maxDD%%). Ensemble kademeli gecis -> DD dusmesi beklenir.\n")
    agg={"N40":[0,0],"ENS":[0,0],"N40+SL":[0,0]}
    for sym in ('ETH','BTC','SOL','BNB','XRP'):
        bars=klines(sym)
        if not bars: print("  %s yok"%sym); continue
        n=len(bars); start=max(NS); span=n-1-start; q=span//4
        cf={"N40":[],"ENS":[],"N40+SL":[]}
        for k in range(4):
            lo=start+k*q; hi=start+(k+1)*q if k<3 else n-1
            cf["N40"].append(run_single(bars,lo,hi,40))
            cf["ENS"].append(run_ensemble(bars,lo,hi))
            cf["N40+SL"].append(run_single(bars,lo,hi,40,300))
        def coin(m):
            alld=[x for d in cf[m] for x in d]; return net(alld),mdd(alld)
        nf,df=coin("N40"); ne,de=coin("ENS"); ns,ds=coin("N40+SL")
        print("  %-4s | N40 %+6.0f(%+5.0f) | ENS %+6.0f(%+5.0f) | N40+SL %+6.0f(%+5.0f)"%(sym,nf,df,ne,de,ns,ds))
        agg["N40"][0]+=nf;agg["N40"][1]+=df; agg["ENS"][0]+=ne;agg["ENS"][1]+=de; agg["N40+SL"][0]+=ns;agg["N40+SL"][1]+=ds
    print("\n=== TOPLAM (net%% | maxDD toplami) ===")
    for m in ("N40","ENS","N40+SL"):
        print("  %-7s net=%+.0f  DD=%+.0f"%(m,agg[m][0],agg[m][1]))
    print("\n(ENS getiri ~ayni + DD daha kucuk (az negatif) ise -> whipsaw yumusatildi = duzeltme.")
    print(" Getiriyi cok kesip DD'yi az dusururse -> degmez. Whipsaw'i YOK etmez, azaltir.)")
