"""
engine/decision_v3.py

Sade karar: seviye -> zone -> CVD -> giris -> RR
(1h yapi yalnizca bilgi; kanal teyidi ve 15m hizasi yok)

DÜZELTME: range_locked kontrolü eklendi.
Fiyat dar kanalda (range_locked=True) dolaşıyorsa
1h DOWN olsa bile RANGE_BUY/RANGE_SELL engellenmez.
"""
from __future__ import annotations

from core.config import cfg
from core.state import state
from core.logger import get_logger
from engine.cvd_v3 import update_cvd_snapshot
from engine.entry_v3 import get_entry_snapshot, update_entry
from engine.levels_v3 import get_levels_snapshot, update_levels
from engine.scenario_v3 import get_scenario_snapshot, update_scenario
from engine.v3_flow_log import maybe_log_v3_flow
from engine.structure_v3 import get_structure_snapshot, update_structure

log = get_logger("V3Decision")
_last_diag_key = ""
_last_periodic_log_ts = 0.0


def _score_supports_side(side: str, scores: dict, *, reverse: bool = False) -> bool:
    side = (side or "").upper()
    action = str(scores.get("action") or "").upper()
    if action == side:
        return True

    p_long = float(scores.get("prob_long_pct") or 0)
    p_short = float(scores.get("prob_short_pct") or 0)
    min_prob = float(getattr(cfg, "V3_REVERSE_MIN_SCORE_PROB", 55.0) if reverse else 50.0)
    if side == "LONG":
        return p_long >= min_prob and p_long > p_short
    if side == "SHORT":
        return p_short >= min_prob and p_short > p_long
    return False


def _log_entry_check(
    *,
    px: float,
    levels: dict,
    scenario: dict,
    cvd: dict,
    signal: dict,
    trade_candidate: bool,
    reasons: list[str],
) -> None:
    scn_name = str(scenario.get("name") or "WAIT")
    zone = str(levels.get("zone") or "MID_RANGE")
    s_px = float(levels.get("active_support") or 0)
    r_px = float(levels.get("active_resistance") or 0)
    cvd_dir = str(cvd.get("direction") or "?")
    cvd_ok = bool(cvd.get("confirmed"))
    entry_ok = bool(signal.get("valid"))
    allow = bool(trade_candidate and entry_ok and not reasons)
    why = str(reasons[0]) if reasons else "-"
    log.info(
        "[ENTRY_CHECK] "
        f"px={px:.2f} S={s_px:.2f} R={r_px:.2f} zone={zone} scn={scn_name} "
        f"cvd={cvd_dir}/{int(cvd_ok)} entry={int(entry_ok)} candidate={int(trade_candidate)} "
        f"allow={int(allow)} why={why}"
    )


