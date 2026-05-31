"""
dashboard/app.py — Gerçek zamanlı Dash dashboard
http://localhost:8050
"""
from __future__ import annotations
import sys, os, sqlite3, time
from datetime import datetime, timezone, timedelta

try:
    from zoneinfo import ZoneInfo

    CHART_TZ = ZoneInfo("Europe/Istanbul")
except Exception:
    CHART_TZ = timezone(timedelta(hours=3))

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import dash
from dash import dcc, html, Input, Output, State, callback_context
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from core.config import cfg, is_paper_mode
from core.state  import state, effective_price, data_is_fresh
from dashboard.binance_chart import get_mtf_package
from engine.structure_display import alignment_detail

app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.DARKLY],
    title="ETH Bot Dashboard",
    update_title=None,
    suppress_callback_exceptions=True,
)

C = {
    "bg":     "#0d1117",
    "card":   "#161b22",
    "border": "#30363d",
    "text":   "#c9d1d9",
    "white":  "#f0f6fc",
    "green":  "#26a641",
    "red":    "#da3633",
    "yellow": "#e8b467",
    "orange": "#f0883e",
    "blue":   "#58a6ff",
    "purple": "#a371f7",
    "muted":  "#8b949e",
}

_S = {"fontSize": "13px", "color": C["muted"], "lineHeight": "1.45"}
_B = {"fontWeight": "600"}


def _card(title, content_id, color_left=None):
    style = {
        "backgroundColor": C["card"],
        "border": f"1px solid {C['border']}",
        "borderRadius": "8px",
    }
    if color_left:
        style["borderLeft"] = f"3px solid {color_left}"
    return dbc.Card([
        dbc.CardHeader(
            title,
            style={
                **_B,
                "fontSize": "13px",
                "color": C["text"],
                "backgroundColor": C["card"],
                "borderColor": C["border"],
                "padding": "8px 12px",
            },
        ),
        dbc.CardBody(html.Div(id=content_id), style={"padding": "12px 14px"}),
    ], style=style, className="mb-2 h-100")


def _metric_row(label, val_id, color=C["text"], size="20px"):
    return html.Div([
        html.Div(label, style=_S),
        html.Div(id=val_id, style={"fontSize": size, "fontWeight": "bold",
                                    "color": color, "marginTop": "2px"}),
    ])


app.layout = dbc.Container([

    # ── Header ────────────────────────────────────────────────
    dbc.Row([
        dbc.Col(html.Div([
            html.Span("⚡ ", style={"color": C["yellow"]}),
            html.Span("ETH/USDT Bot Dashboard",
                      style={"color": C["text"], "fontSize": "16px", "fontWeight": "bold"}),
        ]), width=5),
        dbc.Col(html.Div(id="hdr-status", style={
            "textAlign": "right", "fontSize": "12px", "color": C["muted"]
        }), width=7),
    ], className="py-2 px-3",
       style={"backgroundColor": C["card"], "borderBottom": f"1px solid {C['border']}"}),

    # ── Metrik bantı ──────────────────────────────────────────
    dbc.Row([
        dbc.Col(dbc.Card(dbc.CardBody([
            html.Div("FIYAT", style=_S),
            html.Div(id="m-price", style={"fontSize":"24px","fontWeight":"bold","color":C["blue"]}),
            html.Div(id="m-spread", style={**_S, "marginTop":"2px"}),
        ]), style={"backgroundColor":C["card"],"border":f"1px solid {C['border']}",
                   "borderLeft":f"3px solid {C['blue']}"}), width=2),

        dbc.Col(dbc.Card(dbc.CardBody([
            html.Div("BAKİYE", style=_S),
            html.Div(id="m-balance", style={"fontSize":"24px","fontWeight":"bold","color":C["green"]}),
            html.Div(id="m-api-status", style={**_S, "marginTop":"2px"}),
        ]), style={"backgroundColor":C["card"],"border":f"1px solid {C['border']}",
                   "borderLeft":f"3px solid {C['green']}"}), width=2),

        dbc.Col(dbc.Card(dbc.CardBody([
            html.Div("REJİM", style=_S),
            html.Div(id="m-regime", style={"fontSize":"24px","fontWeight":"bold"}),
            html.Div(id="m-score", style={**_S, "marginTop":"2px"}),
        ]), style={"backgroundColor":C["card"],"border":f"1px solid {C['border']}"}), width=2),

        dbc.Col(dbc.Card(dbc.CardBody([
            html.Div("FLOW", style=_S),
            html.Div(id="m-cvd", style={"fontSize":"24px","fontWeight":"bold"}),
            html.Div(id="m-taker", style={**_S, "marginTop":"2px"}),
        ]), style={"backgroundColor":C["card"],"border":f"1px solid {C['border']}"}), width=2),

        dbc.Col(dbc.Card(dbc.CardBody([
            html.Div("SETUP", style=_S),
            html.Div(id="m-pos", style={"fontSize":"20px","fontWeight":"bold"}),
            html.Div(id="m-pos2", style={**_S, "marginTop":"2px"}),
        ]), style={"backgroundColor":C["card"],"border":f"1px solid {C['border']}"}), width=2),

        dbc.Col(dbc.Card(dbc.CardBody([
            html.Div("EXECUTION", style=_S),
            html.Div(id="m-funding", style={"fontSize":"18px","fontWeight":"bold"}),
            html.Div(id="m-oi", style={**_S, "marginTop":"2px"}),
        ]), style={"backgroundColor":C["card"],"border":f"1px solid {C['border']}"}), width=2),
    ], className="px-2 pt-2 g-2"),

    # ── Ana grafik (tam genişlik) ───────────────────────────────
    dbc.Row([
        dbc.Col(dbc.Card(dbc.CardBody(
            dcc.Graph(
                id="main-chart",
                style={"height": "min(82vh, 900px)"},
                config={"scrollZoom": True, "displayModeBar": True},
            ),
        ), style={"backgroundColor": C["card"], "border": f"1px solid {C['border']}"}),
        width=12),
    ], className="px-2 mt-2"),

    # ── Piyasa Açıkla (Binance ekran görüntüsü = ana yol) ──
    dbc.Row([
        dbc.Col(dbc.Card([
            dbc.CardHeader("Piyasa Açıkla — Binance grafiğinden", style={
                **_S, "backgroundColor": C["card"], "borderColor": C["border"],
            }),
            dbc.CardBody([
                html.P(
                    "Grafiğe tıklayın → «Dashboard grafiğinden açıkla». "
                    "İsteğe bağlı Binance görseli yükleyebilirsiniz.",
                    style={**_S, "marginBottom": "12px"},
                ),
                html.Div([
                    html.Span("Claude API anahtarı: ", style=_S),
                    dcc.Input(
                        id="claude-api-key-input",
                        type="password",
                        placeholder="sk-ant-...",
                        style={
                            "width": "340px", "marginRight": "8px",
                            "backgroundColor": C["bg"], "color": C["text"],
                            "border": f"1px solid {C['border']}", "padding": "4px 8px",
                        },
                    ),
                    dbc.Button("Kaydet", id="btn-claude-save",
                               color="success", size="sm", className="me-2"),
                    html.Span(id="claude-api-status", style={
                        "fontSize": "11px", "color": C["muted"],
                    }),
                ], className="mb-3"),
                dcc.Upload(
                    id="explain-upload",
                    children=html.Div(
                        "1) Binance ekran görüntüsünü buraya sürükle veya tıkla",
                        style={"fontSize": "12px", "color": C["blue"], "padding": "14px",
                               "border": f"2px dashed {C['blue']}", "borderRadius": "6px",
                               "textAlign": "center", "fontWeight": "bold"},
                    ),
                    multiple=False,
                    style={"marginBottom": "10px"},
                ),
                html.Div(id="explain-upload-status", style={"fontSize": "11px", "color": C["muted"],
                                                            "marginBottom": "8px"}),
                dbc.Button(
                    "Dashboard grafiğinden açıkla",
                    id="btn-explain-chart",
                    color="primary", size="sm", className="me-2 mb-2",
                ),
                dbc.Button(
                    "Binance görseli (opsiyonel)",
                    id="btn-explain-screenshot",
                    color="secondary", size="sm", outline=True, className="me-2 mb-2",
                ),
                html.Div([
                    html.P(
                        "Saat grafikten otomatik alınır (son kapanan 15m mum). "
                        "Başka bir mum için üst grafiğe tıklayın; DB kayıtları UTC ile hizalanır.",
                        style={"fontSize": "11px", "color": C["muted"], "marginBottom": "8px"},
                    ),
                    dcc.RadioItems(
                        id="explain-tz",
                        options=[
                            {"label": " Binance saati (Türkiye UTC+3)", "value": "tr"},
                            {"label": " UTC", "value": "utc"},
                        ],
                        value="tr",
                        inline=True,
                        style={"fontSize": "11px", "color": C["text"], "marginBottom": "8px"},
                        inputStyle={"marginRight": "4px"},
                    ),
                    html.Div([
                        html.Span("Seçili mum: ", style=_S),
                        dcc.Input(
                            id="explain-datetime",
                            type="text",
                            placeholder="otomatik — grafik yüklenince dolar",
                            debounce=True,
                            style={
                                "width": "200px", "marginRight": "8px",
                                "backgroundColor": C["bg"], "color": C["text"],
                                "border": f"1px solid {C['border']}", "padding": "4px 8px",
                            },
                        ),
                        html.Span(
                            "üst grafikte başka mum seçmek için tıklayın",
                            style={"fontSize": "10px", "color": C["muted"]},
                        ),
                    ], className="mb-1"),
                    html.Div(id="explain-time-sync", style={
                        "fontSize": "11px", "color": C["blue"],
                        "marginBottom": "8px", "lineHeight": "1.5",
                    }),
                    html.Div([
                        dbc.Button("Kural ile Açıkla", id="btn-explain-rule",
                                   color="secondary", size="sm", outline=True, className="me-1"),
                        dbc.Button("Claude (saat + DB)", id="btn-explain-llm",
                                   color="secondary", size="sm", outline=True, className="me-1"),
                        dbc.Button("Grafikten saat oku", id="btn-vision-time",
                                   color="secondary", size="sm", outline=True),
                    ], className="mb-2"),
                ]),
                dcc.Loading(
                    html.Pre(
                        id="explain-output",
                        style={
                            "whiteSpace": "pre-wrap", "fontSize": "12px",
                            "color": C["text"], "maxHeight": "280px",
                            "overflowY": "auto", "margin": 0,
                            "backgroundColor": C["bg"], "padding": "10px",
                            "borderRadius": "4px", "border": f"1px solid {C['border']}",
                        },
                    ),
                    type="dot", color=C["blue"],
                ),
            ], style={"padding": "12px"}),
        ], style={"backgroundColor": C["card"], "border": f"1px solid {C['border']}"}),
        width=12),
    ], className="px-2 mt-2"),

    dcc.Store(id="explain-image-path", data=""),

    # ── Alt tablolar ──────────────────────────────────────────
    dbc.Row([
        dbc.Col(_card("Son 20 Trade",              "trades-tbl"),  width=7),
        dbc.Col(_card("Giriş Yapılmama Sebepleri", "noentry-tbl"), width=5),
    ], className="px-2 pb-3 mt-0"),

    # Timerlar
    dcc.Interval(id="fast", interval=2000,  n_intervals=0),
    dcc.Interval(id="slow", interval=15000, n_intervals=0),

    # Bakiye store (slow'da güncellenir)
    dcc.Store(id="balance-store", data=0.0),

], fluid=True, style={"backgroundColor": C["bg"], "minHeight": "100vh"})


