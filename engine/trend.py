"""
engine/trend.py — Piyasa görüşü (katmanlı).

Hiyerarşi (benim kurgum):
  1h  → ana yapı (trade kapısı: 1h+15m aynı yön)
  15m → zamanlama + orta yapı + mum dizisi
  1m  → açık 15m içi hareket (sadece izleme / impulse, kapı geçmişse)
  flow→ CVD/taker (onay, tek başına trade açmaz)
"""
from __future__ import annotations

import time
from core.config import cfg
from core.state import state, effective_price
from core.logger import get_logger
from engine.structure import get_bars_15m
from engine.market_layers import compute_market_layers

log = get_logger("Trend")

# Güç eşiği: eski REGIME_MIN=3/4 ≈ %75 → 60/100
STRENGTH_TRADE = 60
STRENGTH_LOG_CHANGE = 12


def _pct(o: float, c: float) -> float:
    return ((c - o) / o * 100.0) if o else 0.0


def momentum_explain(bars: list[dict] | None = None) -> str:
    """
    Grafikte gördüğünüz son hareket vs botun 12 mum penceresi (şeffaflık).
    """
    if not bars:
        bars = get_bars_15m(cfg.TREND_BARS_15M)
    if not bars:
        try:
            from dashboard.binance_chart import fetch_15m_klines
            bars = fetch_15m_klines(cfg.TREND_BARS_15M)
        except Exception:
            return ""

    def _count(bs: list[dict]) -> tuple[int, int, float]:
        if not bs:
            return 0, 0, 0.0
        g = sum(1 for b in bs if b["close"] > b["open"])
        r = len(bs) - g
        chg = _pct(bs[0]["open"], bs[-1]["close"])
        return g, r, chg

    g4, r4, chg4 = _count(bars[-4:])
    n15 = cfg.TIMING_BARS_15M
    gN, rN, chgN = _count(bars[-n15:] if len(bars) >= n15 else bars)
    try:
        from engine.bars_1m import get_bars_1m
        b1 = get_bars_1m(cfg.PULSE_BARS_1M)
        g1, r1, chg1 = _count(b1)
        line1 = (
            f"1m nabız ({cfg.PULSE_BARS_1M}dk): {g1}Y/{r1}K ({chg1:+.2f}%)"
        )
    except Exception:
        line1 = f"1m nabız: veri yok"
    return (
        f"{line1}  |  "
        f"15m onay ({n15}×15m): {gN}Y/{rN}K ({chgN:+.2f}%)  |  "
        f"son4×15m: {g4}Y/{r4}K ({chg4:+.2f}%)"
    )


def _bar_direction(bar: dict) -> int:
    """1=yükseliş mumu, -1=düşüş, 0=doji."""
    o, c = bar.get("open", 0), bar.get("close", 0)
    if c > o:
        return 1
    if c < o:
        return -1
    return 0


def _flow_scores() -> tuple[float, float]:
    """Orderflow: 0-1 down_bias, up_bias."""
    down = up = 0.0
    cvd = state.cvd_5m
    if cvd <= -cfg.CVD_MIN:
        down += 0.35
    elif cvd >= cfg.CVD_MIN:
        up += 0.35
    elif cvd < 0:
        down += 0.15
    else:
        up += 0.15

    bars = list(state.cvd_bars)[-cfg.CVD_BARS:]
    if len(bars) >= 3:
        neg = sum(1 for b in bars if b["delta"] < 0)
        ratio = neg / len(bars)
        if ratio >= cfg.CVD_CONSIST:
            down += 0.35
        elif (1 - ratio) >= cfg.CVD_CONSIST:
            up += 0.35

    tr = state.taker_ratio
    if (1 - tr) >= cfg.TAKER_MIN:
        down += 0.3
    if tr >= cfg.TAKER_MIN:
        up += 0.3

    forming = state.forming_15m or {}
    if forming.get("delta_sum", 0) < 0:
        down += 0.15
    elif forming.get("delta_sum", 0) > 0:
        up += 0.15

    return min(1.0, down), min(1.0, up)


