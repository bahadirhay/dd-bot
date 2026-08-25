import sqlite3,math,statistics
from core.config import cfg
c=sqlite3.connect('file:%s?mode=ro'%cfg.DB_PATH,uri=True)
# 10s -> close serisi (her snapshot)
rows=[(ts,p) for ts,p in c.execute("SELECT ts,price FROM market_snapshots WHERE price>0 ORDER BY ts")]
print('ham nokta=%d'%len(rows))

def resample(sec):
    bars={}
    for ts,p in rows:
        b=int(ts//sec)
        bars[b]=p  # son fiyat = close
    keys=sorted(bars); return [bars[k] for k in keys]

def autocorr(r,lag=1):
    if len(r)<lag+5: return 0,0
    a=r[:-lag]; b=r[lag:]
    ma=statistics.mean(a); mb=statistics.mean(b)
    num=sum((x-ma)*(y-mb) for x,y in zip(a,b))
    da=math.sqrt(sum((x-ma)**2 for x in a)); db=math.sqrt(sum((y-mb)**2 for y in b))
    return (num/(da*db) if da*db>0 else 0), len(a)

def var_ratio(rets,k):
    # VR(k)=Var(k-toplam)/(k*Var(1)); <1 mean-revert, >1 trend
    if len(rets)<k*3: return None
    v1=statistics.pvariance(rets)
    agg=[sum(rets[i:i+k]) for i in range(0,len(rets)-k,k)]
    if len(agg)<3 or v1==0: return None
    vk=statistics.pvariance(agg)
    return vk/(k*v1)

def z_revert(closes,M,K):
    # |z|>K aninda gir, M bar sonra sonuc (mean-revert beklentisi). yon=sapmaya ters.
    n=len(closes); wins=[];net=0;cnt=0;w=0
    for i in range(M,n-M):
        win=closes[i-M:i]; mu=statistics.mean(win); sd=statistics.pstdev(win)
        if sd<=0: continue
        z=(closes[i]-mu)/sd
        if abs(z)<K: continue
        side=-1 if z>0 else 1  # asiri yukari->short
        fwd=(closes[min(i+M,n-1)]-closes[i])/closes[i]*1e4*side
        net+=fwd; cnt+=1;
        if fwd>0: w+=1
    return net,cnt,(100*w/cnt if cnt else 0)

print('\n=== UFUK BAZINDA TAHMIN EDILEBILIRLIK ===')
print('TF       barlar  lag1-autocorr   VR(4)   VR(8)   yorum')
for name,sec in (('1m',60),('5m',300),('15m',900),('1h',3600),('4h',14400)):
    cl=resample(sec)
    rets=[(cl[i]-cl[i-1])/cl[i-1] for i in range(1,len(cl)) if cl[i-1]>0]
    ac,_=autocorr(rets,1)
    vr4=var_ratio(rets,4); vr8=var_ratio(rets,8)
    yorum='MEAN-REVERT' if ac<-0.02 else ('MOMENTUM' if ac>0.02 else 'rastgele(~0)')
    print('%-6s %7d  %+.4f       %s  %s  %s'%(name,len(cl),ac,
        ('%.2f'%vr4 if vr4 else ' -- '),('%.2f'%vr8 if vr8 else ' -- '),yorum))

print('\n=== ASIRI-SAPMA SONRASI DONUS EDGE (z-score MR, fee haric) ===')
print('TF     M(pencere)  K=2.0 sapmada: net-bps  islem  isabet%')
for name,sec,M in (('1m',60,48),('5m',300,48),('15m',900,32),('1h',3600,24)):
    cl=resample(sec)
    net,cnt,hit=z_revert(cl,M,2.0)
    print('%-6s M=%-3d      net %+7.0f  isl=%-5d  isabet %%%.0f'%(name,M,net,cnt,hit))
