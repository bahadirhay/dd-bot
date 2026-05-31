"""
engine/market_narrative.py — Giriş fazı: tanık, retest, çıkış sonrası yeniden arm.

Süre beklemek yerine fiyat/mum ile yapısal yenileme hesaplanır:
- tanık (seviye altına/üstüne dönüş)
- retest (seviyeye geri gelme)
- çıkış sonrası 15m mumlarda aynı kanıtlar
"""
from __future__ import annotations

import time
from typing import Any

from core.config import cfg
from core.logger import get_logger
from core.state import state

log = get_logger("Narrative")

_level_state: dict[str, dict[str, Any]] = {}
_session_start: float = 0.0


def _key(direction: str, level: float) -> str:
    return f"{direction}@{round(level, 2):.2f}"


def _break_threshold(level: float, direction: str) -> float:
    from engine.structure_thresholds import break_threshold_price

    return break_threshold_price(level, direction)


def _retest_price(level: float, direction: str) -> float:
    from engine.structure_thresholds import retest_zone_bps

    px = float(state.mark_price or state.price or 0)
    bps = retest_zone_bps(level, px)
    if direction == "LONG":
        return level * (1.0 + bps / 10000.0)
    return level * (1.0 - bps / 10000.0)


def _extension_bps(price: float, level: float, direction: str) -> float:
    if level <= 0 or price <= 0:
        return 0.0
    if direction == "LONG":
        return max(0.0, (price - level) / level * 10000.0)
    return max(0.0, (level - price) / level * 10000.0)


def _same_level(a: float, b: float) -> bool:
    if a <= 0 or b <= 0:
        return False
    return abs(a - b) / a < 0.003


def _is_sl_like(reason: str) -> bool:
    r = (reason or "").lower()
    return any(
        x in r
        for x in (
            "sl",
            "stop",
            "struct_break",
            "structural",
            "trend_reverse",
            "cvd_reverse",
            "stale",
        )
    )


def _is_tp_like(reason: str) -> bool:
    r = (reason or "").lower()
    return "tp" in r


def _analyze_bars(
    bars: list, direction: str, level: float
) -> tuple[int, bool]:
    if not bars or level <= 0:
        return 0, False
    thresh = _break_threshold(level, direction)
    retest_px = _retest_price(level, direction)
    lookback = int(getattr(cfg, "NARRATIVE_RETEST_BAR_LOOKBACK", 8))

    first_idx: int | None = None
    for i, b in enumerate(bars):
        close = float(b.get("close", 0))
        high = float(b.get("high", close))
        low = float(b.get("low", close))
        if direction == "LONG":
            if close > thresh or high > thresh:
                first_idx = i
                break
        else:
            if close < thresh or low < thresh:
                first_idx = i
                break

    bars_since = (len(bars) - 1 - first_idx) if first_idx is not None else 0

    retest_seen = False
    for b in bars[-lookback:]:
        low = float(b.get("low", 0))
        high = float(b.get("high", 0))
        if direction == "LONG" and low <= retest_px:
            retest_seen = True
            break
        if direction == "SHORT" and high >= retest_px:
            retest_seen = True
            break

    return bars_since, retest_seen


def _bars_rearm_since_exit(
    direction: str, level: float, exit_ts: float
) -> tuple[bool, str]:
    """Çıkıştan sonra 15m mumlarda tanık/retest var mı."""
    from engine.structure import get_bars_15m

    bars = get_bars_15m(32)
    if not bars:
        return False, ""

    thresh = _break_threshold(level, direction)
    retest_px = _retest_price(level, direction)
    since = [b for b in bars if float(b.get("ts", 0)) >= exit_ts - 60.0]
    if not since:
        since = bars[-4:]

    if direction == "LONG":
        for b in since:
            low = float(b.get("low", 0))
            if low <= retest_px:
                return True, "15m retest (çıkış sonrası)"
        for b in since:
            if float(b.get("low", 0)) < thresh:
                return True, "15m seviye altına dönüş (çıkış sonrası)"
        last = since[-1]
        if float(last.get("close", 0)) > thresh and any(
            float(b.get("low", 0)) < thresh for b in since[:-1]
        ):
            return True, "15m yeniden kırılım (çıkış sonrası)"
    else:
        for b in since:
            high = float(b.get("high", 0))
            if high >= retest_px:
                return True, "15m retest (çıkış sonrası)"
        for b in since:
            if float(b.get("high", 0)) > thresh:
                return True, "15m seviye üstüne dönüş (çıkış sonrası)"
        last = since[-1]
        if float(last.get("close", 0)) < thresh and any(
            float(b.get("high", 0)) > thresh for b in since[:-1]
        ):
            return True, "15m yeniden kırılım (çıkış sonrası)"

    return False, ""


def _rec(direction: str, level: float) -> dict[str, Any]:
    k = _key(direction, level)
    if k not in _level_state:
        _level_state[k] = {
            "phase": "UNKNOWN",
            "seen_reset_side": False,
            "retest_armed": False,
            "extension_bps": 0.0,
            "bars_since_break": 0,
            "level": level,
            "direction": direction,
        }
    return _level_state[k]


