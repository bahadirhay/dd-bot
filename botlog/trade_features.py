"""
botlog/trade_features.py
────────────────────────
Pozisyon acilis/kapanis aninda OGRENME ozelliklerini toplar. Tamamen
non-blocking: herhangi bir alan yoksa None doner, trade akisini bozmaz.

Acilis: giris-kenar mesafesi, akis (buy_ratio), zone, path, yapi skorlari, TP1/SL bps.
Kapanis: MFE (max lehte) / MAE (max aleyhte) — 1m mumlardan.
"""
from __future__ import annotations


def _bps(a: float, b: float) -> float | None:
    if a and b and a > 0:
        return round(abs(b - a) / a * 1e4, 1)
    return None


def collect_open_features(side: str, entry: float, tp1: float, sl: float) -> dict:
    feats: dict = {}
    try:
        from core.state import state
    except Exception:
        return feats

    side = (side or "").upper()
    feats["tp1_bps"] = _bps(entry, tp1)
    feats["sl_bps"] = _bps(entry, sl)

    # Akis: taker alis orani
    try:
        from engine.cvd_v3 import get_cvd_snapshot

        cvd = get_cvd_snapshot() or {}
        br = cvd.get("buy_ratio")
        if br is not None:
            feats["buy_ratio_at_open"] = round(float(br), 3)
    except Exception:
        pass

    # Karar baglami: zone, path, yapi skorlari, band kenarlari
    dec = getattr(state, "v3_decision", None) or {}
    lv = getattr(state, "v3_levels", None) or {}
    active = lv.get("active") if isinstance(lv, dict) else None
    active = active if isinstance(active, dict) else lv

    zone = (dec.get("zone") or (active or {}).get("zone") or "").upper()
    if zone:
        feats["zone_at_open"] = zone
    path = (dec.get("channel_path") or dec.get("path") or "").lower()
    if path:
        feats["path_at_open"] = path

    ds = dec.get("direction_scores") or {}
    sl_sc = ds.get("structure_long_score")
    ss_sc = ds.get("structure_short_score")
    if sl_sc is not None:
        feats["struct_long_at_open"] = round(float(sl_sc), 1)
    if ss_sc is not None:
        feats["struct_short_at_open"] = round(float(ss_sc), 1)

    # Giris-kenar mesafesi: fade -> fade edilen seviye; breakout -> kirilan seviye
    try:
        s = float((active or {}).get("active_support") or (active or {}).get("support") or 0)
        r = float((active or {}).get("active_resistance") or (active or {}).get("resistance") or 0)
        edge = 0.0
        if path == "fade":
            edge = r if side == "SHORT" else s
        elif path == "breakout":
            edge = s if side == "SHORT" else r  # kirilan seviye
        else:
            edge = r if side == "SHORT" else s
        d = _bps(entry, edge)
        if d is not None:
            feats["entry_to_edge_bps"] = d
    except Exception:
        pass

    return feats


def collect_close_features(side: str, entry: float, open_ts: float) -> dict:
    """Kapanista MFE/MAE: pozisyon suresince ulasi lan en iyi/en kotu fiyat (bps)."""
    feats: dict = {}
    if not entry or entry <= 0:
        return feats
    try:
        from engine.v3_common import bars_1m

        bars = bars_1m(800)
        rel = [b for b in bars if float(b.get("ts", 0) or 0) >= float(open_ts or 0)]
        if not rel:
            rel = bars[-30:]
        if not rel:
            return feats
        hi = max(float(b.get("high", 0) or 0) for b in rel)
        lo = min(float(b.get("low", 0) or 1e12) for b in rel)
        side = (side or "").upper()
        if side == "SHORT":
            feats["mfe_bps"] = round((entry - lo) / entry * 1e4, 1)  # lehte = dusus
            feats["mae_bps"] = round((hi - entry) / entry * 1e4, 1)  # aleyhte = yukseliş
        else:
            feats["mfe_bps"] = round((hi - entry) / entry * 1e4, 1)
            feats["mae_bps"] = round((entry - lo) / entry * 1e4, 1)
    except Exception:
        pass
    return feats
