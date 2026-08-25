import sqlite3,json,statistics
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
def er(i,N=20):
    if i<N: return None
    net=abs(C[i]-C[i-N]);path=sum(abs(C[j]-C[j-1]) for j in range(i-N+1,i+1))
    return net/path if path>0 else None
# er_thr=0 -> filtre yok (D saf). er_thr>0 -> ER>=thr ise DUR (trendde isleme girme)
def run(er_thr,lo=0,hi=None):
    hi=hi or n;i=max(96,lo);tr=[]
    while i<hi-1:
        if i<lo:i+=1;continue
        pc=poc(i)
        if not pc:i+=1;continue
        dev=(C[i]-pc)/pc*1e4;sig='LONG' if dev<=-DEV else ('SHORT' if dev>=DEV else None)
        if not sig:i+=1;continue
        if er_thr>0:
            e=er(i)
            if e is not None and e>=er_thr: i+=1;continue   # TRENDDE DUR
        ent=C[i];j=i;res=None
        for j in range(i+1,min(i+MH+1,n)):
            cur=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4
            adv=((H[j]-ent) if sig=='SHORT' else (ent-L[j]))/ent*1e4
            if adv>=SL:res=-SL-FEE-2*SLIP;break
            pcj=poc(j);dj=(C[j]-pcj)/pcj*1e4 if pcj else None
            if dj is not None and ((sig=='LONG' and dj>=0) or (sig=='SHORT' and dj<=0)) and cur>=0:res=cur-FEE-2*SLIP;break
            if (j-i)>=MH:res=cur-FEE-2*SLIP;break
        if res is None:res=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4-FEE-2*SLIP
        tr.append(res);i=j+1
    return tr
split=int(n*0.6)
def rep(lbl,thr):
    al=run(thr);tr=run(thr,hi=split);oo=run(thr,lo=split)
    qs=[sum(run(thr,lo=n*q//4,hi=n*(q+1)//4)) for q in range(4)];pos=sum(1 for x in qs if x>0)
    if not al:print('%-18s islem=0'%lbl);return
    gp=sum(x for x in al if x>0);gl=sum(x for x in al if x<=0)
    wl=sum(1 for x in al if x<=0)
    print('%-18s net%+6.0f isl=%-4d | KAZANC%+6.0f / KAYIP%+6.0f (%d adet) | TRAIN%+6.0f OOS%+6.0f cey%d/4'%(
        lbl,sum(al),len(al),gp,gl,wl,sum(tr),sum(oo),pos))
print('\n=== ER esik SAGLAMLIK + kazanc/kayip ayristirma (fee8+slip2, WF) ===')
rep('D saf (filtre yok)',0)
for thr in (0.40,0.45,0.50,0.55,0.60):
    rep('trendde-dur ER%.2f'%thr,thr)
