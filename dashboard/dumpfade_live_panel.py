"""dashboard/dumpfade_live_panel.py — DUMPFADE GERCEK-EMIR izleme paneli.
Acik/bekleyen pozisyonlar + son kapananlar + ozet. READ-ONLY (DB + public fiyat sorgusu),
canli bota/panele SIFIR dokunus, AYRI PORT (8056). Calistir: python dashboard/dumpfade_live_panel.py
"""
import os
import sqlite3
import sys

import dash
from dash import dcc, html
from dash.dependencies import Input, Output

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.config import cfg
from engine.dumpfade_live import DF_MAX_DAILY_LOSS_PCT, MAX_CONCURRENT, STOP_PCT, UNIVERSE, _mark_price, df_guard_status

DB = os.path.join(os.path.dirname(__file__), "..", "data", "bot.db")
BG, UP, DN, TXT, DIM = "#0d1117", "#26a69a", "#ef5350", "#c9d1d9", "#8b949e"


def q(sql: str, params: tuple = ()) -> list[dict]:
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute(sql, params).fetchall()]
    finally:
        c.close()


def fetch_state():
    open_rows = q("SELECT * FROM dumpfade_live WHERE status IN ('PENDING','OPEN') ORDER BY open_ts DESC")
    closed_rows = q("SELECT * FROM dumpfade_live WHERE status='CLOSED' ORDER BY close_ts DESC LIMIT 30")
    agg = q("SELECT COUNT(*) n, COALESCE(SUM(pnl_bps),0) net, "
            "SUM(CASE WHEN pnl_bps>0 THEN 1 ELSE 0 END) wins FROM dumpfade_live WHERE status='CLOSED'")
    return open_rows, closed_rows, (agg[0] if agg else {"n": 0, "net": 0, "wins": 0})


def status_header():
    active = bool(getattr(cfg, "V3_DUMPFADE_LIVE", False))
    color = UP if active else DN
    txt = "AKTIF" if active else "KAPALI"
    gs = df_guard_status()
    guard_color = DN if gs["halted"] else UP
    guard_txt = ("KENDI GUARD DURDU (D ETKILENMEDI)" if gs["halted"]
                 else f"kendi guard PnL {gs['cumulative_pnl_pct']:+.2f}% / limit -{DF_MAX_DAILY_LOSS_PCT:.0f}%")
    return html.Div([
        html.H3("DumpFade Canli-Emir Paneli", style={"display": "inline-block", "marginRight": "16px"}),
        html.Span(txt, style={"color": color, "fontWeight": "bold", "fontSize": "14px",
                               "border": f"1px solid {color}", "borderRadius": "6px", "padding": "2px 10px"}),
        html.Div(f"evren={len(UNIVERSE)} sembol | margin=${cfg.TRADE_MARGIN_USD:.0f} x{cfg.LEVERAGE} | "
                 f"stop={STOP_PCT:.0f}% | max_pos={MAX_CONCURRENT}",
                 style={"color": DIM, "fontSize": "12px", "marginTop": "4px"}),
        html.Div(guard_txt, style={"color": guard_color, "fontSize": "12px", "marginTop": "2px"}),
    ], style={"padding": "12px"})


def _th(cells):
    return html.Tr([html.Th(c, style={"textAlign": "left", "padding": "4px 8px",
                                       "borderBottom": "1px solid #30363d"}) for c in cells])


