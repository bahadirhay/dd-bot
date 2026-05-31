"""
engine/operation_state.py — tek merkezli operasyon görünümü.

Dashboard, trader özeti ve debug ekranları için:
- rejim
- yapı
- setup
- execution
- bir sonraki tetik
tek yerden üretilir.
"""
from __future__ import annotations

from core.config import cfg
from core.state import state, effective_price


def _split_status(text: str) -> tuple[str, str]:
    raw = str(text or "").strip()
    if not raw:
        return "", ""
    parts = [p.strip() for p in raw.split("—", 1)]
    if len(parts) == 2:
        return parts[0], parts[1]
    return raw, ""


def _status_line(code: str, detail: str = "") -> str:
    return f"{code} — {detail}" if detail else code


def _debug_block_text(block_code: str, block_detail: str, headline_code: str, headline_detail: str) -> str:
    code = block_code or headline_code or ""
    detail = block_detail or headline_detail or ""
    if not code:
        return ""
    if code in ("BREAKOUT_LONG_TUTUYOR", "BREAKOUT_SHORT_TUTUYOR"):
        return _status_line("BLOK", "hold bekleniyor")
    if code in (
        "BREAKOUT_CONTINUE_LONG",
        "BREAKOUT_CONTINUE_SHORT",
        "PRESSURE_LONG",
        "PRESSURE_SHORT",
    ) and detail:
        return _status_line("BLOK", detail)
    if code in ("TRIGGER_LONG", "TRIGGER_SHORT") and detail:
        return _status_line("BLOK", detail)
    return _status_line(code, detail)


def _level_role(price: float, level: float, kind: str) -> str:
    if price <= 0 or level <= 0:
        return "none"
    if kind == "resistance":
        return "active" if level > price * 1.0002 else "broken_reference"
    return "active" if level < price * 0.9998 else "broken_reference"


def _pick_setup(entry_mode: str, bv: dict, rv: dict, in_position: bool) -> tuple[str, str]:
    if in_position:
        return (
            str(bv.get("status_code") or "POZISYON"),
            str(bv.get("status_detail") or ""),
        )

    priority = {
        "BREAKOUT_LONG_HAZIR": 100,
        "BREAKOUT_SHORT_HAZIR": 100,
        "BREAKOUT_CONTINUE_LONG": 98,
        "BREAKOUT_CONTINUE_SHORT": 98,
        "PRESSURE_LONG": 96,
        "PRESSURE_SHORT": 96,
        "BREAKOUT_LONG_TUTUYOR": 95,
        "BREAKOUT_SHORT_TUTUYOR": 95,
        "MAJOR_BEKLENIYOR_LONG": 92,
        "MAJOR_BEKLENIYOR_SHORT": 92,
        "TRIGGER_LONG": 88,
        "TRIGGER_SHORT": 88,
        "TAKTIK_LONG_ADAY": 84,
        "TAKTIK_SHORT_ADAY": 84,
        "TAKTIK_LONG_ZAYIF": 76,
        "TAKTIK_SHORT_ZAYIF": 76,
        "BREAKOUT_LONG_BEKLIYOR": 72,
        "BREAKOUT_SHORT_BEKLIYOR": 72,
        "KANAL_DAR": 40,
        "KANAL_YOK": 36,
        "KANAL_KALITESI_DUSUK": 34,
        "CHOP": 30,
        "BAND_ICI_BEKLE": 24,
        "BAND_DISI_BEKLE": 20,
        "KANAL_KAPALI": 10,
    }

    candidates: list[tuple[int, str, str]] = []
    if entry_mode in ("range", "hybrid"):
        code = str(rv.get("status_code") or "")
        if code:
            candidates.append(
                (priority.get(code, 0), code, str(rv.get("status_detail") or ""))
            )
    if entry_mode in ("break", "realtime", "hybrid"):
        code = str(bv.get("status_code") or "")
        if code:
            candidates.append(
                (priority.get(code, 0), code, str(bv.get("status_detail") or ""))
            )
    if not candidates:
        return "BAND_DISI_BEKLE", ""
    candidates.sort(key=lambda t: t[0], reverse=True)
    _, code, detail = candidates[0]
    return code, detail