# ── DB yardımcı ───────────────────────────────────────────────

_backfill_done = False


def _db():
    global _backfill_done
    try:
        c = sqlite3.connect(cfg.DB_PATH, timeout=3)
        c.row_factory = sqlite3.Row
        if not _backfill_done:
            try:
                from botlog.db import backfill_closed_trade_metrics
                backfill_closed_trade_metrics()
            except Exception:
                pass
            _backfill_done = True
        return c
    except Exception:
        return None


def _is_system_trade(t) -> bool:
    """DB temizliği / restart — gerçek SL-TP kapanışı değil."""
    reason = str(t["close_reason"] or "").split("|")[0].strip()
    notes = str(t["notes"] or "")
    if reason == "duplicate_open" or notes == "orphan_duplicate":
        return True
    if reason in (
        "flat_on_startup",
        "no_position_on_startup",
        "orphan_no_position",
    ):
        ep = float(t["entry_price"] or 0)
        xp = float(t["exit_price"] or 0)
        pnl = float(t["pnl"] or 0)
        if abs(pnl) < 1e-8 and (xp <= 0 or abs(ep - xp) < 0.01):
            return True
    return False


# ── Bakiye callback (slow) ────────────────────────────────────

@app.callback(
    Output("balance-store",  "data"),
    Output("m-balance",      "children"),
    Output("m-api-status",   "children"),
    Output("m-api-status",   "style"),
    Input("slow", "n_intervals"),
)
def update_balance(_):
    """mmbot3: bakiye ana loop'ta çekilir; dashboard state'i okur."""
    if is_paper_mode():
        bal = getattr(state, "paper_balance", 0.0) or cfg.PAPER_BALANCE_USD
        sub = "📊 İzleme (paper) — emir gönderilmez"
        if state.in_position:
            sub += f"  |  PnL anlık: {state.unrealized_pnl:+.2f}"
        return bal, f"${bal:,.2f} sim", sub, {**_S, "color": C["purple"]}

    if not cfg.API_KEY:
        return 0.0, "—", "API key yok → İzleme modu", {**_S, "color": C["yellow"]}

    if state.api_ok:
        ts = getattr(state, "account_sync_ts", 0) or state.real_balance_ts
        age = int(time.time() - ts) if ts > 0 else -1
        equity = getattr(state, "equity_balance", 0.0) or state.real_balance
        wallet = getattr(state, "wallet_balance", 0.0)
        avail = getattr(state, "available_balance", 0.0)
        upnl = getattr(state, "unrealized_pnl", 0.0)
        ex = getattr(state, "exchange_position", None) or {}
        if state.in_position and ex.get("pnl") is not None:
            upnl = float(ex.get("pnl", upnl) or upnl)
        # Binance "Margin Balance" = cüzdan + açık pozisyon uPnL
        val = f"${equity:,.2f} USDT"
        pc = C["green"] if upnl >= 0 else C["red"]
        sum_line = wallet + upnl
        sub = html.Div(
            [
                html.Div(
                    "Margin bakiye (Binance ile aynı)",
                    style={"fontSize": "10px", "color": C["muted"], "marginBottom": "2px"},
                ),
                html.Div(
                    [
                        html.Span(f"Cüzdan ${wallet:,.2f}", style={"marginRight": "8px"}),
                        html.Span(
                            f"+ uPnL {upnl:+.2f}",
                            style={"color": pc, "fontWeight": "bold", "marginRight": "8px"},
                        ),
                        html.Span(
                            f"≈ ${sum_line:,.2f}",
                            style={"color": C["muted"], "fontSize": "10px"},
                        ),
                    ],
                    style={"fontSize": "11px"},
                ),
                html.Div(
                    f"Kullanılabilir (yeni emir) ${avail:,.2f}"
                    + (f"  ·  {age}s önce" if age >= 0 else "  ·  CANLI"),
                    style={"fontSize": "10px", "color": C["muted"], "marginTop": "3px"},
                ),
            ]
        )
        if equity <= 0 and wallet <= 0:
            val = "$0.00 USDT"
            sub = "✓ Bağlı · bakiye sıfır"
        return equity, val, sub, {**_S, "color": C["green"]}

    if state.api_error:
        return 0.0, "HATA", state.api_error[:80], {**_S, "color": C["red"]}

    return 0.0, "...", "API bekleniyor...", {**_S, "color": C["yellow"]}


