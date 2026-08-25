import sqlite3,json
from core.config import cfg
c=sqlite3.connect('file:%s?mode=ro'%cfg.DB_PATH,uri=True)
bars={}
for ts,p,pj in c.execute("SELECT ts,price,payload_json FROM market_snapshots WHERE price>0 ORDER BY ts"):
    b=int(ts//900);kv=None
    if pj:
        try:
            fm=json.loads(pj).get('forming_15m')
            if isinstance(fm,dict):kv=fm.get('volume')
        except:pass
    if b not in bars: bars[b]={'h':p,'l':p,'c':p,'v':kv}
    else:
        o=bars[b];o['h']=max(o['h'],p);o['l']=min(o['l'],p);o['c']=p
        if kv is not None:o['v']=kv
k=sorted(bars);H=[bars[x]['h'] for x in k];L=[bars[x]['l'] for x in k];C=[bars[x]['c'] for x in k];V=[bars[x]['v'] for x in k]
n=len(C);MH=16;FEE=8.;SLIP=2.;DEV=85.;SL=90.
def poc(i,M=40):
    nu=de=0.
    for j in range(i-M,i):
        v=V[j] if (V[j] and V[j]>0) else 1.;nu+=C[j]*v;de+=v
    return nu/de if de>0 else None
# macro: 24h (96 bar) slope. mac>0 ise: SHORT'u slope>+mac'ta, LONG'u slope<-mac'ta blokla.
def run(mac,lo=0,hi=None):
    hi=hi or n;i=max(96,lo);tr=[]
    while i<hi-1:
        if i<lo:i+=1;continue
        pc=poc(i)
        if not pc:i+=1;continue
        dev=(C[i]-pc)/pc*1e4;sig='LONG' if dev<=-DEV else ('SHORT' if dev>=DEV else None)
        if not sig:i+=1;continue
        if mac>0 and C[i-96]>0:
            slope=(C[i]-C[i-96])/C[i-96]*1e4
            if (sig=='SHORT' and slope>mac) or (sig=='LONG' and slope<-mac):i+=1;continue
        ent=C[i];j=i;res=None
        for j in range(i+1,min(i+MH+1,n)):
            cur=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4;adv=((H[j]-ent) if sig=='SHORT' else (ent-L[j]))/ent*1e4
            if adv>=SL:res=-SL-FEE-2*SLIP;break
            pcj=poc(j);dj=(C[j]-pcj)/pcj*1e4 if pcj else None
            if dj is not None and ((sig=='LONG' and dj>=0) or (sig=='SHORT' and dj<=0)) and cur>=0:res=cur-FEE-2*SLIP;break
            if (j-i)>=MH:res=cur-FEE-2*SLIP;break
        if res is None:res=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4-FEE-2*SLIP
        tr.append(res);i=j+1
    return tr
split=int(n*0.6)
def rep(lbl,mac):
    al=run(mac);tr=run(mac,hi=split);oo=run(mac,lo=split)
    gp=sum(x for x in al if x>0);gl=sum(x for x in al if x<=0)
    print('%-22s net%+6.0f isl=%-3d | KAZ%+6.0f/KAYIP%+6.0f | TRAIN%+6.0f OOS%+6.0f'%(
        lbl,sum(al),len(al),gp,gl,sum(tr),sum(oo)))
print('=== D crash-koruma: makro slope esigi (crash dahil veri, fee8+slip2, WF) ===')
rep('makro KAPALI (mevcut)',0)
rep('makro 150 (siki)',150)
rep('makro 250 (orta)',250)
rep('makro 350 (genis-sadece crash)',350)
rep('makro 500 (cok genis)',500)
