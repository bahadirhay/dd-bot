"""
engine/level_ladder_v3.py — Tarihsel seviye merdiveni + stabil makro kanal.

İki mimari sorunu çözer:
  1) Aktif S/R her tick değişiyordu → "kanal içi/dışı" titreşiyordu.
     Çözüm: stabil makro kanal — geçmişin en güçlü pivotlarından kalıcı band,
     yalnız onaylı kırılımda (kapanış + displacement) güncellenir.
  2) Fiyat 6 Pine pivotunun altına/üstüne çıkınca liste boşalıp bot kör kalıyordu.
     Çözüm: TÜM geçmişi tarayan seviye merdiveni — herhangi bir fiyatta altta/üstte
     en yakın GERÇEK tarihsel seviyeyi bulur (kaba 24-bar sentetik yerine).

Pivot kind (support/resistance) sadece metadata; bant ataması KONUMA göre:
fiyatın altındaki en yakın seviye destek, üstündeki direnç (rol-flip doğal).
"""
from __future__ import annotations

import time

from core.config import cfg
from core.state import state
from core.logger import get_logger
from engine.v3_common import bars_15m, bars_1h

log = get_logger("LevelLadder")


def _pivots(bars: list[dict], left: int, right: int) -> list[float]:
    """Fractal pivot fiyatları (high + low birlikte, kind ayrımı yapmadan)."""
    out: list[float] = []
    n = len(bars)
    for i in range(left, n - right):
        try:
            h = float(bars[i].get("high", 0) or 0)
            l = float(bars[i].get("low", 0) or 0)
        except (AttributeError, TypeError, ValueError):
            continue
        win = bars[i - left:i + right + 1]
        hs = [float(b.get("high", 0) or 0) for b in win]
        ls = [float(b.get("low", 0) or 0) for b in win]
        if h > 0 and h >= max(hs):
            out.append(h)
        if l > 0 and l <= min(ls):
            out.append(l)
    return out


def build_level_ladder(price: float = 0.0) -> list[dict]:
    """
    Tüm geçmişten (15m derin + 1h) anlamlı pivotların kümelenmiş merdiveni.
    Her seviye: {price, touches}. Dokunuş = güç (tarihsel önem).
    """
    b15 = bars_15m(int(getattr(cfg, "V3_LADDER_15M_BARS", 500) or 500))
    b1h = bars_1h(int(getattr(cfg, "V3_LADDER_1H_BARS", 200) or 200))
    L15 = int(getattr(cfg, "V3_LADDER_PIVOT_LEFT", 3) or 3)
    R15 = int(getattr(cfg, "V3_LADDER_PIVOT_RIGHT", 3) or 3)

    raw: list[float] = []
    if len(b15) >= L15 + R15 + 2:
        raw += _pivots(b15, L15, R15)
    if len(b1h) >= 8:
        raw += _pivots(b1h, 3, 3)
    if not raw:
        return []

    px = float(price or 0) or (raw[-1] if raw else 1600.0)
    tol = max(px * float(getattr(cfg, "V3_LADDER_MERGE_PCT", 0.0015) or 0.0015), 1.0)

    raw.sort()
    ladder: list[dict] = []
    for p in raw:
        if ladder and abs(p - ladder[-1]["price"]) <= tol:
            t = ladder[-1]["touches"] + 1
            ladder[-1]["price"] = round(
                (ladder[-1]["price"] * (t - 1) + p) / t, 2
            )
            ladder[-1]["touches"] = t
        else:
            ladder.append({"price": round(p, 2), "touches": 1})
    return ladder


def nearest_below(price: float, ladder: list[dict] | None = None) -> dict | None:
    """Fiyatın altındaki en yakın tarihsel seviye (destek görevi görür)."""
    if price <= 0:
        return None
    ladder = ladder if ladder is not None else build_level_ladder(price)
    cands = [l for l in ladder if 0 < l["price"] < price]
    return max(cands, key=lambda x: x["price"]) if cands else None


def nearest_above(price: float, ladder: list[dict] | None = None) -> dict | None:
    """Fiyatın üstündeki en yakın tarihsel seviye (direnç görevi görür)."""
    if price <= 0:
        return None
    ladder = ladder if ladder is not None else build_level_ladder(price)
    cands = [l for l in ladder if l["price"] > price]
    return min(cands, key=lambda x: x["price"]) if cands else None


def stable_macro_channel(price: float, ladder: list[dict] | None = None) -> dict:
    """
    Stabil makro kanal: güçlü tarihsel seviyelerden fiyatı çevreleyen kalıcı band.
    Yalnız fiyat banttan onaylı kırılınca (displacement) yeniden hesaplanır →
    her tick titreşmez. state.v3_macro_channel'da saklanır.
    """
    if price <= 0:
        return dict(getattr(state, "v3_macro_channel", {}) or {})
    ladder = ladder if ladder is not None else build_level_ladder(price)
    min_touch = int(getattr(cfg, "V3_MACRO_MIN_TOUCHES", 2) or 2)
    strong = [l for l in ladder if l["touches"] >= min_touch] or ladder

    prev = dict(getattr(state, "v3_macro_channel", {}) or {})
    ps = float(prev.get("support") or 0)
    pr = float(prev.get("resistance") or 0)
    buf = float(getattr(cfg, "V3_MACRO_BREAK_BUFFER_BPS", 15) or 15) / 10000.0

    # Önceki kanal hâlâ fiyatı çevreliyorsa KORU (stabilite) — kırılım yoksa.
    if ps > 0 and pr > ps:
        broke_dn = price < ps * (1.0 - buf)
        broke_up = price > pr * (1.0 + buf)
        if not broke_dn and not broke_up:
            return prev

    below = [l for l in strong if l["price"] < price]
    above = [l for l in strong if l["price"] > price]
    s = max(below, key=lambda x: x["touches"])["price"] if below else 0.0
    r = min(above, key=lambda x: (x["price"]))["price"] if above else 0.0
    # direnç için: fiyat üstündeki güçlü seviyelerden en yakını yeterli
    if above:
        near_above = min(above, key=lambda x: x["price"] - price)
        r = near_above["price"]
    ch = {
        "support": round(s, 2),
        "resistance": round(r, 2),
        "ts": time.time(),
        "source": "ladder",
    }
    state.v3_macro_channel = ch
    return ch


def position_vs_channel(price: float) -> str:
    """Fiyat stabil kanala göre: INSIDE / ABOVE / BELOW."""
    ch = dict(getattr(state, "v3_macro_channel", {}) or {})
    s = float(ch.get("support") or 0)
    r = float(ch.get("resistance") or 0)
    if s <= 0 or r <= s or price <= 0:
        return "UNKNOWN"
    if price < s:
        return "BELOW"
    if price > r:
        return "ABOVE"
    return "INSIDE"
