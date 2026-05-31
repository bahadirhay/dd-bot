"""
engine/signal.py — Trend analizi → trade planı (SL/TP/R:R).
"""
import time
from core.config import cfg
from core.state import state, effective_price
from core.logger import get_logger
from engine.trend import update_trend, trade_direction, STRENGTH_TRADE
from engine.cvd_engine import check_divergence
from botlog.db import log_signal

log = get_logger("Signal")


def make_trade_details(
    direction: str,
    entry_price: float | None = None,
) -> tuple[str, dict]:
    """Yön belli — seviyeler + veto. entry_price: kırılım anındaki fiyat (state.price değil)."""
    price = float(entry_price) if entry_price and entry_price > 0 else effective_price()
    if price <= 0:
        return "FLAT", {"reason": "fiyat verisi yok"}

    tv = state.trend_view or update_trend("signal")

    divergence = check_divergence(direction)
    if divergence:
        reason = f"CVD diverjans — {direction} momentumu zayıflıyor"
        _save_signal(direction, False, reason, tv["strength"] // 25, state.regime_answers)
        return "FLAT", {"reason": reason, "trend": tv}

    fund = state.funding_signal
    if direction == "LONG" and fund == "LONG_CROWD":
        _save_signal(direction, False, "Funding LONG_CROWD", 0, state.regime_answers)
        return "FLAT", {"reason": "Funding LONG_CROWD"}
    if direction == "SHORT" and fund == "SHORT_CROWD":
        _save_signal(direction, False, "Funding SHORT_CROWD", 0, state.regime_answers)
        return "FLAT", {"reason": "Funding SHORT_CROWD"}

    from engine.structure_levels import calc_trade_levels

    entry, sl, tp1, tp2 = calc_trade_levels(direction, price, state)
    sl_dist = abs(entry - sl)
    tp1_dist = abs(tp1 - entry)
    rr = tp1_dist / sl_dist if sl_dist > 0 else 0
    rr2 = abs(tp2 - entry) / sl_dist if sl_dist > 0 else 0

    if sl <= 0 or rr < cfg.MIN_RR:
        reason = f"R:R={rr:.2f} yetersiz (min={cfg.MIN_RR})" if sl > 0 else "SL geçersiz"
        _save_signal(direction, False, reason, tv["strength"] // 25, state.regime_answers)
        return "FLAT", {"reason": reason, "trend": tv}

    details = {
        "direction": direction,
        "price": entry,
        "sl": sl, "tp1": tp1, "tp2": tp2,
        "rr": round(rr, 2), "rr_tp2": round(rr2, 2),
        "trend": tv,
        "entry_reason": tv.get("summary", ""),
    }
    return direction, details


def evaluate_signal() -> tuple[str, dict]:
    """15m kapanış — trend oku, yön seç, plan üret."""
    tv = update_trend("15m")
    direction = trade_direction()

    if direction == "FLAT":
        reason = (
            f"Piyasa okundu — trade yok: {tv['summary']} "
            f"(güç={tv['strength']}, min={STRENGTH_TRADE})"
        )
        state.signal = "FLAT"
        state.no_entry_reason = reason
        _save_signal("FLAT", False, reason, tv["strength"] // 25, state.regime_answers)
        return "FLAT", {"reason": reason, "trend": tv}

    direction, details = make_trade_details(direction)
    if direction == "FLAT":
        state.signal = "FLAT"
        state.no_entry_reason = details.get("reason", "")
        return "FLAT", details

    state.signal = direction
    state.signal_ts = time.time()
    state.signal_reason = f"{tv['summary']}  RR=1:{details['rr']}"

    _save_signal(direction, True, state.signal_reason,
                 tv["strength"] // 25, state.regime_answers,
                 sl=details["sl"], tp1=details["tp1"], tp2=details["tp2"],
                 rr=details["rr"])

    log.info(
        f"\n{'═'*52}\n"
        f"  ANALİZ → SİNYAL: {direction}  {tv['phase']}  güç={tv['strength']}\n"
        f"  {state.signal_reason}\n"
        f"  SL={details['sl']}  TP1={details['tp1']}  TP2={details['tp2']}\n"
        f"{'═'*52}"
    )
    return direction, details


def _save_signal(direction, entered, reason, score, answers,
                 sl=0, tp1=0, tp2=0, rr=0):
    log_signal({
        "ts": time.time(),
        "direction": direction,
        "entered": 1 if entered else 0,
        "no_entry_reason": "" if entered else reason,
        "regime": state.regime,
        "regime_score": score,
        "q1_structure": int(answers.get("structure", False)),
        "q2_cvd": int(answers.get("cvd", False)),
        "q3_oi": int(answers.get("oi", False)),
        "q4_taker": int(answers.get("taker", False)),
        "structure_1h": state.structure_1h,
        "structure_15m": state.structure_15m,
        "cvd_5m": state.cvd_5m,
        "taker_ratio": state.taker_ratio,
        "funding_rate": state.funding_rate,
        "price": effective_price(),
        "sl": sl, "tp1": tp1, "tp2": tp2, "rr": rr,
        "notes": reason,
    })
