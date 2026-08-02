"""D COK-COIN forward-shadow: SOL + LINK (+ETH baz) icin D (POC+SMA-align) paper kaydi.
Amac: broad-scan'de SOL/LINK robust cikti (23.6/ay, +1553/+1593). Simdi FORWARD olarak,
gercek zamanda D'nin bu coinlerde ne yaptigini kaydet. Deterministik (klines) -> canli bota
SIFIR dokunus, KENDI DB'sine yazar (data/d_multicoin_shadow.db). Periyodik calistir (supervisor
ya da elle); her calisma yeni kapanan islemleri ekler (idempotent). EMIR YOK, paper.

DURUST SINIR: bu D'nin EDGE'ini olcer (deterministik). Gercek maker-fill/slippage'i degil -
o ancak kucuk-canli asamasinda olculur. SOL/LINK likit oldugu icin fill riski INJ'den dusuk.
"""
import sqlite3, os, urllib.request, json, time, datetime as dt

# Robust (4-ceyrek backtest): ETH/BTC/SOL/LINK. BNB ZAYIF (1/4) - sadece izleme.
# NOT: hicbiri canli-kanitli degil; forward olcum icin. Backtest-robust != canli-pozitif.
COINS = ["ETHUSDT", "BTCUSDT", "SOLUSDT", "LINKUSDT", "BNBUSDT"]
DB = os.path.join(os.path.dirname(__file__), "..", "data", "d_multicoin_shadow.db")
# FORWARD baslangici: bu tarihten sonrasi "gercek forward" (oncesi baglam/backtest)
FORWARD_TS = dt.datetime(2026, 7, 24, 0, 0).timestamp()

M = 40; DEV = 85.0; MINPROF = 12.0; ER_GATE = 0.5; SMA_LEN = 120
SL_FLOOR, SL_CEIL, ATR_MULT, ATR_N = 300.0, 600.0, 7.5, 16
MAXHOLD = 16; FEE = 16.0