def open_table(rows):
    if not rows:
        return html.Div("Acik/bekleyen pozisyon yok", style={"color": DIM, "padding": "4px 8px"})
    trs = [_th(["Sembol", "Durum", "Dump%", "Limit", "Giris", "Guncel", "PnL(canli,bps)", "Gun"])]
    for r in rows:
        px = _mark_price(r["symbol"]) if r["status"] == "OPEN" and r["entry_px"] else 0.0
        pnl = ((px - r["entry_px"]) / r["entry_px"] * 1e4) if (px and r["entry_px"]) else None
        pnlc = UP if (pnl or 0) >= 0 else DN
        trs.append(html.Tr([
            html.Td(r["symbol"].replace("USDT", ""), style={"padding": "4px 8px"}),
            html.Td(r["status"], style={"padding": "4px 8px"}),
            html.Td("%.1f%%" % (r["dump_pct"] or 0), style={"padding": "4px 8px"}),
            html.Td("%.6g" % (r["limit_px"] or 0), style={"padding": "4px 8px"}),
            html.Td("%.6g" % r["entry_px"] if r["entry_px"] else "-", style={"padding": "4px 8px"}),
            html.Td("%.6g" % px if px else "-", style={"padding": "4px 8px"}),
            html.Td("%+.0f" % pnl if pnl is not None else "-", style={"padding": "4px 8px", "color": pnlc}),
            html.Td(str(r["day_key"]), style={"padding": "4px 8px", "color": DIM}),
        ]))
    return html.Table(trs, style={"width": "100%", "borderCollapse": "collapse", "fontSize": "13px"})


def closed_table(rows):
    if not rows:
        return html.Div("Henuz kapanan islem yok", style={"color": DIM, "padding": "4px 8px"})
    trs = [_th(["Sembol", "Acilis", "Giris", "Cikis", "PnL(bps)", "Sebep"])]
    for r in rows:
        c = UP if (r["pnl_bps"] or 0) >= 0 else DN
        trs.append(html.Tr([
            html.Td(r["symbol"].replace("USDT", ""), style={"padding": "4px 8px"}),
            html.Td(r["open_human"] or "", style={"padding": "4px 8px", "color": DIM}),
            html.Td("%.6g" % (r["entry_px"] or 0), style={"padding": "4px 8px"}),
            html.Td("%.6g" % (r["exit_px"] or 0), style={"padding": "4px 8px"}),
            html.Td("%+.0f" % (r["pnl_bps"] or 0), style={"padding": "4px 8px", "color": c}),
            html.Td(r["close_reason"] or "", style={"padding": "4px 8px", "color": DIM}),
        ]))
    return html.Table(trs, style={"width": "100%", "borderCollapse": "collapse", "fontSize": "13px"})


app = dash.Dash(__name__)
app.title = "DumpFade Live"
app.layout = html.Div([
    dcc.Interval(id="tick", interval=8000, n_intervals=0),
    html.Div(id="header"),
    html.Div(id="summary", style={"padding": "0 12px 12px", "color": DIM, "fontSize": "13px"}),
    html.Div([html.H4("Acik / Bekleyen", style={"padding": "0 12px"}),
              html.Div(id="open-tbl", style={"padding": "0 12px"})],
             style={"background": "#161b22", "borderRadius": "8px", "margin": "0 12px 12px", "paddingBottom": "8px"}),
    html.Div([html.H4("Son Kapananlar", style={"padding": "0 12px"}),
              html.Div(id="closed-tbl", style={"padding": "0 12px"})],
             style={"background": "#161b22", "borderRadius": "8px", "margin": "0 12px 20px", "paddingBottom": "8px"}),
], style={"background": BG, "minHeight": "100vh", "color": TXT, "fontFamily": "monospace"})


@app.callback(
    [Output("header", "children"), Output("summary", "children"),
     Output("open-tbl", "children"), Output("closed-tbl", "children")],
    [Input("tick", "n_intervals")])
def refresh(_):
    open_rows, closed_rows, agg = fetch_state()
    n = agg["n"] or 0
    summary = "Kapanan: %d islem | net %+.0f bps | win%% %.1f" % (
        n, agg["net"] or 0, ((agg["wins"] or 0) / n * 100) if n else 0)
    return status_header(), summary, open_table(open_rows), closed_table(closed_rows)


if __name__ == "__main__":
    print("DumpFade Live panel: http://127.0.0.1:8056")
    app.run(host="127.0.0.1", port=8056, debug=False, use_reloader=False)
