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
def er(i,N=20):  # efficiency ratio: yuksek=trend, dusuk=range
    if i<N: return None
    net=abs(C[i]-C[i-N]);path=sum(abs(C[j]-C[j-1]) for j in range(i-N+1,i+1))
    return net/path if path>0 else None
# REJIM-DEGISTIRICI: ER>=thr -> TREND (trendle git, trail cik) ; ER<thr -> RANGE (POC fade)
def run(er_thr,mode,lo=0,hi=None):
    # mode: 'switch'=range MR + trend follow | 'mr_only'=sadece range MR (trendde dur) | 'd_pure'=D her zaman
    hi=hi or n;i=max(96,lo);tr=[]
    while i<hi-1:
        if i<lo:i+=1;continue
        pc=poc(i)
        if not pc:i+=1;continue
        e=er(i);dev=(C[i]-pc)/pc*1e4
        is_trend = e is not None and e>=er_thr
        sig=None;style=None
        if mode=='d_pure':
            sig='LONG' if dev<=-DEV else ('SHORT' if dev>=DEV else None);style='MR'
        elif mode=='mr_only':
            if not is_trend:
                sig='LONG' if dev<=-DEV else ('SHORT' if dev>=DEV else None);style='MR'
        else:  # switch
            if is_trend:
                # trendle git: yon = son 20 bar yonu
                d20=C[i]-C[i-20]
                if abs(d20)/C[i]*1e4>=DEV: sig='LONG' if d20>0 else 'SHORT'; style='TREND'
            else:
                sig='LONG' if dev<=-DEV else ('SHORT' if dev>=DEV else None);style='MR'
        if not sig:i+=1;continue
        ent=C[i];j=i;res=None;peak=ent
        for j in range(i+1,min(i+MH*3+1,n)):
            cur=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4
            adv=((H[j]-ent) if sig=='SHORT' else (ent-L[j]))/ent*1e4
            if adv>=SL:res=-SL-FEE-2*SLIP;break
            if style=='MR':
                pcj=poc(j);dj=(C[j]-pcj)/pcj*1e4 if pcj else None
                rev=dj is not None and ((sig=='LONG' and dj>=0) or (sig=='SHORT' and dj<=0))
                if rev and cur>=0:res=cur-FEE-2*SLIP;break
                if (j-i)>=MH:res=cur-FEE-2*SLIP;break
            else:  # TREND: trail (kazanani kostur), trend zayiflayinca cik
                if sig=='LONG': peak=max(peak,C[j]); retr=(peak-C[j])/ent*1e4
                else: peak=min(peak,C[j]); retr=(C[j]-peak)/ent*1e4
                ej=er(j)
                if retr>=40 or (ej is not None and ej<er_thr*0.6): res=cur-FEE-2*SLIP;break
                if (j-i)>=MH*3:res=cur-FEE-2*SLIP;break
        if res is None:res=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4-FEE-2*SLIP
        tr.append(res);i=j+1
    return tr
split=int(n*0.6)
def rep(lbl,thr,mode):
    al=run(thr,mode);tr=run(thr,mode,hi=split);oo=run(thr,mode,lo=split)
    qs=[sum(run(thr,mode,lo=n*q//4,hi=n*(q+1)//4)) for q in range(4)];pos=sum(1 for x in qs if x>0)
    if not al:print('%-30s islem=0'%lbl);return
    print('%-30s net%+6.0f isl=%-4d /isl%+5.1f | TRAIN%+6.0f OOS%+6.0f cey%d/4'%(
        lbl,sum(al),len(al),sum(al)/len(al),sum(tr),sum(oo),pos))
print('\n=== REJIM-DEGISTIRICI: range=MR + trend=trend-takip (fee8+slip2, WF) ===')
rep('D saf (her zaman MR)',0.40,'d_pure')
print('--- switch (ER esigi ile) ---')
for thr in (0.30,0.40,0.50,0.60):
    rep('SWITCH ER>=%.2f'%thr,thr,'switch')
print('--- sadece range MR (trendde DUR) ---')
for thr in (0.40,0.50):
    rep('MR-only (trendde dur) ER%.2f'%thr,thr,'mr_only')