# ── Fast metrikler ────────────────────────────────────────────

@app.callback(
    Output("hdr-status",  "children"),
    Output("m-price",     "children"),
    Output("m-spread",    "children"),
    Output("m-regime",    "children"),
    Output("m-regime",    "style"),
    Output("m-score",     "children"),
    Output("m-cvd",       "children"),
    Output("m-cvd",       "style"),
    Output("m-taker",     "children"),
    Output("m-pos",       "children"),
    Output("m-pos",       "style"),
    Output("m-pos2",      "children"),
    Output("m-funding",   "children"),
    Output("m-funding",   "style"),
    Output("m-oi",        "children"),
    Input("fast", "n_intervals"),
)
def update_fast(_):
    from dashboard.live_metrics import get_panel_metrics
    try:
        m = get_panel_metrics()
    except Exception as e:
        import logging
        logging.getLogger("Dashboard").warning(f"Panel metrik: {e}")
        m = {
            "price": effective_price(),
            "bid": state.bid,
            "ask": state.ask,
            "regime": state.regime,
            "score": 0,
            "cvd": state.cvd_5m,
            "taker": state.taker_ratio,
            "operation": {},
            "bot_ok": False,
        }
    op = m.get("operation") or {}
    now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    price = m["price"]
    bid, ask = m["bid"], m["ask"]
    tv = m["tv"]
    regime = m["regime"]
    score = m["score"]
    regime_never_ran = not m["bot_ok"] and regime in ("UNKNOWN", "—", "")
    cvd = m["cvd"]
    taker = m["taker"]
    fund = m["fund"]
    oi_up = m["oi_up"]
    fresh = m["fresh"]
    now_ts = time.time()
    ta = int(now_ts - state.trade_last_update) if state.trade_last_update else -1
    ba = int(now_ts - state.book_last_update) if state.book_last_update else -1
    src = f"book {ba}s" if ba >= 0 and (ta < 0 or ba <= ta) else f"trade {ta}s"
    data_status = f"🟢 Canlı ({src})" if fresh else "🔴 Veri yok"
    mode = "TESTNET" if cfg.TESTNET else "CANLI"
    if is_paper_mode():
        api_dot = "📊 Paper"
    elif cfg.API_KEY:
        api_dot = "🟢 API" if state.api_ok else ("🔴 API" if state.api_error else "🟡 API")
    else:
        api_dot = "⚪ İzleme"
    bot_dot = "🟢 Bot" if m["bot_ok"] else "🔴 Bot kapalı"
    hdr = f"{data_status}  ·  {api_dot}  ·  {bot_dot}  ·  {mode}  ·  {now}"

    # Rejim
    reg_code = str(op.get("regime", {}).get("code") or "")
    reg_detail = str(op.get("regime", {}).get("detail") or m["regime_sub"])
    reg_map = {
        "TREND_UP": ("TREND UP", C["green"]),
        "TREND_DOWN": ("TREND DOWN", C["red"]),
        "RANGE_ACTIVE": ("RANGE", C["yellow"]),
        "REGIME_UNCLEAR": ("UNCLEAR", C["muted"]),
    }
    regime_display, rc = reg_map.get(
        reg_code,
        (m["regime_display"] if not regime_never_ran else "—", C["yellow"] if regime_never_ran else C["muted"]),
    )
    regime_sub = reg_detail or m["regime_sub"]
    regime_style = {"fontSize":"24px","fontWeight":"bold","color": rc}

    # Yapi
    structure = op.get("structure") or {}
    v3 = op.get("v3") or {}
    v3_levels = v3.get("levels") or {}
    v3_structure = v3.get("structure") or {}
    v3_scenario = v3.get("scenario") or {}
    v3_decision = v3.get("decision") or {}
    struct_code = str(structure.get("code") or "NO_STRUCTURE")
    struct_map = {
        "BETWEEN_MAJOR_LEVELS": ("MAJÖR ARASI", C["blue"]),
        "UNDER_MAJOR_R": ("MAJÖR R ALTI", C["red"]),
        "ABOVE_MAJOR_S": ("MAJÖR S ÜSTÜ", C["green"]),
        "ABOVE_BROKEN_MAJOR_R": ("KIRILMIŞ MAJÖR R ÜSTÜ", C["orange"]),
        "BELOW_BROKEN_MAJOR_S": ("KIRILMIŞ MAJÖR S ALTI", C["orange"]),
        "V3_ALIGNED_UP": ("V3 HİZALI UP", C["green"]),
        "V3_ALIGNED_DOWN": ("V3 HİZALI DOWN", C["red"]),
        "V3_MIXED": ("V3 KARIŞIK", C["yellow"]),
        "V3_UNCLEAR": ("V3 UNCLEAR", C["muted"]),
        "NO_STRUCTURE": ("YAPI YOK", C["muted"]),
    }
    struct_label, struct_color = struct_map.get(struct_code, (struct_code, C["muted"]))
    if m.get("trade_ok") or abs(cvd) > 0.01:
        cvd_display = f"{cvd:+,.0f}"
        cvd_color = C["green"] if cvd > 0 else C["red"] if cvd < 0 else C["muted"]
    else:
        cvd_display = "—"
        cvd_color = C["muted"]
    cvd_style = {"fontSize":"24px","fontWeight":"bold","color": cvd_color}
    main_r = float(structure.get("main_resistance") or 0)
    main_s = float(structure.get("main_support") or 0)
    channel_r = float(structure.get("channel_resistance") or 0)
    channel_s = float(structure.get("channel_support") or 0)
    channel_src = str(structure.get("channel_source") or "")
    struct_r = float(structure.get("major_resistance") or 0)
    struct_s = float(structure.get("major_support") or 0)
    struct_deep_r = float(structure.get("deep_major_resistance") or 0)
    struct_deep_s = float(structure.get("deep_major_support") or 0)
    active_struct_r = float(structure.get("active_major_resistance") or 0)
    active_struct_s = float(structure.get("active_major_support") or 0)
    v3_r = float(v3_levels.get("active_resistance") or 0)
    v3_s = float(v3_levels.get("active_support") or 0)
    v3_scn = str(v3_scenario.get("name") or "")
    v3_action = str(v3_decision.get("action") or "")
    v3_reason = str(v3_decision.get("reason") or "")
    v3_pos = float(v3_levels.get("range_position", 0.5) or 0.5)
    v3_1h = str(((v3_structure.get("1h") or {}).get("direction")) or "?")
    v3_mode = bool(getattr(cfg, "STRATEGY_V3_ENABLED", False))
    if v3_mode and (v3_r > 0 or v3_s > 0 or v3_scn):
        v3_band_line = (
            f"V3 bant: R {v3_r:.2f} / S {v3_s:.2f}"
            if v3_r > 0 or v3_s > 0
            else "V3 bant: —"
        )
        zone = str(v3_levels.get("zone") or "MID_RANGE")
        v3_scenario_line = (
            f"Senaryo {v3_scn or '—'} · {v3_action or '—'} · zone {zone} · 1h {v3_1h} · p={v3_pos:.2f}"
        )
        v3_reason_line = f"{v3_reason or '—'}"
        taker_str = html.Div(
            [
                html.Div(
                    f"Taker {taker:.0%} · CVD {cvd_display} · {m.get('cvd_sub', '')}",
                    style={"color": C["white"], "fontSize": "11px"},
                ),
                html.Div(v3_band_line, style={"color": C["blue"], "fontWeight": "600"}),
                html.Div(v3_scenario_line, style={"color": C["yellow"], "fontSize": "11px"}),
                html.Div(v3_reason_line, style={"color": C["muted"], "fontSize": "11px"}),
            ]
        )
    elif main_r > 0 or main_s > 0 or struct_r > 0 or struct_s > 0:
        main_line = (
            f"Mavi bant: R {main_r:.2f} / S {main_s:.2f}"
            if main_r > 0 or main_s > 0
            else "Mavi bant: —"
        )
        channel_line = (
            f"Kanal: R {channel_r:.2f} / S {channel_s:.2f}"
            + (f" ({channel_src})" if channel_src else "")
            if channel_r > 0 or channel_s > 0
            else "Kanal: —"
        )
        ref_line = (
            f"Dış ref: R {struct_r:.2f} / S {struct_s:.2f}"
            + (
                f" · deep {struct_deep_r:.2f}/{struct_deep_s:.2f}"
                if (
                    (struct_deep_r > 0 and abs(struct_deep_r - struct_r) > 0.5)
                    or (struct_deep_s > 0 and abs(struct_deep_s - struct_s) > 0.5)
                )
                else ""
            )
        )
        active_line = (
            f"Aktif ref: R {active_struct_r:.2f} / S {active_struct_s:.2f}"
            if active_struct_r > 0 or active_struct_s > 0
            else "Aktif ref: —"
        )
        v2_band_line = (
            f"V3 bant: R {v3_r:.2f} / S {v3_s:.2f}"
            if v3_r > 0 or v3_s > 0
            else "V3 bant: —"
        )
        v2_scenario_line = (
            f"V3 senaryo: {v3_scn or '—'} | karar {v3_action or '—'} | 1h {v3_1h} | p={v3_pos:.2f}"
        )
        v2_reason_line = f"V3 neden: {v3_reason or '—'}"
        taker_str = html.Div(
            [
                html.Div(
                    f"Taker {taker:.0%} · {struct_label} · {m.get('cvd_sub', '')}",
                    style={"color": C["white"], "fontSize": "11px"},
                ),
                html.Div(main_line, style={"color": C["blue"]}),
                html.Div(channel_line, style={"color": C["white"]}),
                html.Div(ref_line, style={"color": C["muted"]}),
                html.Div(active_line, style={"color": C["muted"]}),
                html.Div(v2_band_line, style={"color": C["yellow"], "marginTop": "6px"}),
                html.Div(v2_scenario_line, style={"color": C["muted"], "fontSize": "11px"}),
                html.Div(v2_reason_line, style={"color": C["muted"], "fontSize": "11px"}),
            ]
        )
    else:
        taker_str = html.Div(
            [
                html.Div(
                    f"Taker {taker:.0%} · {struct_label} · {m.get('cvd_sub', '')}",
                    style={"color": C["muted"], "fontSize": "11px"},
                ),
                html.Div(
                    str(structure.get("structural_quality") or "majör seviye yok"),
                    style={"color": C["muted"], "fontSize": "11px"},
                ),
            ]
        )

    # Setup
    setup = op.get("setup") or {}
    setup_code = str(setup.get("code") or "BAND_DISI_BEKLE")
    setup_detail = str(setup.get("detail") or "")
    setup_color = (
        C["green"] if "LONG" in setup_code else
        C["red"] if "SHORT" in setup_code else
        C["blue"] if "POZISYON_V3" in setup_code else
        C["orange"] if "BEKLENIYOR" in setup_code or "TRIGGER" in setup_code else
        C["muted"]
    )
    pos_str = setup_code
    pos2_str = setup_detail or str(op.get("headline_detail") or state.no_entry_reason or "setup yok")
    pos_style = {"fontSize":"20px","fontWeight":"bold","color": setup_color}

    # Execution
    execution = op.get("execution") or {}
    next_info = op.get("next") or {}
    block_code = str(execution.get("blocking_code") or "")
    in_pos = bool(execution.get("in_position"))
    if in_pos:
        exec_label = "IN POSITION"
        exec_color = C["blue"]
    elif block_code:
        exec_label = block_code
        exec_color = C["yellow"] if "BEKLENIYOR" in block_code or "HEADROOM" in block_code else C["orange"]
    else:
        exec_label = str(op.get("headline_code") or "BEKLE")
        exec_color = C["muted"]
    fund_style = {"fontSize":"18px","fontWeight":"bold","color": exec_color}
    next_bits = [x for x in (str(next_info.get("long_text") or ""), str(next_info.get("short_text") or "")) if x]
    oi_str = " | ".join(next_bits[:2]) if next_bits else (str(execution.get("blocking_detail") or "") or "bir sonraki tetik yok")

    return (
        hdr,
        f"${price:,.2f}" if price > 0 else "Bekleniyor...",
        (
            (
            f"Bid: {bid:.2f}  Ask: {ask:.2f}  ({m['price_src']})"
            if bid and ask
            else f"Kaynak: {m['price_src']}"
        ),
        ),
        regime_display,
        regime_style,
        regime_sub,
        cvd_display,
        cvd_style,
        taker_str,
        pos_str,
        pos_style,
        pos2_str,
        exec_label,
        fund_style,
        oi_str,
    )


