"""
engine/event_engine_v3.py — Wick, sweep, compression, kirilim → tek timeline.
"""
from __future__ import annotations

from core.config import cfg
from engine.event_decay_v3 import (
    aggregate_decayed_score,
    apply_decay_to_events,
    decayed_event_flags,
)
from engine.market_legs_v3 import _compression
from engine.v3_common import avg_body, bars_15m


def _bar_ts(b: dict) -> float:
    return float(b.get("ts", 0) or 0)


def _detect_wick_sweeps(
    bars: list[dict],
    liquidity: dict,
    px: float,
) -> list[dict]:
    events: list[dict] = []
    lows = liquidity.get("lows") or []
    highs = liquidity.get("highs") or []
    if not bars:
        return events

    for i, b in enumerate(bars):
        hi = float(b.get("high", 0) or 0)
        lo = float(b.get("low", 0) or 0)
        close = float(b.get("close", 0) or 0)
        for lv in lows:
            lvl = float(lv.get("price", 0) or 0)
            if lvl <= 0:
                continue
            tol = max(lvl * 0.0015, 0.5)
            if lo < lvl - tol * 0.2 and close > lvl:
                events.append(
                    {
                        "type": "SWEEP_LOW",
                        "bar_index": i,
                        "ts": _bar_ts(b),
                        "price": lvl,
                        "detail": f"alt likidite sweep @{lvl:.2f}",
                        "meta": {"tag": lv.get("tag"), "scale": lv.get("scale")},
                    }
                )
        for hv in highs:
            lvl = float(hv.get("price", 0) or 0)
            if lvl <= 0:
                continue
            tol = max(lvl * 0.0015, 0.5)
            if hi > lvl + tol * 0.2 and close < lvl:
                events.append(
                    {
                        "type": "SWEEP_HIGH",
                        "bar_index": i,
                        "ts": _bar_ts(b),
                        "price": lvl,
                        "detail": f"ust likidite sweep @{lvl:.2f}",
                        "meta": {"tag": hv.get("tag")},
                    }
                )
    return events


def _detect_compression_events(bars: list[dict]) -> list[dict]:
    events: list[dict] = []
    win = int(getattr(cfg, "V3_EVENT_COMPRESSION_BARS", 6) or 6)
    if len(bars) < win + 2:
        return events
    for i in range(win, len(bars)):
        seg = bars[i - win : i + 1]
        if _compression(seg, lookback=win):
            mid = (float(seg[-1].get("high", 0) or 0) + float(seg[-1].get("low", 0) or 0)) / 2
            events.append(
                {
                    "type": "COMPRESSION",
                    "bar_index": i,
                    "ts": _bar_ts(seg[-1]),
                    "price": mid,
                    "detail": f"sikisma ({win}x15m)",
                    "meta": {},
                }
            )
    return events


def _detect_structure_breaks(bars: list[dict], structure: dict) -> list[dict]:
    events: list[dict] = []
    if len(bars) < 3:
        return events
    impulse_to = float(structure.get("impulse_to") or 0)
    bounce_high = float(structure.get("bounce_high") or 0)
    last = bars[-1]
    close = float(last.get("close", 0) or 0)
    # Bullish yapida (trend/fractal yukari) "eski tepe alti kapanis" = geri cekilme,
    # bearish yapi-kirilimi DEGIL. Bayat LOWER_HIGH/STRUCTURE_BREAK_DOWN'u bastir
    # (kok-neden: yukselen piyasada +28 short puani bot'u short'a kilitliyordu).
    fr = structure.get("fractal") or {}
    bullish_struct = (
        str(structure.get("trend") or "") == "bullish"
        or (fr.get("aligned") and str(fr.get("alignment")) == "bullish")
    )
    if bullish_struct:
        return events
    if impulse_to > 0 and close < impulse_to * 0.999:
        events.append(
            {
                "type": "STRUCTURE_BREAK_DOWN",
                "bar_index": len(bars) - 1,
                "ts": _bar_ts(last),
                "price": impulse_to,
                "detail": f"impulse dip alti kapanis {close:.2f} < {impulse_to:.2f}",
                "meta": {},
            }
        )
    if bounce_high > 0 and close < bounce_high * 0.998:
        events.append(
            {
                "type": "LOWER_HIGH",
                "bar_index": len(bars) - 1,
                "ts": _bar_ts(last),
                "price": bounce_high,
                "detail": f"lower high {bounce_high:.2f} altinda",
                "meta": {},
            }
        )
    return events


