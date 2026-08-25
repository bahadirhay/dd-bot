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
n=len(C);SL=90.;MH=16;FEE=8.;SLIP=2.;DEV=85.
def poc(i,M=40):
    nu=de=0.
    for j in range(i-M,i):
        v=V[j] if (V[j] and V[j]>0) else 1.;nu+=C[j]*v;de+=v
    return nu/de if de>0 else None
# mode: 'poc'=POC'ta %100 kapat | ('trail',T)=POC'u gecince trail-T ile kostur | ('half',T)=%50 POC + %50 trail
def run(mode,lo=0,hi=None):
    hi=hi or n;i=max(96,lo);tr=[]
    while i<hi-1:
        if i<lo:i+=1;continue
        pc=poc(i)
        if not pc:i+=1;continue
        dev=(C[i]-pc)/pc*1e4;sig='LONG' if dev<=-DEV else ('SHORT' if dev>=DEV else None)
        if not sig:i+=1;continue
        ent=C[i];j=i;res=None;phase='open';half=0;peak=ent
        for j in range(i+1,min(i+MH*2+1,n)):
            cur=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4
            adv=((H[j]-ent) if sig=='SHORT' else (ent-L[j]))/ent*1e4
            if adv>=SL:
                res=(0.5*half+0.5*(-SL) if phase=='runner' else -SL)-FEE-2*SLIP;break
            pcj=poc(j);dj=(C[j]-pcj)/pcj*1e4 if pcj else None
            rev=dj is not None and ((sig=='LONG' and dj>=0) or (sig=='SHORT' and dj<=0))
            if phase=='open':
                if rev and cur>=0:
                    if mode=='poc': res=cur-FEE-2*SLIP;break
                    elif mode[0]=='trail': phase='runner';peak=C[j]  # POC'u gecti, kostur
                    elif mode[0]=='half': phase='runner';half=cur;peak=C[j]
                if phase=='open' and (j-i)>=MH: res=cur-FEE-2*SLIP;break  # MH'de kapat (tum modlar)
            else:  # runner: POC otesi trail
                T=mode[1]
                if sig=='LONG': peak=max(peak,C[j]);retr=(peak-C[j])/ent*1e4
                else: peak=min(peak,C[j]);retr=(C[j]-peak)/ent*1e4
                if retr>=T or (j-i)>=MH*2:
                    res=(0.5*half+0.5*cur if mode[0]=='half' else cur)-FEE-2*SLIP;break
        if res is None:
            cur=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4
            res=(0.5*half+0.5*cur if phase=='runner' and mode[0]=='half' else cur)-FEE-2*SLIP
        tr.append(res);i=j+1
    return tr
split=int(n*0.6)
def rep(lbl,m):
    al=run(m);tr=run(m,hi=split);oo=run(m,lo=split)
    qs=[sum(run(m,lo=n*q//4,hi=n*(q+1)//4)) for q in range(4)];pos=sum(1 for x in qs if x>0)
    print('%-26s net%+6.0f isl=%-3d isabet%%%.0f | TRAIN%+6.0f OOS%+6.0f cey%d/4'%(
        lbl,sum(al),len(al),100*sum(1 for x in al if x>0)/len(al) if al else 0,sum(tr),sum(oo),pos))
print('\n=== D cikis: POC-kapat vs POC-otesi-kostur (fee8+slip2, WF) ===')
rep('POC kapat (MEVCUT)',('poc',))
rep('POC + trail40 kostur',('trail',40))
rep('POC + trail60 kostur',('trail',60))
rep('POC + trail80',('trail',80))
rep('half: %50 POC + %50 trail60',('half',60))
