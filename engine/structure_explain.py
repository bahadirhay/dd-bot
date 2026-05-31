"""
engine/structure_explain.py — Seçilen ana için swing yapı incelemesi (Binance mumları).
"""
from __future__ import annotations

from core.config import cfg
from engine.structure import _detect_swings, _determine_structure


def _swing_summary(highs: list, lows: list) -> dict:
    last_h = highs[-3:] if len(highs) >= 3 else highs
    last_l = lows[-3:] if len(lows) >= 3 else lows

    def trend(prices):
        if len(prices) < 2:
            return "yetersiz"
        if all(prices[i] > prices[i - 1] for i in range(1, len(prices))):
            return "yükseliyor"
        if all(prices[i] < prices[i - 1] for i in range(1, len(prices))):
            return "düşüyor"
        return "karışık"

    hp = [h["price"] for h in last_h]
    lp = [l["price"] for l in last_l]
    return {
        "high_prices": hp,
        "low_prices": lp,
        "high_trend": trend(hp),
        "low_trend": trend(lp),
        "n_highs": len(highs),
        "n_lows": len(lows),
    }


def analyze_bars(bars: list[dict], lookback: int, name: str) -> dict:
    if len(bars) < lookback * 2 + 5:
        return {
            "tf": name,
            "label": "?",
            "note": f"Yetersiz mum ({len(bars)} adet)",
        }
    highs, lows = _detect_swings(bars, lookback)
    label = _determine_structure(highs, lows)
    sm = _swing_summary(highs, lows)
    down_ok = sm["high_trend"] == "düşüyor" and sm["low_trend"] == "düşüyor"
    up_ok = sm["high_trend"] == "yükseliyor" and sm["low_trend"] == "yükseliyor"
    if label == "DOWN":
        rule = "Son swing tepeleri ve dipleri birlikte aşağı → DOWN."
    elif label == "UP":
        rule = "Son swing tepeleri ve dipleri birlikte yukarı → UP."
    else:
        if down_ok:
            rule = "Tepeler/dipler kısmen aşağı ama kural tam eşleşmedi."
        elif up_ok:
            rule = "Tepeler/dipler kısmen yukarı ama kural tam eşleşmedi."
        else:
            rule = (
                "DOWN için hem tepeler hem dipler düşmeli; "
                f"şu an tepeler={sm['high_trend']}, dipler={sm['low_trend']}."
            )
    return {
        "tf": name,
        "label": label,
        "lookback": lookback,
        "rule": rule,
        **sm,
    }


def bars_until_ts(bars: list[dict], ts: float) -> list[dict]:
    """Seçilen 15m/1h mumuna kadar olan kapalı barlar."""
    out = [b for b in bars if b["ts"] <= ts + 1]
    return out if out else bars


def analyze_structure_at(ts: float) -> dict:
    from dashboard.binance_chart import fetch_15m_klines, fetch_1h_klines

    b15 = bars_until_ts(fetch_15m_klines(96), ts)
    b1h = bars_until_ts(fetch_1h_klines(48), ts)
    a15 = analyze_bars(b15, cfg.SWING_LB_15M, "15m")
    a1h = analyze_bars(b1h, cfg.SWING_LB_1H, "1h")
    return {"15m": a15, "1h": a1h}


def format_structure_block(analysis: dict, bot_s15: str, bot_s1h: str) -> list[str]:
    lines = [
        "--- YAPI İNCELEMESİ (swing, seçilen ana göre Binance) ---",
        f"  Bot kaydındaki etiket: 15m={bot_s15}  1h={bot_s1h}",
        "",
    ]
    for key in ("15m", "1h"):
        a = analysis.get(key) or {}
        if a.get("note"):
            lines.append(f"  {a['tf']}: {a['note']}")
            continue
        lines.append(
            f"  {a['tf']} hesaplanan={a['label']} (lookback={a.get('lookback')})"
        )
        lines.append(
            f"    Tepeler ({a.get('n_highs')} swing): "
            f"{a.get('high_trend')} {a.get('high_prices')}"
        )
        lines.append(
            f"    Dipler ({a.get('n_lows')} swing): "
            f"{a.get('low_trend')} {a.get('low_prices')}"
        )
        lines.append(f"    → {a.get('rule', '')}")
        lines.append("")
    lines.append(
        "  Not: «DOWN downtrend» momentum (mum rengi/CVD); "
        "yukarıdaki DOWN/UP swing onayıdır — trade kapısı ikisini de ister."
    )
    return lines
