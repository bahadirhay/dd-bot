"""
engine/attribution_v3.py — Trade Attribution Engine.

Her karar: trade_reason (+) ve block_reason (-) bileşen skorları.
Amaç: hangi filtre gerçekten alfa üretiyor / kaç iyi işlemi öldürüyor — ölçüm.

Kullanım:
  attr = build_attribution(snap, intended_side="SELL", trade_candidate=True)
  log_attribution_event(attr)  # db + state
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from core.config import cfg
from core.logger import get_logger
from core.state import state, effective_price

log = get_logger("AttributionV3")

_last_log_key = ""

# block_reason: olumsuz katki (toplam negatif)
BLOCK_WEIGHTS: dict[str, int] = {
    "cluster": -30,
    "liquidity_chase": -25,
    "expected_move": -20,
    "archive_memory": -10,
    "zone_mid": -18,
    "zone_lifecycle": -15,
    "cvd": -15,
    "rr": -12,
    "entry": -12,
    "levels_weak": -10,
    "range_invalid": -10,
    "scenario_wait": -8,
    "htf_wall": -22,
    "position_open": -100,
    "verdict_timing": -20,
    "collapse": -25,
    "other": -5,
}

# trade_reason: olumlu katki (toplam pozitif)
TRADE_WEIGHTS: dict[str, int] = {
    "zone": 25,
    "liquidity": 20,
    "trend": 15,
    "expected_move": 10,
    "vacuum": 5,
    "cvd": 12,
    "scenario": 8,
    "rr": 8,
}

_REASON_TO_BLOCK: list[tuple[str, str]] = [
    (r"cluster|savas alani|kovalama yok", "cluster"),
    (r"likidite hedefi|short kovalama blok|long kovalama blok|liq=", "liquidity_chase"),
    (r"expected move|EM zayif|RR yetersiz.*oncelik", "expected_move"),
    (r"Expected move zayif", "expected_move"),
    (r"RR yetersiz", "rr"),
    (r"hafiza|archive|lifecycle=|kanitli destek/direnc degil", "zone_lifecycle"),
    (r"band ortasinda|MID_RANGE|zone\)", "zone_mid"),
    (r"CVD|teyit etmiyor", "cvd"),
    (r"Giris noktasi|entry", "entry"),
    (r"Destek/direnc yok|zayif", "levels_weak"),
    (r"Range gecersiz", "range_invalid"),
    (r"HTF duvar|1h direnc duvari|1h destek duvari", "htf_wall"),
    (r"Senaryo:|Kosullar olusmadi|Band yerlesmiyor", "scenario_wait"),
    (r"pozisyon acik", "position_open"),
    (r"trade verdict|verdict:|timing blok|TRADE_LATER|OBSERVE|TIMING_BLOCK", "verdict_timing"),
    (r"Collapse rejim|Collapse NO_TRADE|allow_trade=hayir", "collapse"),
]


def _intended_side_from_snap(snap: dict) -> str:
    action = str(snap.get("action") or "WAIT").upper()
    if action in ("LONG", "SHORT"):
        return "BUY" if action == "LONG" else "SELL"
    scenario = snap.get("scenario") or {}
    name = str(scenario.get("name") or "")
    if name == "RANGE_BUY" or name == "BREAKOUT_BUY":
        return "BUY"
    if name == "RANGE_SELL" or name == "BREAKOUT_SELL":
        return "SELL"
    if "BUY" in name:
        return "BUY"
    if "SELL" in name:
        return "SELL"
    return ""


def _classify_blocks(reason_text: str) -> dict[str, int]:
    """Metin nedenlerinden block_reason skorları."""
    blocks: dict[str, int] = {}
    text = str(reason_text or "").lower()
    if not text.strip():
        return blocks
    for pattern, code in _REASON_TO_BLOCK:
        if re.search(pattern, text, re.IGNORECASE):
            w = BLOCK_WEIGHTS.get(code, -5)
            blocks[code] = min(blocks.get(code, 0), w)
    if not blocks and text and "wait" not in text:
        blocks["other"] = BLOCK_WEIGHTS["other"]
    return blocks


def _probe_silent_blocks(side: str, price: float) -> dict[str, int]:
    """Karar metninde yazilmamis ama aktif bloklari tespit et."""
    blocks: dict[str, int] = {}
    if not side or price <= 0:
        return blocks
    if not getattr(cfg, "V3_ZONE_LIFECYCLE", True):
        return blocks
    try:
        from engine.zone_engine_v3 import liquidity_blocks_chase

        blk, _ = liquidity_blocks_chase(side, price)
        if blk:
            blocks["liquidity_chase"] = BLOCK_WEIGHTS["liquidity_chase"]
    except Exception:
        pass
    try:
        from engine.expected_move_v3 import expected_move_blocks

        levels = state.v3_levels or {}
        active = levels.get("active") or {}
        s = float((active.get("support") or {}).get("price", 0) or 0)
        r = float((active.get("resistance") or {}).get("price", 0) or 0)
        blk, _ = expected_move_blocks(side, price, support=s, resistance=r)
        if blk:
            blocks["expected_move"] = BLOCK_WEIGHTS["expected_move"]
    except Exception:
        pass
    return blocks


def _score_zone(levels: dict, side: str) -> int:
    side = str(side or "").upper()
    support = levels.get("support") or {}
    resistance = levels.get("resistance") or {}
    zone = str(levels.get("zone") or "")
    base = 0
    if side in ("BUY", "LONG") and zone == "NEAR_SUPPORT":
        base = TRADE_WEIGHTS["zone"]
        st = int(support.get("lifecycle_strength", 0) or support.get("score", 0) or 0)
        base = min(TRADE_WEIGHTS["zone"], int(base * (0.5 + st / 200)))
    elif side in ("SELL", "SHORT") and zone == "NEAR_RESISTANCE":
        base = TRADE_WEIGHTS["zone"]
        st = int(resistance.get("lifecycle_strength", 0) or resistance.get("score", 0) or 0)
        base = min(TRADE_WEIGHTS["zone"], int(base * (0.5 + st / 200)))
    elif zone == "MID_RANGE":
        base = TRADE_WEIGHTS["zone"] // 4
    lc = str((support if side in ("BUY", "LONG") else resistance).get("lifecycle") or "")
    if lc in ("ACTIVE", "ROLE_REVERSAL"):
        base = min(TRADE_WEIGHTS["zone"], base + 5)
    return max(0, base)


def _score_liquidity(side: str, levels: dict) -> int:
    bias = (levels.get("liquidity_bias") or getattr(state, "v3_liquidity_bias", None) or {})
    b = str(bias.get("bias") or "NEUTRAL").upper()
    side = str(side or "").upper()
    sc = 0
    if side in ("BUY", "LONG") and b == "UP":
        sc = TRADE_WEIGHTS["liquidity"]
    elif side in ("SELL", "SHORT") and b == "DOWN":
        sc = TRADE_WEIGHTS["liquidity"]
    elif b == "NEUTRAL":
        sc = TRADE_WEIGHTS["liquidity"] // 3
    up = float(bias.get("up_score", 0) or 0)
    dn = float(bias.get("down_score", 0) or 0)
    if side in ("SELL", "SHORT") and dn > up * 1.1:
        sc = max(sc, TRADE_WEIGHTS["liquidity"] // 2)
    if side in ("BUY", "LONG") and up > dn * 1.1:
        sc = max(sc, TRADE_WEIGHTS["liquidity"] // 2)
    return sc


def _score_trend(side: str) -> int:
    mt = getattr(state, "v3_multi_tf_trend", None) or {}
    d = str(mt.get("direction") or "NEUTRAL").upper()
    ts = int(mt.get("trend_score", 0) or 0)
    side = str(side or "").upper()
    if d == "NEUTRAL" or ts < 10:
        return TRADE_WEIGHTS["trend"] // 4
    align = (side in ("BUY", "LONG") and d == "UP") or (side in ("SELL", "SHORT") and d == "DOWN")
    if align:
        return min(TRADE_WEIGHTS["trend"], int(TRADE_WEIGHTS["trend"] * (0.4 + ts / 100)))
    return max(0, TRADE_WEIGHTS["trend"] // 5)


def _score_expected_move(snap: dict) -> int:
    em = snap.get("expected_move") or getattr(state, "v3_expected_move", None) or {}
    if not em.get("valid"):
        return 0
    pri = int(em.get("trade_priority", 0) or 0)
    rr = float(em.get("rr", 0) or 0)
    sc = min(TRADE_WEIGHTS["expected_move"], pri // 5)
    if rr >= float(getattr(cfg, "V3_MIN_RR_RATIO", 2.0) or 2.0):
        sc = min(TRADE_WEIGHTS["expected_move"], sc + 4)
    return sc


def _score_vacuum(side: str) -> int:
    vac = int(getattr(state, "v3_vacuum_score", 0) or 0)
    if vac < 35:
        return 0
    side = str(side or "").upper()
    if side in ("SELL", "SHORT"):
        return min(TRADE_WEIGHTS["vacuum"], int(vac * TRADE_WEIGHTS["vacuum"] / 100))
    return TRADE_WEIGHTS["vacuum"] // 3 if vac >= 50 else 0


def _score_cvd(snap: dict, side: str) -> int:
    cvd = snap.get("cvd") or {}
    if not cvd.get("confirmed"):
        return 0
    d = str(cvd.get("direction") or "").upper()
    side = str(side or "").upper()
    if (side in ("BUY", "LONG") and d == "BULL") or (side in ("SELL", "SHORT") and d == "BEAR"):
        return TRADE_WEIGHTS["cvd"]
    return TRADE_WEIGHTS["cvd"] // 3


def _score_scenario(snap: dict) -> int:
    name = str((snap.get("scenario") or {}).get("name") or "")
    if name in ("RANGE_BUY", "RANGE_SELL"):
        return TRADE_WEIGHTS["scenario"]
    if name.startswith("BREAKOUT_"):
        return TRADE_WEIGHTS["scenario"] + 2
    return 0


def _score_rr(snap: dict) -> int:
    entry = snap.get("entry") or {}
    rr = float(entry.get("rr", 0) or 0)
    if rr <= 0:
        em = snap.get("expected_move") or {}
        rr = float(em.get("rr", 0) or 0)
    min_rr = float(getattr(cfg, "V3_MIN_RR_RATIO", 2.0) or 2.0)
    if rr >= min_rr * 1.5:
        return TRADE_WEIGHTS["rr"]
    if rr >= min_rr:
        return TRADE_WEIGHTS["rr"] - 2
    return 0


def _build_decision_context(snap: dict, attr: dict) -> dict[str, Any]:
    """DB context_json: zone, path, RR geometrisi, yon skoru."""
    levels = snap.get("levels") or {}
    ch = snap.get("channel_decision") or {}
    scenario = snap.get("scenario") or {}
    entry = snap.get("entry") or ch.get("entry") or {}
    scores = snap.get("direction_scores") or ch.get("direction_scores") or {}

    zone = str(ch.get("zone") or levels.get("zone") or scenario.get("channel_zone") or "")
    path = str(ch.get("path") or scenario.get("channel_path") or "")
    candidate = ""
    if path == "fade":
        if zone == "NEAR_SUPPORT":
            candidate = "LONG"
        elif zone == "NEAR_RESISTANCE":
            candidate = "SHORT"

    ls = float(scores.get("long_score") or snap.get("long_score") or 0)
    ss = float(scores.get("short_score") or snap.get("short_score") or 0)
    favored = str(scores.get("action") or "").upper()
    if not favored or favored == "WAIT":
        if ss > ls + 3:
            favored = "SHORT"
        elif ls > ss + 3:
            favored = "LONG"
        else:
            favored = "WAIT"

    details = snap.get("details") or {}
    ms = levels.get("market_state") or getattr(state, "v3_market_state", None) or {}
    collapse = ms.get("collapse") or {}
    tv = ms.get("trade_verdict") or {}

    px = float(attr.get("price") or snap.get("price") or 0)
    sl = float(entry.get("sl") or details.get("sl") or 0)
    tp = float(entry.get("tp1") or entry.get("tp") or details.get("tp1") or 0)
    tp2 = float(entry.get("tp2") or details.get("tp2") or 0)
    rr = float(entry.get("rr") or details.get("rr") or 0)
    sl_source = str(entry.get("sl_source") or details.get("sl_source") or "")
    sl_anchor = float(entry.get("sl_anchor") or details.get("sl_anchor") or 0)
    risk = abs(px - sl) if px > 0 and sl > 0 else 0.0
    reward = abs((tp2 or tp) - px) if px > 0 and (tp2 or tp) > 0 else 0.0

    reject_layer = str(attr.get("reject_layer") or "")
    if not reject_layer and attr.get("reject_reason"):
        try:
            from engine.reject_reason_v3 import reject_layer_for

            reject_layer = reject_layer_for(str(attr.get("reject_reason") or ""))
        except Exception:
            reject_layer = ""

    return {
        "liquidity_bias": getattr(state, "v3_liquidity_bias", ""),
        "vacuum_score": int(getattr(state, "v3_vacuum_score", 0) or 0),
        "multi_tf_trend": getattr(state, "v3_multi_tf_trend", ""),
        "zone": zone,
        "path": path,
        "channel_candidate": candidate,
        "favored_side": favored,
        "intended_side": str(attr.get("intended_side") or ""),
        "scenario": str(attr.get("scenario") or ""),
        "trade_candidate": bool(attr.get("trade_candidate")),
        "reject_reason": str(attr.get("reject_reason") or ""),
        "reject_layer": reject_layer,
        "entry_sl": sl,
        "entry_tp": tp,
        "entry_tp2": tp2,
        "entry_rr": rr,
        "sl_source": sl_source,
        "sl_anchor": sl_anchor,
        "risk_usd": round(risk, 4),
        "reward_usd": round(reward, 4),
        "band_support": float(levels.get("active_support") or 0),
        "band_resistance": float(levels.get("active_resistance") or 0),
        "band_width_usd": round(
            max(
                float(levels.get("active_resistance") or 0)
                - float(levels.get("active_support") or 0),
                0.0,
            ),
            4,
        ),
        "entry_type": str(entry.get("entry_type") or ""),
        "channel_authority": bool(
            snap.get("channel_authority") or levels.get("channel_authority")
        ),
        "collapse_mode": str(collapse.get("mode") or collapse.get("soft") or ""),
        "collapse_score": int(collapse.get("score") or 0),
        "verdict_tier": str(tv.get("tier") or ""),
        "verdict_allow_entry": bool(tv.get("allow_entry")),
    }


def build_attribution(
    snap: dict,
    *,
    intended_side: str = "",
    trade_candidate: bool = False,
) -> dict[str, Any]:
    """
    trade_reason ve block_reason uret.

    intended_side: senaryonun hedef yonu (BUY/SELL), WAIT olsa bile.
    """
    px = float(
        snap.get("price")
        or (snap.get("levels") or {}).get("price")
        or effective_price()
        or state.mark_price
        or 0
    )
    levels = snap.get("levels") or {}
    action = str(snap.get("action") or "WAIT").upper()
    scenario = snap.get("scenario") or {}
    scn_name = str(scenario.get("name") or "WAIT")
    reason_text = str(snap.get("reason") or "")

    side = str(intended_side or _intended_side_from_snap(snap)).upper()
    if not side and trade_candidate:
        side = "BUY" if scn_name in ("RANGE_BUY", "BREAKOUT_BUY") else "SELL"
        if not side and "BUY" in scn_name:
            side = "BUY"
        if not side and "SELL" in scn_name:
            side = "SELL"

    trade_reason: dict[str, int] = {}
    if side:
        trade_reason["zone"] = _score_zone(levels, side)
        trade_reason["liquidity"] = _score_liquidity(side, levels)
        trade_reason["trend"] = _score_trend(side)
        trade_reason["expected_move"] = _score_expected_move(snap)
        trade_reason["vacuum"] = _score_vacuum(side)
        trade_reason["cvd"] = _score_cvd(snap, side)
        trade_reason["scenario"] = _score_scenario(snap)
        trade_reason["rr"] = _score_rr(snap)
        trade_reason = {k: v for k, v in trade_reason.items() if v > 0}

    block_reason = _classify_blocks(reason_text)
    if action == "WAIT" and side:
        silent = _probe_silent_blocks(side, px)
        for k, v in silent.items():
            if k not in block_reason:
                block_reason[k] = v

    tr_sum = sum(trade_reason.values())
    bl_sum = sum(block_reason.values())
    net = tr_sum + bl_sum

    primary_block = ""
    if block_reason:
        primary_block = min(block_reason.keys(), key=lambda k: block_reason[k])

    primary_support = ""
    if trade_reason:
        primary_support = max(trade_reason.keys(), key=lambda k: trade_reason[k])

    reject_reason = str(snap.get("reject_reason") or "").strip()
    if not reject_reason and action == "WAIT":
        try:
            from engine.reject_reason_v3 import resolve_reject_reason

            reject_reason = resolve_reject_reason(
                snap,
                reasons=[reason_text] if reason_text else [],
                trade_candidate=trade_candidate,
            )
        except Exception:
            reject_reason = ""

    reject_layer = ""
    if reject_reason:
        try:
            from engine.reject_reason_v3 import reject_layer_for

            reject_layer = reject_layer_for(reject_reason)
        except Exception:
            reject_layer = ""

    attr = {
        "ts": time.time(),
        "price": px,
        "action": action,
        "scenario": scn_name,
        "intended_side": side,
        "trade_candidate": bool(trade_candidate),
        "entered": action in ("LONG", "SHORT"),
        "reason_text": reason_text[:2000],
        "trade_reason": trade_reason,
        "block_reason": block_reason,
        "trade_reason_sum": tr_sum,
        "block_reason_sum": bl_sum,
        "net_score": net,
        "primary_block": primary_block,
        "primary_support": primary_support,
        "reject_reason": reject_reason,
        "reject_layer": reject_layer,
        "would_trade": action in ("LONG", "SHORT"),
        "blocked_opportunity": bool(
            trade_candidate and action == "WAIT" and side and scn_name not in ("WAIT",)
        ),
    }
    attr["context"] = _build_decision_context(snap, attr)
    return attr


def format_attribution_line(attr: dict) -> str:
    tr = attr.get("trade_reason") or {}
    bl = attr.get("block_reason") or {}
    tr_s = " ".join(f"{k}:{v:+d}" for k, v in sorted(tr.items()))
    bl_s = " ".join(f"{k}:{v:+d}" for k, v in sorted(bl.items()))
    rej = str(attr.get("reject_reason") or "")
    layer = str(attr.get("reject_layer") or "")
    rej_s = f" reject={rej}" if rej else ""
    layer_s = f" layer={layer}" if layer else ""
    return (
        f"[ATTR] {attr.get('action')} scn={attr.get('scenario')} side={attr.get('intended_side')} "
        f"net={attr.get('net_score')} | +({tr_s or '—'}) | -({bl_s or '—'}) "
        f"blk={attr.get('primary_block') or '—'}{rej_s}{layer_s}"
    )


def maybe_log_attribution(
    snap: dict,
    *,
    trade_candidate: bool = False,
    intended_side: str = "",
    force: bool = False,
) -> dict[str, Any] | None:
    """DB + log; WAIT tekrarlarini dedupe."""
    if not getattr(cfg, "V3_ATTRIBUTION_ENABLED", True):
        return None

    attr = build_attribution(
        snap,
        intended_side=intended_side,
        trade_candidate=trade_candidate,
    )
    state.v3_last_attribution = attr

    action = str(attr.get("action") or "WAIT")
    key = (
        f"{action}|{attr.get('scenario')}|{attr.get('primary_block')}|"
        f"{attr.get('intended_side')}|{round(float(attr.get('price', 0)), 1)}"
    )
    global _last_log_key
    interval = float(getattr(cfg, "V3_ATTRIBUTION_LOG_SEC", 90) or 90)
    now = time.time()
    last_ts = float(getattr(state, "v3_attribution_last_log_ts", 0) or 0)
    periodic = (now - last_ts) >= interval

    should = (
        force
        or action in ("LONG", "SHORT")
        or attr.get("blocked_opportunity")
        or key != _last_log_key
        or periodic
    )
    if not should:
        return attr

    _last_log_key = key
    state.v3_attribution_last_log_ts = now

    line = format_attribution_line(attr)
    log.info(line)

    try:
        from botlog.db import log_v3_attribution

        attr_id = log_v3_attribution(attr)
        attr["attribution_id"] = attr_id
        state.v3_last_attribution = attr
    except Exception as e:
        log.debug(f"[ATTR] db yazilamadi: {e}")

    if action == "WAIT" and attr.get("reject_reason"):
        try:
            from engine.reject_reason_v3 import record_reject

            record_reject(
                snap,
                str(attr["reject_reason"]),
                trade_candidate=bool(trade_candidate),
            )
        except Exception:
            pass
        try:
            from engine.decision_block_stats_v3 import record_decision_wait

            record_decision_wait(attr, context=attr.get("context"))
        except Exception:
            pass

    return attr


def attach_attribution_to_trade(trade_id: int, attr: dict | None = None) -> bool:
    """Acilan isleme attribution bagla; basarisizsa uyari logla."""
    attr = attr or getattr(state, "v3_last_attribution", None) or {}
    if trade_id <= 0:
        return False
    if not attr:
        log.warning(f"[ATTR] trade={trade_id} link BASARISIZ — attribution yok")
        return False

    action = str(
        attr.get("action")
        or getattr(state, "pos_side", "")
        or attr.get("intended_side")
        or ""
    ).upper()
    if action in ("BUY",):
        action = "LONG"
    elif action in ("SELL",):
        action = "SHORT"

    linked_id = 0
    try:
        from botlog.db import link_v3_attribution_trade

        linked_id = link_v3_attribution_trade(
            int(attr.get("attribution_id", 0) or 0),
            trade_id,
            entered=1,
            action=action,
            intended_side=str(attr.get("intended_side") or action or ""),
        )
    except Exception as e:
        log.warning(f"[ATTR] trade={trade_id} link hata: {e}")
        return False

    if linked_id > 0:
        log.info(
            f"[ATTR] trade={trade_id} linked attr_id={linked_id} "
            f"scn={attr.get('scenario')} rr={((attr.get('context') or {}).get('entry_rr') or 0)}"
        )
    else:
        log.warning(
            f"[ATTR] trade={trade_id} link BASARISIZ — attr_id yok "
            f"(son scn={attr.get('scenario')})"
        )
        return False

    try:
        ctx = attr.get("context") or {}
        notes = json.dumps(
            {
                "trade_reason": attr.get("trade_reason"),
                "block_reason": attr.get("block_reason"),
                "net_score": attr.get("net_score"),
                "attribution_id": linked_id,
                "reject_reason": attr.get("reject_reason"),
                "reject_layer": attr.get("reject_layer"),
                "sl_source": ctx.get("sl_source"),
                "sl_anchor": ctx.get("sl_anchor"),
                "entry_rr": ctx.get("entry_rr"),
                "zone": ctx.get("zone"),
                "path": ctx.get("path"),
            },
            ensure_ascii=False,
        )
        from botlog.db import append_trade_attribution_notes

        append_trade_attribution_notes(trade_id, notes)
    except Exception:
        pass
    return True
