"""
engine/structure_engine_v3.py — Tek kaynak: rejim & bias (96–128 mum).

Cikti: trend=bearish|bullish|range, strength=0–100
"""
from __future__ import annotations

from core.config import cfg
from core.logger import get_logger
from engine.market_legs_v3 import build_market_story
from engine.structure_v3 import _detect_range_lock, _direction_from_closes, _close_trend_params
from engine.v3_common import bars_15m, bars_1h

log = get_logger("StructEngineV3")


def _segment_trend(bars: list[dict]) -> str:
    n = int(getattr(cfg, "V3_STRUCTURE_15M_CLOSE_BARS", 8) or 8)
    min_move = float(getattr(cfg, "V3_STRUCTURE_15M_MIN_MOVE_PCT", 0.0004) or 0.0004)
    d = _direction_from_closes(bars, n, min_move)
    if d == "UP":
        return "bullish"
    if d == "DOWN":
        return "bearish"
    return "range"


def compute_fractal_structure(bars15: list[dict]) -> dict:
    """macro 96 / mid 48 / micro 16 — fractal alignment."""
    bars = list(bars15 or [])
    macro_n = int(getattr(cfg, "V3_STRUCTURE_BARS", 96) or 96)
    mid_n = int(getattr(cfg, "V3_STRUCTURE_MID_BARS", 48) or 48)
    micro_n = int(getattr(cfg, "V3_STRUCTURE_MICRO_BARS", 16) or 16)

    macro = bars[-macro_n:] if len(bars) >= macro_n else bars
    mid = bars[-mid_n:] if len(bars) >= mid_n else bars
    micro = bars[-micro_n:] if len(bars) >= micro_n else bars

    t_macro = _segment_trend(macro)
    t_mid = _segment_trend(mid)
    t_micro = _segment_trend(micro)

    aligned = t_macro == t_mid == t_micro and t_macro in ("bullish", "bearish")
    transition = (
        t_macro in ("bearish", "bullish")
        and t_micro in ("bullish", "bearish")
        and t_macro != t_micro
    )

    if aligned:
        alignment = t_macro
        align_score = 100
    elif transition:
        alignment = f"{t_macro}_vs_{t_micro}"
        align_score = 45
    elif t_macro in ("bullish", "bearish") and t_mid == t_macro:
        alignment = t_macro
        align_score = 72
    elif t_macro in ("bullish", "bearish"):
        alignment = t_macro
        align_score = 58
    else:
        alignment = "mixed"
        align_score = 35

    return {
        "macro": {"bars": len(macro), "trend": t_macro},
        "mid": {"bars": len(mid), "trend": t_mid},
        "micro": {"bars": len(micro), "trend": t_micro},
        "alignment": alignment,
        "align_score": align_score,
        "aligned": aligned,
        "transition": transition,
    }


def _structure_bars(limit: int | None = None) -> list[dict]:
    n = int(limit or getattr(cfg, "V3_STRUCTURE_BARS", 96) or 96)
    n = max(64, min(n, 128))
    return list(bars_15m(n))


def compute_structure(price: float = 0, bars15: list[dict] | None = None) -> dict:
    px = float(price or 0)
    bars = list(bars15) if bars15 else _structure_bars()
    story = build_market_story(bars, px)

    n, min_move = _close_trend_params()
    bars_1h_list = bars_1h(100)
    dir_1h = _direction_from_closes(bars_1h_list, n, min_move)
    range_locked = _detect_range_lock(bars_1h_list, lookback=24)

    story_bias = str(story.get("bias") or "NEUTRAL").upper()
    pattern = str(story.get("pattern") or "UNKNOWN")

    if range_locked and story_bias not in ("BEAR", "BULL"):
        trend = "range"
    elif range_locked and story_bias == "BEAR":
        trend = "bearish"
    elif range_locked and story_bias == "BULL":
        trend = "bullish"
    elif story_bias == "BEAR" or dir_1h == "DOWN":
        trend = "bearish"
    elif story_bias == "BULL" or dir_1h == "UP":
        trend = "bullish"
    elif dir_1h == "UNCLEAR":
        trend = "range"
    else:
        trend = "range"

    strength = 50
    if trend == "bearish":
        strength = 55
        if pattern.startswith("IMPULSE_DOWN"):
            strength += 20
        if story.get("is_lower_high"):
            strength += 12
        if story.get("compression"):
            strength += 8
    elif trend == "bullish":
        strength = 55
        if "UP" in pattern:
            strength += 15
    else:
        strength = 40 if range_locked else 35

    strength = max(0, min(100, int(strength)))

    fractal = compute_fractal_structure(bars)
    if fractal.get("aligned") and fractal.get("alignment") == trend:
        strength = min(100, strength + 12)
    elif fractal.get("aligned") and fractal.get("alignment") in ("bullish", "bearish") \
            and fractal.get("alignment") != trend:
        # KOK-NEDEN FIX: macro=mid=micro hepsi NET ters yon -> story/pattern bayat
        # (or. 8h fractal=bullish iken pattern=IMPULSE_DOWN trend'i bearish tutuyordu,
        # bot yukselise short atti). Gercek cok-zamanli HH/HL yapisini izle.
        old_trend = trend
        trend = str(fractal.get("alignment"))
        strength = max(50, strength - 15)  # ters donus -> guveni biraz dusur
        log.info(
            f"[STRUCT] fractal override: trend {old_trend}->{trend} "
            f"(macro=mid=micro={trend}, pattern={pattern} bayat sayildi)"
        )
    elif fractal.get("transition"):
        strength = max(0, strength - 10)
        if trend in ("bearish", "bullish") and fractal["micro"]["trend"] != trend:
            trend = "range" if strength < 60 else trend

    strength = max(0, min(100, int(strength)))

    return {
        "trend": trend,
        "fractal": fractal,
        "bias": story_bias,
        "strength": strength,
        "pattern": pattern,
        "summary": str(story.get("summary") or ""),
        "range_locked": bool(range_locked),
        "dir_1h": dir_1h,
        "compression": bool(story.get("compression")),
        "weak_bounce": bool(story.get("weak_bounce")),
        "is_lower_high": bool(story.get("is_lower_high")),
        "impulse_from": float(story.get("impulse_from") or 0),
        "impulse_to": float(story.get("impulse_to") or 0),
        "bounce_high": float(story.get("bounce_high") or 0),
        "bars_used": len(bars),
        "window": "structure",
    }


def structure_log_line(s: dict | None) -> str:
    x = s or {}
    fr = x.get("fractal") or {}
    fr_txt = ""
    if fr:
        fr_txt = (
            f" fractal={fr.get('macro', {}).get('trend')}/"
            f"{fr.get('mid', {}).get('trend')}/"
            f"{fr.get('micro', {}).get('trend')}"
            f" align={fr.get('align_score')}"
        )
    return (
        f"[STRUCTURE] trend={x.get('trend')} strength={x.get('strength')} "
        f"pattern={x.get('pattern')}{fr_txt} | {x.get('summary', '—')}"
    )