# ── Ana grafik ────────────────────────────────────────────────

def _bar_dt(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=CHART_TZ)


def _x_range(bars: list[dict], step_sec: int = 900) -> list[datetime] | None:
    if not bars:
        return None
    return [
        _bar_dt(float(bars[0]["ts"])),
        _bar_dt(float(bars[-1]["ts"]) + step_sec),
    ]


def _add_candlestick_trace(fig, bars, row, col, name):
    if not bars:
        return
    bx = [_bar_dt(b["ts"]) for b in bars]
    fig.add_trace(go.Candlestick(
        x=bx,
        open=[b["open"] for b in bars],
        high=[b["high"] for b in bars],
        low=[b["low"] for b in bars],
        close=[b["close"] for b in bars],
        name=name,
        increasing_line_color=C["green"],
        decreasing_line_color=C["red"],
        increasing_fillcolor=C["green"],
        decreasing_fillcolor=C["red"],
    ), row=row, col=col)


def _y_range_with_levels(bars, *levels):
    """Mum + seviye çizgileri için Y ekseni (seviyeler mum dışında kalmasın)."""
    ys = []
    if bars:
        ys.extend(b["low"] for b in bars)
        ys.extend(b["high"] for b in bars)
    for lv in levels:
        if lv and float(lv) > 0:
            ys.append(float(lv))
    if not ys:
        return None
    lo, hi = min(ys), max(ys)
    pad = max((hi - lo) * 0.06, 3.0)
    return [lo - pad, hi + pad]


def _fallback_sr_from_bars(bars, price: float) -> dict:
    if not bars or price <= 0:
        return {"main_r": 0.0, "main_s": 0.0, "outer_r": 0.0, "outer_s": 0.0, "used": False}

    lookback = 2
    raw_resistances = []
    raw_supports = []
    for i in range(lookback, len(bars) - lookback):
        cur_high = float(bars[i].get("high", 0) or 0)
        cur_low = float(bars[i].get("low", 0) or 0)
        left = bars[i - lookback : i]
        right = bars[i + 1 : i + lookback + 1]
        if all(cur_high >= float(b.get("high", 0) or 0) for b in left + right):
            raw_resistances.append(round(cur_high, 2))
        if all(cur_low <= float(b.get("low", 0) or 0) for b in left + right):
            raw_supports.append(round(cur_low, 2))

    recent = bars[-48:] if len(bars) > 48 else bars
    if not raw_resistances:
        raw_resistances.extend(
            round(float(b.get("high", 0) or 0), 2)
            for b in recent
            if float(b.get("high", 0) or 0) > price
        )
    if not raw_supports:
        raw_supports.extend(
            round(float(b.get("low", 0) or 0), 2)
            for b in recent
            if 0 < float(b.get("low", 0) or 0) < price
        )

    resistances = sorted({r for r in raw_resistances if r > price})
    supports = sorted({s for s in raw_supports if 0 < s < price}, reverse=True)

    main_r = resistances[0] if resistances else 0.0
    outer_r = resistances[1] if len(resistances) > 1 else 0.0
    main_s = supports[0] if supports else 0.0
    outer_s = supports[1] if len(supports) > 1 else 0.0
    return {
        "main_r": main_r,
        "main_s": main_s,
        "outer_r": outer_r,
        "outer_s": outer_s,
        "used": bool(main_r or main_s),
    }