def format_decision_diag(snap: dict) -> str:
    action = str(snap.get("action") or "WAIT")
    reason = str(snap.get("reason") or "—")
    levels = snap.get("levels") or {}
    structure = snap.get("structure") or {}
    cvd = snap.get("cvd") or {}
    scenario = snap.get("scenario") or {}
    entry = snap.get("entry") or {}
    s1h_data = structure.get("1h") or {}
    s1h = str(s1h_data.get("direction") or "?")
    range_locked = s1h_data.get("range_locked", False)
    lock_tag = " [RANGE_LOCKED]" if range_locked else ""
    scn = str(scenario.get("name") or "—")
    s_px = float(levels.get("active_support") or 0)
    r_px = float(levels.get("active_resistance") or 0)
    macro_s_px = float(levels.get("macro_support") or 0)
    macro_r_px = float(levels.get("macro_resistance") or 0)
    zone = str(levels.get("zone") or "?")
    lock = "kilit" if levels.get("active_locked") else "acik"
    band_txt = f"band {s_px:.0f}/{r_px:.0f}"
    if (
        levels.get("trade_band")
        and macro_s_px > 0
        and macro_r_px > macro_s_px
        and (abs(macro_s_px - s_px) > 1.0 or abs(macro_r_px - r_px) > 1.0)
    ):
        band_txt += f" macro={macro_s_px:.0f}/{macro_r_px:.0f}"
    if levels.get("channel_traversed"):
        band_txt += " traverse=evet"
    cvd_dir = str(cvd.get("direction") or "?")
    cvd_ok = "evet" if cvd.get("confirmed") else "hayir"
    cvd_cum = float(cvd.get("cumulative", 0) or 0)
    buy_r = float(cvd.get("buy_ratio", 0.5) or 0.5)
    rr = float(entry.get("rr", 0) or 0)
    entry_ok = "evet" if entry.get("valid") else "hayir"
    sl_px = float(entry.get("sl", 0) or 0)
    tp_px = float(entry.get("tp2", 0) or 0)
    sl_source = str(entry.get("sl_source") or "")
    sl_anchor = float(entry.get("sl_anchor", 0) or 0)
    sl_src_tag = (
        f" sl_src={sl_source}@{sl_anchor:.2f}"
        if sl_source and sl_anchor > 0
        else ""
    )
    rr_tag = " onizleme" if entry.get("preview") and not entry.get("valid") else ""
    levels_line = (
        f"SL={sl_px:.2f}{sl_src_tag} TP={tp_px:.2f} "
        if sl_px > 0 and tp_px > 0
        else ""
    )
    liq_bias = str((levels.get("liquidity_bias") or {}).get("bias") or "")
    liq_tag = f" liq={liq_bias}" if liq_bias and liq_bias != "NEUTRAL" else ""
    em = snap.get("expected_move") or getattr(state, "v3_expected_move", None) or {}
    em_tag = ""
    if em.get("valid"):
        em_tag = f" EM:RR={em.get('rr')} pri={em.get('trade_priority')}"
    mt = getattr(state, "v3_multi_tf_trend", None) or {}
    tr_tag = ""
    if mt.get("direction"):
        tr_tag = f" trend={mt.get('direction')}/{mt.get('trend_score')}"
    vac = int(getattr(state, "v3_vacuum_score", 0) or 0)
    vac_tag = f" vac={vac}" if vac >= 40 else ""
    ms = levels.get("market_state") or getattr(state, "v3_market_state", None) or {}
    struct_u = (ms.get("structure") or {}) if ms else {}
    story = levels.get("market_story") or struct_u or getattr(state, "v3_market_story", None) or {}
    story_tag = ""
    collapse = (ms.get("collapse") or {}) if ms else {}
    urgency = (ms.get("urgency") or {}) if ms else {}
    if collapse.get("mode"):
        ctrl = collapse.get("controller") or "?"
        story_tag = (
            f" | {collapse.get('mode')} ctrl={ctrl} "
            f"baskın={collapse.get('dominant_bias')} skor={collapse.get('state_score')}"
        )
        tv = (ms.get("trade_verdict") or {}) if ms else {}
        if tv.get("verdict"):
            story_tag += f" | verdict={tv.get('verdict')}"
            t = tv.get("timing") or {}
            if t.get("tier"):
                story_tag += f" tier={t.get('tier')} p={t.get('trade_probability', 0):.2f}"
        elif urgency.get("action"):
            story_tag += f" exec={urgency.get('action')} p={urgency.get('pressure')}"
        if collapse.get("rejection_watch"):
            story_tag += " rejection_watch"
    elif struct_u.get("trend"):
        story_tag = (
            f" | rejim={struct_u.get('trend')} guc={struct_u.get('strength')} "
            f"({struct_u.get('summary', '')})"
        )
    elif story.get("summary"):
        story_tag = f" | yapi={story.get('pattern')} ({story.get('summary')})"
    layers = levels.get("zone_layers") or getattr(state, "v3_zone_layers", None) or {}
    layer_tag = ""
    sm = layers.get("supply_major") or {}
    mid = layers.get("supply_mid") or {}
    dw = layers.get("demand_weak") or {}
    if sm and dw:
        mid_tag = (
            f" M={mid.get('low', 0):.0f}-{mid.get('high', 0):.0f}"
            if mid
            else ""
        )
        layer_tag = (
            f" | katman S={dw.get('low', 0):.0f}-{dw.get('high', 0):.0f} "
            f"R={sm.get('low', 0):.0f}-{sm.get('high', 0):.0f}{mid_tag}"
        )
    eff = str(((structure.get("alignment") or {}).get("effective_bias")) or "")
    eff_tag = f" eff={eff}" if eff and eff != s1h else ""
    ds = snap.get("direction_scores") or {}
    prob_tag = ""
    if ds:
        prob_tag = (
            f" | L={ds.get('long_score', 0):.0f} S={ds.get('short_score', 0):.0f} "
            f"pL={ds.get('prob_long_pct', 0):.0f}% pS={ds.get('prob_short_pct', 0):.0f}% "
            f"mode={ds.get('decision_mode', '')}"
        )
    return (
        f"karar={action} | neden: {reason}{prob_tag} | "
        f"1h={s1h}{lock_tag}{eff_tag}{tr_tag} (bilgi) | senaryo={scn} | "
        f"{band_txt} zone={zone} {lock}{layer_tag}{liq_tag}{vac_tag}{em_tag}{story_tag} | "
        f"CVD {cvd_dir} cum={cvd_cum:+.0f} alim={buy_r:.0%} teyit={cvd_ok} | "
        f"giris={entry_ok} {levels_line}RR={rr:.2f}{rr_tag}"
    )


def log_decision_diag(snap: dict, *, tag: str = "", force: bool = False) -> None:
    import time

    global _last_diag_key, _last_periodic_log_ts
    action = str(snap.get("action") or "WAIT")
    reason = str(snap.get("reason") or "")
    key = f"{action}|{reason}"
    now = time.time()
    interval = float(getattr(cfg, "V3_DECISION_LOG_SEC", 120) or 120)
    periodic = force or (now - _last_periodic_log_ts >= interval)
    if action not in ("LONG", "SHORT"):
        if key == _last_diag_key and not periodic:
            return
    _last_diag_key = key
    if periodic:
        _last_periodic_log_ts = now
    line = format_decision_diag(snap)
    suffix = f" ({tag})" if tag else ""
    log.info(f"[V3] {line}{suffix}")


