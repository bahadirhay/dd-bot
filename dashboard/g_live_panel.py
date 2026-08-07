"""dashboard/g_live_panel.py — STRATEJI G (funding-konumlanma kontraryan) GERCEK-EMIR izleme paneli.

Acik pozisyonlar (canli PnL) + son kapananlar + funding sinyali + guard + ozet.
READ-ONLY: DB'yi file:...?mode=ro ile okur, sadece PUBLIC fiyat/funding sorgusu yapar; canli
bota/emir-thread'ine SIFIR dokunus. AYRI PORT (8060; 8050 botun ana panelinde).
Calistir: python dashboard/g_live_panel.py
"""
import os
import sys
import time
import json
import hmac
import hashlib
import sqlite3
import urllib.parse
import urllib.request

import dash
from dash import dcc, html
from dash.dependencies import Input, Output
import plotly.graph_objects as go

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.config import cfg
from engine.g_live import (
    COINS, W, PCT, HOLD_H, STOP_PCT, MAX_DAILY_LOSS_PCT,
    _funding_signal, _mark,
)

_last_equity = None   # anlik yon (artiyor/dusuyor) icin onceki okuma

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


# ───────── bakiye (signed GET, SADECE OKUMA — asla emir) ve fiyat (public) ─────────
def _signed_get(path, params=None, timeout=8):
    if not cfg.API_KEY or not cfg.API_SECRET:
        return None
    p = dict(params or {}); p["timestamp"] = int(time.time() * 1000); p["recvWindow"] = 5000
    qs = urllib.parse.urlencode(p)
    sig = hmac.new(cfg.API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    req = urllib.request.Request(f"{cfg.REST}{path}?{qs}&signature={sig}", headers={"X-MBX-APIKEY": cfg.API_KEY})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    except Exception:
        return None


def balance():
    """GERCEK hesap bakiyesi. wallet = 'balance' alani (isolated-margin dahil TOPLAM cuzdan);
    crossWalletBalance DEGIL (o, acik isolated pozisyonun teminatini disar birakip yaniltir).
    unpnl = TUM acik pozisyonlarin (cross+isolated) unrealized toplami (positionRisk'ten)."""
    b = _signed_get("/fapi/v2/balance")
    if not isinstance(b, list):
        return None
    wallet = avail = None
    for a in b:
        if a.get("asset") == "USDT":
            wallet = float(a.get("balance", 0) or 0)
            avail = float(a.get("availableBalance", 0) or 0)
    if wallet is None:
        return None
    unpnl = 0.0
    pr = _signed_get("/fapi/v2/positionRisk")
    if isinstance(pr, list):
        for p in pr:
            if abs(float(p.get("positionAmt", 0) or 0)) > 0:
                unpnl += float(p.get("unRealizedProfit", 0) or 0)
    return {"wallet": wallet, "unpnl": unpnl, "equity": wallet + unpnl, "avail": avail}


def klines(sym, interval="15m", limit=192):
    try:
        r = json.loads(urllib.request.urlopen(
            f"{cfg.REST}/fapi/v1/klines?symbol={sym}&interval={interval}&limit={limit}", timeout=8).read())
        # (ts_ms, open, high, low, close)
        return [(int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4])) for x in r]
    except Exception:
        return []


def trades_for(sym):
    if not _has_table():
        return []
    return q("SELECT * FROM g_live WHERE symbol=? ORDER BY open_ts", (sym,))


def main_bot_open(sym):
    """Ana botun (D/V3, cfg.SYMBOL) borsadaki acik pozisyonu — entry/SL/TP1/TP2/liq (trades tablosundan).
    Tek-sembol bot: sadece cfg.SYMBOL grafiginde gecerli."""
    if sym != getattr(cfg, "SYMBOL", "ETHUSDT"):
        return None
    rows = q("SELECT direction,entry_price,sl,tp1,tp2,liq_price,open_ts FROM trades "
             "WHERE status='OPEN' ORDER BY id DESC LIMIT 1")
    return rows[0] if rows else None


def _hline(fig, y, color, dash, label, width=1.4):
    fig.add_hline(y=y, line_dash=dash, line_color=color, line_width=width,
                  annotation_text=label, annotation_position="right",
                  annotation_font_color=color, annotation_font_size=11)