def build_event_timeline(
    price: float,
    structure: dict,
    liquidity: dict,
    bars15: list[dict] | None = None,
) -> dict:
    n = int(getattr(cfg, "V3_EVENT_BARS", 32) or 32)
    n = max(16, min(n, 32))
    bars = list(bars15 or bars_15m(96))[-n:]
    px = float(price or 0)

    events: list[dict] = []
    events.extend(_detect_wick_sweeps(bars, liquidity, px))
    events.extend(_detect_compression_events(bars))
    events.extend(_detect_structure_breaks(bars, structure))

    events.sort(key=lambda e: (e.get("bar_index", 0), e.get("ts", 0)))
    decayed = apply_decay_to_events(events)
    dflags = decayed_event_flags(decayed)
    decayed_score = aggregate_decayed_score(decayed)

    active: dict = {}
    for e in reversed(decayed):
        t = str(e.get("type") or "")
        if t not in active and not e.get("stale"):
            active[t] = e

    latest = [e for e in decayed[-8:] if not e.get("stale")] if decayed else []
    flags = {
        "sweep_low": bool(dflags.get("sweep_low")),
        "sweep_high": bool(dflags.get("sweep_high")),
        "compression": bool(dflags.get("compression")),
        "structure_break": bool(dflags.get("structure_break")),
    }

    return {
        "timeline": decayed,
        "latest": latest,
        "active": active,
        "flags": flags,
        "decayed_score": decayed_score,
        "decay": {
            "best_sweep_low": dflags.get("best_sweep_low"),
            "best_sweep_high": dflags.get("best_sweep_high"),
            "best_break": dflags.get("best_break"),
        },
        "bars_used": len(bars),
        "window": "events",
    }


def event_log_line(ev: dict | None) -> str:
    e = ev or {}
    flags = e.get("flags") or {}
    parts = [k for k, v in flags.items() if v]
    last = (e.get("latest") or [])[-1:] if e.get("latest") else []
    tail = last[0].get("detail", "") if last else "—"
    ds = float(e.get("decayed_score", 0) or 0)
    age = ""
    if last:
        age = f" t={last[0].get('hours_ago', '?')}h decay={last[0].get('decayed_strength', '?')}"
    return (
        f"[EVENTS] {' '.join(parts) or 'yok'} score={ds:.0f}{age} | son: {tail}"
    )


def decision_signals(
    events: dict,
    structure: dict,
    collapse: dict | None = None,
) -> dict:
    """Likidite > event > yapı — collapse ile hizali sinyaller."""
    flags = events.get("flags") or {}
    c = collapse or {}
    dom = str(c.get("dominant_bias") or "neutral")
    mode = str(c.get("mode") or "NO_TRADE")

    signals: dict = {
        "reversal_long": False,
        "reversal_short": False,
        "breakout_short": False,
        "breakout_long": False,
        "wait": not bool(c.get("allow_trade")),
        "reasons": [],
        "collapse_mode": mode,
        "dominant_bias": dom,
    }

    if mode == "STRUCTURE_CONTROLLED":
        signals["wait"] = not bool(c.get("allow_trade"))
        if c.get("rejection_watch"):
            signals["reasons"].append(
                f"STRUCTURE_CONTROLLED baskın={dom} — counter-trend/rejection only"
            )
        if c.get("override_structure") and c.get("event_confirms_long"):
            signals["reversal_long"] = True
            signals["wait"] = False
            signals["reasons"].append("event_override sweep_low (yapi ters)")
        return signals

    if flags.get("sweep_low") and c.get("event_confirms_long"):
        signals["reversal_long"] = True
        signals["wait"] = False
        signals["reasons"].append(
            f"sweep_low + ctrl={c.get('controller')} baskın={dom} ({mode})"
        )
    if flags.get("sweep_high") and (
        c.get("event_confirms_short") or dom in ("bearish", "neutral")
    ):
        signals["reversal_short"] = True
        signals["wait"] = False
        signals["reasons"].append(f"sweep_high + baskın={dom} ({mode}) → short tepki")
    if flags.get("compression") and flags.get("structure_break"):
        if dom == "bearish" or c.get("event_confirms_short"):
            signals["breakout_short"] = True
            signals["wait"] = False
            signals["reasons"].append(f"compression+kirilim baskın={dom} → short breakout")

    if c.get("conflict") and mode == "TRANSITION":
        signals["reasons"].append(
            "yapi≠likidite — TRANSITION: yalnizca event teyitli islem"
        )

    return signals
