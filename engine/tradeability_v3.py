"""
engine/tradeability_v3.py — Tradeability / Conviction Gate.

Karar hattının ÖNÜNDE çalışır: skor LONG/SHORT dese bile, piyasa yapısal olarak
işlenebilir değilse girişi WAIT'e çevirir. Amaç: chop'ta overtrading + akışa ters
giriş kaynaklı sistematik kaybı kesmek.

Üç kural (entry yönü için):
  1) Order-flow hizası (sert): SHORT iken CVD BULL / LONG iken CVD BEAR olmaz.
  2) Chop filtresi: dar bant + zayıf konviksiyon + dengeli akış = WAIT.
  3) (1) ve (2) geçilirse giriş serbest; güçlü yönlü rejim dar bantta da trade eder.

Sadece YENİ girişleri etkiler — açık pozisyon yönetimi/çıkışları ayrı yoldadır.
"""
from __future__ import annotations

from core.config import cfg
from core.state import state


def assess_tradeability(
    action: str,
    levels: dict,
    cvd: dict,
    *,
    px: float = 0.0,
    market_state: dict | None = None,
) -> tuple[bool, str]:
    """
    (tradeable, neden) döndürür. tradeable=False ise giriş WAIT'e çevrilmeli.
    """
    if not bool(getattr(cfg, "V3_TRADEABILITY_GATE_ENABLED", True)):
        return True, ""
    act = str(action or "").upper()
    if act not in ("LONG", "SHORT"):
        return True, ""

    ms = market_state or getattr(state, "v3_market_state", None) or {}
    collapse = ms.get("collapse") or {}
    conv = float(collapse.get("state_score", 0) or 0)

    cvd = cvd or {}
    cvd_dir = str(cvd.get("direction") or "NEUTRAL").upper()
    buy_ratio = float(cvd.get("buy_ratio", 0.5) or 0.5)

    bs = float(levels.get("active_support") or 0)
    br = float(levels.get("active_resistance") or 0)
    band_w = (br - bs) / br if (bs > 0 and br > bs) else 0.0

    # 1) Order-flow hizası (sert kural) — akışa ters girişi engelle
    if bool(getattr(cfg, "V3_TRADEABLE_REQUIRE_FLOW_ALIGN", True)):
        if act == "LONG" and cvd_dir == "BEAR":
            return False, (
                f"order-flow ters: LONG ama CVD BEAR (alım %{buy_ratio * 100:.0f})"
            )
        if act == "SHORT" and cvd_dir == "BULL":
            return False, (
                f"order-flow ters: SHORT ama CVD BULL (alım %{buy_ratio * 100:.0f})"
            )

        # 1b) Kümülatif akış counterflow — anlık direction nötr olsa bile
        # kalıcı (kümülatif) akış güçlü ters yöndeyse girişi engelle.
        # Örn. #60: SHORT açıldı ama cvd_cum=+8706 (yoğun alım) → engellenmeli.
        cum = float(cvd.get("cumulative", 0) or 0)
        cum_thr = float(getattr(cfg, "V3_TRADEABLE_CVD_CUM_COUNTER", 4000.0) or 4000.0)
        if cum_thr > 0:
            if act == "SHORT" and cum >= cum_thr:
                return False, (
                    f"kümülatif akış ters: SHORT ama CVD cum=+{cum:.0f}>={cum_thr:.0f} "
                    f"(kalıcı alım)"
                )
            if act == "LONG" and cum <= -cum_thr:
                return False, (
                    f"kümülatif akış ters: LONG ama CVD cum={cum:.0f}<=-{cum_thr:.0f} "
                    f"(kalıcı satış)"
                )

    # 2) Chop filtresi: dar bant + zayıf konviksiyon + dengeli akış
    min_band = float(getattr(cfg, "V3_TRADEABLE_MIN_BAND_PCT", 0.006) or 0.006)
    min_conv = float(getattr(cfg, "V3_TRADEABLE_MIN_CONVICTION", 70.0) or 70.0)
    min_edge = float(getattr(cfg, "V3_TRADEABLE_MIN_FLOW_EDGE", 0.03) or 0.03)

    tight = 0 < band_w < min_band
    weak = conv < min_conv
    flat = abs(buy_ratio - 0.5) < min_edge
    if tight and weak and flat:
        return False, (
            f"chop: bant %{band_w * 100:.2f}<%{min_band * 100:.2f} + "
            f"konviksiyon {conv:.0f}<{min_conv:.0f} + akış dengeli "
            f"(alım %{buy_ratio * 100:.0f})"
        )

    return True, ""
