"""
engine/trend_magic_paper.py — Trend Magic HA: shadow paper + canli otorite.

Pine: HA sinyal, trend flip giris/cikis, kayipta volume artisi.
Varsayilan TF 30m. Gercek emir yalniz V3_STRATEGY_TM_ENABLED=true iken.
"""
from __future__ import annotations

import time

from core.config import cfg
from core.logger import get_logger
from core.state import state

log = get_logger("TrendMagicPaper")

_pos: dict | None = None
_last_signal_bar = 0.0
_tm_qty_mult = 1.0
_last_journal_bar = 0
_last_shadow_bar: dict[int, float] = {}
_pos_restored = False


def _publish_shadow_pos(side: str | None, entry: float, px: float, buf: float, td: int, tf: int, bar_ts: float) -> None:
    upnl = 0.0
    if side and entry > 0 and px > 0:
        upnl = ((px - entry) if side == "LONG" else (entry - px)) / entry * 1e4
    state.trend_magic_shadow = {
        "side": side,
        "entry": entry,
        "buffer": buf,
        "trend_dir": td,
        "tf_sec": tf,
        "bar_ts": bar_ts,
        "qty_mult": _tm_qty_mult,
        "unrealized_bps": upnl,
        "in_position": bool(side),
    }


def _restore_paper_pos() -> None:
    """DB'den acik TM shadow pozisyonunu yukle; yoksa mevcut trende senkronla."""
    global _pos, _pos_restored, _last_signal_bar
    if _pos_restored:
        return
    _pos_restored = True
    tf = _tf_sec()
    try:
        from botlog.db import get_open_tmagic, log_tmagic_open

        row = get_open_tmagic(tf)
        if row and row.get("entry", 0) > 0:
            _pos = {
                "id": row["id"],
                "side": row["side"],
                "entry": row["entry"],
                "bar_ts": row.get("open_ts") or 0,
                "tf": tf,
            }
            _last_signal_bar = float(row.get("open_ts") or 0)
            lbl = {300: "5m", 900: "15m", 1800: "30m", 3600: "1h"}.get(tf, str(tf))
            log.info(f"[TM-PAPER] restore {row['side']} @{row['entry']:.2f} tf={lbl} id={row['id']}")
            return

        from engine.trend_magic_v3 import snapshot

        closed = _get_closed_bars()
        if len(closed) < 40:
            return
        snap = snapshot(closed)
        if not snap.get("ready"):
            return
        td = int(snap.get("trend_dir") or 0)
        if td == 0:
            return
        px = float(getattr(state, "mark_price", 0) or getattr(state, "price", 0) or 0)
        if px <= 0:
            return
        fill_px = float(closed[-1].get("close") or px)
        sig = "LONG" if td == 1 else "SHORT"
        bar_ts = float(snap.get("bar_ts") or 0)
        buf = float(snap.get("buffer") or 0)
        rid = log_tmagic_open(sig, fill_px, buf, tf, td)
        if rid <= 0:
            return
        _pos = {"id": rid, "side": sig, "entry": fill_px, "bar_ts": bar_ts, "tf": tf}
        _last_signal_bar = bar_ts
        lbl = {300: "5m", 900: "15m", 1800: "30m", 3600: "1h"}.get(tf, str(tf))
        log.info(f"[TM-PAPER] startup-sync {sig} @{fill_px:.2f} tf={lbl} (mevcut trend — flip bekleniyor)")
    except Exception as ex:
        log.warning(f"[TM-PAPER] restore: {ex}")


def _tf_sec() -> int:
    return int(getattr(cfg, "V3_TREND_MAGIC_TF_SEC", 1800) or 1800)


def _fee_bps() -> float:
    return float(getattr(cfg, "V3_TREND_MAGIC_FEE_BPS", 12.0) or 12.0)


def _martingale_on() -> bool:
    return bool(getattr(cfg, "V3_TM_MARTINGALE_ENABLED", True))