def price_chart(sym):
    """D dashboard tarzi: mum grafik + acik pozisyon (giris/SL/24h-cikis cizgileri) + gecmis islem isaretleri."""
    kl = klines(sym)
    fig = go.Figure()
    if kl:
        fig.add_trace(go.Candlestick(
            x=[k[0] for k in kl], open=[k[1] for k in kl], high=[k[2] for k in kl],
            low=[k[3] for k in kl], close=[k[4] for k in kl],
            increasing=dict(line=dict(color=UP), fillcolor=UP),
            decreasing=dict(line=dict(color=DN), fillcolor=DN), name=sym.replace("USDT", ""),
            showlegend=False))
    last_px = kl[-1][4] if kl else 0

    # NOT: ana bot artik PAPER (sadece G canli). G paneli YALNIZ G pozisyonlarini gosterir;
    # ana botun sanal (paper) pozisyonu buraya cizilmez (yoksa "iki pozisyon" gibi gorunurdu).
    for t in trades_for(sym):
        is_long = t["side"] == "LONG"
        e_col = UP if is_long else DN
        ent = t["entry_px"] or 0
        o_ms = t["open_ts"] * 1000
        # giris isareti
        fig.add_trace(go.Scatter(x=[o_ms], y=[ent], mode="markers",
                                 marker=dict(symbol="triangle-up" if is_long else "triangle-down",
                                             size=15, color=e_col, line=dict(color="#fff", width=1.2)),
                                 showlegend=False,
                                 hovertemplate=f"{t['side']} giris %{{y:.4g}}<extra></extra>"))
        if t["status"] == "OPEN":
            # ACIK POZISYON: giris + SL (-10%) + 24h zaman-cikisi cizgileri (D dashboard gibi)
            sl = ent * (1 + STOP_PCT / 100) if is_long else ent * (1 - STOP_PCT / 100)
            _hline(fig, ent, "#c9d1d9", "solid", f"GIRIS {ent:.4g} · ${(t['qty'] or 0)*ent:.0f}", 1.6)
            _hline(fig, sl, DN, "dash", f"SL {sl:.4g} ({STOP_PCT:g}%)", 1.4)
            # 24h cikis: dikey cizgi (fiyat-TP degil, zaman-TP)
            exit_ms = (t["open_ts"] + HOLD_H * 3600) * 1000
            fig.add_vline(x=exit_ms, line_dash="dot", line_color=ACC, line_width=1.4,
                          annotation_text="24h cikis", annotation_position="top",
                          annotation_font_color=ACC, annotation_font_size=11)
            # canli PnL kutusu (giris->son fiyat)
            if last_px:
                pnl_pct = ((last_px - ent) if is_long else (ent - last_px)) / ent * 100
                fig.add_trace(go.Scatter(x=[o_ms, kl[-1][0]], y=[ent, last_px], mode="lines",
                                         line=dict(color=UP if pnl_pct >= 0 else DN, width=1, dash="dot"),
                                         showlegend=False, hoverinfo="skip"))
        elif t["close_ts"] and t["exit_px"]:
            x_col = UP if (t["pnl_bps"] or 0) >= 0 else DN
            fig.add_trace(go.Scatter(x=[o_ms, t["close_ts"] * 1000], y=[ent, t["exit_px"]], mode="lines",
                                     line=dict(color=x_col, width=1, dash="dot"), showlegend=False, hoverinfo="skip"))
            fig.add_trace(go.Scatter(x=[t["close_ts"] * 1000], y=[t["exit_px"]], mode="markers",
                                     marker=dict(symbol="x", size=12, color=x_col), showlegend=False,
                                     hovertemplate=f"cikis %{{y:.4g}} ({t['pnl_bps']:+.0f}bps)<extra></extra>"))

    # VARSAYILAN GORUNUM: son ~96 mum (24h) mumlara odakli -> okunur. SL/24h cizgileri view disinda
    # kalabilir; kullanici PAN ile (surukleyerek) asagi/saga kaydirip gorur. dragmode=pan.
    x0 = x1 = y0 = y1 = None
    if kl:
        vis = kl[-96:] if len(kl) >= 96 else kl
        x0 = vis[0][0]
        x1 = kl[-1][0] + 8 * 3600 * 1000          # sagda ~8h bosluk (giris ucgeni/nefes payi)
        lows = [k[3] for k in vis]; highs = [k[2] for k in vis]
        pad = (max(highs) - min(lows)) * 0.12 or (max(highs) * 0.002)
        y0, y1 = min(lows) - pad, max(highs) + pad

    fig.update_layout(
        height=420, margin=dict(l=8, r=70, t=30, b=24), paper_bgcolor=CARD, plot_bgcolor="#0d1117",
        dragmode="pan",
        title=dict(text=sym.replace("USDT", "") + " · 15m", x=0.01, font=dict(color=TXT, size=14)),
        xaxis=dict(type="date", gridcolor="#1c2230", color=DIM, rangeslider=dict(visible=False),
                   range=[x0, x1] if x0 else None),
        yaxis=dict(gridcolor="#1c2230", color=DIM, side="right", range=[y0, y1] if y0 else None,
                   tickformat=".4g"),
        showlegend=False, font=dict(color=TXT), hovermode="x unified")
    return dcc.Graph(figure=fig, config={
        "displayModeBar": True, "scrollZoom": True, "displaylogo": False,
        "modeBarButtonsToRemove": ["select2d", "lasso2d"], "doubleClick": "autosize",
        "scrollZoom": True},
        style={"background": CARD, "borderRadius": "8px"})


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
        try:
            px = _mark(sym)
        except Exception:
            px = 0.0
        cards.append(html.Div([
            html.Div([
                html.Span(sym.replace("USDT", ""), style={"color": TXT, "fontSize": "13px", "fontWeight": "700"}),
                html.Span(f"  ${px:.4g}" if px else "  -", style={"color": TXT, "fontSize": "18px",
                          "fontWeight": "700", "marginLeft": "6px"}),
            ]),
            html.Div(f"funding {rate*100:+.4f}%", style={"color": DIM, "fontSize": "12px", "marginTop": "3px"}),
            html.Div(txt, style={"color": col, "fontSize": "15px", "fontWeight": "600", "marginTop": "4px"}),
        ], style={"background": CARD, "padding": "12px 16px", "borderRadius": "8px", "minWidth": "170px"}))
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
        notional = (r["qty"] or 0) * ent
        lev = max(float(getattr(cfg, "V3_G_LEVERAGE", 3)), 1)
        body.append(html.Tr([
            td(r["symbol"].replace("USDT", ""), bold=True),
            td(r["side"], UP if side == 1 else DN, bold=True),
            td(f"{ent:.4g}"),
            td(f"${notional:.0f}  ·  tem ${notional/lev:.0f}", TXT, bold=True),
            td(f"{mk:.4g}" if mk else "-"),
            td(f"{pnl_pct:+.2f}%", col, bold=True),
            td(f"{(r['funding'] or 0)*100:+.4f}%", DIM),
            td(f"{held:.1f}h / {HOLD_H}h", ACC if held >= HOLD_H * 0.8 else DIM),
            td(r["open_human"] or "", DIM),
        ]))
    return html.Table([
        html.Thead(html.Tr([th("coin"), th("yon"), th("giris"), th("buyukluk (tem.)"), th("mark"),
                            th("canli pnl"), th("funding"), th("tutus"), th("acilis")])),
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


def balance_cards():
    global _last_equity
    bal = balance()
    if not bal:
        return html.Div("Bakiye okunamadi (API anahtari yok/erisim).",
                        style={"color": DIM, "background": CARD, "padding": "12px 16px", "borderRadius": "8px"})
    eq = bal["equity"]
    arrow, dcol, delta = "", DIM, 0.0
    if _last_equity is not None:
        delta = eq - _last_equity
        if delta > 1e-6:   arrow, dcol = "▲", UP
        elif delta < -1e-6: arrow, dcol = "▼", DN
    _last_equity = eq
    unpnl_col = UP if bal["unpnl"] >= 0 else DN
    return html.Div([
        chip("gercek bakiye (USDT)", f"${bal['wallet']:.2f}"),
        chip("anlik equity", f"${eq:.2f} {arrow}", dcol),
        chip("acik pozisyon PnL", f"${bal['unpnl']:+.3f}", unpnl_col),
        chip("kullanilabilir", f"${bal['avail']:.2f}", DIM),
    ], style={"display": "flex", "gap": "12px", "flexWrap": "wrap"})


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
                 style={"color": DIM, "fontSize": "12px", "marginBottom": "16px"}),

        html.Div("HESAP BAKIYESI (canli, ▲/▼ = son 20sn yon)", style={"color": DIM, "fontSize": "12px",
                 "marginBottom": "8px", "letterSpacing": "0.5px"}),
        balance_cards(),
        html.Div(style={"height": "22px"}),

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

        html.Div("COIN GRAFIKLERI  (▲/▼ = giris, ✕ = cikis; pozisyon nerede acildi gorunur)",
                 style={"color": DIM, "fontSize": "12px", "marginBottom": "8px", "letterSpacing": "0.5px"}),
        html.Div([price_chart(s) for s in COINS],
                 style={"display": "grid", "gridTemplateColumns": "repeat(auto-fit, minmax(420px, 1fr))",
                        "gap": "12px", "marginBottom": "22px"}),

        html.Div("ACIK POZISYONLAR", style={"color": DIM, "fontSize": "12px", "marginBottom": "8px"}),
        open_table(open_rows),

        html.Div("SON KAPANANLAR", style={"color": DIM, "fontSize": "12px", "margin": "22px 0 8px"}),
        closed_table(closed_rows),

        html.Div(f"guncelleme {time.strftime('%H:%M:%S')}  ·  read-only  ·  port 8060",
                 style={"color": DIM, "fontSize": "11px", "marginTop": "20px", "textAlign": "right"}),
    ]


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8060, debug=False)