def _sync_snap_zone_for_logs(snap: dict) -> None:
    levels = snap.get("levels") or {}
    if not isinstance(levels, dict):
        return
    active = (state.v3_levels or {}).get("active") or {}
    zone = str(active.get("zone") or levels.get("zone") or "")
    if not zone:
        return
    current = str(levels.get("zone") or "")
    if zone != current:
        levels = dict(levels)
        levels["zone"] = zone
        snap["levels"] = levels


def _commit_decision(snap: dict, *, flow_tag: str = "", flow_force: bool = False) -> dict:
    _sync_snap_zone_for_logs(snap)
    scn_name = str((snap.get("scenario") or {}).get("name") or "WAIT")
    levels = snap.get("levels") or {}
    zone = str(levels.get("zone") or "MID_RANGE")
    is_breakout = scn_name.startswith("BREAKOUT_")
    trade_candidate = scn_name not in ("WAIT",) and not (
        zone == "MID_RANGE" and not is_breakout
    )
    try:
        from engine.attribution_v3 import maybe_log_attribution

        maybe_log_attribution(
            snap,
            trade_candidate=trade_candidate,
            force=flow_force or bool(flow_tag),
        )
    except Exception as e:
        log.debug(f"[ATTR] log atlandi: {e}")

    state.v3_decision = snap
    snap["attribution"] = getattr(state, "v3_last_attribution", None) or {}
    try:
        from engine.no_trade_log_v3 import maybe_log_no_trade

        maybe_log_no_trade(snap, force=flow_force or bool(flow_tag))
    except Exception as e:
        log.debug(f"[NO_TRADE] log atlandi: {e}")
    log_decision_diag(snap, tag=flow_tag, force=flow_force)
    maybe_log_v3_flow(snap, tag=flow_tag, force=flow_force)
    try:
        from engine.direction_score_v3 import maybe_log_direction_score

        maybe_log_direction_score(snap, force=flow_force or bool(flow_tag))
    except Exception as e:
        log.debug(f"[DIR_SCORE] atlandi: {e}")
    return snap


def _build_v3_thesis_details(
    *,
    action: str,
    side_entry: dict,
    levels: dict,
    scenario: dict,
    px: float,
    rr: float,
    scores: dict,
    range_locked: bool,
    selected_thesis=None,
    reverse_from: str = "",
) -> dict:
    scn_name = str(scenario.get("name") or "WAIT")
    ref_s = float(scenario.get("ref_support") or levels.get("active_support") or 0)
    ref_r = float(scenario.get("ref_resistance") or levels.get("active_resistance") or 0)
    support_price = float((levels.get("support") or {}).get("price", 0) or ref_s)
    resistance_price = float((levels.get("resistance") or {}).get("price", 0) or ref_r)
    if action == "LONG":
        support_price = ref_s or support_price
    else:
        resistance_price = ref_r or resistance_price
    thesis_type = str(getattr(selected_thesis, "thesis_type", "") or "score")
    prefix = f"reverse {reverse_from}->{action} " if reverse_from else ""
    return {
        "direction": action,
        "price": float(side_entry.get("price", 0) or px),
        "signal_price": float(side_entry.get("price", 0) or px),
        "sl": float(side_entry.get("sl", 0) or 0),
        "tp1": float(side_entry.get("tp1", 0) or 0),
        "tp2": float(side_entry.get("tp2", 0) or 0),
        "rr": rr,
        "entry_reason": (
            f"{prefix}thesis {action} RR={rr:.2f} "
            f"+ {side_entry.get('entry_type')} "
            f"(score p={scores.get('prob_short_pct' if action == 'SHORT' else 'prob_long_pct')}%)"
        ),
        "range_active_level": support_price if action == "LONG" else resistance_price,
        "break_level": resistance_price if action == "LONG" else support_price,
        "v3_mode": True,
        "v3_scenario": scn_name,
        "v3_entry_type": str(side_entry.get("entry_type") or "SCORE"),
        "v3_support": support_price,
        "v3_resistance": resistance_price,
        "range_locked": range_locked,
        "score_decision": True,
        "thesis_decision": bool(selected_thesis),
        "reverse_decision": bool(reverse_from),
        "reverse_from": reverse_from,
        "thesis_type": thesis_type,
        "sl_source": str(side_entry.get("sl_source") or ""),
        "sl_anchor": float(side_entry.get("sl_anchor", 0) or 0),
    }


