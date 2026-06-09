"""
engine/state_collapse_v3.py — Dominance + non-linear override (regime physics).

Katmanlar:
  1) Linear score (L 50% / E 30% / S 20%) → soft_mode
  2) HARD: structure >= 80 ve likidite/olay ters → STRUCTURE_CONTROLLED
  3) EVENT: event >= 85 + sweep teyit → override_structure (counter-trend)
  4) Zayıf likidite (L < esik) yön belirleyemez
"""
from __future__ import annotations

from core.config import cfg
from core.logger import get_logger
from engine.adaptation_v3 import get_adaptive_weights
from engine.regime_physics_v3 import apply_regime_physics_to_collapse

log = get_logger("StateCollapse")


def _weights() -> tuple[float, float, float]:
    try:
        return get_adaptive_weights()
    except Exception:
        pass
    wl = float(getattr(cfg, "V3_COLLAPSE_W_LIQUIDITY", 0.50) or 0.50)
    we = float(getattr(cfg, "V3_COLLAPSE_W_EVENT", 0.30) or 0.30)
    ws = float(getattr(cfg, "V3_COLLAPSE_W_STRUCTURE", 0.20) or 0.20)
    total = wl + we + ws
    if total <= 0:
        return 0.5, 0.3, 0.2
    return wl / total, we / total, ws / total


def _dirs_opposed(a: str, b: str) -> bool:
    a, b = str(a or "neutral"), str(b or "neutral")
    if a == "neutral" or b == "neutral":
        return False
    return a != b


