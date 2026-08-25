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
n=len(C);SL=60.;MH=16;FEE=8.;SLIP=2.;DEV=85.;MACRO=150.
def poc(i,M=40):
    nu=de=0.
    for j in range(i-M,i):
        v=V[j] if (V[j] and V[j]>0) else 1.;nu+=C[j]*v;de+=v
    return nu/de if de>0 else None
def er(i,N=20):  # efficiency ratio: |net|/toplam-yol (dusuk=choppy/range, yuksek=trend)
    if i<N: return None
    net=abs(C[i]-C[i-N]); path=sum(abs(C[j]-C[j-1]) for j in range(i-N+1,i+1))
    return net/path if path>0 else None
def atr_bps(i,N=14):  # son N barin ort araligi (bps) — volatilite/whipsaw olcusu
    if i<N: return None
    rs=[(H[j]-L[j])/C[j]*1e4 for j in range(i-N,i) if C[j]>0]
    return sum(rs)/len(rs) if rs else None
# gate: None=yok | ('er_max',x)=ER<x ise ac | ('er_min',x)=ER>x | ('atr_max',x)=ATR<x ise ac
def run(gate=None,lo=0,hi=None):
    hi=hi or n;i=max(96,lo);tr=[]
    while i<hi-1:
        if i<lo:i+=1;continue
        pc=poc(i)
        if not pc:i+=1;continue
        dev=(C[i]-pc)/pc*1e4;sig='LONG' if dev<=-DEV else ('SHORT' if dev>=DEV else None)
        if not sig:i+=1;continue
        mc=(C[i]-C[i-96])/C[i-96]*1e4 if C[i-96]>0 else 0
        if (sig=='SHORT' and mc>MACRO) or (sig=='LONG' and mc<-MACRO):i+=1;continue
        if gate:
            g,thr=gate
            if g=='er_max':
                e=er(i);
                if e is None or e>thr: i+=1;continue
            elif g=='er_min':
                e=er(i)
                if e is None or e<thr: i+=1;continue
            elif g=='atr_max':
                a=atr_bps(i)
                if a is None or a>thr: i+=1;continue
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
def rep(lbl,gate):
    al=run(gate);tr=run(gate,hi=split);oo=run(gate,lo=split)
    qs=[sum(run(gate,lo=n*q//4,hi=n*(q+1)//4)) for q in range(4)];pos=sum(1 for x in qs if x>0)
    if not al:print('%-26s islem=0'%lbl);return
    print('%-26s net%+6.0f isl=%-4d /isl%+5.1f | TRAIN%+6.0f OOS%+6.0f cey%d/4'%(
        lbl,sum(al),len(al),sum(al)/len(al),sum(tr),sum(oo),pos))
print('\n=== D saf vs REJIM kapilari (fee8+slip2, WF) ===')
rep('D saf (rejim kapisi YOK)',None)
print('--- ER kapisi (dusuk ER=range, sadece orda ac) ---')
rep('D + ER<0.35 (range)',('er_max',0.35))
rep('D + ER<0.50',('er_max',0.50))
rep('D + ER<0.25',('er_max',0.25))
print('--- ER tersi (yuksek ER=trend, sadece trendde) ---')
rep('D + ER>0.35',('er_min',0.35))
print('--- ATR kapisi (dusuk vol=sakin, whipsaw kac) ---')
rep('D + ATR<60bps',('atr_max',60))
rep('D + ATR<90bps',('atr_max',90))
rep('D + ATR<120bps',('atr_max',120))
