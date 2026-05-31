"""
engine/thesis_v3.py — Pozisyon = tez (3 katman).

Katman 1 — Seviye: 15m kapanis yapısal invalidation disinda
Katman 2 — Momentum: CVD ters yon + confirmed + fiyat giris disinda
Katman 3 — Zaman: N x 15m sonra beklenen ilerleme yok

Herhangi biri -> tez bitti (15m kapanis aninda degerlendirilir; anlik fiyat degil).
"""
from __future__ import annotations

import time
from typing import Any

from core.config import cfg
from core.state import state
from core.logger import get_logger
from engine.structure_thresholds import break_threshold_price

log = get_logger("ThesisV3")


def _side_from_scenario(scenario: str, direction: str = "") -> str:
    d = (direction or "").upper()
    if d in ("LONG", "SHORT"):
        return d
    scn = (scenario or "").upper()
    if "BUY" in scn or scn == "RANGE_BUY":
        return "LONG"
    if "SELL" in scn or scn == "RANGE_SELL":
        return "SHORT"
    return str(state.pos_side or "").upper()


def build_thesis(details: dict, *, price: float = 0) -> dict[str, Any]:
    """Pozisyon acilirken tez — seviye + momentum + zaman katmanlari."""
    scenario = str(details.get("v3_scenario") or details.get("scenario") or "")
    direction = str(details.get("direction") or "").upper()
    support = float(details.get("v3_support") or details.get("entry_support") or 0)
    resistance = float(details.get("v3_resistance") or details.get("entry_resistance") or 0)
    break_level = float(details.get("break_level") or details.get("range_active_level") or 0)
    entry_px = float(
        price
        or details.get("price")
        or details.get("signal_price")
        or state.pos_entry
        or state.mark_price
        or state.price
        or 0
    )
    side = _side_from_scenario(scenario, direction)
    stale_bars = int(getattr(cfg, "V3_THESIS_STALE_BARS", 8) or 8)
    min_prog = float(getattr(cfg, "V3_THESIS_MIN_PROGRESS", 0.003) or 0.003)
    inv_cvd = "BEAR" if side == "LONG" else "BULL"

    if scenario.startswith("BREAKOUT_") or "BREAKOUT" in scenario:
        if side == "LONG" or "BUY" in scenario:
            key = break_level or resistance
            inv = break_threshold_price(key, "SHORT", entry_px) if key > 0 else 0.0
            return _pack(
                "BREAKOUT_BUY", key, inv, "close_below_invalidation",
                side="LONG", entry_px=entry_px, inv_cvd=inv_cvd,
                stale_bars=stale_bars, min_prog=min_prog,
            )
        key = break_level or support
        inv = break_threshold_price(key, "LONG", entry_px) if key > 0 else 0.0
        return _pack(
            "BREAKOUT_SELL", key, inv, "close_above_invalidation",
            side="SHORT", entry_px=entry_px, inv_cvd=inv_cvd,
            stale_bars=stale_bars, min_prog=min_prog,
        )

    if scenario == "RANGE_BUY" or (side == "LONG" and scenario != "RANGE_SELL"):
        key = support or break_level
        inv = break_threshold_price(key, "SHORT", entry_px) if key > 0 else 0.0
        return _pack(
            scenario or "RANGE_BUY", key, inv, "close_below_invalidation",
            side="LONG", entry_px=entry_px, inv_cvd=inv_cvd,
            stale_bars=stale_bars, min_prog=min_prog,
        )

    key = resistance or break_level
    inv = break_threshold_price(key, "LONG", entry_px) if key > 0 else 0.0
    return _pack(
        scenario or "RANGE_SELL", key, inv, "close_above_invalidation",
        side="SHORT", entry_px=entry_px, inv_cvd=inv_cvd,
        stale_bars=stale_bars, min_prog=min_prog,
    )