def reset_session() -> None:
    global _session_start
    _level_state.clear()
    _session_start = time.time()


def reconcile_startup(price: float, resistance: float, support: float) -> None:
    from engine.structure import get_bars_15m

    reset_session()
    if not getattr(cfg, "NARRATIVE_ENABLED", True):
        return

    bars = get_bars_15m(96)
    px = price if price > 0 else 0.0

    if resistance > 0:
        _bootstrap_level("LONG", resistance, px, bars)
    if support > 0:
        _bootstrap_level("SHORT", support, px, bars)

    state.market_narrative = {k: dict(v) for k, v in _level_state.items()}


def _bootstrap_level(
    direction: str, level: float, price: float, bars: list
) -> None:
    rec = _rec(direction, level)
    thresh = _break_threshold(level, direction)
    from engine.structure_thresholds import extension_progress, is_late_same_level_entry

    bar_min = int(getattr(cfg, "NARRATIVE_EXTENDED_MIN_BARS", 2))

    if direction == "LONG":
        outside = price > thresh if price > 0 else False
    else:
        outside = price < thresh if price > 0 else False

    ext_bps = _extension_bps(price, level, direction) if outside else 0.0
    bars_since, retest_hist = _analyze_bars(bars, direction, level)

    rec["extension_bps"] = round(ext_bps, 1)
    rec["bars_since_break"] = bars_since
    rec["seen_reset_side"] = False
    rec["retest_armed"] = retest_hist

    if not outside:
        rec["phase"] = "INSIDE"
    elif (
        is_late_same_level_entry(price, level, direction)[0]
        and bars_since >= bar_min
    ):
        rec["phase"] = "EXTENDED"
    else:
        rec["phase"] = "FRESH"

    log.info(
        f"[startup] {direction} seviye={level:.2f} faz={rec['phase']} "
        f"uzanti={ext_bps:.0f}bps bars_since={bars_since} "
        f"retest_hist={'evet' if retest_hist else 'hayır'}"
    )


def record_trade_exit(
    direction: str,
    reason: str,
    entry: float = 0.0,
    exit_px: float = 0.0,
    break_level: float = 0.0,
) -> None:
    """TP/SL/flip sonrası — aynı yön tekrar giriş için yapı sıfırlanır (süre değil)."""
    if not getattr(cfg, "NARRATIVE_ENABLED", True):
        return

    side = (direction or "").upper()
    pb = state.position_breakout or {}
    level = float(break_level or pb.get("break_level") or 0)

    if level <= 0:
        from engine.breakout import get_active_levels

        lv = get_active_levels()
        if side == "LONG":
            level = float(lv.get("resistance") or lv.get("support") or 0)
        elif side == "SHORT":
            level = float(lv.get("support") or lv.get("resistance") or 0)

    xp = float(exit_px or state.mark_price or state.price or 0)
    state.last_trade_exit = {
        "direction": side,
        "level": level,
        "reason": reason or "",
        "entry": float(entry or state.pos_entry or 0),
        "exit": xp,
        "ts": time.time(),
    }

    if level > 0 and side:
        rec = _rec(side, level)
        rec["seen_reset_side"] = False
        rec["retest_armed"] = False
        rec["phase"] = "POST_EXIT"
        rec["exit_reason"] = reason or ""
        ext = _extension_bps(xp or state.price, level, side)
        rec["extension_bps"] = round(ext, 1)

    log.info(
        f"[post-exit] {side} kapandı  seviye={level:.2f}  sebep={reason or '—'}  "
        f"exit={xp:.2f} — yeniden giriş yapısal hesapla"
    )
    state.market_narrative = {k: dict(v) for k, v in _level_state.items()}


def clear_trade_exit_context() -> None:
    state.last_trade_exit = {}


def _live_rearm(rec: dict[str, Any]) -> bool:
    return bool(rec.get("retest_armed") or rec.get("seen_reset_side"))


def _post_exit_allows(
    direction: str, level: float, price: float, rec: dict[str, Any]
) -> tuple[bool, str]:
    lx = state.last_trade_exit or {}
    if lx.get("direction") != direction or not _same_level(
        float(lx.get("level") or 0), level
    ):
        return True, ""

    if _live_rearm(rec):
        return True, ""

    exit_ts = float(lx.get("ts") or 0)
    bar_ok, bar_msg = _bars_rearm_since_exit(direction, level, exit_ts)
    if bar_ok:
        return True, bar_msg

    reason = str(lx.get("reason") or "")
    from engine.structure_thresholds import extension_progress, is_late_same_level_entry

    ext = _extension_bps(price, level, direction)
    prog = extension_progress(price, level, direction)

    if _is_sl_like(reason):
        return False, (
            f"SL/yapısal çıkış sonrası aynı {direction} — "
            f"seviye {level:.2f} retest veya yeniden kırılım gerekli (hesap)"
        )

    if _is_tp_like(reason) and is_late_same_level_entry(price, level, direction)[0]:
        return False, (
            f"TP sonrası geç kırılım (ilerleme {prog:.2f}, {ext:.0f}bps) — "
            f"seviye {level:.2f} retest olmadan aynı {direction} yok"
        )

    if _is_tp_like(reason):
        return False, (
            f"TP sonrası aynı {direction} — seviye {level:.2f} "
            f"yapısal teyit (retest/kırılım) gerekli"
        )

    return False, (
        f"Çıkış sonrası aynı {direction} @ {level:.2f} — "
        f"yapısal yenileme hesaplanmadı"
    )


