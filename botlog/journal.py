"""
botlog/journal.py — Piyasa verisi günlüğü (DB).

Her ~10sn örnek + her 1m/15m/1h + önemli olaylar kaydedilir.
Sonra: "bu mum neden düştü?" → explain_at(ts)
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from core.config import cfg
from core.state import state, effective_price
from core.logger import get_logger
from botlog.db import _conn

log = get_logger("Journal")

_last_sample_ts = 0.0
_last_trend_key = ""
_last_struct_key = ""


def _ts_human(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


def _payload_full() -> dict:
    """O anki botun gördüğü her şey (grafik sorusu için)."""
    tv = state.trend_view or {}
    f = state.forming_15m or {}
    intra = state.intra_15m_summary or {}
    cvd_tail = list(state.cvd_bars)[-12:]

    return {
        "price": effective_price(),
        "bid": state.bid,
        "ask": state.ask,
        "mark": state.mark_price,
        "structure_15m": state.structure_15m,
        "structure_1h": state.structure_1h,
        "struct_bias_15m": state.struct_bias_15m,
        "struct_invalidation": state.struct_invalidation,
        "cvd_5m": state.cvd_5m,
        "cvd_raw": state.cvd_raw,
        "buy_vol_5m": state.buy_vol_5m,
        "sell_vol_5m": state.sell_vol_5m,
        "taker_ratio": state.taker_ratio,
        "oi": state.oi_current,
        "oi_rising": state.oi_rising,
        "funding_rate": state.funding_rate,
        "funding_signal": state.funding_signal,
        "trend": tv,
        "forming_15m": f,
        "intra_15m": intra,
        "cvd_bars_tail": [
            {
                "ts": b.get("ts"),
                "delta": b.get("delta"),
                "close": b.get("close"),
            }
            for b in cvd_tail
        ],
        "regime": state.regime,
        "signal": state.signal,
        "in_position": state.in_position,
        "pos_side": state.pos_side,
    }


def record_snapshot(kind: str, note: str = "", extra: dict | None = None) -> int:
    """kind: sample | 1m | 15m | 1h | trade | explain"""
    ts = time.time()
    p = _payload_full()
    if extra:
        p["extra"] = extra

    with _conn() as db:
        cur = db.execute(
            """
            INSERT INTO market_snapshots
            (ts, ts_human, kind, price, note, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                ts,
                _ts_human(ts),
                kind,
                p.get("price") or 0.0,
                note[:500],
                json.dumps(p, ensure_ascii=False, default=str),
            ),
        )
        return cur.lastrowid


def record_event(
    event_type: str,
    title: str,
    detail: str = "",
    severity: str = "info",
    payload: dict | None = None,
) -> int:
    ts = time.time()
    with _conn() as db:
        cur = db.execute(
            """
            INSERT INTO market_events
            (ts, ts_human, event_type, severity, title, detail, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ts,
                _ts_human(ts),
                event_type,
                severity,
                title[:200],
                detail[:2000],
                json.dumps(payload or _payload_full(), ensure_ascii=False, default=str),
            ),
        )
        return cur.lastrowid


def maybe_sample_tick():
    """Her N saniyede bir tam state (aggTrade başına değil)."""
    global _last_sample_ts
    interval = float(getattr(cfg, "JOURNAL_SAMPLE_SEC", 10))
    now = time.time()
    if now - _last_sample_ts < interval:
        return
    if effective_price() <= 0:
        return
    _last_sample_ts = now
    record_snapshot("sample", note="periyodik")


def on_trend_updated():
    """Trend değişince olay + yorum."""
    global _last_trend_key
    tv = state.trend_view or {}
    key = f"{tv.get('bias')}|{tv.get('phase')}|{tv.get('strength', 0) // 5}"
    if key == _last_trend_key:
        return
    _last_trend_key = key

    title = tv.get("summary", "trend degisti")
    detail = (
        f"bias={tv.get('bias')} phase={tv.get('phase')} "
        f"guc={tv.get('strength')} trade_ok={tv.get('trade_ok')} "
        f"1h+15m={tv.get('structure_aligned')}"
    )
    record_event("trend_change", title, detail, "info", tv)
    record_snapshot("event", note=title[:200])


def on_structure_updated():
    global _last_struct_key
    key = f"{state.structure_15m}|{state.structure_1h}"
    if key == _last_struct_key:
        return
    _last_struct_key = key
    title = f"Yapi: 15m={state.structure_15m} 1h={state.structure_1h}"
    record_event("structure_change", title, "", "info")
    record_snapshot("event", note=title)


def on_bar(kind: str, candle: dict, interpretation: str):
    """1m / 15m / 1h kapanış."""
    o, c = candle.get("open", 0), candle.get("close", 0)
    chg = ((c - o) / o * 100) if o else 0
    note = f"{kind} O={o:.2f} C={c:.2f} ({chg:+.2f}%) | {interpretation}"
    record_snapshot(kind, note=note, extra={"candle": candle})
    record_event(
        f"bar_{kind}",
        f"{kind} mum kapandi ({chg:+.2f}%)",
        interpretation,
        "info" if abs(chg) < 0.5 else ("warn" if chg < 0 else "ok"),
        {"candle": candle, "chg_pct": chg},
    )


def on_liquidation(side: str, qty: float, price: float):
    record_event(
        "liquidation",
        f"Tasfiye {side} {qty:.2f} ETH @ {price:.2f}",
        "Agresif likidasyon fiyat hareketini hizlandirabilir",
        "warn",
        {"side": side, "qty": qty, "price": price},
    )
