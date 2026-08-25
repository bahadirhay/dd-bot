"""dashboard/ftsm_live_panel.py — Strateji F (gunluk trend-takip, ftsm_paper) COK-COIN izleme
paneli. Gunluk mum grafigi + giris/cikis noktalari + SL seviyesi + anlik durum, coin secici
butonlarla. READ-ONLY (DB + public fiyat), canli bota SIFIR dokunus, AYRI PORT (8057).
Calistir: python dashboard/ftsm_live_panel.py
"""
import json
import os
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timezone

import dash
import plotly.graph_objects as go
from dash import dcc, html
from dash.dependencies import Input, Output

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.config import cfg

DB = os.path.join(os.path.dirname(__file__), "..", "data", "bot.db")
BG, UP, DN, TXT, DIM, SLC = "#0d1117", "#26a69a", "#ef5350", "#c9d1d9", "#8b949e", "#f0b90b"

# engine/daily_tsm_paper.py ile AYNI (2026-07-30 cok-coin karari)
SYMBOLS = ["ETHUSDT", "BNBUSDT", "XLMUSDT", "LINKUSDT"]
_SL_BY_SYMBOL = {"ETHUSDT": 300.0, "XLMUSDT": 300.0, "LINKUSDT": 300.0}
N = int(getattr(cfg, "V3_FTSM_N", 40) or 40)


def q(sql, params=()):
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute(sql, params).fetchall()]
    finally:
        c.close()


def _iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def daily_klines(sym, limit=150):
    try:
        u = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1d&limit={limit}"
        r = json.loads(urllib.request.urlopen(u, timeout=15).read())
        return [{"ts": x[0] / 1000, "o": float(x[1]), "h": float(x[2]), "l": float(x[3]), "c": float(x[4])} for x in r]
    except Exception:
        return []


def mark_price(sym):
    try:
        u = f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}"
        r = json.loads(urllib.request.urlopen(u, timeout=10).read())
        return float(r.get("price", 0) or 0)
    except Exception:
        return 0.0


def build_fig(sym, bars, open_rows, closed_rows, sl_bps):
    fig = go.Figure()
    if bars:
        fig.add_trace(go.Candlestick(
            x=[_iso(b["ts"]) for b in bars], open=[b["o"] for b in bars], high=[b["h"] for b in bars],
            low=[b["l"] for b in bars], close=[b["c"] for b in bars],
            increasing_line_color=UP, decreasing_line_color=DN, showlegend=False, name=sym))

    for r in closed_rows:
        color = UP if r["side"] == "LONG" else DN
        marker_sym = "triangle-up" if r["side"] == "LONG" else "triangle-down"
        fig.add_trace(go.Scatter(x=[_iso(r["open_ts"])], y=[r["entry"]], mode="markers",
            marker=dict(symbol=marker_sym, size=13, color=color, line=dict(width=1, color=TXT)),
            showlegend=False, hovertext=f"{r['side']} giris @{r['entry']:.4g}"))
        if r.get("close_ts") and r.get("exit"):
            fig.add_trace(go.Scatter(x=[_iso(r["close_ts"])], y=[r["exit"]], mode="markers",
                marker=dict(symbol="x", size=10, color=DIM, line=dict(width=1, color=TXT)),
                showlegend=False, hovertext=f"cikis @{r['exit']:.4g} ({r.get('reason','')}) {r.get('pnl_bps',0):+.0f}bps"))

    for r in open_rows:
        color = UP if r["side"] == "LONG" else DN
        marker_sym = "triangle-up" if r["side"] == "LONG" else "triangle-down"
        fig.add_trace(go.Scatter(x=[_iso(r["open_ts"])], y=[r["entry"]], mode="markers",
            marker=dict(symbol=marker_sym, size=16, color=color, line=dict(width=2, color=TXT)),
            showlegend=False, hovertext=f"ACIK {r['side']} giris @{r['entry']:.4g}"))
        if sl_bps and bars:
            sl_px = r["entry"] * (1 - sl_bps / 1e4) if r["side"] == "LONG" else r["entry"] * (1 + sl_bps / 1e4)
            xs = [_iso(r["open_ts"]), _iso(bars[-1]["ts"] + 86400)]
            fig.add_trace(go.Scatter(x=xs, y=[sl_px, sl_px], mode="lines",
                line=dict(color=SLC, width=1.3, dash="dash"), showlegend=False,
                hovertext=f"SL @{sl_px:.4g} (-{sl_bps:.0f}bps)"))

    fig.update_layout(paper_bgcolor=BG, plot_bgcolor=BG, font=dict(color=TXT, size=11),
        margin=dict(l=8, r=8, t=8, b=8), height=480,
        xaxis=dict(rangeslider=dict(visible=False), gridcolor="#20262d"),
        yaxis=dict(gridcolor="#20262d", side="right"))
    return fig


def strip_item(sym, open_row, px, sl_bps):
    if not open_row:
        return html.Div([
            html.B(sym.replace("USDT", ""), style={"marginRight": "6px"}),
            html.Span("FLAT", style={"color": DIM}),
        ], style={"padding": "6px 10px", "border": "1px solid #30363d", "borderRadius": "6px"})
    side = open_row["side"]
    entry = open_row["entry"]
    pnl = ((px - entry) if side == "LONG" else (entry - px)) / entry * 1e4 if entry else 0.0
    color = UP if pnl >= 0 else DN
    days_open = (time.time() - open_row["open_ts"]) / 86400
    return html.Div([
        html.B(sym.replace("USDT", ""), style={"marginRight": "6px"}),
        html.Span(side, style={"color": color, "fontWeight": "bold", "marginRight": "6px"}),
        html.Span(f"{pnl:+.0f}bps", style={"color": color}),
        html.Div(f"@{entry:.4g}  {days_open:.1f}g  SL:{'-%.0f'%sl_bps if sl_bps else 'yok'}",
                 style={"color": DIM, "fontSize": "11px"}),
    ], style={"padding": "6px 10px", "border": f"1px solid {color}", "borderRadius": "6px"})


