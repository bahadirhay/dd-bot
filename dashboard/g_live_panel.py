"""dashboard/g_live_panel.py — STRATEJI G (funding-konumlanma kontraryan) GERCEK-EMIR izleme paneli.

Acik pozisyonlar (canli PnL) + son kapananlar + funding sinyali + guard + ozet.
READ-ONLY: DB'yi file:...?mode=ro ile okur, sadece PUBLIC fiyat/funding sorgusu yapar; canli
bota/emir-thread'ine SIFIR dokunus. AYRI PORT (8060; 8050 botun ana panelinde).
Calistir: python dashboard/g_live_panel.py
"""
import os
import sys
import time
import sqlite3

import dash
from dash import dcc, html
from dash.dependencies import Input, Output

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.config import cfg
from engine.g_live import (
    COINS, W, PCT, HOLD_H, STOP_PCT, MAX_DAILY_LOSS_PCT,
    _funding_signal, _mark,
)

DB = os.path.join(os.path.dirname(__file__), "..", "data", "bot.db")
BG, CARD, UP, DN, TXT, DIM, ACC = "#0d1117", "#161b22", "#26a69a", "#ef5350", "#c9d1d9", "#8b949e", "#d29922"


def q(sql, params=()):
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute(sql, params).fetchall()]
    except sqlite3.OperationalError:
        return []
    finally:
        c.close()


def _has_table():
    return bool(q("SELECT name FROM sqlite_master WHERE type='table' AND name='g_live'"))


