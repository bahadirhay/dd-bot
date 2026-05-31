"""
engine/explain_context.py — DB'den yapılandırılmış bağlam (kural + LLM girdisi).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from botlog.db import _conn
from engine.explain_decision import (
    build_decision_block,
    infer_visual_from_candle,
    format_visual_compare_block,
    _normalize_trend,
    _normalize_signal,
)
from engine.time_align import parse_when, format_time_sync_line, utc_to_binance_local
from engine.structure_explain import analyze_structure_at, format_structure_block


def _parse_ts(when: str | float, tz_mode: str = "tr") -> float:
    ts, _ = parse_when(when, tz_mode)
    return ts


def _nearest_snapshot_human(ts: float, window_sec: float = 900) -> str | None:
    with _conn() as db:
        row = db.execute(
            """
            SELECT ts_human, ABS(ts - ?) AS d
            FROM market_snapshots
            WHERE ts BETWEEN ? AND ?
            ORDER BY d ASC LIMIT 1
            """,
            (ts, ts - window_sec, ts + window_sec),
        ).fetchone()
    if not row:
        return None
    return f"{row['ts_human']} ({int(row['d'])} sn fark)"


def _human(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


def _load_snapshots(ts: float, window_sec: float = 900) -> list[dict]:
    with _conn() as db:
        rows = db.execute(
            """
            SELECT * FROM market_snapshots
            WHERE ts BETWEEN ? AND ?
            ORDER BY ts ASC
            """,
            (ts - window_sec, ts + window_sec),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["payload"] = json.loads(d["payload_json"] or "{}")
        except json.JSONDecodeError:
            d["payload"] = {}
        out.append(d)
    return out


def _load_events(ts: float, window_sec: float = 900) -> list[dict]:
    with _conn() as db:
        rows = db.execute(
            """
            SELECT * FROM market_events
            WHERE ts BETWEEN ? AND ?
            ORDER BY ts ASC
            """,
            (ts - window_sec, ts + window_sec),
        ).fetchall()
    return [dict(r) for r in rows]


def _closest(rows: list[dict], ts: float) -> dict | None:
    if not rows:
        return None
    return min(rows, key=lambda r: abs(r["ts"] - ts))


def _attribute_causes(p_at, p_before, p_after, events, ts) -> list[str]:
    causes = []
    tv = _normalize_trend(p_at.get("trend"))
    bias = tv.get("bias", "RANGE")
    phase = tv.get("phase", "")

    cvd = p_at.get("cvd_5m", 0)
    taker = p_at.get("taker_ratio", 0.5)
    s15, s1h = p_at.get("structure_15m"), p_at.get("structure_1h")

    if bias == "DOWN" or phase in ("drop", "downtrend"):
        if s15 == "DOWN" and s1h == "DOWN":
            causes.append("Hem 15m hem 1h yapı aşağı — ana trend ile uyumlu düşüş.")
        elif s15 == "DOWN" and s1h != "DOWN":
            causes.append("15m düşüyor ama 1h henüz aşağı değil — counter-trend / düzeltme riski.")
        if cvd < -200:
            causes.append(f"Agresif satış baskısı (CVD5m={cvd:+.0f}) — taker satım dominant.")
        if taker < 0.4:
            causes.append(f"Taker satış oranı yüksek ({1-taker:.0%} satım).")
        if not p_at.get("oi_rising"):
            causes.append("Open Interest artmıyor — düşüşte genelde tasfiye veya kısa kapanış.")

    if bias == "UP" or phase in ("rise", "uptrend"):
        if s15 == "UP" and s1h == "UP":
            causes.append("15m ve 1h yapı yukarı — trend yönünde yükseliş.")
        if cvd > 200:
            causes.append(f"Agresif alım (CVD5m={cvd:+.0f}).")

    for e in events:
        if e.get("event_type") == "liquidation" and abs(e["ts"] - ts) < 300:
            causes.append(f"Yakın tasfiye: {e['title']}")

    if p_before and p_after:
        pb, pa = p_before.get("cvd_5m", 0), p_after.get("cvd_5m", 0)
        if pa - pb < -500 and bias == "DOWN":
            causes.append("Önceki 15dk içinde CVD belirgin düştü — momentum birikti.")

    if not causes:
        causes.append("Belirgin tek faktör yok — yatay / karışık orderflow.")

    return causes[:6]


def build_context(
    when: str | float,
    window_min: int = 15,
    tz_mode: str = "tr",
) -> dict:
    ts, time_meta = parse_when(when, tz_mode)
    win = window_min * 60
    db_near = _nearest_snapshot_human(ts, win)
    time_meta["sync_line"] = format_time_sync_line(time_meta, db_near)
    snaps = _load_snapshots(ts, win)
    events = _load_events(ts, win)

    if not snaps:
        return {
            "ok": False,
            "ts": ts,
            "ts_human": _human(ts),
            "time_meta": time_meta,
            "error": (
                f"{_human(ts)} icin DB kaydi yok.\n"
                f"Saat hizalama: {time_meta['sync_line']}\n"
                "Bot calisirken journal kaydi olusur (main.py + JOURNAL_SAMPLE_SEC)."
            ),
        }

    at = _closest(snaps, ts)
    before = _closest([s for s in snaps if s["ts"] < ts - 60], ts)
    after = _closest([s for s in snaps if s["ts"] > ts + 60], ts)

    p_at = at["payload"] if at else {}
    p_before = before["payload"] if before else {}
    p_after = after["payload"] if after else {}

    extra = p_at.get("extra")
    if isinstance(extra, dict):
        candle = extra.get("candle") or {}
    else:
        candle = {}
    tv = _normalize_trend(p_at.get("trend"))
    causes = _attribute_causes(p_at, p_before, p_after, events, ts)
    decision = build_decision_block(p_at, ts)

    return {
        "ok": True,
        "ts": ts,
        "ts_human": _human(ts),
        "snapshot_kind": at.get("kind"),
        "candle": candle,
        "price": p_at.get("price"),
        "structure_15m": p_at.get("structure_15m"),
        "structure_1h": p_at.get("structure_1h"),
        "trend": tv,
        "cvd_5m": p_at.get("cvd_5m"),
        "taker_ratio": p_at.get("taker_ratio"),
        "oi": p_at.get("oi"),
        "oi_rising": p_at.get("oi_rising"),
        "cvd_delta_15m": (p_after.get("cvd_5m", 0) - p_before.get("cvd_5m", 0))
        if p_before and p_after else None,
        "causes": causes,
        "events": [
            {"ts_human": e["ts_human"], "title": e["title"], "detail": e.get("detail", "")}
            for e in events[-12:]
        ],
        "trade_ok": tv.get("trade_ok"),
        "structure_aligned": tv.get("structure_aligned"),
        "decision": decision,
        "time_meta": time_meta,
        "structure_analysis": analyze_structure_at(ts),
    }


def format_rule_report(ctx: dict) -> str:
    if not ctx.get("ok"):
        return (
            ctx.get("error", "Veri yok")
            + "\n\n--- Ne yapmalisiniz? ---\n"
            "1) python main.py calisiyor olmali (journal yazilir)\n"
            "2) Grafikteki saat, bot calisirken olmali\n"
            "3) Binance gorseli: bot o an kayit tutmadiysa "
            "'neden acmadi' sorusuna veriyle cevap verilemez"
        )

    dec = ctx.get("decision") or {}
    if not isinstance(dec, dict):
        dec = {}
    tv = _normalize_trend(ctx.get("trend"))
    visual = ctx.get("visual") or infer_visual_from_candle(ctx)

    tm = ctx.get("time_meta") or {}
    lines = [f"=== BOT NE GORDU / NEDEN POZISYON YOK: {ctx['ts_human']} ===\n"]
    if tm.get("sync_line"):
        lines.append(f"SAAT ESLESTIRME: {tm['sync_line']}\n")
    if ctx.get("data_source"):
        lines.append(f"VERI KAYNAGI: {ctx['data_source']}\n")
    if ctx.get("cvd_note"):
        lines.append(f"NOT: {ctx['cvd_note']}\n")
    if visual.get("ok") or visual.get("label"):
        lines.extend(format_visual_compare_block(visual, dec))

    lines.extend([
        f"YAPI ETİKETİ (kurallarimiz): {dec.get('structure_label', '?')}",
        f"  → {dec.get('structure_rule', '')}",
        f"  Kapı: {dec.get('trade_gate', '')}",
        "",
        f"BOT KARARI: Pozisyon {dec.get('bot_action', '?')}",
        f"  15m yapı={ctx.get('structure_15m')}  1h yapı={ctx.get('structure_1h')}",
        f"  Trend bias={dec.get('bias')}  phase={dec.get('phase')}  "
        f"güç={dec.get('strength')}/100",
        f"  trade_ok={dec.get('trade_ok')}  1h+15m uyumlu={dec.get('structure_aligned')}",
    ])

    lines.append("\n--- Neden açmadı? (sırayla) ---")
    for i, r in enumerate(dec.get("why_no_trade", []), 1):
        lines.append(f"  {i}. {r}")

    ns = dec.get("nearby_signal")
    if ns:
        lines.append("\n--- En yakın 15m sinyal kaydı ---")
        lines.append(
            f"  {ns.get('ts_human')}  yön={ns.get('direction')}  "
            f"girdi={bool(ns.get('entered'))}"
        )
        if ns.get("no_entry_reason"):
            lines.append(f"  Sebep: {ns['no_entry_reason']}")

    nt = dec.get("nearby_trade")
    if nt:
        lines.append("\n--- O sırada açık trade ---")
        lines.append(f"  {nt.get('direction')}  durum={nt.get('status')}")

    lines.append("\n--- Piyasa verisi (o an) ---")
    c = ctx.get("candle") or {}
    if c:
        o, cl = c.get("open", 0), c.get("close", 0)
        chg = ((cl - o) / o * 100) if o else 0
        lines.append(f"  Mum: O={o:.2f} C={cl:.2f} ({chg:+.2f}%)")
    else:
        lines.append(f"  Fiyat: {ctx.get('price', 0):.2f}")
    if tv.get("summary"):
        lines.append(f"  Özet: {tv['summary']}")
    lines.append(
        f"  CVD5m={ctx.get('cvd_5m', 0):+.0f}  Taker={ctx.get('taker_ratio', 0.5):.0%}  "
        f"OI={ctx.get('oi', 0):,.0f}"
    )

    if ctx.get("causes"):
        lines.append("\n--- Grafikteki hareket (yorum) ---")
        for i, cause in enumerate(ctx["causes"], 1):
            lines.append(f"  {i}. {cause}")

    sa = ctx.get("structure_analysis")
    if sa:
        lines.append("")
        lines.extend(
            format_structure_block(
                sa,
                ctx.get("structure_15m") or "?",
                ctx.get("structure_1h") or "?",
            )
        )

    return "\n".join(lines)
