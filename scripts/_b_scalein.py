import sqlite3,json,statistics
from core.config import cfg
c=sqlite3.connect('file:%s?mode=ro'%cfg.DB_PATH,uri=True)
bars={}
for ts,p,pj in c.execute("SELECT ts,price,payload_json FROM market_snapshots WHERE price>0 ORDER BY ts"):
    b=int(ts//900); fr=None
    if pj:
        try: fr=json.loads(pj).get('funding_rate')
        except Exception: pass
    if b not in bars: bars[b]=[p,p,p,fr]
    else:
        o=bars[b]; o[0]=max(o[0],p);o[1]=min(o[1],p);o[2]=p
        if fr is not None:o[3]=fr
keys=sorted(bars); H=[bars[k][0] for k in keys]; L=[bars[k][1] for k in keys]; C=[bars[k][2] for k in keys]; FR=[bars[k][3] for k in keys]
n=len(keys); FEE=3.0; SL=60.0; TRAIL=30.0; MH=16; T=1.2
def zw(a,i,m):
    w=[a[j] for j in range(max(0,i-m),i) if a[j] is not None]
    if len(w)<m//2 or a[i] is None: return None
    mu=statistics.mean(w); sd=statistics.pstdev(w); return (a[i]-mu)/sd if sd>0 else None
MOM=[((C[i]-C[i-16])/C[i-16]) if i>=16 and C[i-16]>0 else None for i in range(n)]
PZ32=[zw(C,i,32) for i in range(n)]
def stretch(i):
    parts=[v for v in (zw(FR,i,96),zw(MOM,i,96),PZ32[i]) if v is not None]
    return sum(parts)/len(parts) if parts else None
def sig_at(i):
    st=stretch(i)
    if st is None: return None
    s='SHORT' if st>=T else ('LONG' if st<=-T else None)
    if not s: return None
    mc=(C[i]-C[i-96])/C[i-96]*1e4 if C[i-96]>0 else 0
    if s=='SHORT' and mc>150: return None
    if s=='LONG' and mc<-150: return None
    return s

# Tek-unit exit (mean-revert + runner + SL + maxhold). entry bar=eb, side
def exit_unit(side,eb,ent):
    phase='open'; half=None; peak=ent; j=eb
    for j in range(eb+1,min(eb+MH*2+1,n)):
        cur=((C[j]-ent) if side=='LONG' else (ent-C[j]))/ent*1e4
        adv=((H[j]-ent) if side=='SHORT' else (ent-L[j]))/ent*1e4
        if adv>=SL: return -SL-FEE,j
        if phase=='open':
            rev=(side=='LONG' and PZ32[j] is not None and PZ32[j]>=0) or (side=='SHORT' and PZ32[j] is not None and PZ32[j]<=0)
            if (j-eb)>=MH and not rev: return cur-FEE,j
            if rev: phase='runner'; half=cur; peak=C[j]
        else:
            if side=='LONG': peak=max(peak,H[j]); retr=(peak-C[j])/ent*1e4
            else: peak=min(peak,L[j]); retr=(C[j]-peak)/ent*1e4
            if retr>=TRAIL or (j-eb)>=MH*2:
                return 0.5*half+0.5*cur-FEE,j
    cur=((C[j]-ent) if side=='LONG' else (ent-C[j]))/ent*1e4
    return cur-FEE,j

# BASELINE: tek pozisyon, kapaninca sonraki sinyale gec. setup=unit (her biri ayri)
def baseline(lo=0,hi=None):
    hi=hi or n; i=96; tr=[]
    while i<hi-1:
        if i<lo: i+=1; continue
        s=sig_at(i)
        if not s: i+=1; continue
        res,j=exit_unit(s,i,C[i]); tr.append([res]); i=j+1  # her setup tek unitlik liste
    return tr

# SCALE-IN: pozisyon acikken sinyal ayni yonde devam VE fiyat >=GAP bps aleyhe ise YENI unit ekle (max ADDS).
# Her unit bagimsiz exit. Pozisyon bos kalinca yeni temel sinyal aranir.
def scalein(GAP=40,ADDS=2,lo=0,hi=None):
    hi=hi or n; i=96; tr=[]
    while i<hi-1:
        if i<lo: i+=1; continue
        s=sig_at(i)
        if not s: i+=1; continue
        # ilk unit
        units=[(i,C[i])]; last_ent=C[i]; adds=0
        # ilerle: her bar, acik unitleri yonet + ekleme firsati
        k=i+1; live=[(i,C[i],'open',None,C[i])]  # eb,ent,phase,half,peak
        closed=[]
        while k<min(i+MH*4+2,n) and live:
            newlive=[]
            for (eb,ent,phase,half,peak) in live:
                cur=((C[k]-ent) if s=='LONG' else (ent-C[k]))/ent*1e4
                adv=((H[k]-ent) if s=='SHORT' else (ent-L[k]))/ent*1e4
                if adv>=SL: closed.append(-SL-FEE); continue
                if phase=='open':
                    rev=(s=='LONG' and PZ32[k] is not None and PZ32[k]>=0) or (s=='SHORT' and PZ32[k] is not None and PZ32[k]<=0)
                    if (k-eb)>=MH and not rev: closed.append(cur-FEE); continue
                    if rev: newlive.append((eb,ent,'runner',cur,C[k])); continue
                    newlive.append((eb,ent,'open',None,ent)); continue
                else:
                    if s=='LONG': peak=max(peak,H[k]); retr=(peak-C[k])/ent*1e4
                    else: peak=min(peak,L[k]); retr=(C[k]-peak)/ent*1e4
                    if retr>=TRAIL or (k-eb)>=MH*2: closed.append(0.5*half+0.5*cur-FEE); continue
                    newlive.append((eb,ent,'runner',half,peak)); continue
            live=newlive
            # ekleme: hala sinyal ayni yon + fiyat son giristen >=GAP aleyhe
            if live and adds<ADDS:
                s2=sig_at(k)
                adverse_from_last=((C[k]-last_ent) if s=='SHORT' else (last_ent-C[k]))/last_ent*1e4
                if s2==s and adverse_from_last>=GAP:
                    live.append((k,C[k],'open',None,C[k])); last_ent=C[k]; adds+=1
            k+=1
        # kalan acik unitleri kapat
        for (eb,ent,phase,half,peak) in live:
            cur=((C[k-1]-ent) if s=='LONG' else (ent-C[k-1]))/ent*1e4
            if phase=='runner': closed.append(0.5*half+0.5*cur-FEE)
            else: closed.append(cur-FEE)
        tr.append(closed); i=k  # bu setup'in tum unitleri bir grup
    return tr

def rep(lbl,fn):
    setups=fn()  # her eleman: o setup'in unit pnl listesi
    units=[u for s in setups for u in s]
    setp=[sum(s) for s in setups]  # setup bazinda toplam pnl
    net=sum(units)
    losers=[x for x in setp if x<0]
    winners=[x for x in setp if x>0]
    gloss=sum(losers); gprof=sum(winners)
    worst=min(setp) if setp else 0
    avgloss=gloss/len(losers) if losers else 0
    print('%-22s unit=%d setup=%d net %+6.0f | kazanc %+.0f / ZARAR %+.0f (%d adet, ort %+.0f, en kotu %+.0f)'%(
        lbl,len(units),len(setups),net,gprof,gloss,len(losers),avgloss,worst))

print('=== B scale-in: ZARAR tarafi (setup bazinda) ===')
rep('BASELINE tek-unit', baseline)
rep('GAP40 ADD2 (max3 unit)', lambda lo=0,hi=None: scalein(40,2,lo,hi))
rep('GAP40 ADD3 (max4 unit)', lambda lo=0,hi=None: scalein(40,3,lo,hi))
rep('GAP40 ADD4 (max5 unit)', lambda lo=0,hi=None: scalein(40,4,lo,hi))
rep('GAP30 ADD4 (max5 unit)', lambda lo=0,hi=None: scalein(30,4,lo,hi))
