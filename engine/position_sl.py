"""
engine/position_sl.py — Açık pozisyon SL (profil bazlı).

Profiller:
  break_retest — kırılım sonrası retest rafı (~2054), impulsif düşüş
  swing_trail  — 15m swing / yapı takibi (range dışı, trend, retest yok)
  range_band   — range girişi: bant kenarı invalidation
  runner       — TP1 sonrası (ayrı fonksiyon)
"""
from __future__ import annotations

import time
from typing import Literal

from core.config import cfg
from core.state import state
from core.logger import get_logger

log = get_logger("PosSL")

SlProfile = Literal["break_retest", "swing_trail", "range_band", "runner"]


def _mark() -> float:
    return float(
        state.mark_price or state.price or state.bid or state.ask or 0
    )


def _levels() -> tuple[float, float]:
    from engine.breakout import get_active_levels

    px = _mark()
    lv = get_active_levels(px)
    pb = state.position_breakout or {}
    s = float(
        lv.get("invalidation_support")
        or lv.get("support")
        or pb.get("structural_support")
        or pb.get("break_level")
        or 0
    )
    r = float(
        lv.get("resistance")
        or pb.get("structural_resistance")
        or pb.get("active_resistance")
        or 0
    )
    return s, r


def _sl_tighter(side: str, new_sl: float, old_sl: float) -> bool:
    if old_sl <= 0 or new_sl <= 0:
        return True
    if side == "SHORT":
        return new_sl < old_sl
    return new_sl > old_sl


def _sl_valid_trigger(side: str, sl: float, mark: float) -> bool:
    if sl <= 0 or mark <= 0:
        return False
    buf = float(getattr(cfg, "SL_LOCK_MARK_BUFFER_BPS", 10)) / 10000.0
    if side == "SHORT":
        return sl > mark * (1.0 + buf)
    return sl < mark * (1.0 - buf)


def _recent_retest_high(mark: float, entry: float) -> float:
    try:
        from engine.structure import get_bars_15m

        n = int(getattr(cfg, "SL_LOCK_RETEST_BARS_15M", 48))
        bars = get_bars_15m(n)
    except Exception:
        return 0.0
    best = 0.0
    for c in bars:
        try:
            hi = float(c.get("high", 0))
        except (TypeError, ValueError):
            continue
        if hi > mark * 1.0005 and (entry <= 0 or hi < entry * 0.9998):
            best = max(best, hi)
    return round(best, 2) if best > 0 else 0.0


def _recent_retest_low(mark: float, entry: float) -> float:
    try:
        from engine.structure import get_bars_15m

        n = int(getattr(cfg, "SL_LOCK_RETEST_BARS_15M", 48))
        bars = get_bars_15m(n)
    except Exception:
        return 0.0
    lows: list[float] = []
    for c in bars:
        try:
            lo = float(c.get("low", 0))
        except (TypeError, ValueError):
            continue
        if lo < mark * 0.9995 and (entry <= 0 or lo > entry * 1.0002):
            lows.append(lo)
    return round(max(lows), 2) if lows else 0.0


def has_retest_shelf(side: str, mark: float, entry: float) -> bool:
    """Mark–giriş arasında anlamlı retest rafı var mı?"""
    if entry <= 0 or mark <= 0:
        return False
    if side == "SHORT":
        if mark >= entry * 0.999:
            return False
        return _recent_retest_high(mark, entry) > mark
    if mark <= entry * 1.001:
        return False
    return _recent_retest_low(mark, entry) > 0


def resolve_sl_profile(side: str, entry: float, mark: float) -> SlProfile:
    """
    Girişte kayıtlı profil + canlı koşul → hangi SL kilidi kullanılacak.
    Restart: position_breakout boş olsa bile retest rafı varsa break_retest.
    """
    forced = str(getattr(cfg, "SL_LOCK_PROFILE", "auto") or "auto").lower()
    if forced in ("break_retest", "swing_trail", "range_band"):
        if forced == "break_retest" and not has_retest_shelf(side, mark, entry):
            return "swing_trail"
        return forced  # type: ignore[return-value]

    pb = state.position_breakout or {}
    tagged = str(pb.get("sl_profile") or "").lower()
    entry_mode = str(pb.get("entry_mode") or "").lower()

    if entry_mode == "range" or pb.get("range_mode"):
        return "range_band"

    if tagged == "range_band":
        return "range_band"
    if tagged == "break_retest" and has_retest_shelf(side, mark, entry):
        return "break_retest"
    if tagged == "swing_trail":
        return "swing_trail"

    if has_retest_shelf(side, mark, entry):
        return "break_retest"
    if pb.get("break_mode") or float(pb.get("break_level") or 0) > 0:
        return "swing_trail"
    return "swing_trail"