def rebuild_thesis_from_position(pb: dict, side: str, entry_px: float) -> dict[str, Any]:
    """Restart — senaryo + S/R anchor'dan tez; bar sayisi pos_open_ts'den."""
    scenario = str(pb.get("scenario") or "")
    support = float(pb.get("entry_support") or pb.get("active_support") or 0)
    resistance = float(pb.get("entry_resistance") or pb.get("active_resistance") or 0)
    break_level = float(pb.get("break_level") or 0)
    side = (side or "").upper()
    entry_px = float(entry_px or state.pos_entry or 0)

    if scenario in ("", "RESTORED_POSITION", "RESTORE", "WAIT"):
        if side == "LONG":
            scenario = "BREAKOUT_BUY" if resistance > 0 and entry_px >= resistance * 0.998 else "RANGE_BUY"
        elif side == "SHORT":
            scenario = "BREAKOUT_SELL" if support > 0 and entry_px <= support * 1.002 else "RANGE_SELL"

    thesis = build_thesis(
        {
            "v3_scenario": scenario,
            "direction": side,
            "v3_support": support,
            "v3_resistance": resistance,
            "break_level": break_level,
            "price": entry_px,
        },
        price=entry_px,
    )
    thesis["bars_elapsed"] = _bars_since_open(thesis)
    return thesis


def _bars_since_open(thesis: dict) -> int:
    opened = float(thesis.get("opened_ts") or state.pos_open_ts or 0)
    if opened <= 0:
        return int(thesis.get("bars_elapsed") or 0)
    return max(0, int((time.time() - opened) / 900))


def _level_failed(thesis: dict, close_15m: float) -> bool:
    inv = float(thesis.get("invalidation_price") or 0)
    if inv <= 0 or close_15m <= 0:
        return False
    cond = str(thesis.get("invalidation_condition") or "")
    if cond in ("close_below_invalidation", "close_below_key_level"):
        return close_15m < inv
    if cond in ("close_above_invalidation", "close_above_key_level"):
        return close_15m > inv
    return False


def _momentum_failed(thesis: dict, close_15m: float, cvd: dict | None) -> bool:
    """CVD ters yon + confirmed + 15m kapanis giris disinda."""
    if close_15m <= 0:
        return False
    cvd = cvd or {}
    if not cvd.get("confirmed"):
        return False
    inv_cvd = str(thesis.get("invalidation_cvd") or "")
    cvd_dir = str(cvd.get("direction") or "")
    if cvd_dir != inv_cvd:
        return False
    entry = float(thesis.get("entry_price") or 0)
    if entry <= 0:
        return False
    side = _side_from_scenario(str(thesis.get("scenario") or ""))
    if side == "LONG":
        return close_15m < entry
    if side == "SHORT":
        return close_15m > entry
    return False


def _stale_failed(thesis: dict, close_15m: float) -> bool:
    """N x 15m sonra beklenen ilerleme yok."""
    bars = int(thesis.get("bars_elapsed") or 0)
    max_bars = int(thesis.get("invalidation_bars") or 8)
    min_prog = float(thesis.get("min_progress") or 0.003)
    entry = float(thesis.get("entry_price") or 0)
    if bars < max_bars or entry <= 0 or close_15m <= 0:
        return False
    side = _side_from_scenario(str(thesis.get("scenario") or ""))
    if side == "LONG":
        prog = (close_15m - entry) / entry
        return prog < min_prog
    if side == "SHORT":
        prog = (entry - close_15m) / entry
        return prog < min_prog
    return False


def evaluate_thesis_failure(
    thesis: dict | None,
    close_15m: float,
    *,
    cvd: dict | None = None,
) -> tuple[bool, str]:
    """15m kapanis: katmanlardan biri tetiklendi mi? -> (failed, reason)."""
    if not thesis or close_15m <= 0:
        return False, ""

    if _level_failed(thesis, close_15m):
        return True, "thesis_failed_level"

    if _momentum_failed(thesis, close_15m, cvd):
        return True, "thesis_failed_cvd"

    if _stale_failed(thesis, close_15m):
        return True, "thesis_failed_stale"

    return False, ""


def thesis_failed(thesis: dict | None, close_15m: float, *, cvd: dict | None = None) -> bool:
    failed, _ = evaluate_thesis_failure(thesis, close_15m, cvd=cvd)
    return failed


def _pack(
    scenario: str,
    key_level: float,
    invalidation_price: float,
    condition: str,
    *,
    side: str,
    entry_px: float,
    inv_cvd: str,
    stale_bars: int,
    min_prog: float,
) -> dict[str, Any]:
    return {
        "scenario": scenario,
        "side": side,
        "key_level": round(key_level, 2) if key_level > 0 else 0.0,
        "entry_price": round(entry_px, 2) if entry_px > 0 else 0.0,
        "invalidation_price": round(invalidation_price, 2) if invalidation_price > 0 else 0.0,
        "invalidation_condition": condition,
        "invalidation_cvd": inv_cvd,
        "invalidation_bars": stale_bars,
        "min_progress": min_prog,
        "opened_ts": time.time(),
        "bars_elapsed": 0,
    }