def _add_breakout_levels(fig, row, col, bars, op=None):
    """
    15m grafik (V3):
    - mavi = aktif destek / direnc (karar bandi)
    - pozisyon: giris, SL, TP1; giris destegi (yapisal cikis)
    """
    if not bars:
        return
    from engine.operation_state import get_operation_view

    px = effective_price()
    op = op or get_operation_view(px)
    v3 = dict(op.get("v3") or {})
    v3_levels = dict(v3.get("levels") or {})
    v3_structure = dict(v3.get("structure") or {})
    v3_scenario = dict(v3.get("scenario") or {})
    v3_decision = dict(v3.get("decision") or {})
    main_r = float(v3_levels.get("active_resistance") or 0)
    main_s = float(v3_levels.get("active_support") or 0)
    fallback = _fallback_sr_from_bars(bars, px)
    fallback_used = False
    if main_r <= 0:
        main_r = float(fallback.get("main_r") or 0)
        fallback_used = fallback_used or main_r > 0
    if main_s <= 0:
        main_s = float(fallback.get("main_s") or 0)
        fallback_used = fallback_used or main_s > 0
    scenario_name = str(v3_scenario.get("name") or "WAIT")
    action = str(v3_decision.get("action") or "")
    reason = str(v3_decision.get("reason") or v3_scenario.get("detail") or "")
    zone = str(v3_levels.get("zone") or "MID_RANGE")
    range_pos = float(v3_levels.get("range_position", 0.5) or 0.5)
    s1h = str(((v3_structure.get("1h") or {}).get("direction")) or "?")
    pos_v3 = bool(state.in_position) and str((state.position_breakout or {}).get("entry_mode") or "") == "v3"
    status = f"POZISYON_V3 {state.pos_side}" if pos_v3 else (scenario_name or "WAIT")

    def _hline(y, color, dash, label, width=1.5):
        if y <= 0:
            return
        fig.add_hline(
            y=y,
            line_dash=dash,
            line_color=color,
            line_width=width,
            annotation_text=label,
            annotation_position="right",
            annotation_font_size=9,
            annotation_font_color=color,
            row=row,
            col=col,
        )

    if main_r > 0:
        _hline(
            main_r,
            C["blue"],
            "dash" if fallback_used else "solid",
            f"{'Grafik' if fallback_used else 'Ana'} direnç {main_r:.2f}",
            width=2.6,
        )
    if main_s > 0:
        _hline(
            main_s,
            C["blue"],
            "dash" if fallback_used else "solid",
            f"{'Grafik' if fallback_used else 'Ana'} destek {main_s:.2f}",
            width=2.6,
        )
    line_levels = [main_r, main_s, px]
    show_tp2_chart = bool(getattr(cfg, "SEND_TP2_ORDER", False))

    if pos_v3:
        pb = dict(state.position_breakout or {})
        entry_support = float(pb.get("entry_support") or 0)
        if entry_support > 0 and abs(entry_support - main_s) > 0.5:
            _hline(
                entry_support,
                "#5eb3ff",
                "dot",
                f"Giris destegi {entry_support:.2f}",
                width=1.3,
            )
            line_levels.append(entry_support)
        if state.pos_entry > 0:
            _hline(state.pos_entry, C["yellow"], "dash", f"Giris {state.pos_entry:.2f}", width=1.6)
            line_levels.append(state.pos_entry)
        if state.pos_sl > 0:
            _hline(state.pos_sl, C["orange"], "dash", f"SL {state.pos_sl:.2f}", width=1.6)
            line_levels.append(state.pos_sl)
        if state.pos_tp1 > 0 and not state.pos_tp1_hit:
            _hline(state.pos_tp1, C["green"], "dashdot", f"TP1 {state.pos_tp1:.2f}", width=1.4)
            line_levels.append(state.pos_tp1)
        elif state.pos_tp1 > 0 and state.pos_tp1_hit:
            _hline(
                state.pos_tp1,
                C["muted"],
                "dot",
                f"TP1 (alindi) {state.pos_tp1:.2f}",
                width=1.0,
            )
            line_levels.append(state.pos_tp1)
        if show_tp2_chart and state.pos_tp2 > 0:
            _hline(state.pos_tp2, C["green"], "solid", f"TP2 {state.pos_tp2:.2f}", width=1.8)
            line_levels.append(state.pos_tp2)
    else:
        dd = dict(v3_decision.get("details") or {})
        plan_entry = float(dd.get("price") or 0)
        plan_sl = float(dd.get("sl") or 0)
        plan_tp1 = float(dd.get("tp1") or 0)
        plan_tp2 = float(dd.get("tp2") or 0)
        if plan_entry > 0 and action in ("LONG", "SHORT"):
            _hline(plan_entry, C["yellow"], "dash", f"Plan giris {plan_entry:.2f}", width=1.4)
            line_levels.append(plan_entry)
        if plan_sl > 0 and action in ("LONG", "SHORT"):
            _hline(plan_sl, C["orange"], "dash", f"Plan SL {plan_sl:.2f}", width=1.4)
            line_levels.append(plan_sl)
        if plan_tp1 > 0 and action in ("LONG", "SHORT"):
            _hline(plan_tp1, C["green"], "dashdot", f"Plan TP1 {plan_tp1:.2f}", width=1.3)
            line_levels.append(plan_tp1)
        if show_tp2_chart and plan_tp2 > 0 and action in ("LONG", "SHORT"):
            _hline(plan_tp2, C["green"], "solid", f"Plan TP2 {plan_tp2:.2f}", width=1.5)
            line_levels.append(plan_tp2)

    yr = _y_range_with_levels(
        bars,
        *line_levels,
    )
    if yr:
        fig.update_yaxes(range=yr, tickformat=",.2f", row=row, col=col)
    else:
        lo, hi = min(b["low"] for b in bars), max(b["high"] for b in bars)
        pad = max((hi - lo) * 0.04, 2.0)
        fig.update_yaxes(range=[lo - pad, hi + pad], tickformat=",.2f", row=row, col=col)

    rr = float((v3_decision.get("details") or {}).get("rr", 0) or 0)
    band_txt = f"bant {main_s:.2f} – {main_r:.2f}" if main_r > 0 and main_s > 0 else ""
    note = f"<b>{status}</b>"
    if band_txt:
        note += f"<br>{band_txt}"
    if fallback_used:
        note += "<br>band: 15m swing fallback"
    if pos_v3 and state.pos_tp1_hit:
        sl_stage = str((state.position_breakout or {}).get("sl_stage", ""))
        if sl_stage == "tp1_wait_15m":
            note += "<br>TP1 alindi — 15m TP1 onayi bekleniyor"
        elif sl_stage == "tp1_wait_5m":
            note += "<br>TP1 15m onayli — 5m kapanis bekleniyor"
        else:
            note += "<br>TP1 alindi — runner SL trail"
    note += f"<br>1h={s1h} (bilgi) · zone={zone} · p={range_pos:.2f}"
    if action:
        note += f"<br>karar={action}" + (f" · RR={rr:.2f}" if rr > 0 else "")
    if reason:
        note += f"<br>{reason}"
    fig.add_annotation(
        text=note,
        showarrow=False,
        xref="x domain",
        yref="y domain",
        x=0.01,
        y=0.99,
        xanchor="left",
        yanchor="top",
        align="left",
        font=dict(size=9, color=C["text"]),
        bgcolor="rgba(22,27,34,0.88)",
        bordercolor=C["border"],
        borderwidth=1,
        row=row,
        col=col,
    )


