import sqlite3,json
from core.config import cfg
c=sqlite3.connect('file:%s?mode=ro'%cfg.DB_PATH,uri=True)
snaps=[]
for ts,p,pj in c.execute("SELECT ts,price,payload_json FROM market_snapshots WHERE price>0 ORDER BY ts"):
    kv=None
    if pj:
        try:
            fm=json.loads(pj).get('forming_15m')
            if isinstance(fm,dict): kv=fm.get('volume')
        except: pass
    snaps.append((ts,p,kv))
print('snapshot=%d'%len(snaps))

def build_15m():
    bars={}
    for ts,p,kv in snaps:
        b=int(ts//900)
        if b not in bars: bars[b]={'h':p,'l':p,'c':p,'v':kv}
        else:
            o=bars[b]; o['h']=max(o['h'],p);o['l']=min(o['l'],p);o['c']=p
            if kv is not None: o['v']=kv
    k=sorted(bars); return k,[bars[x]['c'] for x in k],[bars[x]['v'] for x in k]
def build_1m():
    bars={}
    for ts,p,kv in snaps:
        b=int(ts//60)
        if b not in bars: bars[b]={'ts':b*60,'h':p,'l':p,'c':p}
        else:
            o=bars[b]; o['h']=max(o['h'],p);o['l']=min(o['l'],p);o['c']=p
    k=sorted(bars); return [(bars[x]['ts'],bars[x]['h'],bars[x]['l'],bars[x]['c']) for x in k]

M=40; DEV=50.; SL=60.; MHmin=240
k15,C15,V15=build_15m(); b1=build_1m()
def poc(i):
    nu=de=0.
    for j in range(i-M,i):
        v=V15[j] if (V15[j] and V15[j]>0) else 1.; nu+=C15[j]*v;de+=v
    return nu/de if de>0 else None
pocmap={}
for idx in range(M,len(k15)):
    pc=poc(idx)
    if pc: pocmap[k15[idx]]=pc
def cur_poc(ts): return pocmap.get(int(ts//900))

# SLIP: her giris ve cikista slip bps eklenir (toplam round-trip = 2*slip, FEE'ye ek)
def sim(FEE,SLIP):
    n=len(b1); i=0; tr=[]
    while i<n-1:
        ts,h,l,cl=b1[i]; pc=cur_poc(ts)
        if not pc: i+=1;continue
        dev=(cl-pc)/pc*1e4; sig='LONG' if dev<=-DEV else ('SHORT' if dev>=DEV else None)
        if not sig: i+=1;continue
        ent=cl; j=i; res=None; ent_ts=ts
        for j in range(i+1,min(i+MHmin+1,n)):
            tsj,hj,lj,clj=b1[j]
            cur=((clj-ent) if sig=='LONG' else (ent-clj))/ent*1e4
            adv=((hj-ent) if sig=='SHORT' else (ent-lj))/ent*1e4
            if adv>=SL:res=-SL-FEE-2*SLIP;break   # SL'de de slip
            pcj=cur_poc(tsj)
            if pcj:
                dj=(clj-pcj)/pcj*1e4
                if (sig=='LONG' and dj>=0) or (sig=='SHORT' and dj<=0):
                    if cur>=0: res=cur-FEE-2*SLIP;break
            if (tsj-ent_ts)/60>=MHmin:res=cur-FEE-2*SLIP;break
        if res is None:
            clj=b1[j][3]; res=((clj-ent) if sig=='LONG' else (ent-clj))/ent*1e4-FEE-2*SLIP
        tr.append(res); i=j+1
    return tr

print('\n=== D (1m canli-birebir) SLIPPAGE duyarliligi ===')
print('fee=8bps sabit; slip = giris+cikis her birine eklenir (round-trip 2x)')
for slip in (0,1,2,3,5):
    tr=sim(8.0,slip)
    net=sum(tr); pertr=net/len(tr) if tr else 0
    print('  slip=%dbps/yon (rt +%dbps): isl=%d net %+6.0f islem-basi %+.1f isabet %%%.0f'%(
        slip,2*slip,len(tr),net,pertr,100*sum(1 for x in tr if x>0)/len(tr) if tr else 0))
print('\nKARSILASTIRMA: B (8bps, slip yok) ~+994 / 76 islem (+13/islem)')