def restore_position_context(trade_id: int = 0) -> None:
    """Restart: DB + aktif seviyelerden position_breakout doldur (SL profili için)."""
    pb = dict(state.position_breakout or {})
    notes = ""
    if trade_id > 0:
        try:
            from botlog.db import get_trade_levels

            row = get_trade_levels(trade_id)
            if row:
                notes = str(row.get("notes") or "").lower()
        except Exception:
            pass

    entry = float(state.pos_entry or 0)
    side = state.pos_side

    if not pb.get("entry_mode"):
        if "range" in notes:
            pb["entry_mode"] = "range"
            pb["range_mode"] = True
            pb["sl_profile"] = "range_band"
        elif any(x in notes for x in ("kırılım", "kirilim", "break")):
            pb["entry_mode"] = "break"
            pb["break_mode"] = True
            pb["sl_profile"] = "break_retest"
        elif side and entry > 0:
            pb["entry_mode"] = "break"
            pb["break_mode"] = True
            pb["sl_profile"] = "break_retest"

    if not float(pb.get("break_level") or 0):
        from engine.breakout import _infer_break_level_long, _infer_break_level_short

        sl = float(state.pos_sl or lv.get("sl") or 0)
        if side == "LONG":
            bl = _infer_break_level_long(entry, sl)
            if bl > 0:
                pb["break_level"] = bl
        elif side == "SHORT":
            bl = _infer_break_level_short(entry, sl)
            if bl > 0:
                pb["break_level"] = bl

    from engine.breakout import get_active_levels

    lv = get_active_levels()
    pb["range_support"] = float(
        pb.get("range_support") or lv.get("support") or pb.get("break_level") or 0
    )
    pb["range_resistance"] = float(
        pb.get("range_resistance") or lv.get("resistance") or 0
    )
    if pb.get("break_level"):
        pb["structural_support"] = float(pb["break_level"])
        pb["break_mode"] = True
    state.position_breakout = pb
    try:
        from engine.breakout import _channel_swing_bounds, _sync_channel_levels_from_swings

        mark = float(state.mark_price or state.price or entry or 0)
        swing_r, swing_s = _channel_swing_bounds(mark)
        if swing_r > 0 or swing_s > 0:
            _sync_channel_levels_from_swings(swing_r, swing_s, pb)
    except Exception:
        pass


def _apply_short_sl(
    level: float,
    src: str,
    entry: float,
    mark: float,
    profile: str,
) -> float:
    buf_bps = float(getattr(cfg, "SL_LOCK_BUFFER_BPS", 12))
    mark_buf = float(getattr(cfg, "SL_LOCK_MARK_BUFFER_BPS", 10)) / 10000.0
    sl = level * (1.0 + buf_bps / 10000.0)
    sl = max(sl, mark * (1.0 + mark_buf))
    if entry > 0:
        lock_bps = float(getattr(cfg, "SL_LOCK_MIN_LOCKED_BPS", 80))
        if profile == "break_retest":
            sl = min(sl, entry * (1.0 - lock_bps / 10000.0))
        else:
            sl = min(sl, entry - entry * 0.0002)
    sl_r = round(sl, 2)
    _log_sl_lock_if_changed(sl_r, mark, f"[{profile}] {src} {level:.2f}")
    return sl_r


def _apply_long_sl(
    level: float,
    src: str,
    entry: float,
    mark: float,
    profile: str,
) -> float:
    buf_bps = float(getattr(cfg, "SL_LOCK_BUFFER_BPS", 12))
    mark_buf = float(getattr(cfg, "SL_LOCK_MARK_BUFFER_BPS", 10)) / 10000.0
    sl = level * (1.0 - buf_bps / 10000.0)
    sl = min(sl, mark * (1.0 - mark_buf))
    if entry > 0:
        lock_bps = float(getattr(cfg, "SL_LOCK_MIN_LOCKED_BPS", 80))
        if profile == "break_retest":
            sl = max(sl, entry * (1.0 + lock_bps / 10000.0))
        else:
            sl = max(sl, entry + entry * 0.0002)
    sl_r = round(sl, 2)
    _log_sl_lock_if_changed(sl_r, mark, f"[{profile}] {src} {level:.2f}")
    return sl_r


