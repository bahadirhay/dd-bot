"""scripts/_orderflow_climax_test.py — KLIMAKS-FADE dedektoru (kullanici hipotezi 09-15).
Gozlem: 14-15 Eyl tepesinde ASIRI taker-alim klimaksi (+67048 CVD) + OBI negatife donus -> dususe gecti.
HIPOTEZ: asiri CVD-klimaks = tukenme -> FADE (klimaks alimsa SHORT, satimsa LONG) kar mi?
Varyant: (a) sadece CVD-klimaks, (b) + OBI onayi (bid cekilmis olsun).
Veri: data/orderflow_shadow.db (dakika CVD + OBI + mid, 18g/8coin). Maliyet-dahil, iki-yari, bagimsiz-olay.
UYARI: 18g kucuk + klimakslar NADIR -> DUSUK GUC, on-bakis. Kanit degil.
"""
import sqlite3, statistics, bisect
DB="file:data/orderflow_shadow.db?mode=ro"
COST=7.0
def load(c,s):
    return c.execute("SELECT ts,mid,cvd_delta,obi5 FROM of_snap WHERE symbol=? ORDER BY ts",(s,)).fetchall()

def test(Q, H, require_obi):
    c=sqlite3.connect(DB,uri=True)
    syms=[r[0] for r in c.execute("SELECT DISTINCT symbol FROM of_snap")]
    coinnet=[]
    for s in syms:
        rows=load(c,s); n=len(rows)
        if n<500: continue
        ts=[r[0] for r in rows]; mid=[r[1] for r in rows]; cvd=[r[2] for r in rows]; obi=[r[3] for r in rows]
        cc=sorted(cvd); hi=cc[int(len(cc)*(1-Q))]; lo=cc[int(len(cc)*Q)]
        tr=[]; i=0
        while i<n-H:
            side=0
            if cvd[i]>=hi:      # alim-klimaksi -> FADE = SHORT
                if (not require_obi) or obi[i]<0: side=-1
            elif cvd[i]<=lo:    # satim-klimaksi -> FADE = LONG
                if (not require_obi) or obi[i]>0: side=1
            if side==0: i+=1; continue
            # H dk sonraki mid (en yakin)
            tgt=ts[i]+H*60; j=bisect.bisect_right(ts,tgt)-1
            if j<=i or ts[j]-tgt>120 or mid[i]<=0: i+=1; continue
            r=(mid[j]-mid[i])/mid[i]*1e4
            tr.append((ts[i], side*r - COST))
            i+=H   # bagimsiz olay
        if len(tr)<30: continue
        net=statistics.mean(p for _,p in tr)
        th=tr[0][0]+(tr[-1][0]-tr[0][0])/2
        h1=[p for t,p in tr if t<th]; h2=[p for t,p in tr if t>=th]
        cons = (h1 and h2 and (statistics.mean(h1)>0)==(statistics.mean(h2)>0))
        coinnet.append((s,net,len(tr),cons))
    return coinnet

print("=== KLIMAKS-FADE on-bakis (18g order-flow, DUSUK GUC) ===")
print("asiri CVD -> FADE (alim-klimaks=SHORT, satim=LONG), H dk tut, maliyet 7bps\n")
for require_obi in [False, True]:
    tag = "CVD-klimaks + OBI-onayi" if require_obi else "sadece CVD-klimaks"
    print("--- %s ---"%tag)
    for Q,H in [(0.02,15),(0.02,30),(0.01,30),(0.05,15)]:
        cn=test(Q,H,require_obi)
        if not cn: continue
        avg=statistics.mean(x[1] for x in cn); pos=sum(1 for x in cn if x[1]>0); cons=sum(1 for x in cn if x[3])
        print("  Q%.2f H%-2d: coin-ort %+6.1f bps | %d/%d coin+ | %d/%d iki-yari-tutarli"%(
            Q,H,avg,pos,len(cn),cons,len(cn)))
    print()
