"""
engine/range_validation_v3.py — Kanal ici islemler icin tek validasyon kapisi.

Range trade yalnizca bu kapidan VALID donerse acilabilir. Scenario, thesis ve
score katmanlari tek basina SUPPORT_HOLD / RESISTANCE_REJECTION actiramaz.
"""
from __future__ import annotations

from typing import Any

from core.config import cfg
from core.state import state


def _side_norm(side: str) -> tuple[str, str, str]:
    s = str(side or "").upper()
    if s in ("BUY", "LONG"):
        return "LONG", "BUY", "RANGE_BUY"
    if s in ("SELL", "SHORT"):
        return "SHORT", "SELL", "RANGE_SELL"
    return "", "", ""


def _scenario_text(scenario: dict | None) -> str:
    scn = scenario or {}
    parts: list[str] = []
    for key in ("name", "reason", "detail", "block_reason", "message"):
        val = scn.get(key)
        if val:
            parts.append(str(val))
    return " | ".join(parts).lower()


def clean_range_scenario(scenario: dict | None, side: str) -> dict:
    """WAIT detail teşhis metnini range kapısına taşımaz (döngüsel blok önlenir)."""
    _, _, scenario_name = _side_norm(side)
    scn = scenario or {}
    return {
        "name": scenario_name or scn.get("name") or "",
        "band_stable": scn.get("band_stable"),
        "band_stability": scn.get("band_stability"),
        "block_reason": scn.get("block_reason"),
    }


def _scenario_blocks_side(side: str, scenario: dict | None) -> str:
    scn = scenario or {}
    scn_name = str(scn.get("name") or "").upper()
    # WAIT detail = önceki reddin açıklaması; yeniden validate'te blok sayılmaz.
    if scn_name == "WAIT":
        text = str(scn.get("block_reason") or "").strip().lower()
        if not text:
            return ""
    else:
        text = _scenario_text(scenario)
    if not text:
        return ""
    if side == "LONG":
        tokens = (
            "long kovalama blok",
            "long kovalama yok",
            "zayif talep",
            "zayıf talep",
            "kirilim bekleniyor",
            "kırılım bekleniyor",
            "direnc (tp) test edilmemis",
            "direnç (tp) test edilmemiş",
            "kanal tek tarafli",
            "kanal tek taraflı",
        )
    else:
        tokens = (
            "short kovalama blok",
            "short kovalama yok",
            "zayif arz",
            "zayıf arz",
            "kirilim bekleniyor",
            "kırılım bekleniyor",
            "destek (tp) test edilmemis",
            "destek (tp) test edilmemiş",
            "kanal tek tarafli",
            "kanal tek taraflı",
        )
    for token in tokens:
        if token in text:
            return f"Senaryo {side} blok: {token}"
    return ""


