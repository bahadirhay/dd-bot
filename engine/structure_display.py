"""
engine/structure_display.py — 1h+15m uyum metni (dashboard).
"""
from __future__ import annotations

from core.state import state
from engine.structure_insight import (
    structure_detail_text,
    why_not_down,
    why_not_down_1h,
)


def alignment_detail(tv: dict | None = None) -> dict:
    """
    Dashboard uyum satırı.

    Önemli: «DOWN downtrend» = mum akışı / CVD momentum.
    «1h+15m UYUMLU» = swing yapısı 15m=DOWN ve 1h=DOWN (sıkı kural).
    İkisi aynı şey değil; grafikte kırmızı mum ≠ yapı onayı.
    """
    tv = tv or state.trend_view or {}
    s15 = state.structure_15m or "?"
    s1h = state.structure_1h or "?"
    bias = tv.get("bias", "RANGE")
    aligned = tv.get("structure_aligned", False)

    struct_down = s15 == "DOWN" and s1h == "DOWN"
    struct_up = s15 == "UP" and s1h == "UP"
    same_label = s15 == s1h

    if aligned:
        status = "UYUMLU"
        reason = (
            f"Momentum {bias} ve swing yapısı onaylı ({s15}/{s1h}). "
            f"Trade kapısı açılabilir (güç≥60 şartı ayrı)."
        )
    elif same_label and s15 == "UNCLEAR":
        status = "BELİRSİZ"
        w15 = why_not_down() or structure_detail_text("15m")
        w1h = why_not_down_1h() or structure_detail_text("1h")
        reason = (
            f"Grafikte düşüş görmeniz normal (kırmızı mumlar) ama bot «yapı» ölçer: "
            f"son swing tepeleri ve dipleri birlikte aşağı olmalı. "
            f"Şu an 15m ve 1h ikisi de UNCLEAR — yön çelişkisi yok, yapı onayı yok. "
            f"{w15} {w1h}"
        )
    elif same_label and s15 not in ("UP", "DOWN", "UNCLEAR"):
        status = "BELİRSİZ"
        reason = f"Yapı henüz hesaplanmadı: 15m={s15} 1h={s1h}"
    elif bias == "DOWN":
        status = "UYUMSUZ"
        if s15 != "DOWN" and s1h != "DOWN":
            reason = (
                f"Momentum DOWN ama swing yapısı DOWN/DOWN değil (15m={s15}, 1h={s1h}). "
                f"{why_not_down() or ''} {why_not_down_1h() or ''}"
            ).strip()
        elif s15 == "DOWN" and s1h != "DOWN":
            reason = (
                f"15m yapı DOWN ama 1h={s1h}. Kısa vade düşüş, saatlik yapı henüz DOWN değil."
            )
        elif s15 != "DOWN" and s1h == "DOWN":
            reason = f"1h DOWN ama 15m={s15} — zaman dilimleri çelişiyor."
        else:
            reason = f"15m={s15} 1h={s1h} — trade için DOWN/DOWN gerekir."
    elif bias == "UP":
        status = "UYUMSUZ"
        reason = f"Momentum UP ama 15m={s15} 1h={s1h} — UP/UP swing onayı yok."
    else:
        status = "YATAY"
        reason = (
            f"Momentum RANGE (güç düşük). Yapı: {structure_detail_text('15m')}; "
            f"{structure_detail_text('1h')}"
        )

    trade_note = ""
    if bias == "DOWN" and not aligned:
        trade_note = " | Trade: momentum var, swing onayı yok"

    return {
        "status": status,
        "reason": reason,
        "s15": s15,
        "s1h": s1h,
        "bias": bias,
        "struct_down": struct_down,
        "struct_up": struct_up,
        "line": f"15m={s15} | 1h={s1h} | yapı: {status}{trade_note}",
    }