def _get_closed_bars() -> list[dict]:
    from engine.trend_magic_v3 import bars_for_tf, closed_bars

    tf = _tf_sec()
    raw = bars_for_tf(tf, limit=220)
    return closed_bars(raw, tf)


def tm_trend_flipped(side: str) -> bool:
    """Canli cikis: trend ters dondu mu? (kapali mumlar)."""
    try:
        from engine.trend_magic_v3 import snapshot

        closed = _get_closed_bars()
        snap = snapshot(closed)
        if not snap.get("ready"):
            return False
        td = int(snap.get("trend_dir") or 0)
        return (side == "LONG" and td == -1) or (side == "SHORT" and td == 1)
    except Exception:
        return False


def tm_record_close(pnl_bps: float) -> None:
    global _tm_qty_mult
    if not _martingale_on():
        return
    pct = float(getattr(cfg, "V3_TM_VOLUME_INCREASE_PCT", 20.0) or 20.0)
    if pnl_bps > 0:
        _tm_qty_mult = 1.0
    elif pnl_bps < 0:
        _tm_qty_mult *= 1.0 + pct / 100.0


def _in_entry_window() -> bool:
    tf = _tf_sec()
    into = time.time() % tf
    thr = float(getattr(cfg, "V3_TM_BARCLOSE_SEC", 180) or 180)
    return into <= thr


