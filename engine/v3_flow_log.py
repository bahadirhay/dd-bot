"""
engine/v3_flow_log.py

V3 karar hattinin tek satirlik degil, blok halinde ozet logu.
data/logs/v3_flow.log — bot.log gurultusunden ayri, 30 dk aralik (veya sinyal).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.config import cfg
from core.state import state, effective_price
from engine.v3_common import bars_15m

_last_flow_log_ts = 0.0
_flow_logger: logging.Logger | None = None


def _flow_logger_instance() -> logging.Logger:
    global _flow_logger
    if _flow_logger is not None:
        return _flow_logger
    Path(cfg.LOG_DIR).mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("V3Flow")
    log.setLevel(logging.INFO)
    log.propagate = False
    if not log.handlers:
        fh = logging.FileHandler(
            str(Path(cfg.LOG_DIR) / "v3_flow.log"),
            encoding="utf-8",
        )
        fh.setFormatter(logging.Formatter("%(message)s"))
        log.addHandler(fh)
    _flow_logger = log
    return log


def _tr_now() -> str:
    utc = datetime.now(timezone.utc)
    tr = utc + timedelta(hours=3)
    return tr.strftime("%Y-%m-%d %H:%M:%S TR")


def _range_side_diag(
    bars15: list[dict], levels: dict, side: str, cvd: dict
) -> str:
    from engine.levels_v3 import (
        cvd_supports_level,
        level_reliability,
        level_respect_now,
        level_tp_reliability,
    )

    support = float(levels.get("active_support") or 0)
    resistance = float(levels.get("active_resistance") or 0)
    side_u = str(side or "").upper()
    if side_u in ("BUY", "LONG"):
        level = support
        direction = "BUY"
    else:
        level = resistance
        direction = "SELL"

    if level <= 0:
        return f"{side_u}: seviye yok"

    historical = level_reliability(
        bars15, level, direction, support=support, resistance=resistance
    )
    recent = level_respect_now(
        bars15, level, direction, support=support, resistance=resistance
    )
    momentum = cvd_supports_level(cvd, direction)
    tp_ret = level_tp_reliability(bars15, support, resistance, side_u)

    k1 = "OK" if historical > 0 else "FAIL"
    k2 = "OK" if recent > 0.5 else "FAIL"
    k3 = "OK" if momentum > 0 else "FAIL"
    k4 = "OK" if historical > 0 and tp_ret > 0 else "FAIL"
    return (
        f"{side_u}: ret={historical:.0%} saygi={recent:.0%} "
        f"tp_ret={tp_ret:.0%} cvd={'ok' if momentum > 0 else 'karsi'} "
        f"[k1={k1} k2={k2} k3={k3} k4={k4}]"
    )


def _position_line() -> str:
    if not state.in_position:
        return "pozisyon: yok"
    pb = dict(state.position_breakout or {})
    side = str(pb.get("side") or pb.get("direction") or "?")
    entry = float(pb.get("entry_price") or pb.get("entry") or 0)
    th = pb.get("thesis") or {}
    if th:
        return (
            f"pozisyon: {side} @ {entry:.2f} | tez={th.get('scenario', '?')} "
            f"key={float(th.get('key_level') or 0):.2f} "
            f"inv={float(th.get('invalidation_price') or 0):.2f}"
        )
    return f"pozisyon: {side} @ {entry:.2f}"


def format_v3_flow_block(snap: dict, *, tag: str = "") -> str:
    levels = snap.get("levels") or {}
    structure = snap.get("structure") or {}
    cvd = snap.get("cvd") or {}
    scenario = snap.get("scenario") or {}
    entry = snap.get("entry") or {}

    px = float(
        levels.get("price")
        or effective_price()
        or state.mark_price
        or state.price
        or 0
    )
    s_px = float(levels.get("active_support") or 0)
    r_px = float(levels.get("active_resistance") or 0)
    zone = str(levels.get("zone") or "?")
    lock = "kilit" if levels.get("active_locked") else "acik"
    range_ok = "evet" if levels.get("range_valid") else "hayir"

    band_stab = str(scenario.get("band_stability") or "")
    if not band_stab and s_px > 0 and r_px > s_px:
        from engine.levels_v3 import band_is_stable

        bars15 = bars_15m(40)
        ok, reason = band_is_stable(bars15, s_px, r_px)
        band_stab = f"{'stabil' if ok else 'degil'} ({reason})"

    s1h = str(((structure.get("1h") or {}).get("direction")) or "?")
    scn = str(scenario.get("name") or "WAIT")
    scn_detail = str(scenario.get("detail") or "—")

    cvd_dir = str(cvd.get("direction") or "?")
    cvd_ok = "evet" if cvd.get("confirmed") else "hayir"
    cvd_cum = float(cvd.get("cumulative", 0) or 0)
    buy_r = float(cvd.get("buy_ratio", 0.5) or 0.5)

    action = str(snap.get("action") or "WAIT")
    reason = str(snap.get("reason") or "—")
    entry_ok = "evet" if entry.get("valid") else "hayir"
    sl_px = float(entry.get("sl", 0) or 0)
    tp_px = float(entry.get("tp2", 0) or 0)
    rr = float(entry.get("rr", 0) or 0)
    rr_tag = " onizleme" if entry.get("preview") and not entry.get("valid") else ""
    levels_line = f"SL={sl_px:.2f} TP={tp_px:.2f} " if sl_px > 0 and tp_px > 0 else ""

    bars15 = bars_15m(40)
    range_buy = _range_side_diag(bars15, levels, "BUY", cvd)
    range_sell = _range_side_diag(bars15, levels, "SELL", cvd)

    tag_s = f" [{tag}]" if tag else ""
    lines = [
        f"=== V3 AKIS @ {_tr_now()}{tag_s} ===",
        (
            f"px={px:.2f} | band {s_px:.2f}/{r_px:.2f} zone={zone} {lock} "
            f"range_valid={range_ok}"
        ),
        f"band_stabilite: {band_stab or '—'}",
        f"1h_yapi: {s1h} (bilgi, kapı degil)",
        f"senaryo: {scn} — {scn_detail}",
        f"range_kosul: {range_buy}",
        f"range_kosul: {range_sell}",
        (
            f"CVD: {cvd_dir} cum={cvd_cum:+.0f} alim={buy_r:.0%} teyit={cvd_ok}"
        ),
        f"giris: {entry_ok} {levels_line}RR={rr:.2f}{rr_tag}",
        f"karar: {action} | neden: {reason}",
        _position_line(),
        "",
    ]
    return "\n".join(lines)


def maybe_log_v3_flow(snap: dict, *, tag: str = "", force: bool = False) -> None:
    """30 dk veya LONG/SHORT sinyalinde v3_flow.log'a yazar."""
    if not getattr(cfg, "STRATEGY_V3_ENABLED", False):
        return

    global _last_flow_log_ts
    now = time.time()
    interval = float(getattr(cfg, "V3_FLOW_LOG_SEC", 1800) or 1800)
    action = str(snap.get("action") or "WAIT")
    signal = action in ("LONG", "SHORT")
    due = force or signal or (now - _last_flow_log_ts >= interval)
    if not due:
        return

    block = format_v3_flow_block(snap, tag=tag)
    _flow_logger_instance().info(block)
    _last_flow_log_ts = now
