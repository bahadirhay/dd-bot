"""
engine/market_structure_v3.py — Canlı yapı durum makinesi (BOS / CHoCH).

Skaler "güç" (impulse hafızası, saatlerce yapışkan) yerine, onaylı swing
dizisinden ŞU ANKİ yapısal durumu çıkarır:

  • Bull state : HH + HL  (higher high + higher low)
  • Bear state : LH + LL  (lower high + lower low)
  • Neutral    : karışık / kırılım yok → CHOP'ta otomatik nötr

Olaylar (fiyat vs son onaylı swing):
  • BOS_DOWN / BOS_UP   : mevcut yön devam (kırılım)
  • CHOCH_UP / CHOCH_DOWN: karakter değişimi (ters tarafa kırılım) → bias flip

Güç zamanla SÖNMEZ — yapı-olayıyla belirlenir; aralığa geri alınınca nötre döner.
Bu, "strength=87 saatlerce sabit" yapışkanlığını ve range'de kalıcı bias'ı çözer.
"""
from __future__ import annotations

from core.config import cfg
from engine.v3_common import bars_15m


def _pivots(bars: list[dict], left: int, right: int) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Fractal pivot: i barı, sol/sağ komşularının tepesi/dibi mi."""
    highs: list[tuple[int, float]] = []
    lows: list[tuple[int, float]] = []
    n = len(bars)
    for i in range(left, n - right):
        try:
            h = float(bars[i].get("high", 0) or 0)
            l = float(bars[i].get("low", 0) or 0)
        except (AttributeError, TypeError, ValueError):
            continue
        window = bars[i - left:i + right + 1]
        hs = [float(b.get("high", 0) or 0) for b in window]
        ls = [float(b.get("low", 0) or 0) for b in window]
        if h >= max(hs) and h > 0:
            highs.append((i, h))
        if l <= min(ls) and l > 0:
            lows.append((i, l))
    return highs, lows


def compute_swing_structure(
    bars15: list[dict] | None = None, price: float = 0.0
) -> dict:
    """
    Onaylı swing'lerden BOS/CHoCH durumu. Çıktı:
      state   : "bull" | "bear" | "neutral"
      bias    : "BULL" | "BEAR" | "NEUTRAL"
      event   : "BOS_UP"|"BOS_DOWN"|"CHOCH_UP"|"CHOCH_DOWN"|"NONE"
      strength: 0.0–1.0 (chop'ta düşük → Structure katkısı küçülür)
    """
    bars = list(bars15) if bars15 else bars_15m(
        int(getattr(cfg, "V3_STRUCT_PIVOT_BARS", 60) or 60)
    )
    px = float(price or 0)
    if px <= 0 and bars:
        px = float(bars[-1].get("close", 0) or 0)

    out = {
        "state": "neutral",
        "bias": "NEUTRAL",
        "event": "NONE",
        "strength": 0.0,
        "last_high": 0.0,
        "last_low": 0.0,
        "window": "swing_structure",
    }
    left = int(getattr(cfg, "V3_STRUCT_PIVOT_LEFT", 3) or 3)
    right = int(getattr(cfg, "V3_STRUCT_PIVOT_RIGHT", 3) or 3)
    if len(bars) < left + right + 2:
        return out

    highs, lows = _pivots(bars, left, right)
    if len(highs) < 2 or len(lows) < 2:
        return out

    (_, ph0), (_, ph1) = highs[-2], highs[-1]   # önceki, son tepe
    (_, pl0), (_, pl1) = lows[-2], lows[-1]      # önceki, son dip
    out["last_high"] = round(ph1, 2)
    out["last_low"] = round(pl1, 2)

    hh = ph1 > ph0
    hl = pl1 > pl0
    lh = ph1 < ph0
    ll = pl1 < pl0

    # --- Durum sınıfı ---
    if hh and hl:
        state, bias = "bull", "BULL"
    elif lh and ll:
        state, bias = "bear", "BEAR"
    else:
        state, bias = "neutral", "NEUTRAL"

    # --- Olay: fiyat son onaylı swing'i kırdı mı (BOS/CHoCH) ---
    buf = float(getattr(cfg, "V3_STRUCT_BREAK_BUFFER_BPS", 5) or 5) / 10000.0
    event = "NONE"
    broke_low = px < pl1 * (1.0 - buf)
    broke_high = px > ph1 * (1.0 + buf)
    if state == "bear":
        if broke_low:
            event = "BOS_DOWN"
        elif broke_high:
            event = "CHOCH_UP"
            bias = "BULL"          # karakter değişimi → bias flip
    elif state == "bull":
        if broke_high:
            event = "BOS_UP"
        elif broke_low:
            event = "CHOCH_DOWN"
            bias = "BEAR"
    else:
        # nötr durumda saf kırılım yön verir
        if broke_high:
            event, bias = "BOS_UP", "BULL"
        elif broke_low:
            event, bias = "BOS_DOWN", "BEAR"

    # --- Güç: durum + olay kararlılığı (zaman değil) ---
    strength = 0.0
    if bias in ("BULL", "BEAR"):
        strength = 0.45                      # net yönlü yapı tabanı
        if event in ("BOS_UP", "BOS_DOWN"):
            # kırılım derinliği (displacement) → güç
            ref = pl1 if event == "BOS_DOWN" else ph1
            disp = abs(px - ref) / px if px > 0 else 0.0
            strength = min(1.0, 0.65 + min(0.35, disp * 40.0))
        elif event in ("CHOCH_UP", "CHOCH_DOWN"):
            strength = 0.55                  # karakter değişimi: orta-güçlü ama taze
    else:
        strength = 0.12                      # chop → neredeyse sıfır katkı

    out.update({
        "state": state,
        "bias": bias,
        "event": event,
        "strength": round(strength, 3),
    })
    return out


def swing_structure_log_line(s: dict | None) -> str:
    x = s or {}
    return (
        f"[SWING_STRUCT] state={x.get('state')} bias={x.get('bias')} "
        f"event={x.get('event')} guc={float(x.get('strength', 0) or 0) * 100:.0f} "
        f"| sonHH={x.get('last_high')} sonLL={x.get('last_low')}"
    )