def build_live_decision(*, bar_close_ok: bool = False) -> dict | None:
    """TM CANLI — flip girisi. None = TM kapali."""
    if not bool(getattr(cfg, "V3_STRATEGY_TM_ENABLED", False)):
        return None

    from engine.trend_magic_eval import live_gate_reason

    gate = live_gate_reason()
    if gate:
        return {"action": "WAIT", "reason": f"TM gate: {gate}", "details": {"v3_strategy": "TM"}}

    closed = _get_closed_bars()
    if len(closed) < 40:
        return {"action": "WAIT", "reason": "TM veri yetersiz", "details": {}}

    from engine.trend_magic_v3 import snapshot

    snap = snapshot(closed)
    if not snap.get("ready"):
        return {"action": "WAIT", "reason": "TM warmup", "details": {}}

    tf = _tf_sec()
    tf_lbl = f"{tf // 60}m" if tf < 3600 else f"{tf // 3600}h"
    px = float(getattr(state, "mark_price", 0) or getattr(state, "price", 0) or 0)
    buf = float(snap.get("buffer") or 0)
    td = int(snap.get("trend_dir") or 0)

    global _last_journal_bar
    bar_key = int(float(snap.get("bar_ts") or 0) // tf)
    if bar_key != _last_journal_bar:
        _last_journal_bar = bar_key
        log.info(
            f"[TM-LIVE] {tf_lbl} td={td} buf={buf:.2f} px={px:.2f} "
            f"flip_L={snap.get('entry_long')} flip_S={snap.get('entry_short')}"
        )

    if not bar_close_ok:
        return {
            "action": "WAIT",
            "reason": f"TM {tf_lbl} bar-kapanis bekleniyor",
            "details": {},
        }

    if not _in_entry_window():
        return {
            "action": "WAIT",
            "reason": f"TM {tf_lbl} giris penceresi disi",
            "details": {},
        }

    side = None
    if snap.get("entry_long"):
        side = "LONG"
    elif snap.get("entry_short"):
        side = "SHORT"

    if not side:
        side_now = "LONG" if td == 1 else ("SHORT" if td == -1 else None)
        return {
            "action": "WAIT",
            "reason": f"TM flip yok (td={side_now})",
            "details": {"v3_strategy": "TM", "tm_trend": td, "tm_buffer": buf},
        }

    if px <= 0:
        return {"action": "WAIT", "reason": "TM fiyat yok", "details": {}}

    global _tm_qty_mult
    sl = buf if buf > 0 else (px * (0.996 if side == "LONG" else 1.004))
    far = float(getattr(cfg, "V3_TM_TP_FAR_BPS", 400) or 400)
    if side == "LONG":
        if sl >= px:
            sl = px * 0.996
        tp1 = px * (1 + far * 0.5 / 1e4)
        tp2 = px * (1 + far / 1e4)
    else:
        if sl <= px:
            sl = px * 1.004
        tp1 = px * (1 - far * 0.5 / 1e4)
        tp2 = px * (1 - far / 1e4)
    risk_usd = float(getattr(cfg, "RISK_PCT", 1.0) or 1.0) * _tm_qty_mult

    details = {
        "direction": side,
        "price": px,
        "sl": round(sl, 2),
        "tp1": round(tp1, 2),
        "tp2": round(tp2, 2),
        "rr": round(abs(tp2 - px) / max(abs(px - sl), px * 0.0001), 2),
        "v3_mode": True,
        "v3_scenario": "STRATEGY_TM",
        "v3_strategy": "TM",
        "entry_reason": f"TM {side} flip {tf_lbl} buf={buf:.2f}",
        "tm_buffer": buf,
        "tm_tf_sec": tf,
        "risk_pct_override": risk_usd,
        "sl_source": "tm_buffer",
        "sl_anchor": round(buf, 2),
    }
    return {
        "action": side,
        "reason": f"TM {side} flip @{px:.2f}",
        "final_decision": side,
        "details": details,
        "direction_scores": {},
    }


def _shadow_tick_tf(tf_sec: int) -> None:
    """Tek TF shadow (DB'ye yazar, emir yok)."""
    global _pos, _last_signal_bar, _tm_qty_mult
    px = float(getattr(state, "mark_price", 0) or getattr(state, "price", 0) or 0)
    if px <= 0:
        return
    try:
        from engine.trend_magic_v3 import bars_for_tf, closed_bars, snapshot

        raw = bars_for_tf(tf_sec, limit=220)
        closed = closed_bars(raw, tf_sec)
        if len(closed) < 40:
            return
        snap = snapshot(closed)
        if not snap.get("ready"):
            return
        bar_ts = float(snap.get("bar_ts") or 0)
        td = int(snap.get("trend_dir") or 0)
        prev = int(snap.get("prev_dir") or 0)
        # Sadece flip aninda DB kaydi (gurultu azalt)
        if not ((td == 1 and prev == -1) or (td == -1 and prev == 1)):
            return
        if bar_ts <= 0:
            return
        global _last_shadow_bar
        if _last_shadow_bar.get(tf_sec) == bar_ts:
            return
        _last_shadow_bar[tf_sec] = bar_ts
        sig = "LONG" if td == 1 and prev == -1 else "SHORT"
        buf = float(snap.get("buffer") or 0)
        from botlog.db import log_tmagic_open

        lbl = {300: "5m", 900: "15m", 1800: "30m", 3600: "1h"}.get(tf_sec, str(tf_sec))
        log_tmagic_open(sig, px, buf, tf_sec, td)
        log.info(f"[TM-SHADOW-{lbl}] flip {sig} @{px:.2f} buf={buf:.2f} (izleme, emir yok)")
    except Exception as ex:
        log.debug(f"[TM-SHADOW] tf={tf_sec}: {ex}")


def _paper_tick_primary() -> None:
    """30m (veya cfg TF) shadow paper — flip PnL, tmagic_paper tablosu."""
    global _pos, _last_signal_bar, _tm_qty_mult
    _restore_paper_pos()
    px = float(getattr(state, "mark_price", 0) or getattr(state, "price", 0) or 0)
    if px <= 0:
        return
    tf = _tf_sec()
    try:
        from engine.trend_magic_v3 import snapshot

        closed = _get_closed_bars()
        if len(closed) < 40:
            return
        snap = snapshot(closed)
        if not snap.get("ready"):
            return

        bar_ts = float(snap.get("bar_ts") or 0)
        td = int(snap.get("trend_dir") or 0)
        prev = int(snap.get("prev_dir") or 0)
        side_now = "LONG" if td == 1 else ("SHORT" if td == -1 else None)
        fee = _fee_bps()
        fill_px = float(closed[-1].get("close") or px) if closed else px

        from botlog.db import log_tmagic_close, log_tmagic_open

        if _pos is not None:
            pos_side = _pos["side"]
            if side_now and side_now != pos_side:
                ent = _pos["entry"]
                cur = ((fill_px - ent) if pos_side == "LONG" else (ent - fill_px)) / ent * 1e4
                net = cur - fee
                log_tmagic_close(_pos["id"], fill_px, net, "flip", tf)
                tm_record_close(net)
                log.info(f"[TM-PAPER] CLOSE {pos_side} @{fill_px:.2f} pnl={net:+.1f}bps mult={_tm_qty_mult:.2f}")
                _pos = None

        if _pos is None:
            if bar_ts <= 0 or bar_ts == _last_signal_bar:
                pass
            elif td == 1 and prev == -1:
                sig = "LONG"
                _last_signal_bar = bar_ts
                buf = float(snap.get("buffer") or 0)
                rid = log_tmagic_open(sig, fill_px, buf, tf, td)
                _pos = {"id": rid, "side": sig, "entry": fill_px, "bar_ts": bar_ts, "tf": tf}
                lbl = {300: "5m", 900: "15m", 1800: "30m", 3600: "1h"}.get(tf, str(tf))
                log.info(f"[TM-PAPER] OPEN {sig} @{fill_px:.2f} buf={buf:.2f} tf={lbl} mult={_tm_qty_mult:.2f}")
            elif td == -1 and prev == 1:
                sig = "SHORT"
                _last_signal_bar = bar_ts
                buf = float(snap.get("buffer") or 0)
                rid = log_tmagic_open(sig, fill_px, buf, tf, td)
                _pos = {"id": rid, "side": sig, "entry": fill_px, "bar_ts": bar_ts, "tf": tf}
                lbl = {300: "5m", 900: "15m", 1800: "30m", 3600: "1h"}.get(tf, str(tf))
                log.info(f"[TM-PAPER] OPEN {sig} @{fill_px:.2f} buf={buf:.2f} tf={lbl} mult={_tm_qty_mult:.2f}")

    except Exception as ex:
        log.warning(f"[TM-PAPER] tick: {ex}")

    if _pos is not None and px > 0:
        try:
            from engine.trend_magic_v3 import snapshot

            closed = _get_closed_bars()
            snap2 = snapshot(closed) if len(closed) >= 40 else {}
            td2 = int(snap2.get("trend_dir") or 0) if snap2.get("ready") else 0
            buf2 = float(snap2.get("buffer") or 0)
            _publish_shadow_pos(_pos["side"], _pos["entry"], px, buf2, td2, tf, float(_pos.get("bar_ts") or 0))
        except Exception:
            pass
    elif px > 0:
        _publish_shadow_pos(None, 0, px, 0, 0, tf, 0)


def paper_tick() -> None:
    if not bool(getattr(cfg, "V3_TREND_MAGIC_PAPER", True)):
        return

    # Birincil TF (30m) shadow paper — her zaman
    _paper_tick_primary()

    # Diger TF flip izleme (5m/15m/1h — emir yok)
    if bool(getattr(cfg, "V3_TM_SHADOW_ALL_TF", True)):
        for tf in (300, 900, 1800, 3600):
            if tf != _tf_sec():
                _shadow_tick_tf(tf)


def publish_shadow_state() -> None:
    if not bool(getattr(cfg, "V3_TREND_MAGIC_PAPER", True)) and not bool(
        getattr(cfg, "V3_STRATEGY_TM_ENABLED", False)
    ):
        return
    tf = _tf_sec()
    try:
        from engine.trend_magic_v3 import compute_series, snapshot

        closed = _get_closed_bars()
        ser = compute_series(closed)
        snap = snapshot(closed)
        state.trend_magic_chart = {
            "tf_sec": tf,
            "series": ser.get("bars") or [],
            "snapshot": snap,
            "ready": bool(ser.get("ready")),
        }
    except Exception:
        pass
