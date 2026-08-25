import sqlite3,json,bisect
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
def build_15m_ohlc():
    bars={}
    for ts,p,kv in snaps:
        b=int(ts//900)
        if b not in bars: bars[b]={'ts':b*900,'h':p,'l':p,'c':p}
        else:
            o=bars[b];o['h']=max(o['h'],p);o['l']=min(o['l'],p);o['c']=p
    k=sorted(bars);return [(bars[x]['ts'],bars[x]['h'],bars[x]['l'],bars[x]['c']) for x in k]
k15,C15,V15=build_15m(); B15=build_15m_ohlc()
def pos15(ts): return bisect.bisect_right(k15,int(ts//900))-1
def poc(i,M=40):
    nu=de=0.
    for j in range(i-M,i):
        v=V15[j] if (V15[j] and V15[j]>0) else 1.;nu+=C15[j]*v;de+=v
    return nu/de if de>0 else None
SL=60.;MHbars=16;FEE=8.;SLIP=2.
def run(DEV,lo=0,hi=None):
    n=len(B15);hi=hi or n;i=max(40,lo);tr=[]
    while i<hi-1:
        if i<lo:i+=1;continue
        pc=poc(i)
        if not pc:i+=1;continue
        cl=B15[i][3];dev=(cl-pc)/pc*1e4;sig='LONG' if dev<=-DEV else ('SHORT' if dev>=DEV else None)
        if not sig:i+=1;continue
        ent=cl;j=i;res=None
        for j in range(i+1,min(i+MHbars+1,n)):
            tsj,hj,lj,clj=B15[j]
            cur=((clj-ent) if sig=='LONG' else (ent-clj))/ent*1e4
            adv=((hj-ent) if sig=='SHORT' else (ent-lj))/ent*1e4
            if adv>=SL:res=-SL-FEE-2*SLIP;break
            pcj=poc(j);dj=(clj-pcj)/pcj*1e4 if pcj else None
            if dj is not None and ((sig=='LONG' and dj>=0) or (sig=='SHORT' and dj<=0)) and cur>=0:res=cur-FEE-2*SLIP;break
            if (j-i)>=MHbars:res=cur-FEE-2*SLIP;break
        if res is None:
            clj=B15[min(j,n-1)][3];res=((clj-ent) if sig=='LONG' else (ent-clj))/ent*1e4-FEE-2*SLIP
        tr.append(res);i=j+1
    return tr
n=len(B15);split=int(n*0.6)
print('\n=== D @15m kapanis, genis DEV WALK-FORWARD (fee8+slip2) ===')
print('DEV   TUM-net  islem  basina | TRAIN(%60)  OOS(%40)  | ceyrek+')
for DEV in (60,70,75,80,85,90,95,100,110):
    al=run(DEV);tr=run(DEV,hi=split);oo=run(DEV,lo=split)
    qs=[sum(run(DEV,lo=n*q//4,hi=n*(q+1)//4)) for q in range(4)];pos=sum(1 for x in qs if x>0)
    print('%3d  %+7.0f  %-4d  %+5.1f | %+7.0f   %+6.0f  | %d/4'%(
        DEV,sum(al),len(al),sum(al)/len(al) if al else 0,sum(tr),sum(oo),pos))
print('\nB@15m ref: +641 (+10.5/isl)')