def fetch_state():
    if not _has_table():
        return [], [], {"n": 0, "net": 0, "wins": 0, "usd": 0}, 0.0
    open_rows = q("SELECT * FROM g_live WHERE status='OPEN' ORDER BY open_ts DESC")
    closed_rows = q("SELECT * FROM g_live WHERE status='CLOSED' ORDER BY close_ts DESC LIMIT 30")
    agg = q("SELECT COUNT(*) n, COALESCE(SUM(pnl_bps),0) net, COALESCE(SUM(pnl_usd),0) usd, "
            "SUM(CASE WHEN pnl_bps>0 THEN 1 ELSE 0 END) wins FROM g_live WHERE status='CLOSED'")
    day0 = int(time.time() // 86400) * 86400
    today = q("SELECT COALESCE(SUM(pnl_usd),0) usd FROM g_live WHERE status='CLOSED' AND close_ts>=?", (day0,))
    today_usd = float(today[0]["usd"]) if today else 0.0
    return open_rows, closed_rows, (agg[0] if agg else {"n": 0, "net": 0, "wins": 0, "usd": 0}), today_usd


def chip(label, value, color=TXT):
    return html.Div([
        html.Div(label, style={"color": DIM, "fontSize": "11px", "textTransform": "uppercase", "letterSpacing": "0.5px"}),
        html.Div(value, style={"color": color, "fontSize": "20px", "fontWeight": "600", "marginTop": "3px"}),
    ], style={"background": CARD, "padding": "12px 16px", "borderRadius": "8px", "minWidth": "120px"})


def th(t):
    return html.Th(t, style={"textAlign": "left", "padding": "6px 10px", "color": DIM,
                             "fontSize": "11px", "textTransform": "uppercase", "borderBottom": f"1px solid {CARD}"})


def td(v, color=TXT, bold=False):
    return html.Td(v, style={"padding": "6px 10px", "color": color, "fontSize": "13px",
                             "fontWeight": "600" if bold else "400"})


def header_row():
    active = bool(getattr(cfg, "V3_G_LIVE", False))
    notional = float(cfg.V3_G_MARGIN_USD) * float(cfg.V3_G_LEVERAGE)
    return html.Div([
        html.Div([
            html.Span("STRATEJI G", style={"fontSize": "24px", "fontWeight": "700", "color": TXT}),
            html.Span("  funding-konumlanma kontraryan", style={"fontSize": "14px", "color": DIM}),
        ]),
        html.Div("● GERCEK EMIR AKTIF" if active else "○ KAPALI",
                 style={"color": UP if active else DN, "fontWeight": "700", "fontSize": "15px"}),
    ], style={"display": "flex", "justifyContent": "space-between", "alignItems": "center", "marginBottom": "6px"})


def signal_cards():
    """Her coin icin guncel funding sinyali (public sorgu)."""
    cards = []
    for sym in COINS:
        try:
            sig = _funding_signal(sym)
        except Exception:
            sig = None
        if not sig:
            cards.append(chip(sym.replace("USDT", ""), "veri yok", DIM)); continue
        side, rate, _ = sig
        if side == -1:
            txt, col = "SHORT sinyal", DN
        elif side == 1:
            txt, col = "LONG sinyal", UP
        else:
            txt, col = "bekle", DIM
        cards.append(html.Div([
            html.Div(sym.replace("USDT", ""), style={"color": TXT, "fontSize": "13px", "fontWeight": "700"}),
            html.Div(f"funding {rate*100:+.4f}%", style={"color": DIM, "fontSize": "12px", "marginTop": "2px"}),
            html.Div(txt, style={"color": col, "fontSize": "15px", "fontWeight": "600", "marginTop": "4px"}),
        ], style={"background": CARD, "padding": "12px 16px", "borderRadius": "8px", "minWidth": "150px"}))
    return cards


def open_table(rows):
    if not rows:
        return html.Div("Acik pozisyon yok — funding ucunu bekliyor.",
                        style={"color": DIM, "padding": "14px", "background": CARD, "borderRadius": "8px"})
    body = []
    for r in rows:
        mk = 0.0
        try:
            mk = _mark(r["symbol"])
        except Exception:
            pass
        ent = r["entry_px"] or 0
        side = 1 if r["side"] == "LONG" else -1
        pnl_pct = ((mk - ent) if side == 1 else (ent - mk)) / ent * 100 if (mk and ent) else 0.0
        held = (time.time() - r["open_ts"]) / 3600.0
        col = UP if pnl_pct >= 0 else DN
        body.append(html.Tr([
            td(r["symbol"].replace("USDT", ""), bold=True),
            td(r["side"], UP if side == 1 else DN, bold=True),
            td(f"{ent:.4g}"),
            td(f"{mk:.4g}" if mk else "-"),
            td(f"{pnl_pct:+.2f}%", col, bold=True),
            td(f"{(r['funding'] or 0)*100:+.4f}%", DIM),
            td(f"{held:.1f}h / {HOLD_H}h", ACC if held >= HOLD_H * 0.8 else DIM),
            td(r["open_human"] or "", DIM),
        ]))
    return html.Table([
        html.Thead(html.Tr([th("coin"), th("yon"), th("giris"), th("mark"), th("canli pnl"),
                            th("funding"), th("tutus"), th("acilis")])),
        html.Tbody(body),
    ], style={"width": "100%", "borderCollapse": "collapse", "background": CARD, "borderRadius": "8px"})


def closed_table(rows):
    if not rows:
        return html.Div("Henuz kapanan islem yok.", style={"color": DIM, "padding": "14px",
                                                            "background": CARD, "borderRadius": "8px"})
    body = []
    for r in rows:
        col = UP if (r["pnl_bps"] or 0) >= 0 else DN
        body.append(html.Tr([
            td(r["symbol"].replace("USDT", ""), bold=True),
            td(r["side"], UP if r["side"] == "LONG" else DN),
            td(f"{r['entry_px']:.4g}" if r["entry_px"] else "-"),
            td(f"{r['exit_px']:.4g}" if r["exit_px"] else "-"),
            td(f"{r['pnl_bps']:+.0f}", col, bold=True),
            td(f"${r['pnl_usd']:+.3f}" if r["pnl_usd"] is not None else "-", col),
            td(r["reason"] or "", DIM),
            td(r["open_human"] or "", DIM),
        ]))
    return html.Table([
        html.Thead(html.Tr([th("coin"), th("yon"), th("giris"), th("cikis"), th("pnl bps"),
                            th("pnl usd"), th("sebep"), th("acilis")])),
        html.Tbody(body),
    ], style={"width": "100%", "borderCollapse": "collapse", "background": CARD, "borderRadius": "8px"})


app = dash.Dash(__name__)
app.title = "Strateji G — Canli"

app.layout = html.Div([
    dcc.Interval(id="tick", interval=20_000, n_intervals=0),
    html.Div(id="body", style={"maxWidth": "1100px", "margin": "0 auto"}),
], style={"background": BG, "minHeight": "100vh", "fontFamily": "Segoe UI, system-ui, sans-serif",
          "padding": "24px", "color": TXT})


@app.callback(Output("body", "children"), Input("tick", "n_intervals"))
def render(_n):
    open_rows, closed_rows, agg, today_usd = fetch_state()
    n = agg["n"] or 0
    wr = (agg["wins"] / n * 100) if n else 0.0
    net = agg["net"] or 0
    usd = agg["usd"] or 0
    notional = float(cfg.V3_G_MARGIN_USD) * float(cfg.V3_G_LEVERAGE)
    guard_col = DN if today_usd <= -abs(MAX_DAILY_LOSS_PCT) else DIM  # kaba gosterge

    return [
        header_row(),
        html.Div(f"ETH+AVAX  ·  funding-uc %{int(PCT*100)} (rolling {W} donem)  ·  tutus {HOLD_H}h  ·  "
                 f"SL {STOP_PCT}%  ·  ${cfg.V3_G_MARGIN_USD:g}x{cfg.V3_G_LEVERAGE}=${notional:g} notional  ·  "
                 f"gunluk-zarar limiti %{MAX_DAILY_LOSS_PCT:g} (kendi guard'i, D'den bagimsiz)",
                 style={"color": DIM, "fontSize": "12px", "marginBottom": "18px"}),

        html.Div("GUNCEL FUNDING SINYALI", style={"color": DIM, "fontSize": "12px", "marginBottom": "8px",
                                                  "letterSpacing": "0.5px"}),
        html.Div(signal_cards(), style={"display": "flex", "gap": "12px", "marginBottom": "22px", "flexWrap": "wrap"}),

        html.Div([
            chip("kapanan islem", str(n)),
            chip("net (bps)", f"{net:+.0f}", UP if net >= 0 else DN),
            chip("toplam usd", f"${usd:+.3f}", UP if usd >= 0 else DN),
            chip("kazanma %", f"{wr:.0f}%", UP if wr >= 50 else ACC),
            chip("bugun usd", f"${today_usd:+.3f}", UP if today_usd >= 0 else DN),
        ], style={"display": "flex", "gap": "12px", "marginBottom": "22px", "flexWrap": "wrap"}),

        html.Div("ACIK POZISYONLAR", style={"color": DIM, "fontSize": "12px", "marginBottom": "8px"}),
        open_table(open_rows),

        html.Div("SON KAPANANLAR", style={"color": DIM, "fontSize": "12px", "margin": "22px 0 8px"}),
        closed_table(closed_rows),

        html.Div(f"guncelleme {time.strftime('%H:%M:%S')}  ·  read-only  ·  port 8060",
                 style={"color": DIM, "fontSize": "11px", "marginTop": "20px", "textAlign": "right"}),
    ]


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8060, debug=False)