def update_decision(*, flow_tag: str = "", flow_force: bool = False) -> dict:
    from core.state import effective_price

    px = float(effective_price() or state.mark_price or state.price or 0)
    update_levels()
    from engine.market_state_v3 import get_market_state, update_market_state

    market_state = get_market_state() or update_market_state(px)
    update_scenario()
    signal = update_entry()
    levels = get_levels_snapshot(px)
    levels["market_state"] = market_state
    scenario = get_scenario_snapshot(px) or {}
    structure = get_structure_snapshot()
    zone = str(levels.get("zone") or "MID_RANGE")
    scn_name = str(scenario.get("name") or "WAIT")
    breakout_side = ""
    if scn_name.startswith("BREAKOUT_"):
        breakout_side = "BUY" if "BUY" in scn_name else "SELL"
    cvd = update_cvd_snapshot(zone=zone, breakout_side=breakout_side)

    # ── Range locked durumu ───────────────────────────────────────────────────
    s1h_data = (structure.get("1h") or {})
    range_locked = s1h_data.get("range_locked", False) or getattr(state, "v3_range_locked", False)
    # ─────────────────────────────────────────────────────────────────────────

    if state.in_position:
        current_side = str(state.pos_side or "").upper()

        # ── Breakout ters yön: pozisyona ters kırılım → hemen çevir ─────────
        if scn_name.startswith("BREAKOUT_") and current_side in ("LONG", "SHORT"):
            breakout_direction = "LONG" if "BUY" in scn_name else "SHORT"
            if breakout_direction != current_side:
                try:
                    from engine.entry_v3 import update_entry as _ue

                    bo_signal = _ue(allow_in_position=True)
                    bo_rr = float((bo_signal or {}).get("rr", 0) or 0)
                    bo_min_rr = float(getattr(cfg, "V3_MIN_RR_RATIO", 2.0) or 2.0)
                    if bo_signal and bo_signal.get("valid") and bo_rr >= bo_min_rr:
                        ref_s_bo = float(
                            scenario.get("ref_support") or levels.get("active_support") or 0
                        )
                        ref_r_bo = float(
                            scenario.get("ref_resistance") or levels.get("active_resistance") or 0
                        )
                        bo_break = ref_r_bo if breakout_direction == "LONG" else ref_s_bo
                        bo_details = {
                            "direction": breakout_direction,
                            "price": float(bo_signal.get("price", 0) or px),
                            "signal_price": float(bo_signal.get("price", 0) or px),
                            "sl": float(bo_signal.get("sl", 0) or 0),
                            "tp1": float(bo_signal.get("tp1", 0) or 0),
                            "tp2": float(bo_signal.get("tp2", 0) or 0),
                            "rr": bo_rr,
                            "entry_reason": (
                                f"BREAKOUT_REVERSE {current_side}→{breakout_direction} "
                                f"senaryo={scn_name}"
                            ),
                            "range_active_level": ref_s_bo if breakout_direction == "LONG" else ref_r_bo,
                            "break_level": bo_break,
                            "v3_mode": True,
                            "v3_scenario": scn_name,
                        }
                        bo_snap = {
                            "action": breakout_direction,
                            "reason": bo_details["entry_reason"],
                            "details": bo_details,
                            "levels": levels,
                            "structure": structure,
                            "scenario": scenario,
                            "cvd": cvd,
                            "entry": bo_signal,
                            "reverse_signal": True,
                            "reverse_from": current_side,
                        }
                        log.info(
                            f"[BREAKOUT_REVERSE] {current_side}→{breakout_direction} "
                            f"senaryo={scn_name} RR={bo_rr:.2f} px={px:.2f}"
                        )
                        return _commit_decision(bo_snap, flow_tag=flow_tag, flow_force=True)
                except Exception as _bo_err:
                    log.debug(f"Breakout reverse kontrol atlandi: {_bo_err}")
        # ─────────────────────────────────────────────────────────────────────

        scores: dict = {}
        theses: dict = {}
        try:
            from engine.direction_score_v3 import compute_probabilistic_decision
            from engine.trade_thesis_v3 import build_trade_theses, thesis_snapshot

            scores = compute_probabilistic_decision(
                levels=levels,
                structure=structure,
                scenario=scenario,
                cvd=cvd,
                entry=None,
            )
            theses = build_trade_theses(levels=levels, scenario=scenario, px=px, cvd=cvd)
            reverse_side = "SHORT" if current_side == "LONG" else "LONG" if current_side == "SHORT" else ""
            thesis_key = "short" if reverse_side == "SHORT" else "long"
            reverse_thesis = theses.get(thesis_key) if reverse_side else None
            if (
                reverse_thesis is not None
                and getattr(reverse_thesis, "state", "") == "VALID"
                and getattr(reverse_thesis, "direction", "") == reverse_side
                and _score_supports_side(reverse_side, scores, reverse=True)
            ):
                side_entry = reverse_thesis.entry
                rr = float(side_entry.get("rr", 0) or 0)
                # Swing high fallback tezi için daha düşük min RR kabul edilir
                is_swing_fallback = str(
                    getattr(reverse_thesis, "thesis_type", "") or ""
                ) == "SWING_HIGH_REJECTION"
                min_rr = float(
                    getattr(cfg, "V3_REVERSE_SWING_HIGH_MIN_RR", 1.5)
                    if is_swing_fallback
                    else getattr(cfg, "V3_MIN_RR_RATIO", 2.0)
                )
                if side_entry.get("valid") and rr >= min_rr:
                    details = _build_v3_thesis_details(
                        action=reverse_side,
                        side_entry=side_entry,
                        levels=levels,
                        scenario=scenario,
                        px=px,
                        rr=rr,
                        scores=scores,
                        range_locked=range_locked,
                        selected_thesis=reverse_thesis,
                        reverse_from=current_side,
                    )
                    snap = {
                        "action": reverse_side,
                        "reason": details["entry_reason"],
                        "details": details,
                        "levels": levels,
                        "structure": structure,
                        "scenario": scenario,
                        "cvd": cvd,
                        "entry": side_entry,
                        "direction_scores": scores,
                        "trade_thesis": thesis_snapshot(theses),
                        "reverse_signal": True,
                        "reverse_from": current_side,
                    }
                    return _commit_decision(snap, flow_tag=flow_tag, flow_force=True)
        except Exception as e:
            log.debug(f"Reverse thesis kontrolu atlandi: {e}")
        snap = {
            "action": "WAIT",
            "reason": "pozisyon acik",
            "levels": levels,
            "structure": structure,
            "scenario": scenario,
            "cvd": cvd,
            "entry": signal,
            "direction_scores": scores,
            "trade_thesis": thesis_snapshot(theses) if theses else {},
        }
        try:
            from engine.reject_reason_v3 import attach_reject_to_snap

            attach_reject_to_snap(snap, reasons=["pozisyon acik"], trade_candidate=False)
        except Exception:
            pass
        return _commit_decision(snap, flow_tag=flow_tag, flow_force=flow_force)

    if getattr(cfg, "V3_SCORE_DECISION_ENABLED", True):
        return _update_decision_probabilistic(
            px=px,
            levels=levels,
            structure=structure,
            scenario=scenario,
            cvd=cvd,
            signal=signal,
            range_locked=range_locked,
            flow_tag=flow_tag,
            flow_force=flow_force,
        )

    reasons: list[str] = []
    expected_move: dict = {}
    support = levels.get("support") or {}
    resistance = levels.get("resistance") or {}
    support_ok = support and int(support.get("score", 0)) >= cfg.V3_LEVEL_SCORE_MEDIUM
    resistance_ok = resistance and int(resistance.get("score", 0)) >= cfg.V3_LEVEL_SCORE_MEDIUM

    is_breakout = scn_name.startswith("BREAKOUT_")
    is_range = scn_name in ("RANGE_BUY", "RANGE_SELL")
    ref_s = float(scenario.get("ref_support") or levels.get("active_support") or 0)
    ref_r = float(scenario.get("ref_resistance") or levels.get("active_resistance") or 0)

    if not is_breakout and not is_range:
        sr_active = bool(
            (support or {}).get("sr_source")
            or (resistance or {}).get("sr_source")
            or levels.get("active_locked")
        )
        has_band = ref_s > 0 and ref_r > ref_s
        if sr_active and has_band and levels.get("range_valid"):
            pass
        elif not support_ok or not resistance_ok:
            reasons.append("Destek/direnc yok veya zayif.")
    if not is_breakout:
        if not levels.get("range_valid"):
            reasons.append("Range gecersiz.")
    if is_breakout:
        if scn_name == "BREAKOUT_SELL" and ref_s <= 0:
            reasons.append("Kirilim referans destegi yok.")
        elif scn_name == "BREAKOUT_BUY" and ref_r <= 0:
            reasons.append("Kirilim referans direnci yok.")

    zone_blocks = zone == "MID_RANGE" and not is_breakout
    if zone_blocks:
        reasons.append("Fiyat band ortasinda (zone) — destek/dirence yaklasinca senaryo acilir.")
    elif scn_name == "WAIT":
        detail = str(scenario.get("detail") or "")
        if detail:
            reasons.append(f"Senaryo: {detail}")
        ms = levels.get("market_state") or getattr(state, "v3_market_state", None) or {}
        collapse = ms.get("collapse") or {}
        struct_u = ms.get("structure") or {}
        sig = ms.get("signals") or {}
        if collapse.get("detail"):
            reasons.append(f"Durum: {collapse.get('detail')}")
        elif struct_u.get("summary"):
            reasons.append(f"Rejim: {struct_u.get('trend')} — {struct_u.get('summary')}")
        for r in sig.get("reasons") or []:
            if r not in str(reasons):
                reasons.append(r)
        if collapse.get("mode") == "NO_TRADE" and not collapse.get("allow_trade"):
            if not any("Collapse" in x for x in reasons):
                reasons.append(
                    f"Collapse NO_TRADE (skor={collapse.get('state_score')}) — belirsiz rejim"
                )
        tm = levels.get("trade_map") or getattr(state, "v3_trade_map", None) or {}
        if tm.get("message") and str(tm.get("bias")) == "BEAR":
            reasons.append(str(tm.get("message")))

    # Breakout'larda CVD hard-veto kaldırıldı — artık score penaltısı uygulanıyor.
    # Sadece CVD kesinlikle zıt yöndeyse (breakout yönüne karşı confirmed) bloke et.
    if is_breakout and not cvd.get("confirmed"):
        cvd_dir = str(cvd.get("direction") or "").upper()
        bo_side = "BULL" if "BUY" in scn_name else "BEAR"
        opposite = "BEAR" if bo_side == "BULL" else "BULL"
        buy_ratio = float(cvd.get("buy_ratio", 0.5) or 0.5)
        # BUY breakout'ta satış ağırlıklı CVD (ratio<0.30) veya
        # SELL breakout'ta alış ağırlıklı CVD (ratio>0.70) → gerçekten ters
        cvd_actively_opposing = (
            cvd_dir == opposite
            and (
                (bo_side == "BULL" and buy_ratio < 0.30)
                or (bo_side == "BEAR" and buy_ratio > 0.70)
            )
        )
        if cvd_actively_opposing:
            reasons.append("CVD kesinlikle ters yonde — breakout engellendi.")
        else:
            log.debug(
                f"[CVD-SOFT] {scn_name} CVD teyitsiz ama bloke edilmedi "
                f"(cvd={cvd_dir} ratio={cvd.get('buy_ratio','?'):.2f})"
                if isinstance(cvd.get("buy_ratio"), float)
                else f"[CVD-SOFT] {scn_name} CVD={cvd_dir} — soft geçti"
            )

    # ── DÜZELTME: range_locked → CVD "confirmed" şartını gevşet ──────────────
    # Eski: RANGE_BUY'da CVD confirmed olmadan entry_v3 preview dönüyordu
    # Yeni: range_locked=True ise bu kontrol entry_v3 içinde zaten gevşetildi
    # Burada sadece loglama amaçlı bilgi ekleyelim
    if range_locked and is_range:
        log.debug(
            f"[V3Decision] range_locked=True + {scn_name} → CVD confirmed şartı gevşek"
        )
    # ─────────────────────────────────────────────────────────────────────────

    trade_candidate = scn_name not in ("WAIT",) and not zone_blocks
    ms_dec = levels.get("market_state") or getattr(state, "v3_market_state", None) or {}

    if trade_candidate and getattr(cfg, "V3_UNIFIED_STATE", True):
        from engine.trade_verdict_v3 import trade_entry_allowed

        side_gate = "BUY" if "BUY" in scn_name or scn_name == "RANGE_BUY" else "SELL"
        ok_gate, gate_msg = trade_entry_allowed(ms_dec, scn_name, side_gate)
        if not ok_gate:
            reasons.append(gate_msg)
            trade_candidate = False

    if trade_candidate and getattr(cfg, "V3_ZONE_LIFECYCLE", True):
        from engine.expected_move_v3 import compute_expected_move, expected_move_blocks

        em_side = "BUY" if scn_name in ("RANGE_BUY", "BREAKOUT_BUY") else "SELL"
        if scn_name.startswith("BREAKOUT_"):
            em_side = "BUY" if "BUY" in scn_name else "SELL"
        expected_move = compute_expected_move(
            em_side,
            px,
            support=float(levels.get("active_support") or ref_s or 0),
            resistance=float(levels.get("active_resistance") or ref_r or 0),
        )
        state.v3_expected_move = expected_move
        em_blk, em_msg = expected_move_blocks(
            em_side,
            px,
            support=float(levels.get("active_support") or ref_s or 0),
            resistance=float(levels.get("active_resistance") or ref_r or 0),
        )
        if em_blk:
            reasons.append(em_msg)

    if trade_candidate:
        if not signal.get("valid"):
            reasons.append("Giris noktasi olusmadi.")
        elif float(signal.get("rr", 0) or 0) < cfg.V3_MIN_RR_RATIO:
            reasons.append(
                f"RR yetersiz: {float(signal.get('rr', 0) or 0):.2f} < {cfg.V3_MIN_RR_RATIO:.2f}"
            )

    _log_entry_check(
        px=px,
        levels=levels,
        scenario=scenario,
        cvd=cvd,
        signal=signal,
        trade_candidate=trade_candidate,
        reasons=reasons,
    )

    if reasons:
        snap = {
            "action": "WAIT",
            "reason": " | ".join(reasons),
            "levels": levels,
            "structure": structure,
            "scenario": scenario,
            "cvd": cvd,
            "entry": signal,
            "expected_move": expected_move or state.v3_expected_move or {},
        }
        try:
            from engine.reject_reason_v3 import attach_reject_to_snap

            attach_reject_to_snap(
                snap, reasons=reasons, trade_candidate=trade_candidate
            )
        except Exception as e:
            log.debug(f"[REJECT] attach atlandi: {e}")
        return _commit_decision(snap, flow_tag=flow_tag, flow_force=flow_force)

    action = "LONG" if str(signal.get("direction") or "") == "BUY" else "SHORT"
    support_price = float((levels.get("support") or {}).get("price", 0) or 0)
    resistance_price = float((levels.get("resistance") or {}).get("price", 0) or 0)
    if is_breakout:
        if ref_s > 0:
            support_price = ref_s
        if ref_r > 0:
            resistance_price = ref_r
    break_level = 0.0
    if "BREAKOUT" in scn_name:
        break_level = resistance_price if action == "LONG" else support_price
    details = {
        "direction": action,
        "price": float(signal.get("price", 0) or 0),
        "signal_price": float(signal.get("price", 0) or 0),
        "sl": float(signal.get("sl", 0) or 0),
        "tp1": float(signal.get("tp1", 0) or 0),
        "tp2": float(signal.get("tp2", 0) or 0),
        "rr": float(signal.get("rr", 0) or 0),
        "entry_reason": f"v3 {scenario.get('name')} + {signal.get('entry_type')}",
        "range_active_level": support_price if action == "LONG" else resistance_price,
        "break_level": break_level,
        "v3_mode": True,
        "v3_scenario": scn_name,
        "v3_entry_type": str(signal.get("entry_type") or ""),
        "v3_support": support_price,
        "v3_resistance": resistance_price,
        "range_locked": range_locked,
    }
    snap = {
        "action": action,
        "reason": details["entry_reason"],
        "details": details,
        "levels": levels,
        "structure": structure,
        "scenario": scenario,
        "cvd": cvd,
        "entry": signal,
        "expected_move": expected_move or state.v3_expected_move or {},
    }
    if (expected_move or {}).get("trade_priority", 0):
        details["trade_priority"] = int(expected_move.get("trade_priority", 0))
    return _commit_decision(snap, flow_tag=flow_tag, flow_force=flow_force)