def update_tick(price: float, resistance: float, support: float) -> None:
    if price <= 0 or not getattr(cfg, "NARRATIVE_ENABLED", True):
        return
    if resistance > 0:
        _tick_level("LONG", resistance, price)
    if support > 0:
        _tick_level("SHORT", support, price)
        _tick_level("LONG", support, price, touch_from_above=True)
    state.market_narrative = {k: dict(v) for k, v in _level_state.items()}


def _tick_level(
    direction: str,
    level: float,
    price: float,
    *,
    touch_from_above: bool = False,
) -> None:
    rec = _rec(direction, level)
    thresh = _break_threshold(level, direction)
    retest_px = _retest_price(level, direction)

    if touch_from_above and direction == "LONG":
        if price <= retest_px:
            rec["retest_armed"] = True
        if price < level * 0.9995:
            rec["seen_reset_side"] = True
        return

    if direction == "LONG":
        if price < thresh:
            rec["seen_reset_side"] = True
        if price <= retest_px:
            rec["retest_armed"] = True
    else:
        if price > thresh:
            rec["seen_reset_side"] = True
        if price >= retest_px:
            rec["retest_armed"] = True

    if rec.get("phase") in ("EXTENDED", "POST_EXIT") and rec.get("retest_armed"):
        rec["phase"] = "RETEST_ARMED"


def trade_entry_allowed(
    direction: str, level: float, price: float
) -> tuple[bool, str]:
    """Tüm girişler (kırılım, range, flip sonrası) — hesap tabanlı kapı."""
    if not getattr(cfg, "NARRATIVE_ENABLED", True):
        return True, ""
    if level <= 0 or price <= 0:
        return True, ""

    rec = _rec(direction, level)
    ok, msg = _post_exit_allows(direction, level, price, rec)
    if not ok:
        return False, msg

    thresh = _break_threshold(level, direction)
    outside = (direction == "LONG" and price > thresh) or (
        direction == "SHORT" and price < thresh
    )

    if not outside:
        if _live_rearm(rec):
            return True, ""
        lx = state.last_trade_exit or {}
        if lx.get("direction") == direction and _same_level(
            float(lx.get("level") or 0), level
        ):
            bar_ok, bar_msg = _bars_rearm_since_exit(
                direction, level, float(lx.get("ts") or 0)
            )
            if bar_ok:
                return True, bar_msg
            return False, (
                f"Range {direction} — çıkış sonrası seviye {level:.2f} "
                f"teyit yok (hesap)"
            )
        return True, ""

    return entry_allowed(direction, level, price)


def entry_allowed(direction: str, level: float, price: float) -> tuple[bool, str]:
    """Kırılım (band dışı) — tanık / retest / geç kırılım."""
    if level <= 0 or price <= 0:
        return True, ""

    rec = _rec(direction, level)
    thresh = _break_threshold(level, direction)

    if direction == "LONG" and price <= thresh:
        return True, ""
    if direction == "SHORT" and price >= thresh:
        return True, ""

    if _live_rearm(rec):
        return True, ""

    phase = rec.get("phase", "UNKNOWN")
    ext = float(rec.get("extension_bps", 0))
    if phase in ("EXTENDED", "POST_EXIT"):
        from engine.structure_thresholds import (
            extension_progress,
            is_late_same_level_entry,
        )

        late, late_msg = is_late_same_level_entry(price, level, direction)
        if late:
            prog = extension_progress(price, level, direction)
            return False, (
                f"Geç kırılım ({late_msg or f'ilerleme {prog:.2f}'}) — "
                f"seviye {level:.2f} retest/tanık gerekli"
            )

    return False, (
        f"Kırılım tanıklanmadı — seviye {level:.2f} "
        f"({'altı' if direction == 'LONG' else 'üstü'} / retest) görülmedi"
    )


def get_display_hint(direction: str, level: float) -> str:
    if not getattr(cfg, "NARRATIVE_ENABLED", True) or level <= 0:
        return ""
    rec = _rec(direction, level)
    phase = rec.get("phase", "")
    if rec.get("retest_armed"):
        return "RETEST ARM"
    if rec.get("seen_reset_side"):
        return "TANIK OK"
    if phase == "POST_EXIT":
        return "ÇIKIŞ SONRASI"
    if phase == "EXTENDED":
        return "RETEST BEKLE"
    if phase == "FRESH":
        return "TANIK BEKLE"
    return phase


def on_level_entered(direction: str, level: float) -> None:
    k = _key(direction, level)
    if k in _level_state:
        _level_state[k]["phase"] = "ENTERED"
        _level_state[k]["seen_reset_side"] = False
        _level_state[k]["retest_armed"] = False
    clear_trade_exit_context()
