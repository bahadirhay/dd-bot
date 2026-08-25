"""
engine/no_trade_log_v3.py — Pozisyon acilmama nedeni (tek satir, bot.log).

[V3] karar diag ve [ATTR] ile birlikte; burada primary_blocker + verdict ozetlenir.
"""
from __future__ import annotations

import time

from core.config import cfg
from core.logger import get_logger
from core.state import state, effective_price

log = get_logger("NoTradeV3")

_last_key = ""
_last_ts = 0.0


def _market_state_from_snap(snap: dict) -> dict:
    levels = snap.get("levels") or {}
    return (
        levels.get("market_state")
        or getattr(state, "v3_market_state", None)
        or {}
    )


def resolve_primary_blocker(snap: dict, attr: dict | None = None) -> str:
    """En baskin blok kodu (reject_reason > attribution > verdict > metin)."""
    rej = str(snap.get("reject_reason") or "").strip()
    if rej:
        return rej
    attr = attr or snap.get("attribution") or getattr(state, "v3_last_attribution", None) or {}
    pb = str(attr.get("reject_reason") or attr.get("primary_block") or "").strip()
    if pb:
        return pb.lower() if pb.isupper() and "_" in pb else pb

    ms = _market_state_from_snap(snap)
    tv = ms.get("trade_verdict") or {}
    if tv and not tv.get("allow_entry"):
        src = str(tv.get("source") or "verdict")
        if src == "collapse":
            return "collapse"
        if src == "execution_timing":
            return "verdict_timing"
        return src

    text = str(snap.get("reason") or "").lower()
    if "pozisyon acik" in text:
        return "position_open"
    if "band ortasinda" in text or "mid_range" in text:
        return "zone_mid"
    if "senaryo:" in text or "kosullar olusmadi" in text or "band yerlesmiyor" in text:
        return "scenario_wait"
    if "collapse" in text:
        return "collapse"
    if "cvd" in text:
        return "cvd"
    if "rr yetersiz" in text:
        return "rr"
    if "giris noktasi" in text:
        return "entry"
    if "expected move" in text or "em zayif" in text:
        return "expected_move"
    return "other"


def format_no_trade_line(snap: dict, *, primary: str = "") -> str:
    levels = snap.get("levels") or {}
    scenario = snap.get("scenario") or {}
    ms = _market_state_from_snap(snap)
    tv = ms.get("trade_verdict") or {}
    collapse = ms.get("collapse") or {}

    px = float(
        levels.get("price")
        or snap.get("price")
        or effective_price()
        or state.mark_price
        or 0
    )
    scn = str(scenario.get("name") or "WAIT")
    zone = str(levels.get("zone") or "?")
    verdict = str(tv.get("verdict") or "—")
    allow = "evet" if tv.get("allow_entry") else "hayir"
    mode = str(collapse.get("mode") or "—")
    reason = str(snap.get("reason") or "—")
    if len(reason) > 400:
        reason = reason[:397] + "..."

    primary = primary or resolve_primary_blocker(snap)
    rej = str(snap.get("reject_reason") or "")
    rej_tag = f" REJECT={rej}" if rej else ""
    return (
        f"[NO_TRADE] px={px:.2f} primary={primary}{rej_tag} "
        f"scn={scn} zone={zone} verdict={verdict} entry={allow} collapse={mode} | "
        f"{reason}"
    )


def maybe_log_no_trade(snap: dict, *, force: bool = False) -> None:
    if not getattr(cfg, "STRATEGY_V3_ENABLED", False):
        return
    if not getattr(cfg, "V3_NO_TRADE_LOG_ENABLED", True):
        return

    action = str(snap.get("action") or "WAIT").upper()
    if action != "WAIT":
        return

    attr = snap.get("attribution") or getattr(state, "v3_last_attribution", None) or {}
    primary = resolve_primary_blocker(snap, attr)
    ms = _market_state_from_snap(snap)
    tv = ms.get("trade_verdict") or {}
    verdict = str(tv.get("verdict") or "—")
    scn = str((snap.get("scenario") or {}).get("name") or "WAIT")

    key = f"{primary}|{scn}|{verdict}|{round(float(levels_px(snap)), 1)}"
    global _last_key, _last_ts
    now = time.time()
    interval = float(getattr(cfg, "V3_NO_TRADE_LOG_SEC", 60) or 60)
    periodic = (now - _last_ts) >= interval
    if not force and key == _last_key and not periodic:
        return

    _last_key = key
    _last_ts = now
    log.info(format_no_trade_line(snap, primary=primary))

    # Dashboard / sinyal kaydi icin kisa ozet
    short = str(snap.get("reason") or "")[:500]
    state.no_entry_reason = f"[{primary}] {short}" if short else f"[{primary}]"


def levels_px(snap: dict) -> float:
    levels = snap.get("levels") or {}
    return float(
        levels.get("price")
        or effective_price()
        or state.mark_price
        or 0
    )


def log_execute_block(
    blocker: str,
    detail: str,
    *,
    source: str = "",
    details: dict | None = None,
) -> None:
    """execute_entry oncesi/sonrasi engel (stale, gunluk limit, auto trade kapali)."""
    if details and details.get("v3_mode"):
        try:
            from engine.good_signal_stats_v3 import note_v3_execute_block

            note_v3_execute_block(details, source, blocker, detail)
        except Exception:
            pass
    if not getattr(cfg, "STRATEGY_V3_ENABLED", False):
        return
    if not getattr(cfg, "V3_NO_TRADE_LOG_ENABLED", True):
        return
    src = f" src={source}" if source else ""
    msg = f"[NO_TRADE] execute_block={blocker}{src} | {detail}"
    log.info(msg)
    state.no_entry_reason = f"[{blocker}] {detail}"