def closed_table(rows):
    if not rows:
        return html.Div("Henuz kapanan islem yok", style={"color": DIM, "padding": "4px 8px"})
    trs = [html.Tr([html.Th(h, style={"textAlign": "left", "padding": "4px 8px", "borderBottom": "1px solid #30363d"})
                     for h in ["Coin", "Yon", "Acilis", "Giris", "Kapanis", "Cikis", "PnL(bps)", "Sebep"]])]
    for r in rows:
        c = UP if (r["pnl_bps"] or 0) >= 0 else DN
        trs.append(html.Tr([
            html.Td((r.get("symbol") or "ETHUSDT").replace("USDT", ""), style={"padding": "4px 8px"}),
            html.Td(r["side"], style={"padding": "4px 8px", "color": c, "fontWeight": "bold"}),
            html.Td(r["open_human"] or "", style={"padding": "4px 8px", "color": DIM}),
            html.Td("%.4g" % (r["entry"] or 0), style={"padding": "4px 8px"}),
            html.Td(time.strftime("%Y-%m-%d %H:%M", time.localtime(r["close_ts"])) if r.get("close_ts") else "",
                    style={"padding": "4px 8px", "color": DIM}),
            html.Td("%.4g" % (r["exit"] or 0), style={"padding": "4px 8px"}),
            html.Td("%+.0f" % (r["pnl_bps"] or 0), style={"padding": "4px 8px", "color": c}),
            html.Td(r["reason"] or "", style={"padding": "4px 8px", "color": DIM}),
        ]))
    return html.Table(trs, style={"width": "100%", "borderCollapse": "collapse", "fontSize": "13px"})


app = dash.Dash(__name__)
app.title = "F Trend-Takip Live"
app.layout = html.Div([
    dcc.Store(id="focus", data=SYMBOLS[0]),
    dcc.Interval(id="tick", interval=15000, n_intervals=0),
    html.Div([
        html.H3("Strateji F — Gunluk Trend-Takip (cok-coin)", style={"display": "inline-block", "marginRight": "16px"}),
        html.Span("PAPER (gercek para degil)", style={"color": DIM, "fontSize": "12px",
                   "border": f"1px solid {DIM}", "borderRadius": "6px", "padding": "2px 8px"}),
        html.Div(f"N={N} gun momentum | per-coin SL: {_SL_BY_SYMBOL} (BNB=flip-only)",
                 style={"color": DIM, "fontSize": "12px", "marginTop": "4px"}),
        html.Div([html.Span("coin sec: ", style={"marginRight": "6px", "color": DIM}),
                  *[html.Button(s.replace("USDT", ""), id="btn-%s" % s, n_clicks=0,
                      style={"margin": "0 4px", "background": "#21262d", "color": TXT,
                             "border": "1px solid #30363d", "borderRadius": "6px", "padding": "4px 10px"})
                    for s in SYMBOLS]], style={"marginTop": "8px"}),
    ], style={"padding": "12px"}),
    html.Div(id="strip", style={"display": "flex", "gap": "8px", "padding": "0 12px 8px", "flexWrap": "wrap"}),
    html.Div([dcc.Graph(id="chart", config={"displayModeBar": False})],
             style={"background": "#161b22", "borderRadius": "8px", "margin": "0 12px 12px"}),
    html.Div([html.H4("Kapanan Islemler (tum coinler)", style={"padding": "0 12px"}),
              html.Div(id="closed-tbl", style={"padding": "0 12px 20px"})]),
], style={"background": BG, "minHeight": "100vh", "color": TXT, "fontFamily": "monospace"})


@app.callback(Output("focus", "data"), [Input("btn-%s" % s, "n_clicks") for s in SYMBOLS])
def pick(*clicks):
    ctx = dash.callback_context
    if not ctx.triggered:
        return SYMBOLS[0]
    return ctx.triggered[0]["prop_id"].split(".")[0].replace("btn-", "")


@app.callback([Output("strip", "children"), Output("chart", "figure"), Output("closed-tbl", "children")],
              [Input("tick", "n_intervals"), Input("focus", "data")])
def refresh(_, focus):
    open_all = q("SELECT * FROM ftsm_paper WHERE status='OPEN'")
    open_by_sym = {r["symbol"] or "ETHUSDT": r for r in open_all}
    strip = []
    for s in SYMBOLS:
        px = mark_price(s)
        strip.append(strip_item(s, open_by_sym.get(s), px, _SL_BY_SYMBOL.get(s, 0.0)))

    bars = daily_klines(focus, 150)
    marker_rows = q("SELECT * FROM ftsm_paper WHERE symbol=? AND status IN ('CLOSED','OPEN') ORDER BY open_ts ASC", (focus,))
    open_rows = [r for r in marker_rows if r["status"] == "OPEN"]
    closed_rows_focus = [r for r in marker_rows if r["status"] == "CLOSED"]
    fig = build_fig(focus, bars, open_rows, closed_rows_focus, _SL_BY_SYMBOL.get(focus, 0.0))

    closed_all = q("SELECT * FROM ftsm_paper WHERE status='CLOSED' ORDER BY close_ts DESC LIMIT 40")
    return strip, fig, closed_table(closed_all)


if __name__ == "__main__":
    print(f"Strateji F Live panel: http://127.0.0.1:8057  (coinler: {', '.join(SYMBOLS)})")
    app.run(host="127.0.0.1", port=8057, debug=False, use_reloader=False)