def _nearest_retest_level(side: str, mark: float, entry: float) -> tuple[float, str]:
    """Kırılım: mark–giriş arası en yakın retest rafı."""
    support, resistance = _levels()
    pb = state.position_breakout or {}
    entry_v = float(state.pos_entry or entry or mark)
    mark_buf = mark * (1.0 + float(getattr(cfg, "SL_LOCK_MARK_BUFFER_BPS", 10)) / 10000.0)
    candidates: list[tuple[float, str]] = []

    def _add(val: float, name: str) -> None:
        if val <= mark_buf:
            return
        if entry_v > 0 and val >= entry_v * 0.9998:
            return
        candidates.append((val, name))

    if side == "SHORT":
        retest = _recent_retest_high(mark, entry_v)
        if retest > 0:
            _add(retest, "15m_retest_raf")
        if not candidates:
            return 0.0, ""
        candidates.sort(key=lambda x: x[0])
        return candidates[0]

    retest = _recent_retest_low(mark, entry_v)
    if retest > 0:
        lo_ceil = mark * (1.0 - float(getattr(cfg, "SL_LOCK_MARK_BUFFER_BPS", 10)) / 10000.0)
        if retest < lo_ceil:
            candidates.append((retest, "15m_retest_raf"))
    if not candidates:
        return 0.0, ""
    candidates.sort(key=lambda x: -x[0])
    return candidates[0]


def _nearest_swing_level(side: str, mark: float) -> tuple[float, str]:
    """Swing / yapı: mark üstündeki (SHORT) veya altındaki (LONG) en yakın swing."""
    from engine.breakout import get_active_levels

    support, resistance = _levels()
    lv = get_active_levels(mark)
    pb = state.position_breakout or {}
    mark_buf_short = mark * (1.0 + float(getattr(cfg, "SL_LOCK_MARK_BUFFER_BPS", 10)) / 10000.0)
    mark_buf_long = mark * (1.0 - float(getattr(cfg, "SL_LOCK_MARK_BUFFER_BPS", 10)) / 10000.0)
    candidates: list[tuple[float, str]] = []

    if side == "SHORT":
        for h in state.swing_highs_15m or []:
            p = float(h.get("price", 0) if isinstance(h, dict) else h)
            if p > mark_buf_short:
                candidates.append((p, "swing_tepe"))
        for val, name in (
            (float(pb.get("active_resistance") or 0), "aktif_direnç"),
            (float(pb.get("structural_resistance") or 0), "direnç_yapı"),
            (resistance, "swing_direnç"),
        ):
            if val > mark_buf_short:
                candidates.append((val, name))
        if not candidates:
            return 0.0, ""
        candidates.sort(key=lambda x: x[0])
        return candidates[0]

    for l in state.swing_lows_15m or []:
        p = float(l.get("price", 0) if isinstance(l, dict) else l)
        if 0 < p < mark_buf_long:
            candidates.append((p, "swing_dip"))
    display_s = float(lv.get("active_support") or 0)
    for val, name in (
        (display_s, "pullback_destek"),
        (float(pb.get("structural_support") or 0), "destek_yapı"),
        (support, "invalidasyon"),
    ):
        if 0 < val < mark_buf_long:
            candidates.append((val, name))
    if not candidates:
        return 0.0, ""
    candidates.sort(key=lambda x: -x[0])
    return candidates[0]


def _range_invalidation_level(side: str) -> tuple[float, str]:
    """Range: SHORT → direnç üstü, LONG → destek altı."""
    support, resistance = _levels()
    pb = state.position_breakout or {}
    if side == "SHORT":
        level = float(
            pb.get("range_resistance")
            or pb.get("active_resistance")
            or resistance
            or pb.get("break_level")
            or 0
        )
        return level, "range_direnç"
    level = float(
        pb.get("range_support")
        or pb.get("active_support")
        or support
        or pb.get("break_level")
        or 0
    )
    return level, "range_destek"


def break_retest_sl_lock_price(side: str, entry: float, mark: float) -> float:
    level, src = _nearest_retest_level(side, mark, entry)
    if level <= 0:
        return swing_trail_sl_lock_price(side, entry, mark)
    if side == "SHORT":
        return _apply_short_sl(level, src, entry, mark, "break_retest")
    return _apply_long_sl(level, src, entry, mark, "break_retest")