def _market_state_blocks_side(
    side: str,
    market_state: dict,
    cvd: dict | None,
    levels: dict | None = None,
) -> str:
    ms = market_state or {}
    collapse = ms.get("collapse") or {}
    struct_u = ms.get("structure") or {}
    dominant = str(collapse.get("dominant_bias") or "").lower()
    mode = str(collapse.get("mode") or "").upper()
    pattern = str(struct_u.get("pattern") or "").upper()
    score = float(collapse.get("state_score") or 0)
    cvd_dir = str((cvd or {}).get("direction") or "").upper()
    cvd_ok = bool((cvd or {}).get("confirmed"))

    # Onayli RANGE: kenar mean-reversion icin TF-trend (bias) veto degil.
    # channel_traversed + range_valid => gercek kanal; destekte LONG / dirençte
    # SHORT yalniz TERS CVD ile bloklanir (ayi/boga state tek basina engellemez).
    lv = levels or {}
    _range_sym = (
        bool(getattr(cfg, "V3_RANGE_SYMMETRIC_EDGE", True))
        and bool(lv.get("channel_traversed"))
        and bool(lv.get("range_valid"))
    )

    if side == "LONG":
        from engine.levels_v3 import trade_band_first_leg_long_ok

        if _range_sym:
            return "Onayli RANGE destek LONG: CVD satis." if cvd_dir == "BEAR" else ""
        if trade_band_first_leg_long_ok(lv):
            if cvd_dir == "BEAR":
                return "Trade band destek LONG: CVD satis."
            return ""
        opposite_control = (
            dominant in ("bear", "bearish")
            and (
                score >= 60
                or collapse.get("rejection_watch")
                or collapse.get("counter_trend_only")
                or mode in ("ACTIVE_BIAS", "STRUCTURE_CONTROLLED", "TRANSITION")
                or "IMPULSE_DOWN" in pattern
            )
        )
        event_ok = bool(collapse.get("event_confirms_long"))
        if opposite_control and not (cvd_dir == "BULL" and cvd_ok and event_ok):
            return "Bearish kontrol altında RANGE_LONG icin CVD+event teyidi yok."
    elif side == "SHORT":
        from engine.levels_v3 import trade_band_first_leg_short_ok

        if _range_sym:
            return "Onayli RANGE direnc SHORT: CVD alim." if cvd_dir == "BULL" else ""
        if trade_band_first_leg_short_ok(lv):
            if cvd_dir == "BULL":
                return "Trade band direnc SHORT: CVD alim."
            return ""
        opposite_control = (
            dominant in ("bull", "bullish")
            and (
                score >= 60
                or collapse.get("rejection_watch")
                or collapse.get("counter_trend_only")
                or mode in ("ACTIVE_BIAS", "STRUCTURE_CONTROLLED", "TRANSITION")
                or "IMPULSE_UP" in pattern
            )
        )
        event_ok = bool(collapse.get("event_confirms_short"))
        if opposite_control and not (cvd_dir == "BEAR" and cvd_ok and event_ok):
            return "Bullish kontrol altında RANGE_SHORT icin CVD+event teyidi yok."
    return ""


def _tp_side_test_ok(
    bars15: list[dict], levels: dict, side: str, support: float, resistance: float
) -> tuple[bool, str, float]:
    from engine.levels_v3 import (
        _trade_channel_traversed,
        level_reliability,
        level_tp_reliability,
        trade_band_first_leg_long_ok,
        trade_band_first_leg_short_ok,
    )

    require_tp = bool(getattr(cfg, "V3_RANGE_REQUIRE_TP_TEST", True))
    tp_ret = level_tp_reliability(bars15, support, resistance, side)
    if require_tp and tp_ret <= 0.0:
        if _trade_channel_traversed(
            bars15, support, resistance
        ) and getattr(cfg, "V3_CHANNEL_TRAVERSE_TP_OK", True):
            return True, "", 1.0
        if side == "LONG" and trade_band_first_leg_long_ok(levels):
            s_hist = level_reliability(
                bars15, support, "BUY", support=support, resistance=resistance
            )
            if s_hist > 0.0:
                return True, "", 1.0
        if side == "SHORT" and trade_band_first_leg_short_ok(levels):
            r_hist = level_reliability(
                bars15, resistance, "SELL", support=support, resistance=resistance
            )
            if r_hist > 0.0:
                return True, "", 1.0
        label = "Direnc (TP)" if side == "LONG" else "Destek (TP)"
        return False, f"Kosul TP: {label} test edilmemis (tp_ret=0%).", tp_ret
    return True, "", tp_ret