def _next_trigger_text(direction: str, kind: str, level: float) -> str:
    if level <= 0:
        return ""
    if direction == "LONG":
        if kind == "major":
            return f"LONG için majör accept {level:.2f}"
        if kind == "deep_major":
            return f"LONG için üst cap {level:.2f}"
        return f"LONG için trigger kırılımı {level:.2f}"
    if kind == "major":
        return f"SHORT için majör kayıp {level:.2f}"
    if kind == "deep_major":
        return f"SHORT için alt cap {level:.2f}"
    return f"SHORT için trigger kırılımı {level:.2f}"


def build_operation_view(price: float = 0.0) -> dict:
    px = float(price or effective_price() or state.mark_price or state.price or 0)
    entry_mode = getattr(cfg, "ENTRY_MODE", "break").lower()
    v3_enabled = bool(getattr(cfg, "STRATEGY_V3_ENABLED", False))

    from engine.breakout import get_status_snapshot
    from engine.levels_v3 import get_levels_snapshot as get_levels_snapshot_v3
    from engine.structure_v3 import get_structure_snapshot as get_structure_snapshot_v3
    from engine.scenario_v3 import get_scenario_snapshot as get_scenario_snapshot_v3
    from engine.decision_v3 import get_decision_snapshot as get_decision_snapshot_v3

    bv = get_status_snapshot(px)
    rv: dict = {}
    if not v3_enabled:
        from engine.range_trade import get_range_snapshot

        rv = get_range_snapshot(px)
    v3_levels = get_levels_snapshot_v3(px)
    v3_structure = get_structure_snapshot_v3()
    v3_scenario = get_scenario_snapshot_v3(px)
    v3_decision = get_decision_snapshot_v3()

    bcode = str(bv.get("status_code") or "")
    bdetail = str(bv.get("status_detail") or "")
    rv_code = str(rv.get("status_code") or "")
    rv_detail = str(rv.get("status_detail") or "")
    block_code, block_detail = _split_status(state.no_entry_reason or "")

    major_r = float(
        bv.get("structural_major_resistance") or bv.get("major_resistance") or 0
    )
    major_s = float(
        bv.get("structural_major_support") or bv.get("major_support") or 0
    )
    deep_major_r = float(bv.get("deep_major_resistance") or 0)
    deep_major_s = float(bv.get("deep_major_support") or 0)
    tactical_r = float(bv.get("tactical_cap_resistance") or 0)
    tactical_s = float(bv.get("tactical_floor_support") or 0)
    main_r = float(bv.get("main_resistance") or tactical_r or 0)
    main_s = float(bv.get("main_support") or tactical_s or 0)
    channel_r = float(bv.get("channel_resistance") or 0)
    channel_s = float(bv.get("channel_support") or 0)
    trigger_r = float(bv.get("trigger_resistance") or 0)
    trigger_s = float(bv.get("trigger_support") or 0)

    active_major_r = float(bv.get("active_major_resistance") or 0)
    active_major_s = float(bv.get("active_major_support") or 0)

    if v3_enabled:
        v3_r = float(v3_levels.get("active_resistance") or 0)
        v3_s = float(v3_levels.get("active_support") or 0)
        main_r = v3_r or main_r
        main_s = v3_s or main_s
        channel_r = 0.0
        channel_s = 0.0
        trigger_r = v3_r or trigger_r
        trigger_s = v3_s or trigger_s
        active_major_r = v3_r or active_major_r
        active_major_s = v3_s or active_major_s
        major_r = v3_r or major_r
        major_s = v3_s or major_s

    v3_1h = str((((v3_structure.get("1h") or {}).get("direction")) or "UNCLEAR")).upper()
    v3_15m = "kapalı"
    v3_5m = "kapalı"
    v3_alignment = v3_structure.get("alignment") or {}
    v3_aligned = bool(v3_alignment.get("aligned"))
    v3_align_dir = str(v3_alignment.get("direction") or "UNCLEAR").upper()
    major_r_role = _level_role(px, major_r, "resistance")
    major_s_role = _level_role(px, major_s, "support")
    broken_major_r = major_r if major_r_role == "broken_reference" and major_r > 0 else 0.0
    broken_major_s = major_s if major_s_role == "broken_reference" and major_s > 0 else 0.0

    structure_code = "NO_STRUCTURE"
    if major_r_role == "active" and major_s_role == "active":
        structure_code = "BETWEEN_MAJOR_LEVELS"
    elif broken_major_r > 0:
        structure_code = "ABOVE_BROKEN_MAJOR_R"
    elif broken_major_s > 0:
        structure_code = "BELOW_BROKEN_MAJOR_S"
    elif major_r_role == "active":
        structure_code = "UNDER_MAJOR_R"
    elif major_s_role == "active":
        structure_code = "ABOVE_MAJOR_S"

    setup_code, setup_detail = _pick_setup(
        entry_mode, bv, rv, bool(state.in_position)
    )

    next_long_level = (
        (trigger_r if trigger_r > px * 1.0002 else 0.0)
        or (tactical_r if tactical_r > px * 1.0002 else 0.0)
        or active_major_r
        or (deep_major_r if deep_major_r > px * 1.0002 else 0.0)
    )
    if trigger_r > px * 1.0002:
        next_long_kind = "trigger"
    elif tactical_r > px * 1.0002:
        next_long_kind = "trigger"
    elif active_major_r > 0:
        next_long_kind = "major"
    elif deep_major_r > px * 1.0002:
        next_long_kind = "deep_major"
    else:
        next_long_kind = ""

    next_short_level = (
        (trigger_s if 0 < trigger_s < px * 0.9998 else 0.0)
        or (tactical_s if 0 < tactical_s < px * 0.9998 else 0.0)
        or active_major_s
        or (deep_major_s if 0 < deep_major_s < px * 0.9998 else 0.0)
    )
    if 0 < trigger_s < px * 0.9998:
        next_short_kind = "trigger"
    elif 0 < tactical_s < px * 0.9998:
        next_short_kind = "trigger"
    elif active_major_s > 0:
        next_short_kind = "major"
    elif 0 < deep_major_s < px * 0.9998:
        next_short_kind = "deep_major"
    else:
        next_short_kind = ""

    structure_15m = (state.structure_15m or "").upper()
    regime_code = "REGIME_UNCLEAR"
    regime_detail = str(state.trend_view.get("summary") or "")
    if structure_15m == "UP":
        regime_code = "TREND_UP"
    elif structure_15m == "DOWN":
        regime_code = "TREND_DOWN"
    elif rv_code in ("TAKTIK_LONG_ADAY", "TAKTIK_SHORT_ADAY", "BAND_ICI_BEKLE", "CHOP"):
        regime_code = "RANGE_ACTIVE"

    blocking_codes = {
        "HEADROOM_YETERSIZ",
        "FLOW_BLOK",
        "HTF_BLOK",
        "OI_BLOK",
        "RR_YETERSIZ",
        "FEED_BLOK",
        "ARCHETYPE_BLOK",
        "NARRATIVE_BLOK",
        "BREAKOUT_PLAN_YOK",
        "BREAKOUT_GEC",
        "BREAKOUT_TUTUNMADI",
        "SEVIYE_YORGUN",
        "KANAL_DAR",
        "KANAL_YOK",
        "KANAL_KALITESI_DUSUK",
    }
    display_block_code = block_code if block_code in blocking_codes else ""
    display_block_detail = block_detail if display_block_code else ""

    headline_code = setup_code or display_block_code or "BAND_DISI_BEKLE"
    headline_detail = setup_detail or display_block_detail
    if display_block_code in (
        "HEADROOM_YETERSIZ",
        "FLOW_BLOK",
        "HTF_BLOK",
        "OI_BLOK",
        "RR_YETERSIZ",
        "FEED_BLOK",
        "ARCHETYPE_BLOK",
        "NARRATIVE_BLOK",
    ):
        headline_code = display_block_code
        headline_detail = display_block_detail

    if v3_enabled:
        v3_code = str((v3_scenario or {}).get("name") or "")
        v3_detail = str((v3_decision or {}).get("reason") or (v3_scenario or {}).get("detail") or "")
        if v3_align_dir == "UP" and v3_aligned:
            regime_code = "TREND_UP"
        elif v3_align_dir == "DOWN" and v3_aligned:
            regime_code = "TREND_DOWN"
        elif bool(v3_levels.get("range_valid")):
            regime_code = "RANGE_ACTIVE"
        else:
            regime_code = "REGIME_UNCLEAR"
        regime_detail = (
            f"1h={v3_1h} (V3 yapi; 15m/5m kapali) | "
            f"zone={v3_levels.get('zone') or '?'} "
            f"zone={v3_levels.get('zone') or '?'} | {v3_detail}"
        )
        if v3_1h == "UNCLEAR":
            structure_code = "V3_UNCLEAR"
        elif v3_aligned and v3_align_dir == "UP":
            structure_code = "V3_ALIGNED_UP"
        elif v3_aligned and v3_align_dir == "DOWN":
            structure_code = "V3_ALIGNED_DOWN"
        else:
            structure_code = "V3_MIXED"
        if state.in_position and str((state.position_breakout or {}).get("entry_mode") or "") == "v3":
            setup_code = "POZISYON_V3"
            pos_scenario = str((state.position_breakout or {}).get("scenario") or "")
            pos_trigger = str((state.position_breakout or {}).get("trigger") or "")
            setup_detail = " | ".join(x for x in (state.pos_side or "", pos_scenario, pos_trigger) if x)
        else:
            setup_code = v3_code or "WAIT"
            setup_detail = str((v3_scenario or {}).get("detail") or v3_detail or "")
        if v3_code:
            headline_code = v3_code
            headline_detail = v3_detail

    summary_parts = [
        f"rejim={regime_code}",
        f"yapi={structure_code}",
        f"setup={setup_code or '-'}",
    ]
    if v3_scenario.get("name"):
        summary_parts.append(f"v3={v3_scenario.get('name')}")
    if v3_decision.get("action"):
        summary_parts.append(f"karar={v3_decision.get('action')}")
    if display_block_code:
        summary_parts.append(f"blok={display_block_code}")
    if next_long_level > 0:
        summary_parts.append(_next_trigger_text("LONG", next_long_kind, next_long_level))
    if next_short_level > 0:
        summary_parts.append(_next_trigger_text("SHORT", next_short_kind, next_short_level))

    debug_block = _debug_block_text(
        display_block_code,
        display_block_detail,
        headline_code,
        headline_detail,
    )

    out = {
        "headline": _status_line(headline_code, headline_detail),
        "headline_code": headline_code,
        "headline_detail": headline_detail,
        "debug_block": debug_block,
        "summary": " | ".join(summary_parts),
        "entry_mode": entry_mode,
        "price": px,
        "regime": {
            "code": regime_code,
            "label": regime_code,
            "detail": regime_detail,
        },
        "structure": {
            "code": structure_code,
            "main_resistance": main_r,
            "main_support": main_s,
            "channel_resistance": channel_r,
            "channel_support": channel_s,
            "channel_source": str(bv.get("channel_source") or ""),
            "major_resistance": major_r,
            "major_support": major_s,
            "deep_major_resistance": deep_major_r,
            "deep_major_support": deep_major_s,
            "active_major_resistance": active_major_r,
            "active_major_support": active_major_s,
            "broken_major_resistance": broken_major_r,
            "broken_major_support": broken_major_s,
            "structural_quality": str(bv.get("structural_major_quality") or ""),
            "structural_layer": str(bv.get("structural_major_layer") or ""),
        },
        "setup": {
            "code": setup_code,
            "detail": setup_detail,
            "range_code": rv_code,
            "range_detail": rv_detail,
            "breakout_code": bcode,
            "breakout_detail": bdetail,
        },
        "execution": {
            "blocking_code": display_block_code,
            "blocking_detail": display_block_detail,
            "blocking_reason": str(state.no_entry_reason or ""),
            "debug_block": debug_block,
            "in_position": bool(state.in_position),
            "position_mode": str((state.position_breakout or {}).get("entry_mode") or ""),
        },
        "next": {
            "long_level": next_long_level,
            "long_kind": next_long_kind,
            "long_text": _next_trigger_text("LONG", next_long_kind, next_long_level)
            if next_long_level > 0
            else "",
            "short_level": next_short_level,
            "short_kind": next_short_kind,
            "short_text": _next_trigger_text("SHORT", next_short_kind, next_short_level)
            if next_short_level > 0
            else "",
        },
        "levels": {
            "main_resistance": main_r,
            "main_support": main_s,
            "channel_resistance": channel_r,
            "channel_support": channel_s,
            "trigger_resistance": trigger_r,
            "trigger_support": trigger_s,
            "tactical_cap_resistance": tactical_r,
            "tactical_floor_support": tactical_s,
            "major_resistance": major_r,
            "major_support": major_s,
            "deep_major_resistance": deep_major_r,
            "deep_major_support": deep_major_s,
            "major_resistance_role": _level_role(px, major_r, "resistance"),
            "major_support_role": _level_role(px, major_s, "support"),
        },
        "v3": {
            "levels": v3_levels,
            "structure": v3_structure,
            "scenario": v3_scenario,
            "decision": v3_decision,
        },
        "breakout": bv,
        "range": rv,
    }
    state.operation_view = out
    return out


def get_operation_view(price: float = 0.0) -> dict:
    return build_operation_view(price)
