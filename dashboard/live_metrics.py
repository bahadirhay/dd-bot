"""
dashboard/live_metrics.py — Üst panel: canlı bot vs Binance API ayrımı.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

from core.config import cfg
from core.state import state, data_is_fresh, trade_is_fresh, effective_price


def _breakout_panel_view(price: float, trade_ok: bool, bars: list) -> dict:
    """Çerçeve paneli: seviye durumu + canlı flow (stale breakout_view CVD sıfır göstermesin)."""
    from engine.breakout import get_status_snapshot

    bv = dict(state.breakout_view) if state.breakout_view else get_status_snapshot(price)
    if not bv.get("status"):
        bv = get_status_snapshot(price)

    bv["cvd_5m"] = state.cvd_5m
    bv["taker"] = state.taker_ratio
    if trade_ok:
        bv["cvd_feed"] = "aggTrade"
        bv["cvd_panel_note"] = ""
    else:
        bv["cvd_feed"] = "aggTrade_kopuk"
        last = bars[-1] if bars else None
        bv["bar_delta_15m"] = float(last.get("delta", 0) or 0) if last else 0.0
        age = (
            int(time.time() - state.trade_last_update)
            if state.trade_last_update
            else -1
        )
        bv["cvd_panel_note"] = (
            f"aggTrade yok ({age}s) — üstteki büyük sayı 15m mum Δ olabilir"
        )
    return bv
from dashboard.binance_chart import get_chart_package
from engine.structure_display import alignment_detail

_ticker_cache = {"ts": 0.0, "price": 0.0}


def fetch_mark_price() -> float:
    px = effective_price()
    if px > 0:
        return px
    now = time.time()
    if now - _ticker_cache["ts"] < 5 and _ticker_cache["price"] > 0:
        return _ticker_cache["price"]
    try:
        q = urllib.parse.urlencode({"symbol": cfg.SYMBOL})
        url = f"{cfg.REST}/fapi/v1/premiumIndex?{q}"
        with urllib.request.urlopen(url, timeout=6) as r:
            data = json.loads(r.read().decode())
        p = float(data.get("markPrice", 0) or 0)
        if p > 0:
            _ticker_cache["ts"] = now
            _ticker_cache["price"] = p
        return p
    except Exception:
        return float(state.mark_price or state.price or 0)


def get_panel_metrics() -> dict:
    """
    Üst bant metrikleri.
    CVD 5m yalnızca aggTrade canlıysa; aksi halde 15m mum delta (farklı metrik).
    """
    fresh = data_is_fresh(max_age_sec=20)
    trade_ok = trade_is_fresh(max_age_sec=30)
    pkg = get_chart_package(96)
    bars = pkg.get("bars") or []
    last = bars[-1] if bars else None

    price = effective_price()
    price_src = "canlı bot"
    if price <= 0:
        price = fetch_mark_price()
        price_src = "Binance API"
    if price <= 0 and last:
        price = last["close"]
        price_src = "son 15m mum"

    tv = state.trend_view or {}
    regime = tv.get("bias") or state.regime
    score = tv.get("strength", state.regime_score * 25)

    trade_age = (
        int(time.time() - state.trade_last_update)
        if state.trade_last_update
        else -1
    )

    if trade_ok:
        cvd = state.cvd_5m
        taker = state.taker_ratio
        cvd_sub = f"CVD 5m — aggTrade ({trade_age}s önce)"
        taker_sub = f"Taker 5m — aggTrade"
        cvd_source = "aggTrade"
    elif fresh and last:
        cvd = last.get("delta", 0)
        taker = last.get("taker", 0.5)
        cvd_sub = (
            f"aggTrade yok ({trade_age}s) — bu 15m mum delta, CVD 5m değil"
        )
        taker_sub = "15m mum taker (aggTrade değil)"
        cvd_source = "15m_bar"
    else:
        cvd = 0.0
        taker = 0.5
        cvd_sub = "Veri yok"
        taker_sub = "—"
        cvd_source = "none"

    trend_age = int(time.time() - tv["ts"]) if tv.get("ts") else -1
    kline_age = (
        int(time.time() - state.kline_last_update)
        if state.kline_last_update
        else -1
    )

    entry_mode = getattr(cfg, "ENTRY_MODE", "break").lower()
    regime_display_override = None
    bot_ok = False

    rv = state.range_view or {}
    op = state.operation_view or {}
    if entry_mode in ("break", "realtime") and fresh:
        from engine.operation_state import get_operation_view

        op = get_operation_view(price)
        bv = dict(op.get("breakout") or _breakout_panel_view(price, trade_ok, bars))
        rv = dict(op.get("range") or {})
    elif entry_mode in ("range", "hybrid") and fresh:
        from engine.operation_state import get_operation_view

        op = get_operation_view(price)
        rv = dict(op.get("range") or {})
        state.range_view = rv
        bv = dict(op.get("breakout") or _breakout_panel_view(price, trade_ok, bars))
    else:
        bv = state.breakout_view or {}
        op = state.operation_view or {}

    if fresh and entry_mode in ("break", "realtime"):
        regime_display_override = op.get("headline") or bv.get("status", "KIRILIM")
        cvd_sub = (
            f"CVD 5m: {bv.get('cvd_5m', 0):+,.0f}  "
            f"Taker: {bv.get('taker', 0.5):.0%}  "
            f"OI: {'↑' if bv.get('oi_rising') else '↓/—'}"
        )
        act = bv.get("active_direction") or "—"
        lvl = bv.get("active_level") or 0
        regime_sub = op.get("summary") or (
            f"AKIS: CVD {bv.get('cvd_5m', 0):+,.0f}  "
            f"Taker {bv.get('taker', 0.5):.0%}  |  "
            f"SEVIYE: {act} @ {lvl:.2f}  mesafe {bv.get('distance', 0):.1f}  "
            f"| {bv.get('status', '')}  "
            f"| rejim bilgi (giriş şartı değil) {regime} {score}%"
        )
        if not bv.get("feeds_ok"):
            regime_sub += f"  |  ⚠ {bv.get('feeds_msg', '')}"
        regime_sub += f"  |  kline:{kline_age}s trade:{trade_age}s"
        bot_ok = True
    elif fresh and entry_mode in ("range", "hybrid"):
        regime_display_override = op.get("headline") or str(rv.get("status", "—"))[:28]
        regime_sub = op.get("summary") or (
            f"L{rv.get('long_score', 0)} "
            f"(e×f {rv.get('long_edge_p', 0):.2f}×{rv.get('long_flow_p', 0):.2f}) / "
            f"S{rv.get('short_score', 0)} "
            f"(e×f {rv.get('short_edge_p', 0):.2f}×{rv.get('short_flow_p', 0):.2f})"
        )
        regime_sub += f"  |  kline:{kline_age}s trade:{trade_age}s"
        bot_ok = True
    elif fresh and tv:
        from engine.trend import momentum_explain
        ad = alignment_detail(tv)
        mom = momentum_explain(bars)
        regime_display_override = None
        cl = tv.get("chart_lines") or []
        if cl:
            regime_sub = "  |  ".join(cl[:3])
            regime_sub += f"  |  Güç: {score}%  |  {tv.get('align_status', '')}"
        else:
            regime_sub = f"Güç: {score}%  |  {ad['line']}"
        if mom:
            regime_sub += f"  |  {mom}"
        regime_sub += f"  |  trend:{trend_age}s kline:{kline_age}s"
        if kline_age > 120:
            regime_sub += " ⚠ kline eski — rejim takili olabilir"
        bot_ok = True
    elif fresh:
        regime_sub = f"Güç: {score}%  |  aggTrade bekleniyor"
        bot_ok = False
    else:
        regime_sub = "Bot kapalı"
        regime = "—" if regime == "UNKNOWN" else regime
        bot_ok = False

    return {
        "fresh": fresh,
        "trade_ok": trade_ok,
        "bot_ok": bot_ok,
        "cvd_source": cvd_source,
        "trade_age": trade_age,
        "price": price,
        "price_src": price_src,
        "bid": state.bid,
        "ask": state.ask,
        "regime": regime,
        "regime_display": (
            regime_display_override
            or (tv.get("headline") if tv else None)
            or f"{regime} {tv.get('phase', '')}".strip()
        ),
        "score": score,
        "regime_sub": regime_sub,
        "cvd": cvd,
        "cvd_sub": cvd_sub,
        "taker": taker,
        "taker_sub": taker_sub,
        "fund": state.funding_rate * 100,
        "oi": state.oi_current,
        "oi_up": state.oi_rising,
        "tv": tv,
        "operation": op,
        "breakout": bv,
        "range": rv,
        "entry_mode": entry_mode,
    }
