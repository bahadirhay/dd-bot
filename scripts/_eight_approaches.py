import sqlite3,json,statistics,math
from core.config import cfg
c=sqlite3.connect('file:%s?mode=ro'%cfg.DB_PATH,uri=True)
# 15m bar: O,H,L,C, vol(buy+sell son), cvd_raw(son), funding(son), oi(son)
bars={}
for ts,p,pj in c.execute("SELECT ts,price,payload_json FROM market_snapshots WHERE price>0 ORDER BY ts"):
    b=int(ts//900); fr=oi=cvd=vol=None
    if pj:
        try:
            d=json.loads(pj); fr=d.get('funding_rate'); oi=d.get('oi'); cvd=d.get('cvd_raw')
            bv=d.get('buy_vol_5m'); sv=d.get('sell_vol_5m')
            if bv is not None and sv is not None: vol=float(bv)+float(sv)
        except Exception: pass
    if b not in bars: bars[b]=[p,p,p,p,fr,oi,cvd,vol]  # O,H,L,C,fr,oi,cvd,vol
    else:
        o=bars[b]; o[1]=max(o[1],p);o[2]=min(o[2],p);o[3]=p
        if fr is not None:o[4]=fr
        if oi is not None:o[5]=oi
        if cvd is not None:o[6]=cvd
        if vol is not None:o[7]=vol
keys=sorted(bars)
O=[bars[k][0] for k in keys]; H=[bars[k][1] for k in keys]; L=[bars[k][2] for k in keys]; C=[bars[k][3] for k in keys]
FR=[bars[k][4] for k in keys]; OI=[bars[k][5] for k in keys]; CVD=[bars[k][6] for k in keys]; VOL=[bars[k][7] for k in keys]
n=len(keys); FEE=3.0; SL=60.0; MH=16
print('15m bar=%d  (veri: fr/oi/cvd/vol dolu mu)'%n)
print('  fr:%d oi:%d cvd:%d vol:%d / %d'%(sum(x is not None for x in FR),sum(x is not None for x in OI),sum(x is not None for x in CVD),sum(x is not None for x in VOL),n))

def zw(a,i,m):
    w=[a[j] for j in range(max(0,i-m),i) if a[j] is not None]
    if len(w)<m//2 or a[i] is None: return None
    mu=statistics.mean(w); sd=statistics.pstdev(w); return (a[i]-mu)/sd if sd>0 else None

# TARAFSIZ CIKIS: SL60 + maxhold16 (ileri yon-getiri). Entry edge'i olcer.
def fwd(sig,i):
    ent=C[i]
    for j in range(i+1,min(i+MH+1,n)):
        adv=((H[j]-ent) if sig=='SHORT' else (ent-L[j]))/ent*1e4
        if adv>=SL: return -SL-FEE
    j=min(i+MH,n-1)
    cur=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4
    return cur-FEE

# Her yaklasim: bar i -> 'LONG'/'SHORT'/None
def sig_structure(i):   # 1) Market Structure: BOS (swing kirilim, trend-devam)
    if i<22: return None
    hh=max(H[i-20:i]); ll=min(L[i-20:i])
    if C[i]>hh: return 'LONG'
    if C[i]<ll: return 'SHORT'
    return None
def sig_liquidity(i):   # 2) Likidite: stop-hunt sweep + reddi (reversal)
    if i<22: return None
    pl=min(L[i-20:i-1]); ph=max(H[i-20:i-1])
    if L[i]<pl and C[i]>pl: return 'LONG'    # alt likidite supurdu, geri kapandi
    if H[i]>ph and C[i]<ph: return 'SHORT'
    return None
def sig_volprofile(i):  # 3) Hacim Profili: POC'a donus (vol-agirlikli mean)
    if i<40 or VOL[i] is None: return None
    num=den=0
    for j in range(i-40,i):
        if VOL[j] is None: continue
        num+=C[j]*VOL[j]; den+=VOL[j]
    if den<=0: return None
    poc=num/den; dev=(C[i]-poc)/poc*1e4
    if dev<=-50: return 'LONG'    # POC altinda -> geri don
    if dev>=50: return 'SHORT'
    return None
def sig_cvd(i):         # 4) Delta/CVD: CVD momentum (akisla git)
    z=zw([(CVD[j]-CVD[j-1]) if (j>0 and CVD[j] is not None and CVD[j-1] is not None) else None for j in range(n)],i,32) if i>0 else None
    if z is None: return None
    if z>=1.5: return 'LONG'     # guclu net alim -> devam
    if z<=-1.5: return 'SHORT'
    return None
def sig_trend(i):       # 5) Trend Filtresi: 24h egim yonunde
    if i<96 or C[i-96]<=0: return None
    slope=(C[i]-C[i-96])/C[i-96]*1e4
    if slope>=150: return 'LONG'
    if slope<=-150: return 'SHORT'
    return None
def sig_sentiment(i):   # 6) Sentiment: funding asiri -> fade (+OI teyit)
    z=zw(FR,i,96)
    if z is None: return None
    if z>=1.2: return 'SHORT'   # funding asiri yuksek (longlar odsuyor) -> fade
    if z<=-1.2: return 'LONG'
    return None
def sig_candle(i):      # 7) Mum Formasyonlari: engulfing + pin
    if i<2: return None
    body=abs(C[i]-O[i]); rng=H[i]-L[i]
    if rng<=0: return None
    pbody=abs(C[i-1]-O[i-1])
    # bullish engulfing
    if C[i]>O[i] and C[i-1]<O[i-1] and body>pbody and C[i]>O[i-1] and O[i]<C[i-1]: return 'LONG'
    if C[i]<O[i] and C[i-1]>O[i-1] and body>pbody and C[i]<O[i-1] and O[i]>C[i-1]: return 'SHORT'
    # pin bar (alt kuyruk uzun -> long)
    lowwick=min(O[i],C[i])-L[i]; upwick=H[i]-max(O[i],C[i])
    if lowwick>2*body and lowwick>upwick: return 'LONG'
    if upwick>2*body and upwick>lowwick: return 'SHORT'
    return None
def sig_chart(i):       # 8) Grafik Formasyonlari: double-bottom/top (W/M)
    if i<30: return None
    seg=L[i-30:i]; segH=H[i-30:i]
    lo=min(seg); hi=max(segH)
    # iki dip yakin + son fiyat dipten yukselmis -> double-bottom long
    lows=[j for j in range(i-30,i) if (L[j]-lo)/lo*1e4<15]
    highs=[j for j in range(i-30,i) if (hi-H[j])/hi*1e4<15]
    if len(lows)>=2 and (lows[-1]-lows[0])>=4 and C[i]>lo*1.003: return 'LONG'
    if len(highs)>=2 and (highs[-1]-highs[0])>=4 and C[i]<hi*0.997: return 'SHORT'
    return None

APPR=[('1 Market Structure',sig_structure),('2 Likidite (sweep)',sig_liquidity),
      ('3 Hacim Profili (POC)',sig_volprofile),('4 Delta/CVD',sig_cvd),
      ('5 Trend Filtresi',sig_trend),('6 Sentiment (funding)',sig_sentiment),
      ('7 Mum Formasyonlari',sig_candle),('8 Grafik Form. (W/M)',sig_chart)]

def run(fn,lo=0,hi=None):
    hi=hi or n; i=96; tr=[]
    while i<hi-1:
        if i<lo: i+=1; continue
        s=fn(i)
        if not s: i+=1; continue
        tr.append(fwd(s,i)); i+=MH+1  # ust uste binmeyi onle
    return tr

print('\n=== 8 YAKLASIM (tarafsiz cikis: SL60+maxhold16) ===')
res=[]
for name,fn in APPR:
    tr=run(fn)
    if not tr: print('%-24s islem=0'%name); continue
    net=sum(tr); hit=100*sum(1 for x in tr if x>0)/len(tr)
    split=int(n*0.6); oos=sum(run(fn,lo=split))
    qs=[sum(run(fn,lo=n*q//4,hi=n*(q+1)//4)) for q in range(4)]; pos=sum(1 for x in qs if x>0)
    res.append((net,name,len(tr),hit,oos,pos))
    print('%-24s isl=%-3d net %+7.0f isabet %%%.0f  OOS %+6.0f  cey+:%d/4'%(name,len(tr),net,hit,oos,pos))
print('\n=== SIRALAMA (net) ===')
for net,name,ntr,hit,oos,pos in sorted(res,reverse=True):
    print('  %-24s net %+7.0f  OOS %+6.0f  cey %d/4'%(name,net,oos,pos))
