"""dashboard/funding_panel.py - STRATEJI G (funding-konumlanma kontraryan) izleme paneli.
Her coin: guncel funding, coin-basi dagilimdaki YERI (uc %15 = sinyal), G yonu, shadow durumu.
G: yuksek funding (kalabalik asiri-long) -> SHORT; negatif (asiri-short) -> LONG. Sikı test gecti
(permutasyon p=0.0245). AYRI PORT 8059, read-only public, canli bota SIFIR dokunus.
python dashboard/funding_panel.py"""
import json, urllib.request, sqlite3, os, bisect
import dash
from dash import dcc, html, Input, Output

MAIN=["ETHUSDT","AVAXUSDT"]; WATCH=["XRPUSDT","LINKUSDT"]; CTX=["BTCUSDT","SOLUSDT"]
COINS=MAIN+WATCH+CTX
DB=os.path.join(os.path.dirname(__file__),"..","data","funding_shadow.db")
W=120; PCT=0.15
BG,TXT,GRN,RED,YEL="#0d1117","#c9d1d9","#26a69a","#ef5350","#f0b90b"

def fhist(sym):
    u="https://fapi.binance.com/fapi/v1/fundingRate?symbol=%s&limit=%d"%(sym,W+1)
    try: return [float(x["fundingRate"]) for x in json.loads(urllib.request.urlopen(u,timeout=8).read())]
    except: return None

def gstate(sym):
    fr=fhist(sym)
    if not fr or len(fr)<W: return None
    cur=fr[-1]; past=sorted(fr[-W-1:-1])
    hi=past[int(W*(1-PCT))]; lo=past[int(W*PCT)]
    # percentile of cur
    rank=bisect.bisect_left(sorted(past),cur)/len(past)*100
    side="SHORT" if cur>=hi else ("LONG" if cur<=lo else None)
    return dict(cur=cur, pct=rank, side=side, hi=hi, lo=lo)

def shadow(sym):
    try:
        c=sqlite3.connect("file:%s?mode=ro"%DB,uri=True)
        allr=c.execute("SELECT pnl_bps,ts FROM funding_shadow WHERE symbol=? AND status='CLOSED'",(sym,)).fetchall()
        import time
        fwd=[r[0] for r in allr if r[1]>=1754352000]  # 2026-08-05
        tot=[r[0] for r in allr]
        return (len(tot),sum(tot),len(fwd),sum(fwd))
    except: return (0,0,0,0)

app=dash.Dash(__name__); app.title="Strateji G (Funding)"
app.layout=html.Div([
    dcc.Interval(id="t",interval=30000,n_intervals=0),
    html.H3("Strateji G — Funding Konumlanma Kontraryan",style={"padding":"10px 14px"}),
    html.Div("yuksek funding (kalabalik asiri-long) -> SHORT | negatif -> LONG | uc %15 = sinyal | permutasyon p=0.0245",
             style={"padding":"0 14px 8px","color":"#8b949e","fontSize":"12px"}),
    html.Div(id="tbl",style={"padding":"0 14px"}),
],style={"background":BG,"minHeight":"100vh","color":TXT,"fontFamily":"monospace"})

def cell(t,c=TXT,b=False): return html.Td(t,style={"padding":"7px 12px","color":c,"fontWeight":"bold" if b else "normal","borderBottom":"1px solid #21262d"})

@app.callback(Output("tbl","children"),Input("t","n_intervals"))
def upd(_):
    head=html.Tr([html.Th(h,style={"padding":"7px 12px","textAlign":"left","color":"#8b949e","borderBottom":"2px solid #30363d"})
                  for h in ["COIN","tier","funding%","dagilim-yeri","G SINYAL","shadow TUM","FORWARD"]])
    rows=[head]
    for s in COINS:
        st=gstate(s); tier="ANA" if s in MAIN else ("izle" if s in WATCH else "baglam")
        if not st: rows.append(html.Tr([cell(s.replace("USDT","")),cell("veri yok")]));continue
        sigc=GRN if st["side"]=="LONG" else RED if st["side"]=="SHORT" else "#8b949e"
        sigt=st["side"] or "-"
        pctc=YEL if (st["pct"]>=85 or st["pct"]<=15) else TXT
        sh=shadow(s); shc=GRN if sh[1]>0 else RED; fc=GRN if sh[3]>0 else (RED if sh[3]<0 else "#8b949e")
        rows.append(html.Tr([
            cell(s.replace("USDT",""),b=True),
            cell(tier, GRN if tier=="ANA" else "#8b949e"),
            cell("%+.4f"%(st["cur"]*100)),
            cell("%.0f%%"%st["pct"],pctc,st["pct"]>=85 or st["pct"]<=15),
            cell(sigt,sigc,st["side"] is not None),
            cell("%d isl / %+.0f"%(sh[0],sh[1]),shc),
            cell("%d isl / %+.0f"%(sh[2],sh[3]),fc),
        ]))
    return html.Table(rows,style={"borderCollapse":"collapse","width":"100%","fontSize":"13px"})

if __name__=="__main__":
    print("Strateji G paneli: http://127.0.0.1:8059")
    app.run(host="127.0.0.1",port=8059,debug=False,use_reloader=False)
