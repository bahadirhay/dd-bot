import sqlite3,json,statistics,math
from core.config import cfg
c=sqlite3.connect('file:%s?mode=ro'%cfg.DB_PATH,uri=True)
bars={}
for ts,p,pj in c.execute("SELECT ts,price,payload_json FROM market_snapshots WHERE price>0 ORDER BY ts"):
    b=int(ts//900)
    d={}
    if pj:
        try: d=json.loads(pj)
        except: pass
    if b not in bars:
        bars[b]={'c':p,'fr':d.get('funding_rate'),'oi':d.get('oi'),'cvd':d.get('cvd_raw'),
                 'cvd5':d.get('cvd_5m'),'tr':d.get('taker_ratio'),
                 'bv':d.get('buy_vol_5m'),'sv':d.get('sell_vol_5m'),
                 'bid':d.get('bid'),'ask':d.get('ask')}
    else:
        o=bars[b];o['c']=p
        for kk,src in (('fr','funding_rate'),('oi','oi'),('cvd','cvd_raw'),('cvd5','cvd_5m'),
                       ('tr','taker_ratio'),('bv','buy_vol_5m'),('sv','sell_vol_5m'),('bid','bid'),('ask','ask')):
            v=d.get(src)
            if v is not None: o[kk]=v
k=sorted(bars);B=[bars[x] for x in k];n=len(B)
C=[b['c'] for b in B]
print('15m bar=%d'%n)
# ozellikler (bar i'de bilinen, gelecege bakmayan)
def feat():
    F={}
    F['funding']=[b['fr'] for b in B]
    F['oi']=[b['oi'] for b in B]
    F['oi_chg']=[ (B[i]['oi']-B[i-1]['oi']) if (i>0 and B[i]['oi'] is not None and B[i-1]['oi'] is not None) else None for i in range(n)]
    F['cvd_delta']=[ (B[i]['cvd']-B[i-1]['cvd']) if (i>0 and B[i]['cvd'] is not None and B[i-1]['cvd'] is not None) else None for i in range(n)]
    F['cvd5']=[b['cvd5'] for b in B]
    F['taker_ratio']=[b['tr'] for b in B]
    F['volume']=[ (b['bv']+b['sv']) if (b['bv'] is not None and b['sv'] is not None) else None for b in B]
    F['vol_imbal']=[ ((b['bv']-b['sv'])/(b['bv']+b['sv'])) if (b['bv'] and b['sv'] and (b['bv']+b['sv'])>0) else None for b in B]
    F['spread']=[ (b['ask']-b['bid']) if (b.get('ask') and b.get('bid')) else None for b in B]
    F['ret_1']=[ (C[i]-C[i-1])/C[i-1] if i>0 and C[i-1]>0 else None for i in range(n)]   # momentum
    F['ret_16']=[ (C[i]-C[i-16])/C[i-16] if i>=16 and C[i-16]>0 else None for i in range(n)]
    F['price_z32']=[None]*n
    for i in range(32,n):
        w=C[i-32:i];mu=statistics.mean(w);sd=statistics.pstdev(w)
        F['price_z32'][i]=(C[i]-mu)/sd if sd>0 else None
    return F
F=feat()
def fwd(i,h): return (C[i+h]-C[i])/C[i] if i+h<n and C[i]>0 else None
def corr(a,b):
    pairs=[(x,y) for x,y in zip(a,b) if x is not None and y is not None]
    if len(pairs)<100: return None,0
    xs=[p[0] for p in pairs];ys=[p[1] for p in pairs]
    mx=statistics.mean(xs);my=statistics.mean(ys)
    num=sum((x-mx)*(y-my) for x,y in pairs)
    dx=math.sqrt(sum((x-mx)**2 for x in xs));dy=math.sqrt(sum((y-my)**2 for y in ys))
    return (num/(dx*dy) if dx*dy>0 else 0),len(pairs)
print('\n=== IC TARAMASI: ozellik -> ileri-getiri korelasyonu (|IC|>0.05 dikkate deger) ===')
print('%-14s   IC(h=1)   IC(h=4)   IC(h=16)'%'ozellik')
hor=[1,4,16]
rows=[]
for name,series in F.items():
    ics=[]
    for h in hor:
        fw=[fwd(i,h) for i in range(n)]
        ic,_=corr(series,fw); ics.append(ic)
    rows.append((max(abs(x) for x in ics if x is not None),name,ics))
for mx,name,ics in sorted(rows,reverse=True):
    s='  '.join(('%+.3f'%x if x is not None else '  NA ') for x in ics)
    flag=' <-- DIKKAT' if mx>=0.05 else ''
    print('%-14s   %s%s'%(name,s,flag))
print('\nNot: + IC=momentum(devam), - IC=mean-revert(fade). |IC|<0.05 ~ tahmin gucu yok.')
