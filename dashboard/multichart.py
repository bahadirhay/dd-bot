"""dashboard/multichart.py — COK-COIN grid paneli (ETH/SOL/LINK yan yana).
Her hucre: mumlar + POC(40) + SMA120 + D sinyal durumu + shadow pozisyon (d_multicoin_shadow.db).
Ust: secili coin BUYUK; alt: hepsi kucuk grid (3 sutun). Coin butonuyla buyut.
AYRI PORT (8055), canli panele/bota SIFIR dokunus. Calistir: python dashboard/multichart.py
"""
import os, json, sqlite3, urllib.request
import dash
from dash import dcc, html, Input, Output
import plotly.graph_objects as go

COINS = ["ETHUSDT", "SOLUSDT", "LINKUSDT"]  # istersen ekle
SHADOW_DB = os.path.join(os.path.dirname(__file__), "..", "data", "d_multicoin_shadow.db")
M, DEV, SMA_LEN, ER_WIN, ER_GATE = 40, 85.0, 120, 20, 0.5
BG, UP, DN, POC_C, SMA_C, TXT = "#0d1117", "#26a69a", "#ef5350", "#f0b90b", "#5c9ded", "#c9d1d9"

def fetch(sym, limit=240):
    u = "https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=15m&limit=%d" % (sym, limit)
    try:
        r = json.loads(urllib.request.urlopen(u, timeout=12).read())
    except Exception:
        return []
    return [{"ts": x[0] / 1000, "o": float(x[1]), "h": float(x[2]), "l": float(x[3]),
             "c": float(x[4]), "v": float(x[5])} for x in r]

def d_state(bars):
    if len(bars) < SMA_LEN + 1:
        return None
    C = [b["c"] for b in bars]; V = [b["v"] for b in bars]; px = C[-1]
    nu = de = 0.0
    for j in range(len(C) - M, len(C)):
        w = V[j] if V[j] > 0 else 1.0; nu += C[j] * w; de += w
    poc = nu / de if de > 0 else px
    dev = (px - poc) / poc * 1e4
    sma = sum(C[-SMA_LEN:]) / SMA_LEN
    net = abs(C[-1] - C[-1 - ER_WIN]); path = sum(abs(C[-k] - C[-k - 1]) for k in range(1, ER_WIN + 1))
    er = net / path if path > 0 else 0.0
    sig = "LONG" if dev <= -DEV else ("SHORT" if dev >= DEV else None)
    blocked = ""
    if sig and er >= ER_GATE:
        blocked = "ER-trend"; sig = None
    elif sig and ((sig == "LONG" and px <= sma) or (sig == "SHORT" and px >= sma)):
        blocked = "SMA-hiza"; sig = None
    return {"px": px, "poc": poc, "dev": dev, "sma": sma, "sig": sig, "blocked": blocked, "er": er}

def shadow_pos(sym):
    try:
        c = sqlite3.connect("file:%s?mode=ro" % SHADOW_DB, uri=True)
        r = c.execute("SELECT side,entry,pnl_bps,status,open_human FROM d_paper WHERE symbol=? ORDER BY open_ts DESC LIMIT 1", (sym,)).fetchone()
        n = c.execute("SELECT COUNT(*),COALESCE(SUM(pnl_bps),0) FROM d_paper WHERE symbol=? AND status='CLOSED'", (sym,)).fetchone()
        return r, n
    except Exception:
        return None, (0, 0)

def make_fig(sym, bars, st, big):
    show = bars[-(96 if big else 60):]
    fig = go.Figure()
    fig.add_trace(go.Candlestick(x=[b["ts"] for b in show], open=[b["o"] for b in show],
        high=[b["h"] for b in show], low=[b["l"] for b in show], close=[b["c"] for b in show],
        increasing_line_color=UP, decreasing_line_color=DN, showlegend=False))
    if st:
        xs = [show[0]["ts"], show[-1]["ts"]]
        fig.add_trace(go.Scatter(x=xs, y=[st["poc"], st["poc"]], mode="lines",
            line=dict(color=POC_C, width=1.2, dash="dot"), name="POC", showlegend=False))
        fig.add_trace(go.Scatter(x=xs, y=[st["sma"], st["sma"]], mode="lines",
            line=dict(color=SMA_C, width=1.2), name="SMA120", showlegend=False))
    fig.update_layout(paper_bgcolor=BG, plot_bgcolor=BG, font=dict(color=TXT, size=11 if big else 9),
        margin=dict(l=8, r=8, t=4, b=4), height=460 if big else 210,
        xaxis=dict(rangeslider=dict(visible=False), gridcolor="#20262d", showticklabels=big),
        yaxis=dict(gridcolor="#20262d", side="right"))
    return fig

