import sqlite3,json,statistics
from core.config import cfg
c=sqlite3.connect('file:%s?mode=ro'%cfg.DB_PATH,uri=True)
bars={}
for ts,p,pj in c.execute("SELECT ts,price,payload_json FROM market_snapshots WHERE price>0 ORDER BY ts"):
    b=int(ts//900);d={}
    if pj:
        try:d=json.loads(pj)
        except:pass
    if b not in bars: bars[b]={'h':p,'l':p,'c':p,'v':(d.get('forming_15m') or {}).get('volume'),'fr':d.get('funding_rate'),'oi':d.get('oi')}
    else:
        o=bars[b];o['h']=max(o['h'],p);o['l']=min(o['l'],p);o['c']=p
        fv=(d.get('forming_15m') or {}).get('volume')
        if fv is not None:o['v']=fv
        if d.get('funding_rate') is not None:o['fr']=d['funding_rate']
        if d.get('oi') is not None:o['oi']=d['oi']
k=sorted(bars);H=[bars[x]['h'] for x in k];L=[bars[x]['l'] for x in k];C=[bars[x]['c'] for x in k];V=[bars[x]['v'] for x in k];FR=[bars[x]['fr'] for x in k];OI=[bars[x]['oi'] for x in k]
n=len(C);SL=60.;MH=16;FEE=8.;SLIP=2.
print('15m bar=%d'%n)
def zw(a,i,m):
    w=[a[j] for j in range(max(0,i-m),i) if a[j] is not None]
    if len(w)<m//2 or a[i] is None: return None
    mu=statistics.mean(w);sd=statistics.pstdev(w);return (a[i]-mu)/sd if sd>0 else None
OICHG=[ (OI[i]-OI[i-1]) if (i>0 and OI[i] is not None and OI[i-1] is not None) else None for i in range(n)]
def poc(i,M=40):
    nu=de=0.
    for j in range(i-M,i):
        v=V[j] if (V[j] and V[j]>0) else 1.;nu+=C[j]*v;de+=v
    return nu/de if de>0 else None
# mode: 'pure'(DEV85) | 'fund_conf' | 'oi_conf' | 'composite'
def run(mode,DEV=85,T=1.0,lo=0,hi=None):
    hi=hi or n;i=max(96,lo);tr=[]
    while i<hi-1:
        if i<lo:i+=1;continue
        pc=poc(i)
        if not pc:i+=1;continue
        dev=(C[i]-pc)/pc*1e4
        fz=zw(FR,i,96); oz=zw(OICHG,i,96)
        if mode=='pure':
            sig='LONG' if dev<=-DEV else ('SHORT' if dev>=DEV else None)
        elif mode=='fund_conf':
            sig='LONG' if dev<=-DEV else ('SHORT' if dev>=DEV else None)
            # funding teyidi: SHORT icin fz>0 (longlar odsuyor), LONG icin fz<0
            if sig=='SHORT' and not (fz is not None and fz>0): sig=None
            if sig=='LONG' and not (fz is not None and fz<0): sig=None
        elif mode=='oi_conf':
            sig='LONG' if dev<=-DEV else ('SHORT' if dev>=DEV else None)
            if sig and oz is None: sig=None
            elif sig=='SHORT' and oz<0: sig=None   # OI dususte short'u alma (zayif)
            elif sig=='LONG' and oz<0: pass
        else:  # composite: POC-dev'i z'le + funding + oi, blend
            dz=dev/85.0  # POC-dev'i ~normalize (85bps=1 birim)
            parts=[x for x in (dz,fz,oz) if x is not None]
            if not parts:i+=1;continue
            st=sum(parts)/len(parts)
            sig='SHORT' if st>=T else ('LONG' if st<=-T else None)
        if not sig:i+=1;continue
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
def rep(lbl,**kw):
    al=run(lo=0,hi=None,**kw);tr=run(hi=split,**kw);oo=run(lo=split,**kw)
    qs=[sum(run(lo=n*q//4,hi=n*(q+1)//4,**kw)) for q in range(4)];pos=sum(1 for x in qs if x>0)
    if not al:print('%-22s islem=0'%lbl);return
    print('%-22s net%+6.0f isl=%-4d /isl%+5.1f | TRAIN%+6.0f OOS%+6.0f cey%d/4'%(
        lbl,sum(al),len(al),sum(al)/len(al),sum(tr),sum(oo),pos))
print('\n=== D cok-ozellikli (POC+funding+OI) vs saf-POC (fee8+slip2, WF) ===')
rep('D saf POC (DEV85)',mode='pure')
rep('D + funding teyidi',mode='fund_conf')
rep('D + OI teyidi',mode='oi_conf')
rep('D composite T1.0',mode='composite',T=1.0)
rep('D composite T1.2',mode='composite',T=1.2)