def swing_trail_sl_lock_price(side: str, entry: float, mark: float) -> float:
    level, src = _nearest_swing_level(side, mark)
    buf_bps = float(getattr(cfg, "SL_LOCK_BUFFER_BPS", 12))
    mark_buf = float(getattr(cfg, "SL_LOCK_MARK_BUFFER_BPS", 10)) / 10000.0
    if level <= 0:
        if side == "SHORT":
            sl = mark * (1.0 + mark_buf + buf_bps / 10000.0)
        else:
            sl = mark * (1.0 - mark_buf - buf_bps / 10000.0)
        sl_r = round(sl, 2)
        _log_sl_lock_if_changed(sl_r, mark, "[swing_trail] mark+buf")
        return sl_r
    if side == "SHORT":
        return _apply_short_sl(level, src, entry, mark, "swing_trail")
    return _apply_long_sl(level, src, entry, mark, "swing_trail")


def range_band_sl_lock_price(side: str, entry: float, mark: float) -> float:
    level, src = _range_invalidation_level(side)
    if level <= 0:
        return swing_trail_sl_lock_price(side, entry, mark)
    if side == "SHORT":
        return _apply_short_sl(level, src, entry, mark, "range_band")
    return _apply_long_sl(level, src, entry, mark, "range_band")


def structural_sl_lock_price(side: str, entry: float, mark: float) -> float:
    """Profil seçimi → ilgili SL kilidi."""
    profile = resolve_sl_profile(side, entry, mark)
    pb = dict(state.position_breakout or {})
    if pb.get("sl_profile") != profile:
        pb["sl_profile"] = profile
        state.position_breakout = pb

    if profile == "break_retest":
        return break_retest_sl_lock_price(side, entry, mark)
    if profile == "range_band":
        return range_band_sl_lock_price(side, entry, mark)
    return swing_trail_sl_lock_price(side, entry, mark)


def sl_lock_reason_tag(side: str, entry: float, mark: float) -> str:
    p = resolve_sl_profile(side, entry, mark)
    labels = {
        "break_retest": "kırılım retest rafı",
        "swing_trail": "swing takip",
        "range_band": "range bant",
    }
    return labels.get(p, p)


def _log_sl_lock_if_changed(sl: float, mark: float, detail: str) -> None:
    cur = float(state.pos_sl or 0)
    if cur > 0 and abs(sl - cur) < 0.5:
        return
    log.info(f"SL kilidi ({detail}): {sl:.2f}  mark={mark:.2f}")


def in_profit_min_bps(side: str, entry: float, mark: float) -> bool:
    need = float(getattr(cfg, "SL_LOCK_MIN_PROFIT_BPS", 12))
    if entry <= 0 or mark <= 0:
        return False
    if side == "SHORT":
        return (entry - mark) / entry * 10000.0 >= need
    return (mark - entry) / entry * 10000.0 >= need


def pre_tp1_structural_lock_enabled() -> bool:
    """
    TP1 öncesi swing_trail / break_retest kâr kilidi kapalı mı?

    Varsayılan: kapalı — borsada yalnızca girişteki invalidation SL (pos_sl_initial).
    TP1 sonrası runner_sl_after_tp1 ayrı dalda çalışır.
    """
    return bool(getattr(cfg, "SL_STRUCTURAL_PRE_TP1", False))


def sl_replace_allowed_vs_initial(side: str, new_sl: float) -> bool:
    """
    TP1 öncesi SL yalnızca yapısal invalidation seviyesinde kalabilir.
    LONG: new_sl > initial → girişe yakın kâr kilidi (yasak).
    SHORT: new_sl < initial → girişe yakın kâr kilidi (yasak).
    """
    if state.pos_tp1_hit:
        return True
    initial = float(state.pos_sl_initial or state.pos_sl or 0)
    if initial <= 0 or new_sl <= 0:
        return True
    tol = max(initial * 0.00005, 0.01)
    if side == "LONG":
        if new_sl > initial + tol:
            log.info(
                f"SL güncelleme ertelendi (TP1 öncesi): {new_sl:.2f} > "
                f"invalidation {initial:.2f} — kâr kilidi TP1 sonrası"
            )
            return False
        return True
    if new_sl < initial - tol:
        log.info(
            f"SL güncelleme ertelendi (TP1 öncesi): {new_sl:.2f} < "
            f"invalidation {initial:.2f} — kâr kilidi TP1 sonrası"
        )
        return False
    return True