def data_panel(sym, st, big):
    if not st:
        return html.Div("veri yok", style={"color": "#777"})
    sig = st["sig"]; sigc = UP if sig == "LONG" else DN if sig == "SHORT" else "#888"
    sigtxt = sig or ("BLOK:%s" % st["blocked"] if st["blocked"] else "sinyal yok")
    r, n = shadow_pos(sym)
    pos_line = "shadow: -"
    if r:
        pc = UP if (r[2] or 0) > 0 else DN
        pos_line = html.Span(["shadow ", html.B("%s @%.4f" % (r[0], r[1])),
            " %s" % (r[3]), " pnl=%+.0fbps" % (r[2] or 0)], style={"color": pc})
    fs = "13px" if big else "10px"
    return html.Div([
        html.Div([html.B(sym.replace("USDT", ""), style={"fontSize": "16px" if big else "12px"}),
                  html.Span("  %.4f" % st["px"], style={"marginLeft": "6px"})]),
        html.Div(["dev ", html.B("%+.0f" % st["dev"], style={"color": sigc}), " bps  |  ",
                  html.Span(sigtxt, style={"color": sigc, "fontWeight": "bold"}),
                  "  |  ER %.2f" % st["er"]], style={"fontSize": fs}),
        html.Div(pos_line, style={"fontSize": fs}),
        html.Div("kapali %d islem  net %+.0f bps" % (n[0], n[1]), style={"fontSize": fs, "color": "#9aa"}),
    ], style={"padding": "4px 8px"})

app = dash.Dash(__name__)
app.title = "D Cok-Coin Grid"
app.layout = html.Div([
    dcc.Store(id="focus", data=COINS[0]),
    dcc.Interval(id="tick", interval=15000, n_intervals=0),
    html.Div([html.H3("D Cok-Coin Panel", style={"display": "inline", "marginRight": "16px"}),
              html.Span("buyut:"), *[html.Button(s.replace("USDT", ""), id="btn-%s" % s,
                  n_clicks=0, style={"margin": "0 4px", "background": "#21262d", "color": TXT,
                  "border": "1px solid #30363d", "borderRadius": "6px", "padding": "4px 10px"})
                  for s in COINS]],
             style={"padding": "8px 12px"}),
    html.Div([dcc.Graph(id="big-graph", config={"displayModeBar": False}),
              html.Div(id="big-data")],
             style={"background": "#161b22", "borderRadius": "8px", "margin": "0 12px 12px"}),
    html.Div([html.Div([dcc.Graph(id="sm-%s" % s, config={"displayModeBar": False}),
                        html.Div(id="dat-%s" % s)],
                       style={"background": "#161b22", "borderRadius": "8px", "padding": "4px"})
              for s in COINS],
             style={"display": "grid", "gridTemplateColumns": "repeat(3, 1fr)", "gap": "10px",
                    "padding": "0 12px 20px"}),
], style={"background": BG, "minHeight": "100vh", "color": TXT, "fontFamily": "monospace"})

@app.callback(Output("focus", "data"), [Input("btn-%s" % s, "n_clicks") for s in COINS])
def pick(*clicks):
    ctx = dash.callback_context
    if not ctx.triggered:
        return COINS[0]
    return ctx.triggered[0]["prop_id"].split(".")[0].replace("btn-", "")

@app.callback(
    [Output("big-graph", "figure"), Output("big-data", "children")] +
    [Output("sm-%s" % s, "figure") for s in COINS] +
    [Output("dat-%s" % s, "children") for s in COINS],
    [Input("tick", "n_intervals"), Input("focus", "data")])
def refresh(_, focus):
    cache = {}
    for s in COINS:
        b = fetch(s); cache[s] = (b, d_state(b) if b else None)
    fb, fst = cache[focus]
    out = [make_fig(focus, fb, fst, True) if fb else go.Figure(), data_panel(focus, fst, True)]
    out += [make_fig(s, cache[s][0], cache[s][1], False) if cache[s][0] else go.Figure() for s in COINS]
    out += [data_panel(s, cache[s][1], False) for s in COINS]
    return out

if __name__ == "__main__":
    print("Cok-coin grid: http://127.0.0.1:8055  (coinler: %s)" % ", ".join(COINS))
    app.run(host="127.0.0.1", port=8055, debug=False, use_reloader=False)
