"""FUNDING-KONTRARYAN forward-shadow. Sikı test gecti (rolling esik + permutasyon p=0.0245;
ETH p=0.040, AVAX p=0.037 net-gercek; XRP/LINK sinirda). Bu, seansin ilk sıkı-dogrulanmis edge'i
(fiyat-DISI = konumlanma). Simdi FORWARD doğrula: idealize backtest canliya nasil yansiyor.
Rolling yuzdelik esik (look-ahead YOK), 24h tutus. Kendi DB (data/funding_shadow.db), EMIR YOK,
canli bota SIFIR dokunus. Haftalik calistir. Deploy/forward: 2026-08-05."""
import sqlite3, os, urllib.request, json, time, datetime as dt, bisect

MAIN=["ETHUSDT","AVAXUSDT"]              # likit + permutasyon p<0.05 (canli-oncelik)
SCAN=["INJUSDT","ETCUSDT","SUIUSDT"]     # genis-tarama p<0.05 (INJ p=0.000, ETC 0.005, SUI 0.010)
                                          # AMA az-likit -> forward-fill'de +130/+75 erir mi? OLC.
WATCH=["XRPUSDT","LINKUSDT"]             # sinirda (p~0.05)
COINS=MAIN+SCAN+WATCH
DB=os.path.join(os.path.dirname(__file__),"..","data","funding_shadow.db")
FEE=6.0; HOLD=24; W=120; PCT=0.15
FORWARD_TS=dt.datetime(2026,8,5,0,0).timestamp()

def funding(sym,limit=1000):
    u="https://fapi.binance.com/fapi/v1/fundingRate?symbol=%s&limit=%d"%(sym,limit)
    try: return [(int(x["fundingTime"])//1000,float(x["fundingRate"])) for x in json.loads(urllib.request.urlopen(u,timeout=20).read())]
    except: return None
def kl1h(sym,days=340):
    out={};end=int(time.time()*1000);need=days*24
    while len(out)<need:
        u="https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=1h&limit=1500&endTime=%d"%(sym,end)
        try: r=json.loads(urllib.request.urlopen(u,timeout=20).read())
        except: break
        if not r: break
        for x in r: out[int(x[0])//1000]=float(x[4])
        end=r[0][0]-1
        if len(r)<1500: break
    return out
def pat(kd,ks,ts):
    p=bisect.bisect_right(ks,ts)-1; return kd[ks[p]] if p>=0 else None

def ensure():
    c=sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS funding_shadow(
        symbol TEXT, ts INTEGER, human TEXT, funding REAL, side INTEGER, entry REAL,
        exit_ts INTEGER, exit REAL, pnl_bps REAL, status TEXT, PRIMARY KEY(symbol,ts))""")
    c.commit(); return c

def signals(sym):
    fr=funding(sym); kd=kl1h(sym)
    if not fr or not kd: return []
    ks=sorted(kd); now=time.time(); out=[]
    for k in range(W,len(fr)):
        ts,rate=fr[k]
        past=sorted(r for _,r in fr[k-W:k]); hi=past[int(W*(1-PCT))]; lo=past[int(W*PCT)]
        side=-1 if rate>=hi else (1 if rate<=lo else 0)
        if not side: continue
        p0=pat(kd,ks,ts)
        if not p0 or p0<=0: continue
        ext=ts+HOLD*3600
        if now>=ext:
            p1=pat(kd,ks,ext)
            if not p1: continue
            pnl=side*(p1-p0)/p0*1e4-FEE
            out.append((ts,rate,side,p0,ext,p1,round(pnl,1),"CLOSED"))
        else:
            out.append((ts,rate,side,p0,None,None,None,"OPEN"))
    return out

if __name__=="__main__":
    c=ensure()
    print("=== FUNDING-kontraryan forward-shadow | %s ==="%dt.datetime.now().strftime("%Y-%m-%d %H:%M"))
    print("forward: %s | ana=ETH/AVAX (p<0.05), izleme=XRP/LINK | DB: data/funding_shadow.db\n"%dt.datetime.fromtimestamp(FORWARD_TS).strftime("%Y-%m-%d"))
    for sym in COINS:
        for (ts,rate,side,p0,ext,p1,pnl,st) in signals(sym):
            c.execute("INSERT OR REPLACE INTO funding_shadow VALUES(?,?,?,?,?,?,?,?,?,?)",
                (sym,ts,dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M"),rate,side,round(p0,4),ext,round(p1,4) if p1 else None,pnl,st))
        c.commit()
        allr=c.execute("SELECT pnl_bps,ts FROM funding_shadow WHERE symbol=? AND status='CLOSED'",(sym,)).fetchall()
        fwd=[r[0] for r in allr if r[1]>=FORWARD_TS]; tot=[r[0] for r in allr]
        def s(v): return (len(v),sum(v),100*sum(1 for x in v if x>0)//len(v) if v else 0)
        nt,nett,wt=s(tot); nf,netf,wf=s(fwd)
        tag="ANA" if sym in MAIN else ("tarama" if sym in SCAN else "izle")
        print("  %-8s[%s] TUM: %d isl net%+.0f win%d%% | FORWARD(>=08-05): %d isl net%+.0f"%(sym.replace("USDT",""),tag,nt,nett,wt,nf,netf))
    print("\nHaftalik calistir -> FORWARD buyur. ETH/AVAX forward-pozitif kalirsa kucuk-canli adayi.")
