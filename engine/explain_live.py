"""
engine/explain_live.py — Dashboard grafiği + canlı bot + DB birleşik açıklama.
"""
from __future__ import annotations

import time

from core.state import state, data_is_fresh, effective_price
from engine.explain_context import build_context, format_rule_report
from engine.explain_decision import build_decision_block, infer_visual_from_candle
from engine.time_align import parse_when, format_time_sync_line
from engine.explain_context import _nearest_snapshot_human, _human, _attribute_causes
from engine.structure_explain import analyze_structure_at
from dashboard.binance_chart import get_chart_package, latest_closed_15m_ts


def resolve_explain_when(
    when: str | float | None, tz_mode: str = "tr"
) -> tuple[str, float, dict]:
    """
    Saat girilmemişse dashboard grafiğindeki son kapanan 15m mumu kullanır.
    """
    if when is not None and str(when).strip():
        ts, meta = parse_when(when, tz_mode)
        return str(when).strip(), ts, meta

    ts = latest_closed_15m_ts()
    _, meta = parse_when(ts, tz_mode)
    if tz_mode == "utc":
        display = meta["utc_human"].replace(" UTC", "")
    else:
        display = meta["tr_human"].replace(" (Binance TR)", "")
    meta["auto"] = True
    return display, ts, meta


def _bar_at(bars: list[dict], ts: float) -> dict | None:
    if not bars:
        return None
    return min(bars, key=lambda b: abs(b["ts"] - ts))


def _candle_from_bar(bar: dict) -> dict:
    return {
        "open": bar["open"],
        "high": bar["high"],
        "low": bar["low"],
        "close": bar["close"],
    }


def build_dashboard_context(when: str | float, tz_mode: str = "tr") -> dict:
    """
    Öncelik: DB journal → canlı bot state → dashboard Binance 15m mum.
    Binance ekran görüntüsü gerekmez; üst grafikteki saat yeterli.
    """
    _, ts, time_meta = resolve_explain_when(when, tz_mode)
    db_near = _nearest_snapshot_human(ts)
    time_meta["sync_line"] = format_time_sync_line(time_meta, db_near)

    db_ctx = build_context(when, tz_mode=tz_mode)
    if db_ctx.get("ok"):
        db_ctx["data_source"] = "DB journal + bot kaydı (en güvenilir)"
        db_ctx["time_meta"] = time_meta
        db_ctx.setdefault("structure_analysis", analyze_structure_at(ts))
        return db_ctx

    pkg = get_chart_package(96)
    bar = _bar_at(pkg.get("bars") or [], ts)
    live = data_is_fresh(max_age_sec=30)
    tv = state.trend_view or {}

    if not bar and not live:
        return {
            "ok": False,
            "ts": ts,
            "ts_human": _human(ts),
            "time_meta": time_meta,
            "error": (
                f"{_human(ts)} için veri yok.\n"
                f"{time_meta['sync_line']}\n\n"
                "Üst grafikte bir mum seçin veya python main.py çalıştırın."
            ),
        }

    sources = []
    if bar:
        sources.append("Dashboard grafiği (Binance 15m API)")
    if live:
        sources.append("Canlı bot (aggTrade/CVD 5m)")
    else:
        sources.append("UYARI: CVD 5m / rejim üst paneli sıfır — main.py kapalı veya feed yok")

    chg = ((bar["close"] - bar["open"]) / bar["open"] * 100) if bar and bar["open"] else 0
    visual_label = "DÜŞÜŞ" if chg < -0.05 else ("YÜKSELİŞ" if chg > 0.05 else "YATAY")

    p_at = {
        "price": bar["close"] if bar else effective_price(),
        "structure_15m": state.structure_15m if live else "?",
        "structure_1h": state.structure_1h if live else "?",
        "cvd_5m": state.cvd_5m if live else (bar.get("delta", 0) if bar else 0),
        "taker_ratio": state.taker_ratio if live else (bar.get("taker", 0.5) if bar else 0.5),
        "oi": state.oi_current,
        "oi_rising": state.oi_rising,
        "trend": tv if live else {
            "bias": "RANGE",
            "phase": "range",
            "strength": 0,
            "trade_ok": False,
            "structure_aligned": False,
            "summary": "Canlı trend yok — main.py çalıştırın",
        },
        "in_position": state.in_position,
        "pos_side": state.pos_side,
        "extra": {"candle": _candle_from_bar(bar)} if bar else {},
    }

    if bar and not live:
        p_at["cvd_note"] = (
            f"Üst panel CVD 0 normal: 5m CVD sadece main.py aggTrade ile gelir. "
            f"Bu mum 15m delta={bar.get('delta', 0):+.0f} ETH"
        )

    decision = build_decision_block(p_at, ts)
    causes = _attribute_causes(p_at, {}, {}, [], ts)
    if bar and not live:
        causes.insert(0, f"15m mum: {chg:+.2f}% (dashboard grafiği)")

    visual = {
        "ok": True,
        "label": visual_label,
        "source": "Dashboard 15m mum",
        "detail": f"Mum {chg:+.2f}%",
    }

    return {
        "ok": True,
        "ts": ts,
        "ts_human": _human(ts),
        "snapshot_kind": "dashboard_chart",
        "candle": _candle_from_bar(bar) if bar else {},
        "price": p_at["price"],
        "structure_15m": p_at["structure_15m"],
        "structure_1h": p_at["structure_1h"],
        "trend": p_at["trend"],
        "cvd_5m": p_at["cvd_5m"],
        "taker_ratio": p_at["taker_ratio"],
        "oi": p_at.get("oi"),
        "oi_rising": p_at.get("oi_rising"),
        "cvd_delta_15m": bar.get("delta") if bar else None,
        "causes": causes,
        "events": [],
        "trade_ok": tv.get("trade_ok") if live else False,
        "structure_aligned": tv.get("structure_aligned") if live else False,
        "decision": decision,
        "time_meta": time_meta,
        "visual": visual,
        "data_source": " | ".join(sources),
        "live_bot": live,
        "cvd_note": p_at.get("cvd_note", ""),
        "structure_analysis": analyze_structure_at(ts),
    }


def explain_from_dashboard_chart(when: str | float, tz_mode: str = "tr") -> str:
    ctx = build_dashboard_context(when, tz_mode)
    report = format_rule_report(ctx)
    src = ctx.get("data_source", "")
    note = ctx.get("cvd_note", "")
    extra = f"\n\n--- Veri kaynağı ---\n{src}"
    if note:
        extra += f"\n{note}"
    return report + extra
