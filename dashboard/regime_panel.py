"""dashboard/regime_panel.py - REJIM + D-sinyal izleme paneli (cok-coin).
Her coin icin: kisa-ER (anlik trend gucu), uzun-ER (rejim: CHOP/TREND), D dev + sinyal/blok,
'D icin uygun mu' durumu. Amac: hangi coinde D aktif/uygun rejim geldi mi CANLI gor.
AYRI PORT (8058), read-only public fiyat, canli bota SIFIR dokunus. python dashboard/regime_panel.py
"""
import json, urllib.request, sqlite3, os
import dash
from dash import dcc, html, Input, Output

COINS = ["ETHUSDT","BTCUSDT","SOLUSDT","LINKUSDT","BNBUSDT"]
SHADOW_DB = os.path.join(os.path.dirname(__file__),"..","data","d_multicoin_shadow.db")
M,DEV,ER_WIN,ER_GATE,SMA_LEN,REGW=40,85.0,20,0.5,120,192
BG,TXT,GRN,RED,YEL="#0d1117","#c9d1d9","#26a69a","#ef5350","#f0b90b"

def fetch(sym,limit=240):
    u="https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=15m&limit=%d"%(sym,limit)
    try: r=json.loads(urllib.request.urlopen(u,timeout=10).read())
    except: return None
    return [(float(x[4]),float(x[5]),float(x[2]),float(x[3])) for x in r]  # C,V,H,L

def state(sym):
    d=fetch(sym)
    if not d or len(d)<REGW+1: return None
    C=[x[0] for x in d];V=[x[1] for x in d];px=C[-1]
    nu=de=0.
    for j in range(len(C)-M,len(C)):
        w=V[j] if V[j]>0 else 1;nu+=C[j]*w;de+=w
    poc=nu/de;dev=(px-poc)/poc*1e4;sma=sum(C[-SMA_LEN:])/SMA_LEN
    def er(n):
        net=abs(C[-1]-C[-1-n]);path=sum(abs(C[-k]-C[-k-1]) for k in range(1,n+1));return net/path if path else 0
    ser,ler=er(ER_WIN),er(REGW)
    sig="LONG" if dev<=-DEV else ("SHORT" if dev>=DEV else None)
    blok=""
    if sig and ser>=ER_GATE: blok="ER-trend"
    elif sig and ((sig=="LONG" and px<=sma) or (sig=="SHORT" and px>=sma)): blok="SMA-hiza"
    regime="TREND" if ler>=0.35 else ("GECIS" if ler>=0.22 else "CHOP/RANGE")
    return dict(px=px,dev=dev,ser=ser,ler=ler,sig=sig,blok=blok,regime=regime,
                fav=(sig is not None and blok==""))

def shadow(sym):
    try:
        c=sqlite3.connect("file:%s?mode=ro"%SHADOW_DB,uri=True)
        n=c.execute("SELECT COUNT(*),COALESCE(SUM(pnl_bps),0) FROM d_paper WHERE symbol=? AND status='CLOSED'",(sym,)).fetchone()
        return n
    except: return (0,0)

app=dash.Dash(__name__); app.title="Rejim Izleme"
app.layout=html.Div([
    dcc.Interval(id="t",interval=5000,n_intervals=0),
    html.H3("Rejim + D-Sinyal Izleme",style={"padding":"10px 14px"}),
    html.Div(id="tbl",style={"padding":"0 14px"}),
    html.Div("CHOP/RANGE = D icin uygun (mean-reversion calisir) | TREND = D bekler, F bolsa aktif olurdu",
             style={"padding":"12px 14px","color":"#8b949e","fontSize":"12px"}),
],style={"background":BG,"minHeight":"100vh","color":TXT,"fontFamily":"monospace"})

def cell(t,c=TXT,b=False):
    return html.Td(t,style={"padding":"7px 12px","color":c,"fontWeight":"bold" if b else "normal","borderBottom":"1px solid #21262d"})

@app.callback(Output("tbl","children"),Input("t","n_intervals"))
def upd(_):
    head=html.Tr([html.Th(h,style={"padding":"7px 12px","textAlign":"left","color":"#8b949e","borderBottom":"2px solid #30363d"})
                  for h in ["COIN","fiyat","dev","kisa-ER","uzun-ER","REJIM","D sinyal","durum","shadow(kapali/net)"]])
    rows=[head]
    for s in COINS:
        st=state(s)
        if not st: rows.append(html.Tr([cell(s.replace("USDT","")),cell("veri yok")])); continue
        regc={"CHOP/RANGE":GRN,"GECIS":YEL,"TREND":RED}[st["regime"]]
        sigc=GRN if st["sig"]=="LONG" else RED if st["sig"]=="SHORT" else "#8b949e"
        sigt=st["sig"] or "yok"
        if st["fav"]: durum,dc=("AKTIF (girebilir)",GRN)
        elif st["blok"]: durum,dc=("BLOK: %s"%st["blok"],YEL)
        else: durum,dc=("bekle (dev<esik)","#8b949e")
        sn=shadow(s); snc=GRN if sn[1]>0 else RED
        rows.append(html.Tr([
            cell(s.replace("USDT",""),b=True),
            cell("%.4f"%st["px"]),
            cell("%+.0f"%st["dev"],sigc),
            cell("%.2f"%st["ser"]),
            cell("%.2f"%st["ler"]),
            cell(st["regime"],regc,True),
            cell(sigt,sigc),
            cell(durum,dc,True),
            cell("%d / %+.0f"%(sn[0],sn[1]),snc),
        ]))
    return html.Table(rows,style={"borderCollapse":"collapse","width":"100%","fontSize":"13px"})

if __name__=="__main__":
    print("Rejim izleme paneli: http://127.0.0.1:8058")
    app.run(host="127.0.0.1",port=8058,debug=False,use_reloader=False)