def initial_trail_sl_at_tp1(side: str, tp1: float, mark: float) -> float:
    """TP1 sonrası ilk SL: TP1 seviyesi (isteğe bağlı küçük buffer)."""
    if tp1 <= 0:
        return 0.0
    mark_buf = float(getattr(cfg, "SL_LOCK_MARK_BUFFER_BPS", 10)) / 10000.0
    buf_bps = float(getattr(cfg, "TRAIL_SL_TP1_BUFFER_BPS", 0))
    side = (side or "").upper()
    if side == "SHORT":
        sl = tp1 * (1.0 + buf_bps / 10000.0) if buf_bps > 0 else tp1
        if mark > 0:
            sl = max(sl, mark * (1.0 + mark_buf))
        return round(sl, 2)
    sl = tp1 * (1.0 - buf_bps / 10000.0) if buf_bps > 0 else tp1
    if mark > 0:
        sl = min(sl, mark * (1.0 - mark_buf))
    return round(sl, 2)


def tp1_15m_close_confirmed(side: str, close_15m: float, tp1: float) -> bool:
    """TP1 kirilim onayi: LONG close>TP1, SHORT close<TP1 (+ buffer)."""
    return tp1_break_confirmed(side, close_15m, tp1)


def tp1_break_confirmed(side: str, close_px: float, tp1: float) -> bool:
    """
    TP1 sonrasi onay (15m veya 5m kapanis).
    LONG: close > TP1 (+ buffer); SHORT: close < TP1 (- buffer).
    """
    if close_px <= 0 or tp1 <= 0:
        return False
    if not getattr(cfg, "TP1_CONFIRM_15M", True):
        return True
    buf = float(getattr(cfg, "TP1_CONFIRM_BUFFER_BPS", 0)) / 10000.0
    side = (side or "").upper()
    if side == "LONG":
        return close_px > tp1 * (1.0 + buf)
    if side == "SHORT":
        return close_px < tp1 * (1.0 - buf)
    return False


def trailing_sl_from_15m_close(
    side: str, close_15m: float, current_sl: float
) -> float:
    """
    15m kapanışına göre SL adayı.
    LONG: close > current_sl → close; aksi halde 0.
    SHORT: close < current_sl → close; aksi halde 0.
    """
    if close_15m <= 0 or current_sl <= 0:
        return 0.0
    side = (side or "").upper()
    if side == "LONG":
        if close_15m > current_sl:
            return round(close_15m, 2)
        return 0.0
    if side == "SHORT":
        if close_15m < current_sl:
            return round(close_15m, 2)
        return 0.0
    return 0.0


def runner_sl_after_tp1(side: str, entry: float, tp1: float, mark: float) -> float:
    buf_bps = float(getattr(cfg, "RUNNER_SL_BUFFER_BPS", 18))
    if tp1 <= 0:
        tp1 = state.pos_tp1

    mark_buf = float(getattr(cfg, "SL_LOCK_MARK_BUFFER_BPS", 10)) / 10000.0
    if side == "SHORT":
        sl = tp1 * (1.0 + buf_bps / 10000.0) if tp1 > 0 else entry * 0.998
        sl = max(sl, mark * (1.0 + mark_buf))
        if entry > 0:
            sl = min(sl, entry - entry * 0.0003)
        return round(sl, 2)

    sl = tp1 * (1.0 - buf_bps / 10000.0) if tp1 > 0 else entry * 1.002
    sl = min(sl, mark * (1.0 - mark_buf))
    if entry > 0:
        sl = max(sl, entry + entry * 0.0003)
    return round(sl, 2)


def cap_tp1_distance(
    direction: str, entry: float, tp1: float, tp2: float
) -> tuple[float, float]:
    from engine.structure_levels import cap_tp1_distance as _cap

    return _cap(direction, entry, tp1, tp2)


def sl_manage_cooldown_ok() -> bool:
    last = float(getattr(state, "pos_sl_manage_ts", 0) or 0)
    cd = float(getattr(cfg, "SL_MANAGE_COOLDOWN_SEC", 45))
    return (time.time() - last) >= cd


def mark_sl_managed() -> None:
    state.pos_sl_manage_ts = time.time()
