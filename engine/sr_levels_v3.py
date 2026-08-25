"""
engine/sr_levels_v3.py — S/R adaptörü (hesap YOK).

Tüm Pine matematigi: engine/sr_calculator.compute_sr_snapshot
Bu dosya: tek cagri, log, dict donusumu, dedupe.
"""
from __future__ import annotations

from core.config import cfg
from core.logger import get_logger
from engine.sr_calculator import (
    SRSnapshot,
    compute_sr_snapshot,
    min_bars_for_sr_params,
    snapshot_chart_level_dicts,
    snapshot_trade_level_dicts,
    sr_params_from_config,
)

log = get_logger("SRLevelsV3")
_last_sr_log_key: str = ""


def _sr_role_label(price: float, level: float, nominal: str) -> str:
    px = float(price or 0)
    lv = float(level or 0)
    tol = max(abs(px) * 0.0001, 0.05) if px > 0 else 0.0
    if nominal == "support" and px > 0 and lv > px + tol:
        return "kırılan destek"
    if nominal == "resistance" and px > 0 and lv < px - tol:
        return "kırılan direnç"
    return "aktif destek" if nominal == "support" else "aktif direnc"


def _log_sr_snapshot(snap: SRSnapshot) -> None:
    global _last_sr_log_key
    tf = snap.timeframe
    px = snap.price
    act_s = snap.active_support
    act_r = snap.active_resistance
    act_sp = round(float(act_s.price), 2) if act_s else 0.0
    act_rp = round(float(act_r.price), 2) if act_r else 0.0
    log_key = (
        f"{tf}|{round(px, 1)}|{act_sp}|{act_rp}|"
        f"{len(snap.trade_supports)}|{len(snap.trade_resistances)}"
    )
    if log_key == _last_sr_log_key:
        return
    _last_sr_log_key = log_key

    quick_prev = [
        round(float(lv.price), 2)
        for lv in snap.all_levels
        if str(getattr(lv, "tag", "") or "") == "quick_prev_support"
    ]
    pine_detail = []
    for lv in snap.pine_lines:
        side = str(getattr(lv, "direction", "") or "?")
        pine_detail.append(f"L{lv.slot}={lv.price:.2f}({side[:1].upper()})")
    log.info(
        f"[SR] {tf} px={px:.2f} Pine "
        + " ".join(pine_detail)
        + (f" quick_prev={quick_prev}" if quick_prev else "")
        + f" | trade S={len(snap.trade_supports)} R={len(snap.trade_resistances)}"
    )
    if act_s:
        s_label = _sr_role_label(px, act_s.price, "support")
        log.info(
            f"[SR] {tf} {s_label}={act_s.price:.2f} (mesafe {px - act_s.price:.2f}) "
            f"trade={[round(l.price, 2) for l in snap.trade_supports[:5]]}"
        )
    if act_r:
        r_label = _sr_role_label(px, act_r.price, "resistance")
        log.info(
            f"[SR] {tf} {r_label}={act_r.price:.2f} (mesafe {act_r.price - px:.2f}) "
            f"trade={[round(l.price, 2) for l in snap.trade_resistances[:5]]}"
        )


def run_sr_analysis(
    bars: list[dict],
    price: float = 0,
    *,
    is_htf: bool = False,
) -> dict | None:
    """
    Tek S/R hesabi — strateji ve grafik ayni snapshot'tan.
    Donus: snapshot, trade_levels, chart_levels (primary henuz isaretlenmemis olabilir).
    """
    tf = "1h" if is_htf else "15m"
    p = sr_params_from_config(tf)
    need = min_bars_for_sr_params(p)
    n = len(bars)
    if n < need:
        log.warning(
            f"[SR] {tf} yetersiz mum n={n} (min {need} for L={p['lookback_left']} R={p['lookback_right']})"
        )
        return None

    snap = compute_sr_snapshot(bars, price=price, timeframe=tf)
    if snap is None:
        return None

    _log_sr_snapshot(snap)
    chart_levels = snapshot_chart_level_dicts(snap)
    # Pine: yalnizca level1..num_lines_to_show (TV ile ayni set)
    trade_levels = list(chart_levels)
    return {
        "snapshot": snap,
        "trade_levels": trade_levels,
        "chart_levels": chart_levels,
    }


def build_sr_level_dicts(
    bars: list[dict],
    price: float = 0,
    *,
    is_htf: bool = False,
) -> list[dict]:
    """Geriye uyumluluk: trade seviyeleri."""
    pkg = run_sr_analysis(bars, price, is_htf=is_htf)
    return list((pkg or {}).get("trade_levels") or [])


def build_sr_chart_level_dicts(
    bars: list[dict],
    price: float = 0,
    *,
    is_htf: bool = False,
    active_support: float = 0,
    active_resistance: float = 0,
) -> list[dict]:
    """Geriye uyumluluk: grafik cizgileri (tek snapshot)."""
    pkg = run_sr_analysis(bars, price, is_htf=is_htf)
    if not pkg:
        return []
    snap: SRSnapshot = pkg["snapshot"]
    if active_support or active_resistance:
        return snapshot_chart_level_dicts(
            snap,
            active_support=active_support,
            active_resistance=active_resistance,
        )
    return list(pkg.get("chart_levels") or [])


build_pine_sr_level_dicts = build_sr_level_dicts


def dedupe_sr_levels(levels: list[dict]) -> list[dict]:
    tol_pct = float(getattr(cfg, "V3_SR_MERGE_TOL_PCT", 0.0006) or 0.0006)
    out: list[dict] = []
    for kind in ("support", "resistance"):
        pool = sorted(
            [l for l in levels if str(l.get("kind") or "") == kind],
            key=lambda x: float(x.get("price", 0) or 0),
        )
        for lvl in pool:
            p = float(lvl.get("price", 0) or 0)
            if p <= 0:
                continue
            tol = max(p * tol_pct, 0.8)
            if any(
                str(o.get("kind")) == kind
                and abs(float(o.get("price", 0) or 0) - p) <= tol
                for o in out
            ):
                continue
            out.append(dict(lvl))
    return out


def nearest_sr_from_calculator(
    bars: list[dict],
    price: float,
    *,
    is_htf: bool = False,
) -> tuple:
    pkg = run_sr_analysis(bars, price, is_htf=is_htf)
    if not pkg:
        return None, None
    snap: SRSnapshot = pkg["snapshot"]
    return snap.active_support, snap.active_resistance


def find_sr_swing_candidates(
    bars: list[dict],
    *,
    is_htf: bool = False,
    current_price: float = 0,
) -> tuple[list[dict], list[dict]]:
    levels = build_sr_level_dicts(bars, current_price, is_htf=is_htf)
    highs = [s for s in levels if str(s.get("kind")) == "resistance"]
    lows = [s for s in levels if str(s.get("kind")) == "support"]
    return highs, lows