def update_trend(trigger: str = "tick") -> dict:
    """
    Piyasa trend görünümünü hesapla → state.trend_view.
    trigger: '1m' | '15m' | 'tick'
    """
    layers = compute_market_layers()
    pulse = layers["pulse_1m"]
    timing = layers["timing_15m"]
    bars = get_bars_15m(cfg.TIMING_BARS_15M)
    forming = state.forming_15m or {}
    s15, s1h = state.structure_15m, state.structure_1h

    f_chg = float(pulse.get("forming_chg") or 0.0)
    if not f_chg and forming:
        f_chg = _pct(forming.get("open", 0), forming.get("close", 0))

    # Nabız (1m) ağırlıklı momentum — grafikte gördüğünüz hareket
    pn = max(pulse.get("n", 0), 1)
    tn = max(timing.get("n", 0), 1)
    up_momentum = (pulse.get("green", 0) / pn) * 0.70 + (timing.get("green", 0) / tn) * 0.30
    down_momentum = (pulse.get("red", 0) / pn) * 0.70 + (timing.get("red", 0) / tn) * 0.30
    green = pulse.get("green", 0)
    red = pulse.get("red", 0)

    dirs = [_bar_direction(b) for b in bars]
    last3 = dirs[-3:] if len(dirs) >= 3 else dirs
    serial_down = len(last3) >= 2 and last3.count(-1) >= 2
    serial_up = len(last3) >= 2 and last3.count(1) >= 2
    if pulse.get("bias") == "UP" and pulse.get("chg_pct", 0) >= 0.12:
        serial_up = True
    if pulse.get("bias") == "DOWN" and pulse.get("chg_pct", 0) <= -0.12:
        serial_down = True

    # ── Yapı ─────────────────────────────────────────────────
    # Tam uyum: her iki TF aynı yönde
    struct_down = s15 == "DOWN" and s1h == "DOWN"
    struct_up = s15 == "UP" and s1h == "UP"
    # Yumuşatılmış uyum: 1h UNCLEAR (belirsiz) ise 15m'e izin ver
    # UNCLEAR = zıt yönde değil, sadece net sinyal yok
    s1h_unclear = s1h not in ("UP", "DOWN")
    struct_down_soft = s15 == "DOWN" and (s1h == "DOWN" or s1h_unclear)
    struct_up_soft = s15 == "UP" and (s1h == "UP" or s1h_unclear)
    struct_weak_down = s15 == "DOWN"
    struct_weak_up = s15 == "UP"

    flow_dn, flow_up = _flow_scores()

    # ── Skor 0-100 ───────────────────────────────────────────
    down_score = 0.0
    up_score = 0.0

    # Yapı: tam uyum (1h+15m) ödüllendirilir; sadece 15m zayıf puan (trade kapısı ayrı)
    if struct_down:
        down_score += 35
    elif struct_weak_down:
        down_score += 8
    if struct_up:
        up_score += 35
    elif struct_weak_up:
        up_score += 8

    down_score += down_momentum * 32
    up_score += up_momentum * 32

    down_score += flow_dn * 25
    up_score += flow_up * 25

    if serial_down:
        down_score += 8
    if serial_up:
        up_score += 8

    if f_chg <= -0.25:
        down_score += min(12, abs(f_chg) * 4)
    if f_chg >= 0.25:
        up_score += min(12, f_chg * 4)

    ps = int(pulse.get("strength", 0))
    if pulse.get("bias") == "UP":
        up_score = min(100, max(up_score, ps))
    elif pulse.get("bias") == "DOWN":
        down_score = min(100, max(down_score, ps))

    down_score = min(100, int(down_score))
    up_score = min(100, int(up_score))

    # ── Bias / faz (önce nabız, 15m onay destekler) ───────────
    drop_active = (
        f_chg <= -0.35
        or pulse.get("chg_pct", 0) <= -0.35
        or (bars and _pct(bars[-1]["open"], bars[-1]["close"]) <= -0.4)
    )
    rise_active = (
        f_chg >= 0.35
        or pulse.get("chg_pct", 0) >= 0.25
        or (bars and _pct(bars[-1]["open"], bars[-1]["close"]) >= 0.4)
    )

    pulse_lead = pulse.get("bias")
    thresh = 36 if pulse_lead in ("UP", "DOWN") else 40

    if down_score >= up_score + 12 and down_score >= thresh:
        bias = "DOWN"
        strength = down_score
        if drop_active or (serial_down and f_chg < 0):
            phase = "drop"
        else:
            phase = "downtrend"
    elif up_score >= down_score + 12 and up_score >= thresh:
        bias = "UP"
        strength = up_score
        if rise_active or (serial_up and f_chg > 0):
            phase = "rise"
        else:
            phase = "uptrend"
    elif pulse_lead == "UP" and timing.get("bias") != "DOWN":
        bias = "UP"
        strength = max(up_score, int(pulse.get("strength", 0)))
        phase = "rise" if rise_active else "uptrend"
    elif pulse_lead == "DOWN" and timing.get("bias") != "UP":
        bias = "DOWN"
        strength = max(down_score, int(pulse.get("strength", 0)))
        phase = "drop" if drop_active else "downtrend"
    else:
        bias = "RANGE"
        strength = max(down_score, up_score)
        phase = "range"

    structure_aligned = (
        (bias == "DOWN" and struct_down) or (bias == "UP" and struct_up)
    )
    # Soft alignment: 1h UNCLEAR ise 15m yeterli (strength ≥ 60 şartıyla)
    structure_soft_aligned = (
        (bias == "DOWN" and struct_down_soft) or (bias == "UP" and struct_up_soft)
    )
    flow_ok = strength >= STRENGTH_TRADE and bias in ("UP", "DOWN")
    if cfg.REQUIRE_HTF_ALIGN:
        # Tam uyum veya (soft uyum + yüksek güç ≥ 60)
        trade_ok = flow_ok and (
            structure_aligned
            or (structure_soft_aligned and strength >= 60)
        )
    else:
        trade_ok = flow_ok

    block = ""
    if flow_ok and not structure_aligned and cfg.REQUIRE_HTF_ALIGN:
        if structure_soft_aligned and strength >= 60:
            block = f" | 1h UNCLEAR ama 15m güçlü ({s15}/güç={strength:.0f}) — soft izin"
        else:
            block = f" | 1h+15m uyumsuz ({s15}/{s1h}) — 15m tek basina trade yok"

    summary = _make_summary(bias, phase, strength, f_chg, s15, s1h, trigger) + block

    display_bias = layers.get("lead_bias") or bias
    if display_bias == bias:
        headline = f"{bias} {phase}"
    else:
        headline = f"{bias} ({display_bias} nabız)"

    view = {
        "ts": time.time(),
        "trigger": trigger,
        "bias": bias,
        "phase": phase,
        "strength": strength,
        "down_score": down_score,
        "up_score": up_score,
        "drop_active": drop_active,
        "rise_active": rise_active,
        "trade_ok": trade_ok,
        "structure_aligned": structure_aligned,
        "flow_ok": flow_ok,
        "summary": summary,
        "headline": headline,
        "layers": layers,
        "lead_bias": layers.get("lead_bias"),
        "confirm_bias": layers.get("confirm_bias"),
        "align_status": layers.get("align_status"),
        "chart_lines": layers.get("chart_lines", []),
        "display_bias": display_bias,
        "structure_15m": s15,
        "structure_1h": s1h,
        "struct_down": struct_down,
        "struct_up": struct_up,
        "forming_chg_pct": round(f_chg, 3),
        "closed_red": red,
        "closed_green": green,
        "serial_down": serial_down,
        "serial_up": serial_up,
        "cvd_5m": state.cvd_5m,
        "taker": state.taker_ratio,
        "flow_down": round(flow_dn, 2),
        "flow_up": round(flow_up, 2),
    }
    state.trend_view = view

    # Dashboard / DB uyumluluk (rejim alanları)
    state.regime = "TREND" if trade_ok else "RANGE"
    state.regime_score = min(4, strength // 25)
    state.regime_answers = _legacy_answers(bias, struct_down, struct_up, flow_dn, flow_up)

    _log_if_changed(view)
    try:
        from botlog.journal import on_trend_updated
        on_trend_updated()
    except Exception:
        pass
    return view


def _legacy_answers(bias, struct_down, struct_up, flow_dn, flow_up) -> dict:
    if bias == "DOWN":
        return {
            "structure": struct_down or state.structure_15m == "DOWN",
            "cvd": flow_dn >= 0.35,
            "oi": state.oi_rising,
            "taker": flow_dn >= 0.25 or (1 - state.taker_ratio) >= cfg.TAKER_MIN,
        }
    if bias == "UP":
        return {
            "structure": struct_up or state.structure_15m == "UP",
            "cvd": flow_up >= 0.35,
            "oi": state.oi_rising,
            "taker": flow_up >= 0.25 or state.taker_ratio >= cfg.TAKER_MIN,
        }
    return {
        "structure": False,
        "cvd": False,
        "oi": state.oi_rising,
        "taker": False,
    }


def _make_summary(bias, phase, strength, f_chg, s15, s1h, trigger) -> str:
    phase_tr = {
        "drop": "düşüş",
        "rise": "yükseliş",
        "downtrend": "aşağı trend",
        "uptrend": "yukarı trend",
        "range": "yatay",
    }.get(phase, phase)
    intra = f" açık15m={f_chg:+.2f}%" if f_chg else ""
    return f"{bias} {phase_tr} güç={strength}%{intra} yapı={s15}/{s1h} [{trigger}]"


_prev_trend_key = ""


def _log_if_changed(view: dict):
    global _prev_trend_key
    key = f"{view['bias']}|{view['phase']}|{view['strength']//10}"
    if key == _prev_trend_key:
        return
    _prev_trend_key = key

    icon = {"DOWN": "DN", "UP": "UP", "RANGE": "RG"}.get(view["bias"], "?")
    log.info(
        f"TREND {icon}  {view['summary']}  "
        f"(down={view['down_score']} up={view['up_score']}  "
        f"drop={'evet' if view['drop_active'] else 'hayır'}  "
        f"rise={'evet' if view['rise_active'] else 'hayır'})"
    )


def on_15m_closed(candle: dict) -> dict:
    """15m mum kapandı — trend + detay log."""
    view = update_trend("15m")
    o, h, l, c = candle["open"], candle["high"], candle["low"], candle["close"]
    chg = _pct(o, c)
    arrow = "DN" if c < o else "UP"
    log.info(
        f"15m KAPANDI {arrow}  O={o:.2f} C={c:.2f} ({chg:+.2f}%)  →  {view['summary']}"
    )
    if view["bias"] == "DOWN" and view.get("flow_ok") and not view.get("structure_aligned"):
        s15 = view.get("structure_15m") or state.structure_15m or "?"
        s1h = view.get("structure_1h") or state.structure_1h or "?"
        log.info(
            f"       15m dusus var ama 1h uyumsuz ({s15}/{s1h}) — "
            f"trade icin 1h+15m ayni yon gerekli"
        )
    elif view["bias"] == "DOWN" and not view["trade_ok"]:
        log.info(
            f"       Dusus zayif; trade icin guc>={STRENGTH_TRADE} "
            f"(simdi {view['strength']})"
        )
    state.last_15m_summary = {
        "ts": candle["ts"],
        "open": o, "high": h, "low": l, "close": c,
        "bearish": c < o,
        "chg_pct": round(chg, 3),
        "trend_bias": view["bias"],
        "trend_phase": view["phase"],
        "trend_strength": view["strength"],
    }
    try:
        from botlog.journal import on_bar
        on_bar("15m", candle, view.get("summary", ""))
    except Exception:
        pass
    return view


def trade_direction() -> str:
    """Sinyal yönü: trend yeterince güçlüyse UP/DOWN, değilse FLAT."""
    tv = state.trend_view
    if not tv:
        update_trend("tick")
        tv = state.trend_view
    if not tv.get("trade_ok"):
        return "FLAT"
    return tv["bias"] if tv["bias"] in ("UP", "DOWN") else "FLAT"
