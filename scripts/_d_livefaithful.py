import sqlite3,json,statistics
from core.config import cfg
c=sqlite3.connect('file:%s?mode=ro'%cfg.DB_PATH,uri=True)
# snapshot: ts, price, kline-vol (forming_15m.volume), taker-vol (buy+sell)
snaps=[]
for ts,p,pj in c.execute("SELECT ts,price,payload_json FROM market_snapshots WHERE price>0 ORDER BY ts"):
    kv=tv=None
    if pj:
        try:
            d=json.loads(pj)
            fm=d.get('forming_15m')
            if isinstance(fm,dict): kv=fm.get('volume')
            bv=d.get('buy_vol_5m');sv=d.get('sell_vol_5m')
            if bv is not None and sv is not None: tv=float(bv)+float(sv)
        except: pass
    snaps.append((ts,p,kv,tv))
print('snapshot=%d'%len(snaps))

def build_15m(volsrc):  # volsrc: 'kline'|'taker'|'equal'
    bars={}
    for ts,p,kv,tv in snaps:
        b=int(ts//900)
        v = (kv if volsrc=='kline' else tv if volsrc=='taker' else 1.0)
        if b not in bars: bars[b]={'h':p,'l':p,'c':p,'v':v}
        else:
            o=bars[b]; o['h']=max(o['h'],p);o['l']=min(o['l'],p);o['c']=p
            if v is not None: o['v']=v
    k=sorted(bars)
    return k,[bars[x]['h'] for x in k],[bars[x]['l'] for x in k],[bars[x]['c'] for x in k],[bars[x]['v'] for x in k]

def build_1m():
    bars={}
    for ts,p,kv,tv in snaps:
        b=int(ts//60)
        if b not in bars: bars[b]={'ts':b*60,'h':p,'l':p,'c':p}
        else:
            o=bars[b]; o['h']=max(o['h'],p);o['l']=min(o['l'],p);o['c']=p
    k=sorted(bars); return [(bars[x]['ts'],bars[x]['h'],bars[x]['l'],bars[x]['c']) for x in k]

FEE=8.0; SL=60.; M=40; DEV=50.; MHmin=16*15  # maxhold 16x15m = 240 dk

# --- bar-close sim (orijinal tarz) ---
def poc(C,V,i):
    nu=de=0.
    for j in range(i-M,i):
        v=V[j] if (V[j] and V[j]>0) else 1.; nu+=C[j]*v;de+=v
    return nu/de if de>0 else None
def sim_barclose(volsrc):
    k,H,L,C,V=build_15m(volsrc); n=len(C); i=max(M,96); tr=[]
    while i<n-1:
        pc=poc(C,V,i)
        if not pc: i+=1;continue
        dev=(C[i]-pc)/pc*1e4; sig='LONG' if dev<=-DEV else ('SHORT' if dev>=DEV else None)
        if not sig: i+=1;continue
        ent=C[i];j=i;res=None
        for j in range(i+1,min(i+17,n)):
            cur=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4;adv=((H[j]-ent) if sig=='SHORT' else (ent-L[j]))/ent*1e4
            if adv>=SL:res=-SL-FEE;break
            pcj=poc(C,V,j);dj=(C[j]-pcj)/pcj*1e4 if pcj else None
            rev=dj is not None and ((sig=='LONG' and dj>=0) or (sig=='SHORT' and dj<=0))
            if rev and cur>=0:res=cur-FEE;break
            if (j-i)>=16:res=cur-FEE;break
        if res is None:res=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4-FEE
        tr.append(res);i=j+1
    return tr

# --- 1m-ritmi sim (CANLI BIREBIR): POC tamamlanan 15m'lerden, dev her 1m fiyatla ---
def sim_1m(volsrc):
    k15,H15,L15,C15,V15=build_15m(volsrc); b1=build_1m()
    # her 15m bar icin POC (kapanmis barlardan) -> zaman damgasiyla erisim
    pocmap={}  # bar_id -> POC (o bar BASINDA gecerli, onceki M kapanmis bar)
    for idx in range(M,len(k15)):
        pc=poc(C15,V15,idx)
        if pc: pocmap[k15[idx]]=pc  # k15[idx]*900 = bu 15m barin baslangici
    def cur_poc(ts):
        b15=int(ts//900); return pocmap.get(b15)
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
            if adv>=SL:res=-SL-FEE;break
            pcj=cur_poc(tsj)
            if pcj:
                dj=(clj-pcj)/pcj*1e4; rev=(sig=='LONG' and dj>=0) or (sig=='SHORT' and dj<=0)
                if rev and cur>=0:res=cur-FEE;break
            if (tsj-ent_ts)/60>=MHmin:res=cur-FEE;break
        if res is None:
            clj=b1[j][3]; res=((clj-ent) if sig=='LONG' else (ent-clj))/ent*1e4-FEE
        tr.append(res); i=j+1
    return tr

def rep(lbl,tr):
    if not tr: print('%-34s islem=0'%lbl);return
    print('%-34s isl=%-4d net %+6.0f isabet %%%.0f (islem-basi %+.1f)'%(lbl,len(tr),sum(tr),100*sum(1 for x in tr if x>0)/len(tr),sum(tr)/len(tr)))

print('\n=== D backtest: hacim kaynagi + ritim etkisi (fee 8bps, maxhold, SL60) ===')
print('--- bar-kapanis ritmi ---')
rep('taker-vol (ORIJINAL backtest)', sim_barclose('taker'))
rep('kline-vol (CANLININ kullandigi)', sim_barclose('kline'))
rep('equal-weight (hacimsiz)', sim_barclose('equal'))
print('--- 1m ritmi (CANLI BIREBIR) ---')
rep('1m + kline-vol (TAM CANLI)', sim_1m('kline'))
