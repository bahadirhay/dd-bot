"""
engine/intraday_box.py — Adaptif intraday islem kutusu.

Pine S/R genis ve fiyat dar bir alt-aralikta konsolide olunca (or. destek 1653
ama fiyat 1672-1688'de), gercek aktif kutuyu son N x 15m swing high/low'dan cikar.
Boylece bot iki kenari da (taban-LONG dahil) ulasilabilir hedeflerle oynar.

Veri (06-13): Pine bant 201bps, adaptif kutu 98bps; fiyat %32 kutu-alt kenarinda
(bot kacirdi cunku Pine destegi 1653'e %0 degdi).
"""
from __future__ import annotations

from core.config import cfg
from core.logger import get_logger

log = get_logger("IntradayBox")


def compute_intraday_box(
    price: float, pine_s: float, pine_r: float
) -> dict:
    """
    Donus: {valid, box_s, box_r, reason}. valid=True ise box_s/box_r fade bandi
    olarak kullanilabilir.
    """
    out = {"valid": False, "box_s": 0.0, "box_r": 0.0,
           "pine_s": float(pine_s or 0), "pine_r": float(pine_r or 0), "reason": ""}
    if not bool(getattr(cfg, "V3_ADAPTIVE_BOX_ENABLED", True)):
        out["reason"] = "kapali"
        return out
    px = float(price or 0)
    if px <= 0:
        return out

    from engine.v3_common import bars_15m

    n = int(getattr(cfg, "V3_BOX_LOOKBACK_BARS", 24) or 24)
    bars = bars_15m(n + 4)[-n:]
    if len(bars) < max(8, n // 2):
        out["reason"] = "yetersiz bar"
        return out

    box_r = max(float(b.get("high", 0) or 0) for b in bars)
    box_s = min(float(b.get("low", 0) or 1e12) for b in bars)
    if box_r <= box_s or box_s <= 0:
        return out

    # Kutu kenarini GERCEK Pine yatay seviyesiyle hizala: spike-high'i direnc
    # sanma. Fiyatin altinda/ustunde aktif Pine seviyesi varsa kutu kenari onu
    # asmasin (06-14: ham tavan 1690 spike iken gercek direnc L6=1682.6; fiyat
    # 1681'de orada tepki veriyordu — clamp ile NEAR_RESISTANCE -> fade short).
    if pine_r and px < pine_r < box_r:
        box_r = float(pine_r)
    if pine_s and box_s < pine_s < px:
        box_s = float(pine_s)
    if box_r <= box_s:
        return out

    width_bps = (box_r - box_s) / px * 1e4
    min_w = float(getattr(cfg, "V3_BOX_MIN_WIDTH_BPS", 40) or 40)
    max_w = float(getattr(cfg, "V3_BOX_MAX_WIDTH_BPS", 220) or 220)
    if width_bps < min_w or width_bps > max_w:
        out["reason"] = f"kutu genisligi {width_bps:.0f}bps araliksiz"
        return out

    # Fiyat kutu icinde mi (konsolidasyon)
    if not (box_s <= px <= box_r):
        out["reason"] = "fiyat kutu disinda"
        return out

    # Yalniz Pine bandi GENIS ve kutu belirgin DAR ise (kutu Pine'in alt-araligi)
    if pine_s > 0 and pine_r > pine_s:
        pine_w = (pine_r - pine_s) / px * 1e4
        # Pine destegi cok uzaksa (fiyatin altinda > kutu genisligi kadar) kutu sart
        s_far = (px - pine_s) / px * 1e4 > width_bps * float(
            getattr(cfg, "V3_BOX_PINE_FAR_MULT", 1.5) or 1.5
        )
        tighter = width_bps < pine_w * 0.85
        if not (s_far or tighter):
            out["reason"] = "Pine bandi zaten dar/yakin — kutu gerekmez"
            return out

    out.update({"valid": True, "box_s": round(box_s, 2), "box_r": round(box_r, 2),
                "reason": f"aktif kutu {width_bps:.0f}bps"})
    return out
