#!/usr/bin/env python3
"""
Karar katmani WAIT engelleri — DB + oturum JSON.

  python scripts/decision_block_report.py --hours 24
  python scripts/decision_block_report.py --hours 24 --channel
  python scripts/decision_block_report.py --hours 24 --rr-geometry
  python scripts/decision_block_report.py --hours 24 --full
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import cfg

RR_RE = re.compile(r"RR yetersiz:\s*([\d.]+)", re.I)
SCORE_RE = re.compile(r"fade_score edge yok\s*\((\d+)\s*vs\s*(\d+)\)", re.I)

ZONES = ("NEAR_SUPPORT", "NEAR_RESISTANCE", "MID_RANGE", "—")
PATHS = ("fade", "breakout", "none", "—")


def _print_table(title: str, rows: list[tuple[str, int]], *, total: int | None = None) -> None:
    print(f"\n=== {title} ===\n")
    if not rows:
        print("(kayit yok)\n")
        return
    w = max(len(r[0]) for r in rows)
    print(f"{'Sebep':<{w}}  Adet   %")
    print(f"{'-' * w}  ----  -----")
    denom = total or sum(r[1] for r in rows)
    for code, cnt in rows:
        pct = 100.0 * cnt / max(denom, 1)
        print(f"{code:<{w}}  {cnt:4d}  {pct:5.1f}%")
    print(f"{'TOPLAM':<{w}}  {denom:4d}  100.0%")
    print()


def _print_cross(title: str, ctr: Counter, row_keys: tuple[str, ...], col_key: str) -> None:
    """Ornek: zone satirlari, RR_TOO_LOW sutunu."""
    print(f"\n=== {title} ===\n")
    cols = sorted({k.split("|", 1)[1] for k in ctr if "|" in k})
    if not cols:
        print("(kayit yok)\n")
        return
    cw = max(14, max(len(c) for c in cols))
    rw = max(16, max(len(r) for r in row_keys))
    header = f"{'':<{rw}}  " + "  ".join(f"{c:>{cw}}" for c in cols) + "  TOPLAM"
    print(header)
    print("-" * len(header))
    col_totals: Counter = Counter()
    grand = 0
    for row in row_keys:
        cells = []
        row_sum = 0
        for col in cols:
            n = int(ctr.get(f"{row}|{col}", 0))
            cells.append(f"{n:>{cw}}")
            col_totals[col] += n
            row_sum += n
        if row_sum > 0 or row in ctr:
            print(f"{row:<{rw}}  " + "  ".join(cells) + f"  {row_sum:>6}")
            grand += row_sum
    print(f"{'TOPLAM':<{rw}}  " + "  ".join(f"{col_totals[c]:>{cw}}" for c in cols) + f"  {grand:>6}")
    print()


def db_reject_rows(hours: int) -> list[sqlite3.Row]:
    conn = sqlite3.connect(cfg.DB_PATH)
    conn.row_factory = sqlite3.Row
    cutoff = time.time() - hours * 3600
    rows = conn.execute(
        """
        SELECT ts, ts_human, reject_reason, scenario, context_json, reason_text, price,
               intended_side, primary_block, trade_id
        FROM v3_attribution
        WHERE ts > ? AND action = 'WAIT' AND reject_reason != ''
        ORDER BY ts DESC
        """,
        (cutoff,),
    ).fetchall()
    conn.close()
    return rows


def _filter_rows(
    rows: list[sqlite3.Row],
    *,
    decision_only: bool = False,
    layer: str = "",
) -> list[sqlite3.Row]:
    if not decision_only and not layer:
        return rows
    out: list[sqlite3.Row] = []
    for r in rows:
        ctx = _parse_ctx(r)
        ly = str(ctx.get("reject_layer") or "")
        if not ly:
            try:
                from engine.reject_reason_v3 import reject_layer_for

                ly = reject_layer_for(str(r["reject_reason"] or ""))
            except Exception:
                ly = ""
        if layer and ly != layer:
            continue
        if decision_only and ly != "decision":
            continue
        out.append(r)
    return out


def _parse_ctx(row: sqlite3.Row) -> dict:
    try:
        return json.loads(row["context_json"] or "{}")
    except Exception:
        return {}


def _channel_rows(rows: list[sqlite3.Row]) -> list[sqlite3.Row]:
    return [r for r in rows if str(r["scenario"] or "").startswith("CHANNEL")]


def _geom_stats(values: list[float]) -> str:
    if not values:
        return "—"
    if len(values) == 1:
        return f"ort={values[0]:.2f}"
    return (
        f"ort={statistics.mean(values):.2f}  "
        f"med={statistics.median(values):.2f}  "
        f"min={min(values):.2f}  max={max(values):.2f}"
    )


def report_layer_breakdown(hours: int) -> None:
    rows = db_reject_rows(hours)
    layer_ctr: Counter = Counter()
    for r in rows:
        ctx = _parse_ctx(r)
        ly = str(ctx.get("reject_layer") or "")
        if not ly:
            try:
                from engine.reject_reason_v3 import reject_layer_for

                ly = reject_layer_for(str(r["reject_reason"] or ""))
            except Exception:
                ly = "other"
        layer_ctr[ly or "other"] += 1
    _print_table(f"Son {hours}h WAIT — reject_layer", layer_ctr.most_common(), total=len(rows))


def report_trade_links(hours: int) -> None:
    conn = sqlite3.connect(cfg.DB_PATH)
    cutoff = time.time() - hours * 3600
    total_trades = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE open_ts > ?", (cutoff,)
    ).fetchone()[0]
    linked = conn.execute(
        """
        SELECT COUNT(DISTINCT trade_id) FROM v3_attribution
        WHERE trade_id IS NOT NULL AND trade_id > 0 AND ts > ?
        """,
        (cutoff,),
    ).fetchone()[0]
    entered = conn.execute(
        """
        SELECT COUNT(*) FROM v3_attribution
        WHERE ts > ? AND action IN ('LONG', 'SHORT') AND trade_id IS NOT NULL
        """,
        (cutoff,),
    ).fetchone()[0]
    conn.close()
    print(f"\n=== Trade ↔ attribution (son {hours}h) ===\n")
    print(f"  trades acildi:     {total_trades}")
    print(f"  attr trade_id:     {linked}")
    print(f"  attr LONG/SHORT+link: {entered}")
    if total_trades and linked < total_trades:
        print(f"  >> UYARI: {total_trades - linked} trade baglanmamis olabilir")
    print()


def report_db(
    hours: int,
    *,
    channel_only: bool = False,
    decision_only: bool = False,
) -> None:
    from botlog.db import init

    init()
    rows = _filter_rows(db_reject_rows(hours), decision_only=decision_only)
    if channel_only:
        rows = _channel_rows(rows)

    cats: Counter = Counter()
    zone_reject: Counter = Counter()
    for r in rows:
        code = str(r["reject_reason"] or "OTHER")
        cats[code] += 1
        ctx = _parse_ctx(r)
        zone = str(ctx.get("zone") or "—").upper()
        fav = str(ctx.get("favored_side") or "")
        if zone != "—":
            zone_reject[f"{zone}|{code}"] += 1
            if fav and fav not in ("WAIT", "—", ""):
                zone_reject[f"{zone}|fav={fav}|{code}"] += 1

    title = f"Son {hours} saat WAIT (DB"
    title += ", CHANNEL only" if channel_only else ""
    title += ", decision katmani" if decision_only else ""
    title += ")"
    _print_table(title, cats.most_common(), total=len(rows))

    zr_items = [(k, v) for k, v in zone_reject.most_common() if "fav=" in k]
    if not zr_items:
        zr_items = zone_reject.most_common(15)
    if zr_items:
        print("=== Zone x reject (ust 15) ===\n")
        for k, v in zr_items[:15]:
            print(f"  {k:<50} {v:4d}")
        print()


def _classify_rr_scenario(ctx: dict) -> str:
    """Senaryo A: SL uzak / dar odul. Senaryo B: gec giris ipucu."""
    zone = str(ctx.get("zone") or "").upper()
    cand = str(ctx.get("channel_candidate") or "").upper()
    risk = float(ctx.get("risk_usd") or 0)
    reward = float(ctx.get("reward_usd") or 0)
    band_w = float(ctx.get("band_width_usd") or 0)
    px = float(ctx.get("price") or 0)
    band_s = float(ctx.get("band_support") or 0)
    band_r = float(ctx.get("band_resistance") or 0)

    if risk <= 0:
        return "unknown"
    if reward > 0 and risk > reward * 1.8:
        return "A_sl_uzak"
    if zone == "NEAR_SUPPORT" and cand == "LONG" and band_w > 0 and reward < band_w * 0.35:
        return "A_dar_bant_tp"
    if zone == "NEAR_RESISTANCE" and cand == "SHORT" and band_r > 0 and px < band_r * 0.998:
        return "B_gec_kisa_rr"
    if zone == "NEAR_SUPPORT" and cand == "LONG" and band_s > 0 and px > band_s + band_w * 0.55:
        return "B_destek_ustu_gec"
    return "mixed"


def report_rr_geometry(hours: int, *, decision_only: bool = False) -> None:
    rows = _channel_rows(
        _filter_rows(db_reject_rows(hours), decision_only=decision_only)
    )
    rr_rows = [r for r in rows if str(r["reject_reason"]) == "RR_TOO_LOW"]

    print(f"\n{'=' * 60}")
    print(f"  RR GEOMETRY — CHANNEL_WAIT (son {hours}h)")
    print(f"{'=' * 60}")

    if not rr_rows:
        print("\nRR_TOO_LOW kaydi yok.\n")
        return

    zone_ctr: Counter = Counter()
    path_ctr: Counter = Counter()
    zone_path_ctr: Counter = Counter()
    scenario_ctr: Counter = Counter()
    risks: list[float] = []
    rewards: list[float] = []
    rrs: list[float] = []
    legacy = 0
    samples: list[dict] = []

    for r in rr_rows:
        ctx = _parse_ctx(r)
        zone = str(ctx.get("zone") or "—").upper()
        path = str(ctx.get("path") or "—").lower()
        zone_ctr[zone] += 1
        path_ctr[path] += 1
        zone_path_ctr[f"{zone}|{path}"] += 1

        risk = float(ctx.get("risk_usd") or 0)
        reward = float(ctx.get("reward_usd") or 0)
        rr = float(ctx.get("entry_rr") or 0)
        if rr <= 0:
            m = RR_RE.search(str(r["reason_text"] or ""))
            if m:
                rr = float(m.group(1))

        if risk > 0:
            ctx["price"] = float(r["price"] or ctx.get("price") or 0)
            risks.append(risk)
            rewards.append(reward)
            rrs.append(rr)
            scenario_ctr[_classify_rr_scenario(ctx)] += 1
            if len(samples) < 5:
                samples.append(
                    {
                        "ts": r["ts_human"],
                        "px": ctx["price"],
                        "sl": ctx.get("entry_sl"),
                        "tp": ctx.get("entry_tp"),
                        "rr": rr,
                        "risk": risk,
                        "reward": reward,
                        "zone": zone,
                        "path": path,
                        "cand": ctx.get("channel_candidate"),
                        "band": f"{ctx.get('band_support')}/{ctx.get('band_resistance')}",
                    }
                )
        else:
            legacy += 1

    print(f"\nToplam RR_TOO_LOW: {len(rr_rows)}  (geometri context'li: {len(rrs)}, eski/bos: {legacy})")

    if legacy and not rrs:
        print(
            "\nUyari: Tum kayitlar context'siz (bot yeniden baslatilmadan once).\n"
            "Yeniden baslattiktan sonra zone/path/SL/TP dolacak.\n"
        )
        print("CHANNEL_WAIT RR_TOO_LOW (zone bilinmiyor):", len(rr_rows))
        return

    # Zone x RR
    _print_cross(
        "RR_TOO_LOW — Zone",
        Counter({f"{z}|RR_TOO_LOW": zone_ctr[z] for z in zone_ctr}),
        ZONES[:3],
        "RR_TOO_LOW",
    )
    _print_cross(
        "RR_TOO_LOW — Path",
        Counter({f"{p}|RR_TOO_LOW": path_ctr[p] for p in path_ctr}),
        PATHS[:2],
        "RR_TOO_LOW",
    )

    print("=== RR_TOO_LOW — Zone x Path ===\n")
    for z in ("NEAR_SUPPORT", "NEAR_RESISTANCE", "MID_RANGE"):
        parts = []
        for p in ("fade", "breakout"):
            n = zone_path_ctr.get(f"{z}|{p}", 0)
            if n:
                parts.append(f"{p}={n}")
        if parts:
            print(f"  {z:<18} {' | '.join(parts)}  (toplam {zone_ctr.get(z, 0)})")
    other_z = sum(v for k, v in zone_ctr.items() if k not in ZONES[:3])
    if other_z:
        print(f"  (diger zone)       {other_z}")
    print()

    print("=== RR geometri ozeti (context'li ornekler) ===\n")
    print(f"  risk_usd   {_geom_stats(risks)}")
    print(f"  reward_usd {_geom_stats(rewards)}")
    print(f"  RR         {_geom_stats(rrs)}")
    if risks and rewards:
        ratio = statistics.mean(rewards) / max(statistics.mean(risks), 0.01)
        print(f"  reward/risk ort={ratio:.2f}  (filtre esigi genelde {getattr(cfg, 'V3_MIN_RR_RATIO', 2.0)})")
    print()

    if scenario_ctr:
        print("=== Senaryo sinifi (heuristic) ===\n")
        labels = {
            "A_sl_uzak": "A — SL cok uzak (risk >> odul)",
            "A_dar_bant_tp": "A — dar bant, TP kisa (destek LONG)",
            "B_gec_kisa_rr": "B — direnc altinda gec SHORT?",
            "B_destek_ustu_gec": "B — destek ustunde gec LONG?",
            "mixed": "karisik / net degil",
            "unknown": "bilinmiyor",
        }
        for key, cnt in scenario_ctr.most_common():
            pct = 100.0 * cnt / max(len(rrs), 1)
            print(f"  {labels.get(key, key):<42} {cnt:4d}  ({pct:4.1f}%)")
        print()

    if samples:
        print("=== Son ornekler (SL/TP/RR) ===\n")
        for s in samples:
            print(
                f"  {s['ts']} px={s['px']:.2f} SL={s['sl']} TP={s['tp']} "
                f"RR={s['rr']:.2f} risk={s['risk']:.1f}$ reward={s['reward']:.1f}$ "
                f"{s['zone']}/{s['path']} cand={s['cand']} band={s['band']}"
            )
        print()

    _report_score_low(hours, rows)


def _report_score_low(hours: int, channel_rows: list[sqlite3.Row] | None = None) -> None:
    rows = channel_rows or _channel_rows(db_reject_rows(hours))
    sc_rows = [r for r in rows if str(r["reject_reason"]) == "CHANNEL_SCORE_LOW"]

    print(f"{'=' * 60}")
    print(f"  CHANNEL_SCORE_LOW (son {hours}h)")
    print(f"{'=' * 60}\n")

    if not sc_rows:
        print("(kayit yok)\n")
        return

    zone_ctr: Counter = Counter()
    fav_zone_ctr: Counter = Counter()
    margins: list[int] = []

    for r in sc_rows:
        ctx = _parse_ctx(r)
        zone = str(ctx.get("zone") or "—").upper()
        fav = str(ctx.get("favored_side") or "—").upper()
        zone_ctr[zone] += 1
        if fav not in ("—", "WAIT", ""):
            fav_zone_ctr[f"{zone}|fav={fav}"] += 1
        m = SCORE_RE.search(str(r["reason_text"] or ""))
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            margins.append(b - a)

    _print_cross(
        "CHANNEL_SCORE_LOW — Zone",
        Counter({f"{z}|CHANNEL_SCORE_LOW": zone_ctr[z] for z in zone_ctr}),
        ("NEAR_SUPPORT", "NEAR_RESISTANCE", "MID_RANGE"),
        "CHANNEL_SCORE_LOW",
    )

    print("=== CHANNEL_SCORE_LOW — Zone x favored_side ===\n")
    for k, v in fav_zone_ctr.most_common(12):
        print(f"  {k:<45} {v:4d}")
    n_rs_short = fav_zone_ctr.get("NEAR_RESISTANCE|fav=SHORT", 0)
    print(f"\n  >> NEAR_RESISTANCE + favored=SHORT: {n_rs_short} / {len(sc_rows)}")
    if margins:
        print(f"\n  Skor farki (vs threshold): {_geom_stats([float(x) for x in margins])}")
    print()


def report_session() -> None:
    from engine.decision_block_stats_v3 import _load_store, format_summary_line
    from engine.reject_reason_v3 import get_reject_counters

    store = _load_store()
    totals = store.get("totals") or {}
    _print_table("Oturum JSON (decision_blocked.json)", sorted(totals.items(), key=lambda x: -x[1]))
    print(format_summary_line(totals))
    print()
    sess = get_reject_counters()
    if sess:
        _print_table("Oturum bellek (reject_reason_v3)", sorted(sess.items(), key=lambda x: -x[1]))


def main() -> None:
    p = argparse.ArgumentParser(description="V3 karar katmani WAIT engelleri")
    p.add_argument("--hours", type=int, default=24, help="DB penceresi (saat)")
    p.add_argument("--session", action="store_true", help="Sadece bu oturum JSON/bellek")
    p.add_argument("--channel", action="store_true", help="Sadece CHANNEL_WAIT ozeti")
    p.add_argument("--rr-geometry", action="store_true", help="RR + CHANNEL_SCORE_LOW geometri")
    p.add_argument("--full", action="store_true", help="channel ozet + rr geometry")
    p.add_argument(
        "--decision-only",
        action="store_true",
        help="Sadece karar katmani (execute/gate haric)",
    )
    p.add_argument("--layers", action="store_true", help="reject_layer dagilimi")
    p.add_argument("--trade-links", action="store_true", help="trade-attribution baglanti")
    args = p.parse_args()

    if args.session:
        report_session()
        return
    if args.layers:
        report_layer_breakdown(args.hours)
    if args.trade_links:
        report_trade_links(args.hours)
    if args.full:
        report_layer_breakdown(args.hours)
        report_db(args.hours, channel_only=True, decision_only=args.decision_only)
        report_rr_geometry(args.hours, decision_only=args.decision_only)
        report_trade_links(args.hours)
        return
    if args.rr_geometry:
        report_rr_geometry(args.hours, decision_only=args.decision_only)
        return
    if args.layers or args.trade_links:
        if not args.channel and not args.rr_geometry:
            report_db(args.hours, decision_only=args.decision_only)
        return
    report_db(args.hours, channel_only=args.channel, decision_only=args.decision_only)
    if args.channel:
        report_rr_geometry(args.hours, decision_only=args.decision_only)


if __name__ == "__main__":
    main()