@app.callback(
    Output("main-chart", "figure"),
    Input("fast", "n_intervals"),
)
def update_chart(_):
    try:
        pkg = get_mtf_package(force=True)
    except Exception as e:
        import logging
        logging.getLogger("Dashboard").warning(f"Grafik verisi: {e}")
        pkg = {"bars_15m": [], "bars_1h": [], "bars_1m": [], "series": {}}
    bars_15m = pkg.get("bars_15m") or []
    bars_1h = pkg.get("bars_1h") or []
    bars_1m = pkg.get("bars_1m") or []
    ser = pkg.get("series") or {}
    from engine.operation_state import get_operation_view

    px = effective_price()
    op = get_operation_view(px)
    v3 = op.get("v3") or {}
    v3_structure = v3.get("structure") or {}
    s1h = str(((v3_structure.get("1h") or {}).get("direction")) or "?")
    v3_struct_line = f"v3 yapi: 1h={s1h} (bilgi) · 15m/5m kapali"
    src = str(pkg.get("source") or "?")
    last_lbl = ""
    if bars_15m:
        last_lbl = _bar_dt(bars_15m[-1]["ts"]).strftime("%d.%m %H:%M")

    fig = make_subplots(
        rows=2, cols=2,
        shared_xaxes=False,
        column_widths=[0.5, 0.5],
        row_heights=[0.72, 0.28],
        vertical_spacing=0.08,
        horizontal_spacing=0.04,
        subplot_titles=(
            f"15m ({len(bars_15m)} mum, son={last_lbl} TR, kaynak={src}) — {v3_struct_line}",
            f"1h Binance ({len(bars_1h)} mum) — 1h yapi {s1h}",
            f"1m ({len(bars_1m)} mum)",
            "CVD paneli (aşağı)",
        ),
        specs=[
            [{"type": "candlestick"}, {"type": "candlestick"}],
            [{"type": "candlestick"}, {"type": "scatter"}],
        ],
    )

    _add_candlestick_trace(fig, bars_15m, 1, 1, "15m")
    _add_breakout_levels(fig, 1, 1, bars_15m, op=op)
    _add_candlestick_trace(fig, bars_1h, 1, 2, "1h")
    lo1h, hi1h = (
        (min(b["low"] for b in bars_1h), max(b["high"] for b in bars_1h))
        if bars_1h
        else (0, 0)
    )
    if bars_1h:
        pad1h = max((hi1h - lo1h) * 0.04, 2.0)
        fig.update_yaxes(
            range=[lo1h - pad1h, hi1h + pad1h],
            tickformat=",.2f",
            row=1,
            col=2,
        )
    _add_candlestick_trace(fig, bars_1m, 2, 1, "1m")
    if bars_1m:
        lo1m, hi1m = min(b["low"] for b in bars_1m), max(b["high"] for b in bars_1m)
        pad1m = max((hi1m - lo1m) * 0.04, 2.0)
        fig.update_yaxes(range=[lo1m - pad1m, hi1m + pad1m], tickformat=",.2f", row=2, col=1)

    if ser.get("cvd"):
        sx = [_bar_dt(t) for t in ser["ts"]]
        fig.add_trace(go.Scatter(
            x=sx, y=ser["cvd"], mode="lines", name="CVD 15m (kline kümülatif)",
            line=dict(color=C["purple"], width=1.2),
        ), row=2, col=2)
    hist = list(state.metrics_history)
    if hist:
        hx = [datetime.fromtimestamp(h["ts"], tz=timezone.utc) for h in hist]
        fig.add_trace(go.Scatter(
            x=hx, y=[h["cvd"] for h in hist], mode="lines", name="CVD 5m (aggTrade)",
            line=dict(color=C["yellow"], width=1.5),
        ), row=2, col=2)
    if ser.get("cvd") or hist:
        fig.add_hline(y=0, line_color=C["border"], row=2, col=2)

    if px > 0 and bars_15m:
        fig.add_hline(
            y=px,
            line_dash="dot",
            line_color=C["blue"],
            line_width=1,
            annotation_text=f"Fiyat {px:.2f}",
            annotation_position="left",
            annotation_font_size=9,
            annotation_font_color=C["blue"],
            row=1,
            col=1,
        )

    fig.update_layout(
        paper_bgcolor=C["bg"], plot_bgcolor=C["bg"],
        font=dict(color=C["text"], size=10),
        margin=dict(l=52, r=20, t=56, b=16),
        hovermode="x unified",
        showlegend=False,
    )

    xr15 = _x_range(bars_15m, 900)
    xr1h = _x_range(bars_1h, 3600)
    xr1m = _x_range(bars_1m, 60)

    for r in range(1, 3):
        for c in range(1, 3):
            fig.update_yaxes(
                matches=None,
                gridcolor=C["border"],
                tickfont=dict(color=C["muted"], size=9),
                row=r, col=c,
            )
            fig.update_xaxes(
                gridcolor=C["border"],
                tickfont=dict(color=C["muted"], size=9),
                row=r, col=c,
            )

    if xr15:
        fig.update_xaxes(range=xr15, autorange=False, row=1, col=1)
    if xr1h:
        fig.update_xaxes(range=xr1h, autorange=False, row=1, col=2)
    if xr1m:
        fig.update_xaxes(range=xr1m, autorange=False, row=2, col=1)
    if ser.get("ts") and xr15:
        fig.update_xaxes(range=xr15, autorange=False, row=2, col=2)

    return fig


# ── Tablolar (slow) ───────────────────────────────────────────

_CLOSE_REASON_TR = {
    "stop_loss": "Stop loss",
    "take_profit": "Take profit",
    "market_close": "Piyasa kapanış",
    "exchange_closed": "Borsa kapattı",
    "exchange_closed_poll": "Borsa kapattı (senkron)",
    "trend_reverse": "Trend ters",
    "cvd_reverse": "CVD ters",
    "stale_data": "Veri bayat",
    "tp1_retest_weak_flow": "TP1 retest + zayıf akış",
    "tp1_retest_exit": "TP1 retest çıkış",
    "flat_on_startup": "Restart — pozisyon yoktu",
    "duplicate_open": "Çift kayıt (temizlendi)",
    "orphan_no_position": "Senkron — pozisyon yok",
    "no_position_on_startup": "Restart — kayıt yok",
    "restored_from_exchange": "Borsadan geri yüklendi",
}


def _format_close_reason(raw: str, status: str) -> str:
    if status == "OPEN":
        return "Açık pozisyon"
    if not raw:
        return "—"
    base = str(raw).split("|")[0].strip()
    if base in _CLOSE_REASON_TR:
        return _CLOSE_REASON_TR[base]
    if base.startswith("reverse_to_"):
        return f"Ters yön → {base.replace('reverse_to_', '')}"
    if base.startswith("exchange_"):
        return base.replace("exchange_", "Borsa: ").replace("_", " ")
    return base.replace("_", " ")


def _trade_display_pnl(t, st: str, *, system: bool = False) -> tuple[float | None, str]:
    """PnL USDT + yüzde metni."""
    if system and st != "OPEN":
        return None, "—"
    pnl = float(t["pnl"] or 0)
    pct = float(t["pnl_pct"] or 0)
    ep = float(t["entry_price"] or 0)
    xp = float(t["exit_price"] or 0)
    qty = float(t["qty"] or 0)
    if st == "OPEN" and state.in_position and t["direction"] == state.pos_side:
        pnl = float(state.unrealized_pnl or 0)
        if state.pos_entry > 0 and state.pos_qty > 0:
            pct = pnl / (state.pos_entry * state.pos_qty) * 100
        return pnl, f"{pnl:+.2f} USDT ({pct:+.1f}%)"
    if st != "OPEN" and abs(pnl) < 1e-8 and ep > 0 and xp > 0 and qty > 0:
        sign = 1.0 if t["direction"] == "LONG" else -1.0
        pnl = (xp - ep) * qty * sign
        pct = pnl / (ep * qty) * 100 if ep * qty > 0 else 0.0
    if abs(pct) < 1e-6 and ep > 0 and qty > 0:
        pct = pnl / (ep * qty) * 100
    return pnl, f"{pnl:+.2f} USDT ({pct:+.1f}%)"


