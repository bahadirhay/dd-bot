"""Swing / seviye izleme hazır mı — startup log."""
from __future__ import annotations

from core.config import cfg
from core.state import state
from core.logger import get_logger

log = get_logger("Breakout")


def log_swing_readiness(context: str = "startup") -> None:
    """
    15m backfill sonrası seviye izlemenin ne zaman açılacağını logla.
    Tam swing için kabaca SWING_LB_15M*2+5 kapalı 15m mum gerekir.
    """
    n_hi = len(state.swing_highs_15m or [])
    n_lo = len(state.swing_lows_15m or [])
    lb = cfg.SWING_LB_15M
    bars_needed = lb * 2 + 5
    bars_have = max(n_hi, n_lo)
    mins_per_bar = 15
    if bars_have >= 2 and n_hi >= 2 and n_lo >= 2:
        log.info(
            f"[{context}] Swing hazır: {n_hi} tepe / {n_lo} dip — seviye izleme AÇIK"
        )
        try:
            from engine.breakout import get_active_levels

            px = float(state.mark_price or state.price or 0)
            lv = get_active_levels(px)
            ck_r = float(lv.get("cookie_resistance") or lv.get("resistance") or 0)
            ck_s = float(lv.get("support") or 0)
            ck_q = lv.get("cookie_quality") or "—"
            bw = float(lv.get("band_width_bps") or 0)
            mc = float(lv.get("min_edge_confidence") or 0)
            rs = float(lv.get("support_confidence") or 0)
            rr = float(lv.get("resistance_confidence") or 0)
            src = lv.get("support_source") or "?"
            rng_s = float(lv.get("range_support") or 0)
            if ck_r > 0 and ck_s > 0:
                log.info(
                    f"[{context}] Seviye bandı: R={ck_r:.2f} S={ck_s:.2f} "
                    f"(kırılım altı swing={rng_s:.2f} | "
                    f"{bw:.0f}bps, {ck_q}, conf S={rs:.2f} R={rr:.2f}, src={src}) — işlem bandı"
                )
        except Exception:
            pass
        return

    missing = max(0, bars_needed - bars_have)
    wait_min = missing * mins_per_bar
    log.warning(
        f"[{context}] Swing henüz yetersiz ({n_hi} tepe, {n_lo} dip; "
        f"~{bars_needed} swing noktası için ~{bars_needed * mins_per_bar} dk 15m veri). "
        f"Seviye izleme KAPALI — her yeni 15m kapanışında güncellenir. "
        f"(Tahmini bekleme: ~{wait_min} dk veya backfill {bars_needed}+ mum)"
    )
