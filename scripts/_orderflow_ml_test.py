"""scripts/_orderflow_ml_test.py — ORDER-FLOW ML testi (kullanici: 'yapi ogrensin').
GERCEK ogrenme denemesi: GradientBoosting, order-flow feature -> ileri-yon, WALK-FORWARD (gecmiste egit,
gelecekte test), maliyet-dahil, iki-yari. 'Ogrenme' var-olan sinyali cikarabilir; olmayani yaratamaz.
Bu test onu kesin soyler: ML, maliyeti gecen bir yon ogreniyor mu?

DONDURULMUS: feature seti + walk-forward + maliyet + GECME-KURALI degistirilemez. 15 Ekim'de tekrar (daha cok veri).
UYARI: ~24 gun veri = DUSUK GUC. On-bakis; kesin karar daha cok veriyle.
"""
import sqlite3, numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
DB="file:data/orderflow_shadow.db?mode=ro"
H=10          # ileri dakika (tahmin ufku)
COST=7.0      # round-trip bps
THR=0.03      # olasilik esigi: |p-0.5|>THR ise islem (guven filtresi)
FOLDS=5

def load(c,s):
    r=c.execute("SELECT ts,mid,spread_bps,obi5,obi10,obi25,tk_buy,tk_sell,cvd_delta "
                "FROM of_snap WHERE symbol=? ORDER BY ts",(s,)).fetchall()
    return np.array(r,dtype=float)

def features(a):
    ts=a[:,0]; mid=a[:,1]; spr=a[:,2]; o5=a[:,3]; o10=a[:,4]; o25=a[:,5]; cvd=a[:,8]
    n=len(a)
    def roll_mean(x,k):
        c=np.cumsum(np.insert(x,0,0)); return (c[k:]-c[:-k])/k
    def ret(k):
        r=np.zeros(n)
        r[k:]=(mid[k:]-mid[:-k])/np.where(mid[:-k]==0,1,mid[:-k])*1e4
        return r
    X=np.column_stack([
        o5,o10,o25,spr,cvd,
        np.concatenate([np.zeros(5), roll_mean(o5,5)])[:n],      # obi5 5-dk ort
        np.concatenate([np.zeros(5), roll_mean(cvd,5)])[:n],     # cvd 5-dk ort
        ret(5), ret(15),                                          # kisa momentum
        o5-o25,                                                    # bant farki (yakin vs uzak baski)
    ])
    fwd=np.zeros(n)
    fwd[:-H]=(mid[H:]-mid[:-H])/np.where(mid[:-H]==0,1,mid[:-H])*1e4
    return X, fwd, ts

def evaluate():
    c=sqlite3.connect(DB,uri=True)
    syms=[r[0] for r in c.execute("SELECT DISTINCT symbol FROM of_snap")]
    span=c.execute("SELECT MIN(ts),MAX(ts) FROM of_snap").fetchone()
    days=(span[1]-span[0])/86400
    print("=== ORDER-FLOW ML testi (walk-forward, %d coin, %.1f gun) ==="%(len(syms),days))
    print("H=%d dk, maliyet=%.0fbps, guven-esigi=%.2f, %d fold\n"%(H,COST,THR,FOLDS))
    if days<25: print("!! UYARI: <25 gun = DUSUK GUC. On-bakis.\n")
    pooled=[]
    for s in syms:
        a=load(c,s)
        if len(a)<2000: continue
        X,fwd,ts=features(a)
        # gecerli araliki (ilk 15 + son H disla), NON-OVERLAP ornekle (H atla)
        idx=np.arange(15, len(a)-H, H)
        Xs=X[idx]; ys=(fwd[idx]>0).astype(int); rs=fwd[idx]; tss=ts[idx]
        m=len(idx)
        if m<300: continue
        fold=m//(FOLDS+1)
        nets=[]; trades=0; tstamps=[]
        for k in range(1,FOLDS+1):
            tr_end=fold*k; te_end=fold*(k+1)
            Xtr,ytr=Xs[:tr_end],ys[:tr_end]
            Xte,rte,tte=Xs[tr_end:te_end],rs[tr_end:te_end],tss[tr_end:te_end]
            if len(Xtr)<100 or len(Xte)<20: continue
            model=HistGradientBoostingClassifier(max_iter=120,max_depth=4,learning_rate=0.05,
                                                 min_samples_leaf=40,random_state=0)
            model.fit(Xtr,ytr)
            p=model.predict_proba(Xte)[:,1]
            for j in range(len(Xte)):
                if p[j]>0.5+THR: nets.append(rte[j]-COST); tstamps.append(tte[j]); trades+=1
                elif p[j]<0.5-THR: nets.append(-rte[j]-COST); tstamps.append(tte[j]); trades+=1
        if len(nets)<30:
            print("  %-9s islem az (%d)"%(s,len(nets))); continue
        nets=np.array(nets); tstamps=np.array(tstamps)
        net=nets.mean(); win=100*(nets>0).mean()
        th=tstamps.min()+(tstamps.max()-tstamps.min())/2
        h1=nets[tstamps<th].mean() if (tstamps<th).sum()>5 else 0
        h2=nets[tstamps>=th].mean() if (tstamps>=th).sum()>5 else 0
        cons = "OK" if (h1>0)==(h2>0) else "XX"
        pooled.append(net)
        print("  %-9s NET %+6.2f bps/isl | win %2.0f%% | n=%d | 1yari %+5.1f 2yari %+5.1f %s"%(
            s,net,win,len(nets),h1,h2,cons))
    if pooled:
        avg=np.mean(pooled); pos=sum(1 for x in pooled if x>0)
        print("\n>>> COIN-ORT NET %+.2f bps/isl | %d/%d coin+"%(avg,pos,len(pooled)))
        print(">>> GECME-KURALI: coin-cogunlugu NET+ VE iki-yari tutarli VE maliyet-dahil+.")
        print("    "+("GECTI (nadir!) -> daha cok veri + permut" if avg>0 and pos>len(pooled)/2 else
                     "GECMEDI = ML de maliyeti gecen yon OGRENEMIYOR (sinyal sub-cost/yok)."))

if __name__=="__main__":
    evaluate()