def kl(sym, days=45):
    out = {}; end = int(time.time() * 1000); need = days * 96
    while len(out) < need:
        u = ("https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=15m&limit=1500&endTime=%d" % (sym, end))
        try: r = json.loads(urllib.request.urlopen(u, timeout=20).read())
        except Exception: return None
        if not r: break
        for x in r:
            # sadece KAPANMIS barlar (forming'i alma)
            if int(x[6]) < int(time.time() * 1000):
                out[int(x[0])] = (int(x[0]) // 1000, float(x[2]), float(x[3]), float(x[4]), float(x[5]))
        end = r[0][0] - 1
        if len(r) < 1500: break
    return [out[k] for k in sorted(out)]

def prep(bars):
    T=[b[0] for b in bars];H=[b[1] for b in bars];L=[b[2] for b in bars];C=[b[3] for b in bars];V=[b[4] for b in bars]
    n=len(C); poc=[None]*n; sma=[None]*n; er=[0.0]*n; atr=[400.0]*n
    for i in range(M,n):
        nu=de=0.0
        for j in range(i-M,i):
            v=V[j] if V[j]>0 else 1.0; nu+=C[j]*v; de+=v
        poc[i]=nu/de if de>0 else None
    for i in range(n):
        if i>20:
            net=abs(C[i]-C[i-20]); path=sum(abs(C[i-k]-C[i-k-1]) for k in range(20)); er[i]=net/path if path>0 else 0.0
        if i>=SMA_LEN: sma[i]=sum(C[i-SMA_LEN+1:i+1])/SMA_LEN
        if i>=ATR_N:
            s=sum(max(H[j]-L[j],abs(H[j]-C[j-1]),abs(L[j]-C[j-1])) for j in range(i-ATR_N+1,i+1))
            atr[i]=(s/ATR_N)/C[i]*1e4 if C[i]>0 else 400.0
    return T,H,L,C,poc,er,sma,atr

def trades(pk):
    T,H,L,C,poc,er,sma,atr=pk; n=len(C); out=[]; i=max(M,SMA_LEN,96)
    while i<n-1:
        pc=poc[i]
        if not pc or sma[i] is None: i+=1; continue
        dev=(C[i]-pc)/pc*1e4
        sig="LONG" if dev<=-DEV else ("SHORT" if dev>=DEV else None)
        if not sig or er[i]>=ER_GATE: i+=1; continue
        if (sig=="LONG" and C[i]<=sma[i]) or (sig=="SHORT" and C[i]>=sma[i]): i+=1; continue
        ent=C[i]; sl=min(max(ATR_MULT*atr[i],SL_FLOOR),SL_CEIL); res=None; jend=i; reason="maxhold"; exitpx=C[i]
        for j in range(i+1,min(i+MAXHOLD+1,n)):
            jend=j; cur=((C[j]-ent) if sig=="LONG" else (ent-C[j]))/ent*1e4
            adv=((H[j]-ent) if sig=="SHORT" else (ent-L[j]))/ent*1e4
            if adv>=sl: res=-sl-FEE; reason="sl"; exitpx=ent*(1-sl/1e4) if sig=="LONG" else ent*(1+sl/1e4); break
            pcj=poc[j]
            if pcj:
                dj=(C[j]-pcj)/pcj*1e4
                if ((sig=="LONG" and dj>=0) or (sig=="SHORT" and dj<=0)) and cur>=MINPROF: res=cur-FEE; reason="poc_revert"; exitpx=C[j]; break
            if (j-i)>=MAXHOLD: res=cur-FEE; reason="maxhold"; exitpx=C[j]; break
        closed = res is not None or jend < n-1
        if res is None: res=((C[jend]-ent) if sig=="LONG" else (ent-C[jend]))/ent*1e4-FEE; exitpx=C[jend]
        out.append((T[i], sig, ent, T[jend], exitpx, res, reason, closed))
        i=jend+1
    return out

def ensure_db():
    c=sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS d_paper(
        symbol TEXT, open_ts INTEGER, open_human TEXT, side TEXT, entry REAL,
        exit_ts INTEGER, exit REAL, pnl_bps REAL, reason TEXT, status TEXT,
        PRIMARY KEY(symbol, open_ts))""")
    c.commit(); return c

if __name__=="__main__":
    c=ensure_db()
    print("=== D cok-coin forward-shadow (%s) | %s ==="%("/".join(s.replace("USDT","") for s in COINS), dt.datetime.now().strftime("%Y-%m-%d %H:%M")))
    print("forward baslangic: %s | DB: data/d_multicoin_shadow.db\n"%dt.datetime.fromtimestamp(FORWARD_TS).strftime("%Y-%m-%d"))
    for sym in COINS:
        bars=kl(sym,45)
        if not bars or len(bars)<3000: print("  %-9s veri az"%sym); continue
        for (ots,side,ent,xts,xpx,pnl,reason,closed) in trades(prep(bars)):
            c.execute("INSERT OR REPLACE INTO d_paper VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (sym,ots,dt.datetime.fromtimestamp(ots).strftime("%Y-%m-%d %H:%M"),side,round(ent,4),
                       xts,round(xpx,4),round(pnl,1),reason,"CLOSED" if closed else "OPEN"))
        c.commit()
        # ozet: toplam + forward-only
        allr=c.execute("SELECT pnl_bps,open_ts FROM d_paper WHERE symbol=? AND status='CLOSED'",(sym,)).fetchall()
        fwd=[r[0] for r in allr if r[1]>=FORWARD_TS]
        tot=[r[0] for r in allr]
        def s(v): return (len(v), sum(v), 100*sum(1 for x in v if x>0)//len(v) if v else 0)
        n_t,net_t,w_t=s(tot); n_f,net_f,w_f=s(fwd)
        print("  %-9s TUM: %d isl net%+.0f win%d%%  |  FORWARD(>=07-24): %d isl net%+.0f win%d%%"%(
            sym,n_t,net_t,w_t,n_f,net_f,w_f))
    print("\nHer calistirmada yeni kapanan islemler eklenir (idempotent). Haftalik calistir ->")
    print("FORWARD kolonu buyur; SOL/LINK canli-forward'da tutuyorsa kucuk-canli asamasina gec.")
