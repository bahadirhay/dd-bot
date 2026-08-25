import sqlite3,json,statistics
from core.config import cfg
c=sqlite3.connect('file:%s?mode=ro'%cfg.DB_PATH,uri=True)
snaps=[]
for ts,p,pj in c.execute("SELECT ts,price,payload_json FROM market_snapshots WHERE price>0 ORDER BY ts"):
    kv=fr=None
    if pj:
        try:
            d=json.loads(pj); fr=d.get('funding_rate')
            fm=d.get('forming_15m')
            if isinstance(fm,dict): kv=fm.get('volume')
        except: pass
    snaps.append((ts,p,kv,fr))
print('snapshot=%d'%len(snaps))

# 15m bar: C, kline-vol, funding
def build_15m():
    bars={}
    for ts,p,kv,fr in snaps:
        b=int(ts//900)
        if b not in bars: bars[b]={'c':p,'v':kv,'fr':fr}
        else:
            o=bars[b];o['c']=p
            if kv is not None:o['v']=kv
            if fr is not None:o['fr']=fr
    k=sorted(bars); return k,[bars[x]['c'] for x in k],[bars[x]['v'] for x in k],[bars[x]['fr'] for x in k]
def build_1m():
    bars={}
    for ts,p,kv,fr in snaps:
        b=int(ts//60)
        if b not in bars: bars[b]={'ts':b*60,'h':p,'l':p,'c':p}
        else:
            o=bars[b];o['h']=max(o['h'],p);o['l']=min(o['l'],p);o['c']=p
    k=sorted(bars); return [(bars[x]['ts'],bars[x]['h'],bars[x]['l'],bars[x]['c']) for x in k]

k15,C15,V15,FR15=build_15m(); b1=build_1m()
SL=60.; MHmin=240
def idx15(ts): return int(ts//900)
def pos15(ts):  # kac tane 15m bar tamamlandi (bu ts'den onceki)
    import bisect; return bisect.bisect_right(k15,idx15(ts))-1

def zw(a,i,m):
    w=[a[j] for j in range(max(0,i-m),i) if a[j] is not None]
    if len(w)<m//2: return None
    mu=statistics.mean(w);sd=statistics.pstdev(w);return mu,sd
def poc(i,M=40):
    nu=de=0.
    for j in range(i-M,i):
        v=V15[j] if (V15[j] and V15[j]>0) else 1.;nu+=C15[j]*v;de+=v
    return nu/de if de>0 else None

# --- ortak 1m sim cercevesi: sig_fn(ts,price)->'LONG'/'SHORT'/None ; rev_fn(side,ts,price)->bool
def run(sig_fn,rev_fn,FEE,SLIP):
    n=len(b1);i=0;tr=[]
    while i<n-1:
        ts,h,l,cl=b1[i]; sig=sig_fn(ts,cl)
        if not sig: i+=1;continue
        ent=cl;j=i;res=None;ent_ts=ts
        for j in range(i+1,min(i+MHmin+1,n)):
            tsj,hj,lj,clj=b1[j]
            cur=((clj-ent) if sig=='LONG' else (ent-clj))/ent*1e4
            adv=((hj-ent) if sig=='SHORT' else (ent-lj))/ent*1e4
            if adv>=SL:res=-SL-FEE-2*SLIP;break
            if rev_fn(sig,tsj,clj) and cur>=0:res=cur-FEE-2*SLIP;break
            if (tsj-ent_ts)/60>=MHmin:res=cur-FEE-2*SLIP;break
        if res is None:
            clj=b1[j][3];res=((clj-ent) if sig=='LONG' else (ent-clj))/ent*1e4-FEE-2*SLIP
        tr.append(res);i=j+1
    return tr

# D (POC)
def d_sig(ts,price):
    p=pos15(ts)
    if p<40: return None
    pc=poc(p)
    if not pc: return None
    dev=(price-pc)/pc*1e4
    return 'LONG' if dev<=-50 else ('SHORT' if dev>=50 else None)
def d_rev(side,ts,price):
    p=pos15(ts);pc=poc(p) if p>=40 else None
    if not pc: return False
    dev=(price-pc)/pc*1e4
    return (side=='LONG' and dev>=0) or (side=='SHORT' and dev<=0)

# B (kompozit): funding-z(96) + mom-z(96,16bar) + price-z(32). price-z latest=current price.
def b_pricez(ts,price):
    p=pos15(ts)
    if p<32: return None
    ms=zw(C15,p,32)
    if not ms or ms[1]<=0: return None
    return (price-ms[0])/ms[1]
def b_stretch(ts,price):
    p=pos15(ts)
    if p<96: return None
    parts=[]
    fz=zw(FR15,p,96)
    if fz and fz[1]>0 and FR15[p-1] is not None: parts.append((FR15[p-1]-fz[0])/fz[1])
    MOM=[((C15[q]-C15[q-16])/C15[q-16]) if q>=16 and C15[q-16]>0 else None for q in range(p-96,p)]
    mm=[x for x in MOM if x is not None]
    if len(mm)>=48:
        mu=statistics.mean(mm);sd=statistics.pstdev(mm)
        cm=(C15[p-1]-C15[p-17])/C15[p-17] if C15[p-17]>0 else None
        if cm is not None and sd>0: parts.append((cm-mu)/sd)
    pz=b_pricez(ts,price)
    if pz is not None: parts.append(pz)
    return sum(parts)/len(parts) if parts else None
def b_sig(ts,price):
    st=b_stretch(ts,price)
    if st is None: return None
    sig='SHORT' if st>=1.2 else ('LONG' if st<=-1.2 else None)
    if not sig: return None
    p=pos15(ts)
    if C15[p-96]>0:
        mc=(price-C15[p-96])/C15[p-96]*1e4
        if (sig=='SHORT' and mc>150) or (sig=='LONG' and mc<-150): return None
    pz=b_pricez(ts,price)  # coh 0.5
    if pz is not None and ((sig=='LONG' and pz>-0.5) or (sig=='SHORT' and pz<0.5)): return None
    return sig
def b_rev(side,ts,price):
    pz=b_pricez(ts,price)
    if pz is None: return False
    return (side=='LONG' and pz>=0) or (side=='SHORT' and pz<=0)

print('\n=== ADIL: B vs D, AYNI 1m-ritmi canli-birebir, fee 8bps + slippage ===')
for slip in (0,1,2,3):
    b=run(b_sig,b_rev,8.0,slip); d=run(d_sig,d_rev,8.0,slip)
    fb='%+.0f(%d,%.1f/isl)'%(sum(b),len(b),sum(b)/len(b) if b else 0)
    fd='%+.0f(%d,%.1f/isl)'%(sum(d),len(d),sum(d)/len(d) if d else 0)
    print('  slip=%dbps/yon:  B %-22s  D %-22s'%(slip,fb,fd))
