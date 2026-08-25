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
n=len(C);SL=60.;MH=16;FEE=8.;SLIP=2.;DEV=85.
def poc(i,M=40):
    nu=de=0.
    for j in range(i-M,i):
        v=V[j] if (V[j] and V[j]>0) else 1.;nu+=C[j]*v;de+=v
    return nu/de if de>0 else None
# exit_mode: 'poc'=tam POC donus | ('tp',T)=+T bps'de kapat | ('tp_half',T)=%50 +T, kalan POC
def run(exit_mode,lo=0,hi=None):
    hi=hi or n;i=max(96,lo);tr=[]
    while i<hi-1:
        if i<lo:i+=1;continue
        pc=poc(i)
        if not pc:i+=1;continue
        dev=(C[i]-pc)/pc*1e4;sig='LONG' if dev<=-DEV else ('SHORT' if dev>=DEV else None)
        if not sig:i+=1;continue
        ent=C[i];j=i;res=None;half_done=False;half_pnl=0
        for j in range(i+1,min(i+MH+1,n)):
            cur=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4
            hi_fav=((H[j]-ent) if sig=='LONG' else (ent-L[j]))/ent*1e4  # bar-ici en iyi
            adv=((H[j]-ent) if sig=='SHORT' else (ent-L[j]))/ent*1e4
            if adv>=SL:
                if half_done: res=0.5*half_pnl+0.5*(-SL)-FEE-2*SLIP
                else: res=-SL-FEE-2*SLIP
                break
            if exit_mode[0]=='tp':
                T=exit_mode[1]
                if hi_fav>=T: res=T-FEE-2*SLIP; break
            elif exit_mode[0]=='tp_half':
                T=exit_mode[1]
                if not half_done and hi_fav>=T: half_done=True; half_pnl=T
            pcj=poc(j);dj=(C[j]-pcj)/pcj*1e4 if pcj else None
            rev=dj is not None and ((sig=='LONG' and dj>=0) or (sig=='SHORT' and dj<=0))
            if rev and cur>=0:
                if half_done: res=0.5*half_pnl+0.5*cur-FEE-2*SLIP
                else: res=cur-FEE-2*SLIP
                break
            if (j-i)>=MH:
                if half_done: res=0.5*half_pnl+0.5*cur-FEE-2*SLIP
                else: res=cur-FEE-2*SLIP
                break
        if res is None:
            cur=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4
            res=(0.5*half_pnl+0.5*cur if half_done else cur)-FEE-2*SLIP
        tr.append(res);i=j+1
    return tr
split=int(n*0.6)
def rep(lbl,em):
    al=run(em);tr=run(em,hi=split);oo=run(em,lo=split)
    qs=[sum(run(em,lo=n*q//4,hi=n*(q+1)//4)) for q in range(4)];pos=sum(1 for x in qs if x>0)
    if not al:print('%-26s islem=0'%lbl);return
    print('%-26s net%+6.0f isl=%-4d /isl%+5.1f isabet%%%.0f | TRAIN%+6.0f OOS%+6.0f cey%d/4'%(
        lbl,sum(al),len(al),sum(al)/len(al),100*sum(1 for x in al if x>0)/len(al),sum(tr),sum(oo),pos))
print('\n=== D cikis: yakin TP1 vs tam-POC-donus (fee8+slip2, WF) ===')
rep('tam POC donus (MEVCUT)',('poc',0))
print('--- sabit TP (yakin, erken kar al) ---')
rep('TP +30bps',('tp',30))
rep('TP +40bps',('tp',40))
rep('TP +50bps',('tp',50))
rep('TP +60bps',('tp',60))
rep('TP +80bps',('tp',80))
print('--- %50 yakin TP + kalan POC ---')
rep('half +40 + POC',('tp_half',40))
rep('half +60 + POC',('tp_half',60))