def validate_range_trade(
    side: str,
    *,
    levels: dict,
    scenario: dict | None = None,
    market_state: dict | None = None,
    cvd: dict | None = None,
    px: float = 0.0,
    bars15: list[dict] | None = None,
) -> dict[str, Any]:
    direction, order_side, scenario_name = _side_norm(side)
    if not direction:
        return {"valid": False, "side": side, "reason": "Gecersiz range yonu."}

    px = float(px or levels.get("price") or state.mark_price or state.price or 0)
    support = float(levels.get("active_support") or 0)
    resistance = float(levels.get("active_resistance") or 0)
    zone = str(levels.get("zone") or "").upper()
    ms = market_state or levels.get("market_state") or getattr(state, "v3_market_state", None) or {}

    if px <= 0 or support <= 0 or resistance <= support:
        return {"valid": False, "side": direction, "reason": "Aktif kanal kurulamadı."}
    if not levels.get("range_valid"):
        return {"valid": False, "side": direction, "reason": "Range gecersiz."}
    if scenario and scenario.get("band_stable") is False:
        detail = str(scenario.get("band_stability") or scenario.get("detail") or "")
        suffix = f" — {detail}" if detail else ""
        return {
            "valid": False,
            "side": direction,
            "reason": f"Band stabil degil{suffix}",
        }
    if not (support < px < resistance):
        return {"valid": False, "side": direction, "reason": "Fiyat kanal icinde degil."}
    if direction == "LONG" and zone != "NEAR_SUPPORT":
        return {"valid": False, "side": direction, "reason": "LONG icin fiyat destek kenarinda degil."}
    if direction == "SHORT" and zone != "NEAR_RESISTANCE":
        return {"valid": False, "side": direction, "reason": "SHORT icin fiyat direnc kenarinda degil."}

    block = _scenario_blocks_side(direction, scenario)
    if block:
        return {"valid": False, "side": direction, "reason": block}

    block = _market_state_blocks_side(direction, ms, cvd, levels)
    if block:
        return {"valid": False, "side": direction, "reason": block}

    if bars15 is None:
        from engine.v3_common import bars_15m

        bars15 = bars_15m(40)

    from engine.levels_v3 import level_trade_ready

    ready, ready_detail = level_trade_ready(bars15, levels, order_side, cvd=cvd)
    if not ready:
        return {"valid": False, "side": direction, "reason": ready_detail}

    tp_ok, tp_reason, tp_ret = _tp_side_test_ok(
        bars15, levels, direction, support, resistance
    )
    if not tp_ok:
        return {"valid": False, "side": direction, "reason": tp_reason}

    from engine.expected_move_v3 import compute_expected_move

    expected = compute_expected_move(
        order_side,
        px,
        support=support,
        resistance=resistance,
    )
    if expected.get("valid") and not expected.get("meets_min_rr"):
        from engine.levels_v3 import (
            trade_band_first_leg_long_ok,
            trade_band_first_leg_short_ok,
        )

        min_rr = float(getattr(cfg, "V3_MIN_RR_RATIO", 2.0) or 2.0)
        if direction == "LONG":
            from engine.entry_v3 import _structural_sl_long

            fallback = min(support * 0.999, px * 0.9998)
            swing_sl = _structural_sl_long(px, support, fallback)
            risk = (px - swing_sl) if swing_sl < px else (px - support)
            reward = resistance - px
            if risk > 0 and reward > 0:
                band_rr = reward / risk
                if band_rr >= min_rr:
                    expected = {
                        **expected,
                        "rr": round(band_rr, 2),
                        "meets_min_rr": True,
                        "band_target": True,
                        "structural_sl": True,
                        "reward_usd": round(reward, 2),
                        "risk_usd": round(risk, 2),
                    }
        elif direction == "SHORT":
            from engine.entry_v3 import _structural_sl_short

            fallback = max(resistance * 1.001, px * 1.0002)
            swing_sl = _structural_sl_short(px, resistance, fallback)
            risk = (swing_sl - px) if swing_sl > px else (resistance - px)
            reward = px - support
            if risk > 0 and reward > 0:
                band_rr = reward / risk
                if band_rr >= min_rr:
                    expected = {
                        **expected,
                        "rr": round(band_rr, 2),
                        "meets_min_rr": True,
                        "band_target": True,
                        "structural_sl": True,
                        "reward_usd": round(reward, 2),
                        "risk_usd": round(risk, 2),
                    }
        if not expected.get("meets_min_rr"):
            return {
                "valid": False,
                "side": direction,
                "reason": f"Range RR zayif: RR={expected.get('rr')}",
                "expected_move": expected,
            }

    return {
        "valid": True,
        "side": direction,
        "order_side": order_side,
        "scenario": scenario_name,
        "reason": f"{scenario_name} valid: {ready_detail} tp_ret={tp_ret:.0%}",
        "support": support,
        "resistance": resistance,
        "zone": zone,
        "expected_move": expected,
    }
