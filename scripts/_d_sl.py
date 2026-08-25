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
n=len(C);MH=16;FEE=8.;SLIP=2.;DEV=85.
def poc(i,M=40):
    nu=de=0.
    for j in range(i-M,i):
        v=V[j] if (V[j] and V[j]>0) else 1.;nu+=C[j]*v;de+=v
    return nu/de if de>0 else None
# slmode: ('fixed',60) | ('struct',K,buf,cap) -> long SL=Kbar-low-buf, short SL=Kbar-high+buf, cap'le sinirli
def run(slmode,lo=0,hi=None):
    hi=hi or n;i=max(96,lo);tr=[];slc=0;slsum=0
    while i<hi-1:
        if i<lo:i+=1;continue
        pc=poc(i)
        if not pc:i+=1;continue
        dev=(C[i]-pc)/pc*1e4;sig='LONG' if dev<=-DEV else ('SHORT' if dev>=DEV else None)
        if not sig:i+=1;continue
        ent=C[i]
        if slmode[0]=='fixed':
            slbps=slmode[1]
        else:
            _,K,buf,cap=slmode
            if sig=='LONG':
                lvl=min(L[i-K:i]); slbps=(ent-lvl)/ent*1e4+buf
            else:
                lvl=max(H[i-K:i]); slbps=(lvl-ent)/ent*1e4+buf
            slbps=max(20,min(slbps,cap))  # 20-cap bps arasi
        j=i;res=None
        for j in range(i+1,min(i+MH+1,n)):
            cur=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4
            adv=((H[j]-ent) if sig=='SHORT' else (ent-L[j]))/ent*1e4
            if adv>=slbps:res=-slbps-FEE-2*SLIP;slc+=1;slsum+=slbps;break
            pcj=poc(j);dj=(C[j]-pcj)/pcj*1e4 if pcj else None
            if dj is not None and ((sig=='LONG' and dj>=0) or (sig=='SHORT' and dj<=0)) and cur>=0:res=cur-FEE-2*SLIP;break
            if (j-i)>=MH:res=cur-FEE-2*SLIP;break
        if res is None:res=((C[j]-ent) if sig=='LONG' else (ent-C[j]))/ent*1e4-FEE-2*SLIP
        tr.append(res);i=j+1
    return tr,slc,slsum
split=int(n*0.6)
def rep(lbl,sm):
    al,sc,ss=run(sm);tr,_,_=run(sm,hi=split);oo,_,_=run(sm,lo=split)
    qs=[sum(run(sm,lo=n*q//4,hi=n*(q+1)//4)[0]) for q in range(4)];pos=sum(1 for x in qs if x>0)
    if not al:print('%-26s islem=0'%lbl);return
    avgsl=ss/sc if sc else 0
    print('%-26s net%+6.0f isl=%-3d SL=%-2d(ort%.0fbps) | TRAIN%+6.0f OOS%+6.0f cey%d/4'%(
        lbl,sum(al),len(al),sc,avgsl,sum(tr),sum(oo),pos))
print('\n=== D SL: sabit vs yapisal (son dip/tepe alti) fee8+slip2 WF ===')
rep('sabit 60bps (mevcut)',('fixed',60))
rep('sabit 90bps',('fixed',90))
print('--- yapisal (Kbar dip/tepe + buffer, cap150) ---')
rep('struct K8 buf10',('struct',8,10,150))
rep('struct K16 buf15',('struct',16,15,150))
rep('struct K8 buf20 cap120',('struct',8,20,120))
rep('struct K16 buf20 cap100',('struct',16,20,100))
