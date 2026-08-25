import sqlite3,json,statistics,bisect
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
        if b not in bars: bars[b]={'c':p,'v':kv}
        else:
            o=bars[b];o['c']=p
            if kv is not None:o['v']=kv
    k=sorted(bars);return k,[bars[x]['c'] for x in k],[bars[x]['v'] for x in k]
def build_nm(N):
    sec=N*60;bars={}
    for ts,p,kv in snaps:
        b=int(ts//sec)
        if b not in bars: bars[b]={'ts':b*sec,'h':p,'l':p,'c':p}
        else:
            o=bars[b];o['h']=max(o['h'],p);o['l']=min(o['l'],p);o['c']=p
    k=sorted(bars);return [(bars[x]['ts'],bars[x]['h'],bars[x]['l'],bars[x]['c']) for x in k]
k15,C15,V15=build_15m()
def pos15(ts): return bisect.bisect_right(k15,int(ts//900))-1
def poc(i,M=40):
    nu=de=0.
    for j in range(i-M,i):
        v=V15[j] if (V15[j] and V15[j]>0) else 1.;nu+=C15[j]*v;de+=v
    return nu/de if de>0 else None
SL=60.;MHmin=240
def run(N,DEV,FEE,SLIP):
    bN=build_nm(N);n=len(bN);i=0;tr=[]
    while i<n-1:
        ts,h,l,cl=bN[i];p=pos15(ts)
        if p<40:i+=1;continue
        pc=poc(p)
        if not pc:i+=1;continue
        dev=(cl-pc)/pc*1e4;sig='LONG' if dev<=-DEV else ('SHORT' if dev>=DEV else None)
        if not sig:i+=1;continue
        ent=cl;j=i;res=None;ent_ts=ts
        for j in range(i+1,n):
            tsj,hj,lj,clj=bN[j]
            if (tsj-ent_ts)/60>MHmin+N:break
            cur=((clj-ent) if sig=='LONG' else (ent-clj))/ent*1e4
            adv=((hj-ent) if sig=='SHORT' else (ent-lj))/ent*1e4
            if adv>=SL:res=-SL-FEE-2*SLIP;break
            pj=pos15(tsj);pcj=poc(pj) if pj>=40 else None
            if pcj:
                dj=(clj-pcj)/pcj*1e4
                if ((sig=='LONG' and dj>=0) or (sig=='SHORT' and dj<=0)) and cur>=0:res=cur-FEE-2*SLIP;break
            if (tsj-ent_ts)/60>=MHmin:res=cur-FEE-2*SLIP;break
        if res is None:
            clj=bN[min(j,n-1)][3];res=((clj-ent) if sig=='LONG' else (ent-clj))/ent*1e4-FEE-2*SLIP
        tr.append(res);i=j+1
    return tr
print('\n=== D EDGE KALINLASTIRMA: genis DEV esigi, fee8 + slip2bps/yon ===')
print('(amac: az ama kalin-edge islem -> slippage-saglam. B@15m ref: +641, +10.5/isl)')
for N in (5,15):
    print('--- %dm kapanis ---'%N)
    for DEV in (50,80,110,140,170,200):
        tr=run(N,DEV,8.0,2.0)
        if not tr: print('  DEV=%d: islem=0'%DEV);continue
        print('  DEV=%3d: isl=%-4d net %+6.0f islem-basi %+.1f isabet %%%.0f'%(
            DEV,len(tr),sum(tr),sum(tr)/len(tr),100*sum(1 for x in tr if x>0)/len(tr)))