def _update_decision_probabilistic(
    *,
    px: float,
    levels: dict,
    structure: dict,
    scenario: dict,
    cvd: dict,
    signal: dict,
    range_locked: bool,
    flow_tag: str = "",
    flow_force: bool = False,
) -> dict:
    """Olasilik skoru — veto yok; en yuksek prob > esik ise giris."""
    from engine.direction_score_v3 import compute_probabilistic_decision
    from engine.entry_v3 import build_entry_for_score_side
    from engine.trade_thesis_v3 import build_trade_theses, thesis_snapshot

    scores = compute_probabilistic_decision(
        levels=levels,
        structure=structure,
        scenario=scenario,
        cvd=cvd,
        entry=None,
    )
    scn_name = str(scenario.get("name") or "WAIT")
    theses = {}
    selected_thesis = None
    if getattr(cfg, "V3_THESIS_DECISION_ENABLED", True):
        theses = build_trade_theses(levels=levels, scenario=scenario, px=px, cvd=cvd)
        selected_thesis = theses.get("selected")
        action = str(getattr(selected_thesis, "direction", "") or "WAIT")
    else:
        action = str(scores.get("action") or "WAIT")

    _log_entry_check(
        px=px,
        levels=levels,
        scenario=scenario,
        cvd=cvd,
        signal=signal,
        trade_candidate=True,
        reasons=[
            f"prob L={scores.get('prob_long_pct')}% S={scores.get('prob_short_pct')}% "
            f"mode={scores.get('decision_mode')}",
            f"thesis={theses.get('reason', 'disabled') if theses else 'disabled'}",
        ],
    )

    if action == "WAIT":
        wait_entry = signal
        if theses:
            thesis_entries = [
                t for t in (theses.get("short"), theses.get("long"))
                if hasattr(t, "entry")
            ]
            if thesis_entries:
                wait_entry = max(
                    thesis_entries,
                    key=lambda t: float(getattr(t, "rr", 0) or 0),
                ).entry
        if getattr(cfg, "V3_THESIS_DECISION_ENABLED", True):
            reason = (
                f"TEZ YOK | {theses.get('reason', 'Executable tez yok')} | "
                f"L={scores.get('long_score'):.0f} S={scores.get('short_score'):.0f} "
                f"pL={scores.get('prob_long_pct')}% pS={scores.get('prob_short_pct')}%"
            )
            reject_reason = "THESIS_WAIT"
        else:
            reason = (
                f"EDGE YOK | L={scores.get('long_score'):.0f} S={scores.get('short_score'):.0f} "
                f"pL={scores.get('prob_long_pct')}% pS={scores.get('prob_short_pct')}%"
            )
            reject_reason = "SCORE_EDGE"
        snap = {
            "action": "WAIT",
            "reason": reason,
            "levels": levels,
            "structure": structure,
            "scenario": scenario,
            "cvd": cvd,
            "entry": wait_entry,
            "direction_scores": scores,
            "trade_thesis": thesis_snapshot(theses) if theses else {},
            "reject_reason": reject_reason,
        }
        return _commit_decision(snap, flow_tag=flow_tag, flow_force=flow_force)

    # --- Minimum edge kapısı (tez yolu da skoru baypas edemez) ---
    # Tez geçerli olsa bile, yön olasılığı eşiğin altındaysa (50/50 = edge yok)
    # giriş yapma. "EDGE YOK iken thesis LONG açma" sorununun kökü.
    _side_prob = float(
        scores.get("prob_long_pct" if action == "LONG" else "prob_short_pct") or 0
    ) / 100.0
    _min_edge = float(getattr(cfg, "V3_THESIS_MIN_PROB", 0.55) or 0.55)
    if _side_prob < _min_edge:
        snap = {
            "action": "WAIT",
            "reason": (
                f"EDGE YOK (tez yolu): {action} prob %{_side_prob * 100:.1f} "
                f"< %{_min_edge * 100:.0f} eşik — yazı-tura giriş engellendi"
            ),
            "levels": levels,
            "structure": structure,
            "scenario": scenario,
            "cvd": cvd,
            "entry": signal,
            "direction_scores": scores,
            "trade_thesis": thesis_snapshot(theses) if theses else {},
            "reject_reason": "LOW_EDGE",
        }
        return _commit_decision(snap, flow_tag=flow_tag, flow_force=flow_force)

    # --- Tradeability / Conviction Gate ---
    # Skor LONG/SHORT dese bile piyasa işlenebilir değilse (akışa ters veya chop)
    # girişi WAIT'e çevir. Açık pozisyon/çıkışları etkilemez.
    from engine.tradeability_v3 import assess_tradeability

    _tradeable, _gate_reason = assess_tradeability(
        action, levels, cvd, px=px, market_state=None
    )
    if not _tradeable:
        snap = {
            "action": "WAIT",
            "reason": f"TRADEABILITY: {_gate_reason}",
            "levels": levels,
            "structure": structure,
            "scenario": scenario,
            "cvd": cvd,
            "entry": signal,
            "direction_scores": scores,
            "trade_thesis": thesis_snapshot(theses) if theses else {},
            "reject_reason": "TRADEABILITY",
        }
        return _commit_decision(snap, flow_tag=flow_tag, flow_force=flow_force)

    if selected_thesis is not None:
        side_entry = selected_thesis.entry
    else:
        side_entry = build_entry_for_score_side(action, levels, px, scenario)
    min_rr = float(getattr(cfg, "V3_MIN_RR_RATIO", 2.0) or 2.0)
    rr = float(side_entry.get("rr", 0) or 0)

    # Dar bant + güçlü yön: RR eşiğini gevşet (1.5). Dar kanalda RR düşük ama
    # isabet yüksek; SHORT/LONG skoru baskınsa düşük RR'yi kabul et.
    _prob = float(
        scores.get("prob_short_pct" if action == "SHORT" else "prob_long_pct") or 0
    )
    _band_w = 0.0
    _bs = float(levels.get("active_support") or 0)
    _br = float(levels.get("active_resistance") or 0)
    if _bs > 0 and _br > _bs:
        _band_w = (_br - _bs) / _br  # bant genişliği oranı
    _narrow = 0 < _band_w <= float(getattr(cfg, "V3_NARROW_BAND_PCT", 0.02) or 0.02)
    _strong_dir = _prob >= float(getattr(cfg, "V3_STRONG_DIR_PROB", 65.0) or 65.0)
    if _narrow and _strong_dir:
        min_rr = float(getattr(cfg, "V3_MIN_RR_NARROW_STRONG", 1.5) or 1.5)

    if not side_entry.get("valid") and rr < min_rr:
        snap = {
            "action": "WAIT",
            "reason": f"Tez {action} ama RR/giris yetersiz (RR={rr:.2f})",
            "levels": levels,
            "structure": structure,
            "scenario": scenario,
            "cvd": cvd,
            "entry": side_entry,
            "direction_scores": scores,
            "trade_thesis": thesis_snapshot(theses) if theses else {},
            "reject_reason": "RR_TOO_LOW",
        }
        return _commit_decision(snap, flow_tag=flow_tag, flow_force=flow_force)

    details = _build_v3_thesis_details(
        action=action,
        side_entry=side_entry,
        levels=levels,
        scenario=scenario,
        px=px,
        rr=rr,
        scores=scores,
        range_locked=range_locked,
        selected_thesis=selected_thesis,
    )
    snap = {
        "action": action,
        "reason": details["entry_reason"],
        "details": details,
        "levels": levels,
        "structure": structure,
        "scenario": scenario,
        "cvd": cvd,
        "entry": side_entry,
        "direction_scores": scores,
        "trade_thesis": thesis_snapshot(theses) if theses else {},
    }
    return _commit_decision(snap, flow_tag=flow_tag, flow_force=flow_force)


def get_decision_snapshot() -> dict:
    snap = state.v3_decision or {}
    if not snap:
        snap = update_decision()
    return snap