@app.callback(
    Output("trades-tbl",   "children"),
    Output("noentry-tbl",  "children"),
    Input("slow", "n_intervals"),
)
def update_tables(_):
    db = _db()
    trades, no_entry = [], []
    if db:
        try:
            trades = db.execute("""
                SELECT direction, entry_price, exit_price, pnl, pnl_pct, qty,
                       close_reason, status, sl, tp1, tp2, notes,
                       datetime(open_ts,'unixepoch','localtime') as open_dt,
                       datetime(close_ts,'unixepoch','localtime') as close_dt,
                       duration_min, tp1_hit, be_activated
                FROM trades
                ORDER BY
                    CASE WHEN status='OPEN' THEN 0 ELSE 1 END,
                    COALESCE(close_ts, open_ts) DESC
                LIMIT 20
            """).fetchall()
            no_entry = db.execute("""
                SELECT no_entry_reason, COUNT(*) as cnt,
                       MAX(datetime(ts,'unixepoch')) as last
                FROM signals
                WHERE entered=0 AND no_entry_reason != ''
                GROUP BY no_entry_reason
                ORDER BY cnt DESC LIMIT 8
            """).fetchall()
            db.close()
        except Exception:
            pass

    th_style = {
        "fontSize": "12px",
        "color": C["muted"],
        "padding": "6px 8px",
        "borderBottom": f"1px solid {C['border']}",
        "fontWeight": "600",
    }
    td_style = {"fontSize": "13px", "padding": "6px 8px", "color": C["text"]}

    mode_lbl = "PAPER" if is_paper_mode() else ("CANLI" if cfg.API_KEY else "İZLEME")

    if trades:
        rows = []
        for t in trades:
            st = t["status"] or "—"
            sys_row = _is_system_trade(t)
            pnl_val, pnl_txt = _trade_display_pnl(t, st, system=sys_row)
            pc = C["muted"] if pnl_val is None else (C["green"] if pnl_val >= 0 else C["red"])
            dc = C["green"] if t["direction"] == "LONG" else C["red"]
            ep = float(t["entry_price"] or 0)
            if ep <= 0 and st == "OPEN" and state.in_position:
                if t["direction"] == state.pos_side and state.pos_entry > 0:
                    ep = state.pos_entry
            xp = float(t["exit_price"] or 0)
            reason = _format_close_reason(t["close_reason"] or "", st)
            if sys_row and st != "OPEN":
                reason = f"{reason} · sistem"
            if st == "OPEN":
                q = float(t["qty"] or state.pos_qty or 0)
                reason = f"Açık · {q:.4f} ETH" if q > 0 else "Açık"

            open_dt = (t["open_dt"] or "")[:16]
            close_dt = (t["close_dt"] or "")[:16] if st != "OPEN" else "—"
            dur = float(t["duration_min"] or 0)
            dur_txt = f"{dur:.0f} dk" if dur > 0 and st != "OPEN" else ("—" if st == "OPEN" else "—")

            sl_v = float(t["sl"] or 0)
            tp1_v = float(t["tp1"] or 0)
            tp2_v = float(t["tp2"] or 0)
            if st == "OPEN" and t["direction"] == state.pos_side and state.in_position:
                sl_v = sl_v or float(state.pos_sl or 0)
                tp1_v = tp1_v or float(state.pos_tp1 or 0)
                tp2_v = tp2_v or float(state.pos_tp2 or 0)
                ex = state.exchange_position or {}
                if ex.get("live_from_api"):
                    sl_v = float(ex.get("sl") or sl_v or 0)
                    tp1_v = float(ex.get("tp1") or tp1_v or 0)
                    tp2_v = float(ex.get("tp2") or tp2_v or 0)

            def _tp1_hit_row() -> bool:
                if bool(t["tp1_hit"]):
                    return True
                if st != "OPEN" or t["direction"] != state.pos_side:
                    return False
                if state.pos_tp1_hit:
                    return True
                ex = state.exchange_position or {}
                return bool(ex.get("tp1_hit"))

            def _lvl(px: float, color: str, hit: bool = False) -> html.Td:
                txt = f"{px:.2f}" if px > 0 else "—"
                cell_style = {**td_style, "color": color if px > 0 else C["muted"]}
                if hit and px > 0:
                    cell_style["textDecoration"] = "line-through"
                    cell_style["opacity"] = "0.65"
                return html.Td(txt, style=cell_style)

            sl_txt = f"{sl_v:.2f}" if sl_v > 0 else "—"
            if t["be_activated"] and sl_v > 0:
                sl_txt += " BE"

            row_td = {**td_style}
            if sys_row:
                row_td = {**td_style, "opacity": "0.55"}

            rows.append(html.Tr([
                html.Td(open_dt, style={**row_td, "color": C["muted"], "fontSize": "10px"}),
                html.Td(close_dt, style={**row_td, "color": C["muted"], "fontSize": "10px"}),
                html.Td(t["direction"], style={**row_td, "color": dc, "fontWeight": "bold"}),
                html.Td(f"{ep:.2f}" if ep > 0 else "—", style=row_td),
                html.Td(
                    f"{xp:.2f}" if xp > 0 else "—",
                    style=row_td,
                ),
                html.Td(
                    pnl_txt if st != "OPEN" or state.in_position else "—",
                    style={**row_td, "color": pc, "fontWeight": "bold" if pnl_val is not None else "normal"},
                ),
                html.Td(dur_txt, style={**row_td, "color": C["muted"], "fontSize": "10px"}),
                html.Td(reason, style={**row_td, "fontSize": "10px", "color": C["yellow"] if st == "OPEN" else C["muted"]}),
                html.Td(sl_txt, style={**row_td, "color": C["red"] if sl_v > 0 else C["muted"]}),
                _lvl(tp1_v, C["green"], _tp1_hit_row()),
                _lvl(tp2_v, C["green"]),
            ]))
        trade_widget = html.Div([
            html.Table([
                html.Thead(html.Tr([html.Th(h, style=th_style)
                                    for h in [
                                        "Açılış", "Kapanış", "Yön", "Giriş", "Çıkış",
                                        "PnL", "Süre", "Kapanış sebebi", "SL", "TP1", "TP2",
                                    ]])),
                html.Tbody(rows),
            ], style={"width": "100%", "borderCollapse": "collapse"}),
        ])
    else:
        trade_widget = html.Div(
            f"Henüz trade yok ({mode_lbl})",
            style={"fontSize":"12px","color":C["muted"]},
        )

    if no_entry:
        ne_rows = [html.Tr([
            html.Td(r["no_entry_reason"],
                    style={**td_style,"color":C["yellow"],"fontSize":"11px"}),
            html.Td(str(r["cnt"]),
                    style={**td_style,"fontWeight":"bold","textAlign":"right"}),
            html.Td((r["last"] or "")[:16],
                    style={**td_style,"color":C["muted"],"fontSize":"10px"}),
        ]) for r in no_entry]
        ne_widget = html.Table([
            html.Thead(html.Tr([html.Th(h, style=th_style)
                                for h in ["Sebep","Adet","Son"]])),
            html.Tbody(ne_rows),
        ], style={"width":"100%","borderCollapse":"collapse"})
    else:
        ne_widget = html.Div("Veri yok", style={"fontSize":"12px","color":C["muted"]})

    return trade_widget, ne_widget