def _structure_score(structure: dict) -> tuple[int, str]:
    trend = str(structure.get("trend") or "range")
    strength = int(structure.get("strength", 0) or 0)
    fractal = structure.get("fractal") or {}
    if fractal.get("aligned") and fractal.get("alignment") in ("bullish", "bearish"):
        strength = min(100, strength + int(fractal.get("align_score", 0) or 0) // 10)
    elif fractal.get("transition"):
        strength = max(0, strength - 8)
    if trend == "range":
        return max(25, min(50, strength // 2)), "neutral"
    if trend == "bearish":
        return max(30, min(100, strength)), "bearish"
    if trend == "bullish":
        return max(30, min(100, strength)), "bullish"
    return 35, "neutral"


def _liquidity_score(liquidity: dict, price: float) -> tuple[int, str, str]:
    px = float(price or 0)
    bias = liquidity.get("bias") or {}
    bname = str(bias.get("bias") or "NEUTRAL").upper()
    up = float(bias.get("up_score", 0) or 0)
    down = float(bias.get("down_score", 0) or 0)
    total = up + down + 1e-9
    ratio = float(getattr(cfg, "V3_LIQ_CHASE_RATIO", 1.3) or 1.3)

    direction = "neutral"
    if up > down * ratio:
        direction = "bullish"
    elif down > up * ratio:
        direction = "bearish"

    clarity = abs(up - down) / total
    score = int(35 + clarity * 55)

    highs = liquidity.get("highs") or []
    lows = liquidity.get("lows") or []
    sweeps = liquidity.get("sweep_zones") or []

    for h in highs:
        if h.get("untouched") and float(h.get("price", 0) or 0) > px:
            score = min(100, score + 12)
            break
    for lo in lows:
        if lo.get("untouched") and float(lo.get("price", 0) or 0) < px:
            score = min(100, score + 10)
            break

    if len(sweeps) >= 2:
        score = max(20, score - 15)

    avg_q = int(liquidity.get("avg_quality", 0) or 0)
    min_q = int(getattr(cfg, "V3_LIQ_MIN_QUALITY_DIRECTION", 40) or 40)
    if avg_q > 0:
        if avg_q >= 65:
            score = min(100, score + 8)
        elif avg_q < min_q:
            score = max(15, score - 20)
            direction = "neutral"

    min_dir = int(getattr(cfg, "V3_COLLAPSE_LIQ_MIN_DIRECTION", 50) or 50)
    pools = liquidity.get("pools") or []
    near = [p for p in pools if abs(float(p.get("price", 0) or 0) - px) / max(px, 1) < 0.008]
    if near:
        best_q = max(int(p.get("quality_score", 0) or 0) for p in near)
        if best_q < min_q and direction != "neutral":
            direction = "neutral"
            score = min(score, min_dir)

    if bname == "NEUTRAL" and direction == "neutral":
        score = min(score, 45)
    if score < min_dir:
        direction = "neutral"

    return max(0, min(100, score)), direction, bname


def _event_score(events: dict, liquidity_dir: str) -> tuple[int, str, bool]:
    flags = events.get("flags") or {}
    latest = events.get("latest") or []
    decayed_base = float(events.get("decayed_score", 0) or 0)

    sweep_low = bool(flags.get("sweep_low"))
    sweep_high = bool(flags.get("sweep_high"))
    compression = bool(flags.get("compression"))
    struct_break = bool(flags.get("structure_break"))

    conflict = sweep_low and sweep_high
    score = 25
    trigger_dir = "neutral"
    clear = False

    if conflict:
        score = 30
    elif sweep_low and not sweep_high:
        score = 72
        trigger_dir = "bullish"
        clear = True
    elif sweep_high and not sweep_low:
        score = 72
        trigger_dir = "bearish"
        clear = True
    elif struct_break and compression:
        score = 78
        trigger_dir = "bearish"
        clear = True
    elif struct_break:
        score = 65
        trigger_dir = "bearish"
        clear = True
    elif compression:
        score = 48
    elif latest:
        score = 42

    if decayed_base > 0:
        score = int(max(score * 0.35, min(100, decayed_base)))
        if decayed_base >= 55 and trigger_dir != "neutral":
            clear = True
    if not latest and decayed_base < 22:
        score = min(score, 28)
        trigger_dir = "neutral"
        clear = False

    return max(0, min(100, score)), trigger_dir, clear


def _liquidity_sweep_confirmed(events: dict, liquidity: dict) -> tuple[bool, str]:
    """Olay + likidite havuzunda gercek sweep teyidi."""
    flags = events.get("flags") or {}
    sweeps = liquidity.get("sweep_zones") or []
    if flags.get("sweep_low"):
        if sweeps or True:
            return True, "sweep_low"
    if flags.get("sweep_high"):
        if sweeps or True:
            return True, "sweep_high"
    return False, ""


def _linear_soft_mode(state_score: int) -> str:
    active_thr = int(getattr(cfg, "V3_COLLAPSE_ACTIVE", 70) or 70)
    trans_thr = int(getattr(cfg, "V3_COLLAPSE_TRANSITION", 40) or 40)
    if state_score >= active_thr:
        return "ACTIVE_BIAS"
    if state_score >= trans_thr:
        return "TRANSITION"
    return "NO_TRADE"


def _resolve_dominant_linear(
    liq_dir: str,
    event_dir: str,
    struct_dir: str,
    liq_sc: int,
    event_sc: int,
    struct_sc: int,
) -> tuple[str, str]:
    """
    Yön secimi. controller: hangi katman baskın.
    Zayıf likidite (L < min) asla tek basina yön vermez.
    """
    min_liq = int(getattr(cfg, "V3_COLLAPSE_LIQ_MIN_DIRECTION", 50) or 50)
    struct_ctrl = int(getattr(cfg, "V3_COLLAPSE_STRUCTURE_CONTROL", 80) or 80)

    # Event çok güçlü + sweep + yapıya zıt → event controller (adaptasyon çeşitleniyor)
    if (
        event_sc >= 80
        and event_dir in ("bearish", "bullish")
        and struct_sc >= struct_ctrl
        and _dirs_opposed(event_dir, struct_dir)
    ):
        return struct_dir, "event"  # yön yapıdan, controller event

    if struct_sc >= struct_ctrl and struct_dir in ("bearish", "bullish"):
        # Likidite de güçlüyse ve yapıya katılıyorsa → likidite controller ver
        if liq_sc >= 70 and not _dirs_opposed(liq_dir, struct_dir):
            return struct_dir, "liquidity"
        return struct_dir, "structure"

    if event_sc >= 70 and event_dir in ("bearish", "bullish"):
        if liq_sc < min_liq or not _dirs_opposed(event_dir, liq_dir):
            return event_dir, "event"

    if liq_sc >= min_liq and liq_dir in ("bearish", "bullish"):
        if struct_dir in ("bearish", "bullish") and _dirs_opposed(liq_dir, struct_dir):
            if struct_sc >= 65:
                return struct_dir, "structure"
        return liq_dir, "liquidity"

    if struct_sc >= 55 and struct_dir in ("bearish", "bullish"):
        return struct_dir, "structure"
    if event_sc >= 60 and event_dir in ("bearish", "bullish"):
        return event_dir, "event"

    return "neutral", "blend"


def collapse_market_state(
    structure: dict,
    liquidity: dict,
    events: dict,
    price: float = 0,
) -> dict:
    struct_sc, struct_dir = _structure_score(structure)
    liq_sc, liq_dir, liq_bias_name = _liquidity_score(liquidity, price)
    event_sc, event_dir, event_clear = _event_score(events, liq_dir)

    wl, we, ws = _weights()
    state_score = max(0, min(100, int(wl * liq_sc + we * event_sc + ws * struct_sc)))
    soft_mode = _linear_soft_mode(state_score)

    dominant, controller = _resolve_dominant_linear(
        liq_dir, event_dir, struct_dir, liq_sc, event_sc, struct_sc
    )

    flags = events.get("flags") or {}
    sweep_ok, sweep_kind = _liquidity_sweep_confirmed(events, liquidity)

    struct_ctrl_thr = int(getattr(cfg, "V3_COLLAPSE_STRUCTURE_CONTROL", 80) or 80)
    # Event override eşiği düşürüldü: 85 → 75 (sweep + zıt yapı varsa daha kolay devreye gir)
    event_override_thr = int(getattr(cfg, "V3_COLLAPSE_EVENT_OVERRIDE", 75) or 75)

    liq_opposes = _dirs_opposed(struct_dir, liq_dir) and liq_dir != "neutral"
    event_opposes = _dirs_opposed(struct_dir, event_dir) and event_dir != "neutral"

    override_structure = False
    mode = soft_mode
    counter_trend_only = False
    rejection_watch = False

    if (
        event_sc >= event_override_thr
        and sweep_ok
        and struct_dir in ("bearish", "bullish")
        and _dirs_opposed(struct_dir, event_dir)
    ):
        override_structure = True
        mode = "EVENT_OVERRIDE"
        controller = "event"
        counter_trend_only = True
        dominant = struct_dir
    elif (
        struct_sc >= struct_ctrl_thr
        and struct_dir in ("bearish", "bullish")
        and (liq_opposes or event_opposes)
        and not override_structure
    ):
        mode = "STRUCTURE_CONTROLLED"
        controller = "structure"
        dominant = struct_dir
        counter_trend_only = True
        rejection_watch = True
    elif struct_sc >= struct_ctrl_thr and struct_dir in ("bearish", "bullish"):
        if dominant != struct_dir and liq_sc < int(
            getattr(cfg, "V3_COLLAPSE_LIQ_MIN_DIRECTION", 50) or 50
        ):
            mode = "STRUCTURE_CONTROLLED"
            controller = "structure"
            dominant = struct_dir
            counter_trend_only = True
            rejection_watch = True

    conflict = liq_opposes or event_opposes

    event_confirms_long = False
    event_confirms_short = False

    if flags.get("sweep_low") and sweep_ok:
        event_confirms_long = True
    if flags.get("sweep_high") and sweep_ok:
        event_confirms_short = True

    if mode == "STRUCTURE_CONTROLLED":
        if struct_dir == "bearish":
            event_confirms_short = event_confirms_short or bool(
                flags.get("structure_break")
            )
            event_confirms_long = (
                override_structure and event_confirms_long and sweep_kind == "sweep_low"
            )
        elif struct_dir == "bullish":
            event_confirms_long = event_confirms_long or bool(flags.get("structure_break"))
            event_confirms_short = (
                override_structure
                and event_confirms_short
                and sweep_kind == "sweep_high"
            )
    elif mode in ("TRANSITION", "ACTIVE_BIAS", "EVENT_OVERRIDE"):
        if event_clear and event_dir == "bullish":
            event_confirms_long = True
        if event_clear and event_dir == "bearish":
            event_confirms_short = True

    with_structure_long = dominant == "bullish" or struct_dir == "bullish"
    with_structure_short = dominant == "bearish" or struct_dir == "bearish"

    fast_path = (
        event_sc >= event_override_thr
        and sweep_ok
        and mode == "NO_TRADE"
    )

    allow_trade = False
    if mode == "ACTIVE_BIAS":
        allow_trade = True
    elif mode == "EVENT_OVERRIDE":
        allow_trade = event_confirms_long or event_confirms_short
    elif mode == "STRUCTURE_CONTROLLED":
        allow_trade = (
            event_confirms_long
            or event_confirms_short
            or with_structure_long
            or with_structure_short
        )
    elif mode == "TRANSITION":
        allow_trade = event_confirms_long or event_confirms_short
    elif fast_path:
        allow_trade = True
        mode = "EVENT_OVERRIDE"

    detail_parts = [
        f"skor={state_score}",
        f"soft={soft_mode}",
        f"mod={mode}",
        f"baskın={dominant}",
        f"ctrl={controller}",
        f"L{liq_sc}/{liq_dir}",
        f"E{event_sc}/{event_dir}",
        f"S{struct_sc}/{struct_dir}",
    ]
    if conflict:
        detail_parts.append("catisma=regime")
    if override_structure:
        detail_parts.append("event_override=evet")
    if counter_trend_only:
        detail_parts.append("counter_trend_only")
    if rejection_watch:
        detail_parts.append("rejection_watch")

    base = {
        "state_score": state_score,
        "mode": mode,
        "soft_mode": soft_mode,
        "dominant_bias": dominant,
        "controller": controller,
        "allow_trade": allow_trade,
        "fast_path": fast_path,
        "conflict": conflict,
        "override_structure": override_structure,
        "counter_trend_only": counter_trend_only,
        "rejection_watch": rejection_watch,
        "liquidity_sweep_confirmed": sweep_ok,
        "sweep_kind": sweep_kind,
        "with_structure_long": with_structure_long,
        "with_structure_short": with_structure_short,
        "scores": {"structure": struct_sc, "liquidity": liq_sc, "event": event_sc},
        "directions": {
            "structure": struct_dir,
            "liquidity": liq_dir,
            "event": event_dir,
            "liquidity_bias": liq_bias_name,
        },
        "weights": {"liquidity": wl, "event": we, "structure": ws},
        "event_confirms_long": bool(event_confirms_long),
        "event_confirms_short": bool(event_confirms_short),
        "event_clear": event_clear,
        "detail": " | ".join(detail_parts),
    }
    return apply_regime_physics_to_collapse(base, structure, liquidity, events)


def collapse_log_line(c: dict | None) -> str:
    x = c or {}
    extra = ""
    if x.get("rejection_watch"):
        extra = " rejection_watch"
    return (
        f"[COLLAPSE] {x.get('detail', '—')} | "
        f"trade={'evet' if x.get('allow_trade') else 'hayir'}{extra}"
    )


def scenario_allowed(
    collapse: dict,
    scenario_name: str,
    side: str,
) -> tuple[bool, str]:
    from core.config import cfg

    if getattr(cfg, "V3_SCORE_DECISION_ENABLED", True):
        return True, ""

    if not collapse:
        return True, ""

    mode = str(collapse.get("mode") or "NO_TRADE")
    dom = str(collapse.get("dominant_bias") or "neutral")
    struct_dom = dom
    sn = str(scenario_name or "").upper()
    side_u = str(side or "").upper()

    if collapse.get("fast_path"):
        return True, ""

    if mode == "STRUCTURE_CONTROLLED":
        if side_u in ("SELL", "SHORT") and sn in ("RANGE_SELL", "BREAKOUT_SELL"):
            if collapse.get("inertia_continuation_short") or collapse.get("trap_reversal_short"):
                return True, ""
        if side_u in ("BUY", "LONG") and sn in ("RANGE_BUY", "BREAKOUT_BUY"):
            if collapse.get("override_structure") and collapse.get("event_confirms_long"):
                return True, ""
            if collapse.get("trap_reversal_long"):
                return True, ""
            return False, (
                f"STRUCTURE_CONTROLLED ({struct_dom}) — long yasak; "
                f"yalnizca sweep+event_override veya direnç red short "
                f"({collapse.get('detail', '')})"
            )
        if side_u in ("SELL", "SHORT"):
            if struct_dom == "bearish":
                return True, ""
            if collapse.get("override_structure") and collapse.get("event_confirms_short"):
                return True, ""
            return False, (
                f"STRUCTURE_CONTROLLED ({struct_dom}) — short yapıyla uyumsuz"
            )
        return True, ""

    if mode == "EVENT_OVERRIDE":
        if side_u in ("BUY", "LONG"):
            if not collapse.get("event_confirms_long"):
                return False, "EVENT_OVERRIDE — long icin sweep_low teyit gerekli"
        if side_u in ("SELL", "SHORT"):
            if not collapse.get("event_confirms_short"):
                return False, "EVENT_OVERRIDE — short icin sweep_high teyit gerekli"
        return True, ""

    if mode == "NO_TRADE":
        if sn == "RANGE_BUY" and collapse.get("event_confirms_long"):
            return True, ""
        if sn in ("RANGE_SELL", "BREAKOUT_SELL") and collapse.get("event_confirms_short"):
            return True, ""
        if sn.startswith("BREAKOUT_") and collapse.get("event_clear"):
            return True, ""
        return False, (
            f"Collapse NO_TRADE (skor={collapse.get('state_score')}) — "
            "yalnizca likidite+event hizali tetik"
        )

    if mode == "TRANSITION":
        if side_u in ("BUY", "LONG") and not collapse.get("event_confirms_long"):
            if sn != "WAIT":
                return False, f"TRANSITION — long icin event teyit yok (baskın={dom})"
        if side_u in ("SELL", "SHORT") and not collapse.get("event_confirms_short"):
            if sn != "WAIT":
                return False, f"TRANSITION — short icin event teyit yok (baskın={dom})"

    if mode == "ACTIVE_BIAS" and dom == "bearish":
        if side_u in ("BUY", "LONG") and sn in ("RANGE_BUY", "BREAKOUT_BUY"):
            if not collapse.get("event_confirms_long"):
                return False, (
                    f"ACTIVE bearish — long yalnizca alt sweep sonrasi "
                    f"({collapse.get('detail', '')})"
                )
    if mode == "ACTIVE_BIAS" and dom == "bullish":
        if side_u in ("SELL", "SHORT") and sn in ("RANGE_SELL", "BREAKOUT_SELL"):
            if not collapse.get("event_confirms_short"):
                return False, "ACTIVE bullish — short yalnizca ust sweep sonrasi"

    return True, ""
