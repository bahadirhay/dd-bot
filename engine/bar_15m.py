"""
engine/bar_15m.py — Her 15m kapanışında botun ne gördüğünü kaydeder (Binance 15m ile aynı).
"""
from __future__ import annotations

from core.config import cfg
from core.state import state
from core.logger import get_logger
from engine.regime import evaluate as eval_regime

log = get_logger("Bar15m")


def observe_15m_close(candle: dict) -> dict:
    """
    15m mum kapandı — düşüş/yükseliş + SHORT/LONG rejim skoru.
    Kurallar değişmez; sadece gözlem ve log.
    """
    o, h, l, c = candle["open"], candle["high"], candle["low"], candle["close"]
    bearish = c < o
    chg_pct = ((c - o) / o * 100.0) if o else 0.0
    body = abs(c - o)
    rng = h - l if h > l else 0.0
    body_ratio = (body / rng) if rng > 0 else 0.0

    _, score_l, ans_l = eval_regime("LONG")
    _, score_s, ans_s = eval_regime("SHORT")

    cvd_bars = list(state.cvd_bars)[-cfg.CVD_BARS:]
    neg_15m = sum(1 for b in cvd_bars if b["delta"] < 0)
    pos_15m = sum(1 for b in cvd_bars if b["delta"] > 0)
    oi_hist = list(state.oi_history)[-cfg.OI_LOOKBACK:]

    summary = {
        "ts": candle["ts"],
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "delta": candle.get("delta", 0),
        "bearish": bearish,
        "chg_pct": round(chg_pct, 3),
        "body_ratio": round(body_ratio, 2),
        "structure_15m": state.structure_15m,
        "structure_1h": state.structure_1h,
        "short_score": score_s,
        "long_score": score_l,
        "short_ans": dict(ans_s),
        "long_ans": dict(ans_l),
        "cvd_5m": state.cvd_5m,
        "taker": state.taker_ratio,
        "cvd_bars_n": len(cvd_bars),
        "cvd_neg_15m": neg_15m,
        "cvd_pos_15m": pos_15m,
        "oi_rising": state.oi_rising,
        "oi_last": [h["oi"] for h in oi_hist],
        "last_bar_delta": candle.get("delta", 0),
    }
    state.last_15m_summary = summary

    arrow = "▼" if bearish else "▲"
    def _flags(ans: dict) -> str:
        return (
            f"yap={'✓' if ans.get('structure') else '✗'} "
            f"cvd={'✓' if ans.get('cvd') else '✗'} "
            f"oi={'✓' if ans.get('oi') else '✗'} "
            f"tak={'✓' if ans.get('taker') else '✗'}"
        )

    log.info(
        f"15m MUM {arrow}  O={o:.2f} H={h:.2f} L={l:.2f} C={c:.2f}  "
        f"({chg_pct:+.2f}%  gövde/range={body_ratio:.0%})  "
        f"yapı: 15m={state.structure_15m}  1h={state.structure_1h}\n"
        f"       SHORT {score_s}/{cfg.REGIME_MIN} gerekli  {_flags(ans_s)}  |  "
        f"LONG {score_l}/{cfg.REGIME_MIN}  {_flags(ans_l)}"
    )

    if bearish and chg_pct <= -0.25:
        need = cfg.REGIME_MIN - score_s
        miss = []
        if not ans_s.get("structure"):
            miss.append("yapı(15m+1h DOWN)")
        if not ans_s.get("cvd"):
            miss.append(
                f"cvd(15m {neg_15m}/{len(cvd_bars)} neg, "
                f"≥{cfg.CVD_CONSIST:.0%} gerek)"
            )
        if not ans_s.get("oi"):
            miss.append(f"oi(artış gerek, son={state.oi_rising})")
        if not ans_s.get("taker"):
            miss.append(
                f"taker(15mΔ={candle.get('delta', 0):+.1f} "
                f"5m={state.taker_ratio:.0%})"
            )
        if need > 0:
            log.info(
                f"       ↳ Düşüş mumu GÖRÜLDÜ; SHORT {score_s}/4 — "
                f"eksik: {', '.join(miss) or '?'}"
            )
        else:
            log.info("       ↳ Düşüş mumu + SHORT rejim ≥3/4 (sinyal adımına geçilir)")

    return summary
