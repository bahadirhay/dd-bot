import sqlite3,json
from core.config import cfg
c=sqlite3.connect('file:%s?mode=ro'%cfg.DB_PATH,uri=True)
snaps=[]
for ts,p,pj in c.execute("SELECT ts,price,payload_json FROM market_snapshots WHERE price>0 ORDER BY ts"):
    kv=None
    if pj:
        try:
            fm=json.loads(pj).get('forming_15m')
            if isinstance(fm,dict):kv=fm.get('volume')
        except:pass
    snaps.append((ts,p,kv))
# 15m bar (POC icin) + 1m bar (degerlendirme)
def b15():
    b={}
    for ts,p,kv in snaps:
        x=int(ts//900)
        if x not in b:b[x]={'c':p,'v':kv}
        else:
            b[x]['c']=p
            if kv is not None:b[x]['v']=kv
    k=sorted(b);return k,[b[x]['c'] for x in k],[b[x]['v'] for x in k]
def b1():
    b={}
    for ts,p,kv in snaps:
        x=int(ts//60)
        if x not in b:b[x]={'ts':x*60,'h':p,'l':p,'c':p}
        else:
            d=b[x];d['h']=max(d['h'],p);d['l']=min(d['l'],p);d['c']=p
    k=sorted(b);return [(b[x]['ts'],b[x]['h'],b[x]['l'],b[x]['c']) for x in k]
k15,C15,V15=b15();B1=b1()
import bisect
def pos15(ts):return bisect.bisect_right(k15,int(ts//900))-1
def poc(i,M=40):
    nu=de=0.
    for j in range(i-M,i):
        v=V15[j] if (V15[j] and V15[j]>0) else 1.;nu+=C15[j]*v;de+=v
    return nu/de if de>0 else None
SL=60.;MHmin=240;FEE=8.;SLIP=2.;DEV=85.
# spike_t=0 -> spike kapali (sadece bar-kapanis). spike_t>0 -> dev>=spike_t bar-ici giris.
def run(spike_t,lo=0,hi=None):
    n=len(B1);hi=hi or n;i=max(0,lo);tr=[];last_bar=-1
    while i<hi-1:
        ts,h,l,cl=B1[i];p=pos15(ts)
        if p<40:i+=1;continue
        pc=poc(p)
        if not pc:i+=1;continue
        dev=(cl-pc)/pc*1e4
        bar15=int(ts//900)
        is_close=(int((ts+60)//900)!=bar15)  # bu 1m, 15m barin son dakikasi mi
        sig=None;spike=False
        if spike_t>0 and abs(dev)>=spike_t:
            sig='LONG' if dev<0 else 'SHORT';spike=True
        elif is_close and abs(dev)>=DEV and bar15!=last_bar:
            sig='LONG' if dev<0 else 'SHORT'
        if not sig:i+=1;continue
        last_bar=bar15
        ent=cl;j=i;res=None;ent_ts=ts;slip=2*SLIP
        for j in range(i+1,min(i+MHmin+1,n)):
            tsj,hj,lj,clj=B1[j]
            cur=((clj-ent) if sig=='LONG' else (ent-clj))/ent*1e4
            adv=((hj-ent) if sig=='SHORT' else (ent-lj))/ent*1e4
            if adv>=SL:res=-SL-FEE-slip;break
            pj=pos15(tsj);pcj=poc(pj) if pj>=40 else None
            if pcj:
                dj=(clj-pcj)/pcj*1e4
                if ((sig=='LONG' and dj>=0) or (sig=='SHORT' and dj<=0)) and cur>=0:res=cur-FEE-slip;break
            if (tsj-ent_ts)/60>=MHmin:res=cur-FEE-slip;break
        if res is None:
            clj=B1[min(j,n-1)][3];res=((clj-ent) if sig=='LONG' else (ent-clj))/ent*1e4-FEE-slip
        tr.append(res);i=j+1
    return tr
n=len(B1);split=int(n*0.6)
def rep(lbl,st):
    al=run(st);tr=run(st,hi=split);oo=run(st,lo=split)
    # ceyrekleri 1m-index ile
    qs=[sum(run(st,lo=n*q//4,hi=n*(q+1)//4)) for q in range(4)];pos=sum(1 for x in qs if x>0)
    if not al:print('%-26s islem=0'%lbl);return
    print('%-26s net%+6.0f isl=%-3d isabet%%%.0f | TRAIN%+6.0f OOS%+6.0f cey%d/4'%(
        lbl,sum(al),len(al),100*sum(1 for x in al if x>0)/len(al),sum(tr),sum(oo),pos))
print('1m bar=%d'%n)
print('\n=== D: bar-kapanis vs +spike-fade (1m gercek-zaman, fee8+slip2, WF) ===')
rep('SADECE bar-kapanis (mevcut)',0)
rep('+ spike-fade dev>=120',120)
rep('+ spike-fade dev>=150',150)
rep('+ spike-fade dev>=180',180)
rep('+ spike-fade dev>=250',250)
