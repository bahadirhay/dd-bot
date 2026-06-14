"""
engine/regime_vr.py — Istatistiksel rejim testi (saf hesap, indikator yok).

Variance Ratio + lag-1 otokorelasyon ile fiyat serisinin DOGASI:
  VR(k) = Var(k-period getiri) / (k * Var(1-period getiri))
    VR < 1  -> mean-reverting (fade calisir)
    VR > 1  -> trending       (breakout/takip calisir)
    VR ~ 1  -> rastgele yuruyus = EDGE YOK (oynama)

Rastgele yuruyusta hicbir S/R fade'i kar beklentisi tasimaz; bu test onu
matematikle soyler. Zaman-bazli degil, tamamen olcum-bazli.
"""
from __future__ import annotations

from statistics import mean, pvariance

from core.config import cfg
from core.logger import get_logger

log = get_logger("RegimeVR")


def _closes_1m(n: int) -> list[float]:
    try:
        from engine.v3_common import bars_1m

        bars = bars_1m(n + 5)
        return [float(b.get("close", 0) or 0) for b in bars if float(b.get("close", 0) or 0) > 0]
    except Exception:
        return []


def variance_ratio(prices: list[float], k: int) -> float:
    """VR(k). Rastgele yuruyus -> ~1.0."""
    if len(prices) < k * 3 or k < 2:
        return 1.0
    # log getiriler
    import math
    r1 = [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices))
          if prices[i] > 0 and prices[i - 1] > 0]
    if len(r1) < k * 3:
        return 1.0
    var1 = pvariance(r1)
    if var1 <= 0:
        return 1.0
    # k-period ust uste binmeyen getiriler
    rk = [sum(r1[i:i + k]) for i in range(0, len(r1) - k + 1, k)]
    if len(rk) < 2:
        return 1.0
    vark = pvariance(rk)
    return vark / (k * var1)


def lag1_autocorr(prices: list[float]) -> float:
    import math
    r = [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices))
         if prices[i] > 0 and prices[i - 1] > 0]
    if len(r) < 10:
        return 0.0
    m = mean(r)
    num = sum((r[i] - m) * (r[i - 1] - m) for i in range(1, len(r)))
    den = sum((x - m) ** 2 for x in r)
    return num / den if den > 0 else 0.0


def classify_regime(prices: list[float] | None = None) -> dict:
    """Donus: {regime, vr, ac1, n, tradeable, allow_fade, allow_breakout}."""
    n = int(getattr(cfg, "V3_VR_WINDOW_BARS", 120) or 120)
    k = int(getattr(cfg, "V3_VR_K", 5) or 5)
    px = prices if prices is not None else _closes_1m(n)
    px = px[-n:]
    out = {"regime": "unknown", "vr": 1.0, "ac1": 0.0, "n": len(px),
           "tradeable": True, "allow_fade": True, "allow_breakout": True}
    if len(px) < max(30, k * 4):
        out["regime"] = "yetersiz_veri"
        return out

    vr = variance_ratio(px, k)
    ac1 = lag1_autocorr(px)
    out["vr"] = round(vr, 3)
    out["ac1"] = round(ac1, 3)

    mr_th = float(getattr(cfg, "V3_VR_MEANREVERT_MAX", 0.80) or 0.80)
    tr_th = float(getattr(cfg, "V3_VR_TREND_MIN", 1.20) or 1.20)

    if vr <= mr_th:
        out.update({"regime": "mean_revert", "allow_fade": True, "allow_breakout": False})
    elif vr >= tr_th:
        out.update({"regime": "trend", "allow_fade": False, "allow_breakout": True})
    else:
        # rastgele yuruyus bandi -> edge yok
        out.update({"regime": "random", "tradeable": False,
                    "allow_fade": False, "allow_breakout": False})
    return out


def edge_gate(price: float, ref_target_bps: float) -> dict:
    """
    ASIL kapi (veriyle dogrulandi): son realized range, hedef (TP1) mesafesini
    karsilamiyorsa fade'e girme — hedef ulasilmaz, kar beklentisi yok.
    Saf hesap: son N x 1m bar'in high-low araligi (bps) vs ref_target_bps.
    Donus: {allow, range_bps, need_bps}.
    """
    out = {"allow": True, "range_bps": 0.0, "need_bps": 0.0}
    if not bool(getattr(cfg, "V3_EDGE_GATE_ENABLED", True)) or price <= 0:
        return out
    win = int(getattr(cfg, "V3_EDGE_RANGE_WINDOW_BARS", 120) or 120)
    try:
        from engine.v3_common import bars_1m

        bars = bars_1m(win + 5)[-win:]
    except Exception:
        return out
    if len(bars) < 20:
        return out
    hi = max(float(b.get("high", 0) or 0) for b in bars)
    lo = min(float(b.get("low", 0) or 1e12) for b in bars)
    if hi <= 0 or lo <= 0 or hi <= lo:
        return out
    rng = (hi - lo) / price * 1e4
    mult = float(getattr(cfg, "V3_EDGE_RANGE_MULT", 1.0) or 1.0)
    need = float(ref_target_bps or 0) * mult
    out.update({"allow": rng >= need, "range_bps": round(rng, 1),
                "need_bps": round(need, 1)})
    return out
