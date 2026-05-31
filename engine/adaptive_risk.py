"""
engine/adaptive_risk.py
───────────────────────
İki iyileştirme:

1. UNCLEAR yapıda tam WAIT yerine azaltılmış pozisyon boyutu
2. Liquidation feed verisi varsa yakın likit kasım kontrolü

Kullanım (decision_v3.py veya trader.py içinde execute_entry öncesinde):
    from engine.adaptive_risk import get_effective_risk, liq_filter_ok
"""

from __future__ import annotations

from core.logger import get_logger

log = get_logger("AdaptiveRisk")


# ─── 1. UNCLEAR'da azaltılmış risk ───────────────────────────────────────────

def get_effective_risk() -> tuple[float, float]:
    """
    Mevcut yapı durumuna göre efektif risk ve minimum RR döner.

    Returns:
        (risk_pct, min_rr)
    """
    try:
        from core.config import cfg
        from core.state import state

        base_risk = float(getattr(cfg, "RISK_PCT", 1.0))
        base_rr   = float(getattr(cfg, "BREAK_MIN_RR_UNCLEAR", 2.0))

        s1h  = getattr(state, "structure_1h", "UNCLEAR")
        s15m = getattr(state, "structure_15m", "UNCLEAR")

        # Her iki zaman dilimi de net → tam risk
        if s1h != "UNCLEAR" and s15m != "UNCLEAR":
            return base_risk, base_rr

        # Sadece 15m net, 1h belirsiz → %60 risk, RR biraz yüksek
        if s1h == "UNCLEAR" and s15m != "UNCLEAR":
            reduced = round(base_risk * 0.6, 3)
            higher_rr = round(base_rr * 1.2, 2)
            log.debug(
                f"AdaptiveRisk: 1h=UNCLEAR → risk={reduced}% (base={base_risk}%) "
                f"min_rr={higher_rr}"
            )
            return reduced, higher_rr

        # Her ikisi de UNCLEAR → %40 risk, RR daha yüksek
        if s1h == "UNCLEAR" and s15m == "UNCLEAR":
            reduced = round(base_risk * 0.4, 3)
            higher_rr = round(base_rr * 1.4, 2)
            log.debug(
                f"AdaptiveRisk: 1h+15m=UNCLEAR → risk={reduced}% "
                f"min_rr={higher_rr}"
            )
            return reduced, higher_rr

        return base_risk, base_rr

    except Exception as e:
        log.warning(f"AdaptiveRisk get_effective_risk hatası: {e}")
        # Hata durumunda güvenli: düşük risk
        return 0.5, 2.5


# ─── 2. Liquidation filtresi ─────────────────────────────────────────────────

# Yakın likit kasım mesafesi eşiği (bps cinsinden, 1 bps = 0.01%)
LIQ_PROXIMITY_BPS = 40  # fiyatın ±%0.4'ü içinde büyük likit kasım varsa engelle
LIQ_MIN_VALUE_USD = 500_000  # minimum likit kasım büyüklüğü (USDT)


def liq_filter_ok(direction: str) -> bool:
    """
    Açılmak istenen yönde yakın büyük likit kasım varsa True döner (işlem uygun).
    Likit kasım yoksa veya feed aktif değilse → True (engelleme yok).

    direction: "LONG" veya "SHORT"
    """
    try:
        from core.state import state
        from core.config import cfg

        if not getattr(cfg, "LIQ_WS_ENABLED", False):
            return True  # feed kapalı, filtre devre dışı

        liqs = getattr(state, "recent_liquidations", [])
        if not liqs:
            return True  # veri yok, engelleme yok

        price = getattr(state, "mark_price", 0.0) or 0.0
        if price <= 0:
            return True

        threshold = price * (LIQ_PROXIMITY_BPS / 10000)
        now_ts = __import__("time").time()
        recent_window = 300  # son 5 dakika

        for liq in liqs:
            # liq formatı: {"price": float, "side": "BUY"/"SELL", "qty": float, "ts": float}
            ts    = liq.get("ts", 0)
            lprice = liq.get("price", 0)
            qty   = liq.get("qty", 0)
            side  = liq.get("side", "")

            if now_ts - ts > recent_window:
                continue

            usd_value = lprice * qty
            if usd_value < LIQ_MIN_VALUE_USD:
                continue

            dist = abs(price - lprice)
            if dist > threshold:
                continue

            # Yakın büyük likit kasım var — yönüne bak
            # Long likit kasım (SELL) fiyatın altında → long için destekleyici
            # Short likit kasım (BUY) fiyatın üstünde → short için destekleyici
            if direction == "LONG" and side == "SELL" and lprice < price:
                log.debug(
                    f"LiqFilter: LONG destekli, yakın SELL liq @ {lprice:.2f} "
                    f"({usd_value/1e6:.1f}M$)"
                )
                return True

            if direction == "SHORT" and side == "BUY" and lprice > price:
                log.debug(
                    f"LiqFilter: SHORT destekli, yakın BUY liq @ {lprice:.2f} "
                    f"({usd_value/1e6:.1f}M$)"
                )
                return True

            # Ters yönde yakın büyük likit kasım → dikkatli ol
            log.debug(
                f"LiqFilter: {direction} için ters likit kasım @ {lprice:.2f} "
                f"dist={dist:.2f} value={usd_value/1e6:.1f}M$ → zayıf sinyal"
            )
            # Engelleme yok ama log'a düştü — ileride puan sistemine eklenebilir

        return True

    except Exception as e:
        log.warning(f"LiqFilter hatası: {e}")
        return True  # hata → engelleme yok