def _upgrade_thesis(thesis: dict) -> dict[str, Any]:
    """Eski tek-katmanli tez kaydini tam modele genislet."""
    if thesis.get("invalidation_cvd") and thesis.get("invalidation_bars"):
        return thesis
    side = _side_from_scenario(
        str(thesis.get("scenario") or ""),
        str(thesis.get("side") or state.pos_side or ""),
    )
    entry = float(
        thesis.get("entry_price") or state.pos_entry or 0
    )
    stale_bars = int(getattr(cfg, "V3_THESIS_STALE_BARS", 8) or 8)
    min_prog = float(getattr(cfg, "V3_THESIS_MIN_PROGRESS", 0.003) or 0.003)
    thesis.setdefault("side", side)
    thesis.setdefault("entry_price", round(entry, 2) if entry > 0 else 0.0)
    thesis.setdefault("invalidation_cvd", "BEAR" if side == "LONG" else "BULL")
    thesis.setdefault("invalidation_bars", stale_bars)
    thesis.setdefault("min_progress", min_prog)
    thesis.setdefault("opened_ts", state.pos_open_ts or time.time())
    thesis.setdefault("bars_elapsed", _bars_since_open(thesis))
    return thesis


def ensure_thesis(pb: dict | None = None) -> dict[str, Any]:
    pb = dict(pb or state.position_breakout or {})
    thesis = pb.get("thesis")
    if thesis and float(thesis.get("invalidation_price") or 0) > 0:
        thesis = _upgrade_thesis(dict(thesis))
    else:
        thesis = rebuild_thesis_from_position(
            pb, str(state.pos_side or pb.get("direction") or ""), state.pos_entry
        )
    pb["thesis"] = thesis
    state.position_breakout = pb
    return thesis


def _advance_thesis_bar(thesis: dict) -> dict[str, Any]:
    thesis = dict(thesis)
    thesis["bars_elapsed"] = int(thesis.get("bars_elapsed") or 0) + 1
    return thesis


def _format_fail_log(thesis: dict, close_15m: float, reason: str, cvd: dict | None) -> str:
    scn = thesis.get("scenario", "?")
    key = float(thesis.get("key_level") or 0)
    inv = float(thesis.get("invalidation_price") or 0)
    entry = float(thesis.get("entry_price") or 0)
    bars = int(thesis.get("bars_elapsed") or 0)
    cvd = cvd or {}
    if reason == "thesis_failed_level":
        return (
            f"Tez bitti [katman=seviye] {scn}: 15m kapanis {close_15m:.2f} "
            f"key={key:.2f} invalidation={inv:.2f}"
        )
    if reason == "thesis_failed_cvd":
        return (
            f"Tez bitti [katman=momentum] {scn}: CVD {cvd.get('direction')} teyit=evet "
            f"kapanis {close_15m:.2f} vs giris {entry:.2f}"
        )
    return (
        f"Tez bitti [katman=zaman] {scn}: {bars} x 15m ilerleme yetersiz "
        f"kapanis {close_15m:.2f} giris {entry:.2f} min={float(thesis.get('min_progress') or 0):.2%}"
    )


async def check_thesis_on_15m_close(executor, close_15m: float) -> bool:
    """15m kapanis: 3 katmanli tez kontrolu. True = pozisyon kapandi."""
    if not state.in_position or close_15m <= 0:
        return False
    pb = dict(state.position_breakout or {})
    if str(pb.get("strategy") or pb.get("entry_mode") or "") != "v3":
        return False

    thesis = ensure_thesis(pb)
    thesis = _advance_thesis_bar(thesis)
    pb["thesis"] = thesis
    state.position_breakout = pb

    cvd = dict(state.v3_cvd or {})
    failed, reason = evaluate_thesis_failure(thesis, close_15m, cvd=cvd)
    if not failed:
        return False

    log.info(_format_fail_log(thesis, close_15m, reason, cvd))
    await executor.close_position(reason)
    return True