# ── Piyasa Açıkla ─────────────────────────────────────────────

@app.callback(
    Output("claude-api-status", "children"),
    Input("btn-claude-save", "n_clicks"),
    State("claude-api-key-input", "value"),
    prevent_initial_call=False,
)
def claude_save_key(n_clicks, key_value):
    from engine.claude_credentials import set_key, status_text
    if callback_context.triggered_id == "btn-claude-save" and n_clicks and key_value:
        set_key(key_value)
        return "Anahtar kaydedildi (data/claude_key.txt — git'e eklenmez)."
    return status_text()


def _click_x_to_ts(x) -> float:
    if x is None:
        raise ValueError("tıklama yok")
    if isinstance(x, (int, float)):
        sec = x / 1000.0 if x > 1e12 else float(x)
        return sec
    s = str(x).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        dt = datetime.strptime(s[:16], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _ts_to_input(ts: float, tz_mode: str) -> str:
    from engine.time_align import utc_to_binance_local, snap_15m_open
    ts = snap_15m_open(ts)
    if tz_mode == "utc":
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    return utc_to_binance_local(ts).strftime("%Y-%m-%d %H:%M")


@app.callback(
    Output("explain-time-sync", "children"),
    Input("explain-datetime", "value"),
    Input("explain-tz", "value"),
    prevent_initial_call=False,
)
def explain_time_sync_preview(when, tz_mode):
    try:
        from engine.explain_live import resolve_explain_when
        from engine.time_align import format_time_sync_line
        from engine.explain_context import _nearest_snapshot_human

        display, ts, meta = resolve_explain_when(when, tz_mode or "tr")
        db_near = _nearest_snapshot_human(ts)
        prefix = "Otomatik (grafik)" if meta.get("auto") else "Seçili"
        meta["sync_line"] = format_time_sync_line(meta, db_near)
        return f"{prefix}: {display} — {meta['sync_line']}"
    except ValueError as e:
        return f"Saat hatası: {e}"


@app.callback(
    Output("explain-datetime", "value", allow_duplicate=True),
    Input("fast", "n_intervals"),
    Input("explain-tz", "value"),
    State("explain-datetime", "value"),
    prevent_initial_call="initial_duplicate",
)
def auto_fill_explain_time(_n, tz_mode, current):
    """Grafik yenilenince saat alanını doldur; kullanıcı tıklamışsa dokunma."""
    if current and str(current).strip():
        raise dash.exceptions.PreventUpdate
    from engine.explain_live import resolve_explain_when
    display, _, _ = resolve_explain_when(None, tz_mode or "tr")
    return display


@app.callback(
    Output("explain-datetime", "value"),
    Input("main-chart", "clickData"),
    State("explain-tz", "value"),
    prevent_initial_call=True,
)
def chart_pick_time(click, tz_mode):
    if not click or not click.get("points"):
        raise dash.exceptions.PreventUpdate
    try:
        ts = _click_x_to_ts(click["points"][0].get("x"))
        return _ts_to_input(ts, tz_mode or "tr")
    except Exception:
        raise dash.exceptions.PreventUpdate


@app.callback(
    Output("explain-image-path", "data"),
    Output("explain-upload-status", "children"),
    Input("explain-upload", "contents"),
    State("explain-upload", "filename"),
    prevent_initial_call=True,
)
def explain_save_upload(contents, filename):
    if not contents:
        raise dash.exceptions.PreventUpdate
    from engine.explain_llm import save_upload
    path = save_upload(contents, filename or "chart.png")
    return path, (
        f"Yüklendi: {filename or 'chart.png'} — "
        "şimdi «Binance görselinden açıkla» butonuna basın"
    )


@app.callback(
    Output("explain-datetime", "value", allow_duplicate=True),
    Output("explain-upload-status", "children", allow_duplicate=True),
    Input("btn-vision-time", "n_clicks"),
    State("explain-image-path", "data"),
    State("claude-api-key-input", "value"),
    State("explain-tz", "value"),
    prevent_initial_call=True,
)
def explain_vision_time(_n, img_path, api_key, tz_mode):
    if not img_path:
        return dash.no_update, "Önce ekran görüntüsü yükleyin."
    from engine.explain_llm import vision_guess_time
    guessed = vision_guess_time(img_path, api_key=api_key)
    if guessed.startswith("Claude API") or guessed.startswith("Vision hata"):
        return dash.no_update, guessed
    from engine.time_align import parse_when
    clean_utc = guessed.replace(" UTC", "").strip()
    try:
        _, meta = parse_when(clean_utc, "utc")
        display = (
            meta["tr_human"].replace(" (Binance TR)", "")
            if (tz_mode or "tr") == "tr"
            else meta["utc_human"].replace(" UTC", "")
        )
        return display, f"Görselden okundu → {meta['sync_line']}"
    except ValueError:
        return clean_utc, f"Grafikten okunan: {guessed}"


@app.callback(
    Output("explain-output", "children"),
    Output("explain-datetime", "value", allow_duplicate=True),
    Input("btn-explain-chart", "n_clicks"),
    Input("btn-explain-screenshot", "n_clicks"),
    Input("btn-explain-rule", "n_clicks"),
    Input("btn-explain-llm", "n_clicks"),
    State("explain-datetime", "value"),
    State("explain-image-path", "data"),
    State("claude-api-key-input", "value"),
    State("explain-tz", "value"),
    prevent_initial_call=True,
)
def explain_run(_nc, _ns, _nr, _nl, when, img_path, api_key, tz_mode):
    tz = tz_mode or "tr"
    trig = callback_context.triggered_id
    try:
        from engine.explain_context import build_context, format_rule_report
        from engine.explain_live import build_dashboard_context, explain_from_dashboard_chart
        from engine.explain_llm import (
            explain_from_binance_screenshot,
            explain_narrative,
            vision_chart_bias,
        )

        def _ctx_with_visual(when_val: str, img: str | None):
            c = build_dashboard_context(when_val, tz_mode=tz)
            if not c.get("ok"):
                c = build_context(when_val, tz_mode=tz)
            if img:
                v = vision_chart_bias(img, api_key=api_key)
                if v.get("ok"):
                    c["visual"] = v
            return c

        if trig == "btn-explain-chart":
            from engine.explain_live import resolve_explain_when
            use_when, _, _ = resolve_explain_when(when, tz)
            return explain_from_dashboard_chart(use_when, tz_mode=tz), use_when

        if trig == "btn-explain-screenshot":
            if not img_path:
                return "Önce Binance ekran görüntüsünü yükleyin.", dash.no_update
            report, used_when = explain_from_binance_screenshot(
                img_path, api_key=api_key, tz_mode=tz
            )
            if used_when:
                return report, used_when
            return report, dash.no_update

        from engine.explain_live import resolve_explain_when
        use_when, _, _ = resolve_explain_when(when, tz)
        ctx = _ctx_with_visual(use_when, img_path)
        if trig == "btn-explain-llm":
            return (
                explain_narrative(
                    use_when, img_path or None, api_key=api_key, ctx=ctx, tz_mode=tz
                ),
                use_when,
            )
        return format_rule_report(ctx), use_when
    except ValueError as e:
        return str(e), dash.no_update
    except Exception as e:
        return f"Hata: {e}", dash.no_update


def run(host="0.0.0.0", port=8050, debug=False):
    import logging

    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    # Dash 4.1 bazen component suite path'lerini ilk istekten once kaydetmiyor.
    # Plotly bundle'inin "registered library" hatasi vermemesi icin server'i hazirla.
    app._setup_server()
    app.run(host=host, port=port, debug=debug, use_reloader=False)


if __name__ == "__main__":
    print("UYARI: Dashboard tek başına çalışırsa veri akmaz. Önce: python main.py")
    run(debug=False)
