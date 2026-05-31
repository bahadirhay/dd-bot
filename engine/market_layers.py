"""
engine/market_layers.py — Grafikle hizalı çok katmanlı görünüm.

Katmanlar (sabit pencereler, bahane yok):
  1m nabız   → Dashboard/grafikte son N dakika (varsayılan 15)
  15m zamanlama → Son M kapalı 15m onay (varsayılan 4 = 1 saat)
  15m/1h yapı → Swing (trade kapısı; nabızla karıştırılmaz)
"""
from __future__ import annotations

from core.config import cfg
from core.state import state
from engine.bars_1m import get_bars_1m
from engine.structure import get_bars_15m


def _pct(o: float, c: float) -> float:
    return ((c - o) / o * 100.0) if o else 0.0


def _window_stats(bars: list[dict], label: str, minutes: int) -> dict:
    if not bars:
        return {
            "label": label,
            "minutes": minutes,
            "n": 0,
            "green": 0,
            "red": 0,
            "chg_pct": 0.0,
            "bias": "—",
            "strength": 0,
            "line": f"{label}: veri yok",
        }

    green = sum(1 for b in bars if b["close"] > b["open"])
    red = len(bars) - green
    chg = _pct(bars[0]["open"], bars[-1]["close"])
    n = len(bars)

    up_ratio = green / n
    dn_ratio = red / n

    if chg >= 0.20 and up_ratio >= 0.55:
        bias, strength = "UP", min(100, int(40 + up_ratio * 40 + min(chg, 1.0) * 15))
    elif chg <= -0.20 and dn_ratio >= 0.55:
        bias, strength = "DOWN", min(100, int(40 + dn_ratio * 40 + min(abs(chg), 1.0) * 15))
    elif chg >= 0.08 and green >= red:
        bias, strength = "UP", int(25 + up_ratio * 25)
    elif chg <= -0.08 and red >= green:
        bias, strength = "DOWN", int(25 + dn_ratio * 25)
    else:
        bias, strength = "RANGE", int(max(up_ratio, dn_ratio) * 30)

    arrow = {"UP": "↑", "DOWN": "↓", "RANGE": "→"}.get(bias, "?")
    line = (
        f"{label} ({minutes}dk): {green}Y/{red}K {chg:+.2f}% {arrow} {bias}"
    )
    return {
        "label": label,
        "minutes": minutes,
        "n": n,
        "green": green,
        "red": red,
        "chg_pct": round(chg, 3),
        "bias": bias,
        "strength": strength,
        "line": line,
    }


def _forming_as_bar() -> dict | None:
    f = state.forming_15m or {}
    if not f.get("open"):
        return None
    return {
        "ts": f.get("period_start", 0),
        "open": f["open"],
        "high": f["high"],
        "low": f["low"],
        "close": f.get("close", f["open"]),
        "volume": f.get("volume", 0),
    }


def compute_market_layers() -> dict:
    n1 = cfg.PULSE_BARS_1M
    n15 = cfg.TIMING_BARS_15M

    bars_1m = get_bars_1m(n1)
    bars_15m = get_bars_15m(n15)

    pulse = _window_stats(bars_1m, "1m nabız", n1)
    timing = _window_stats(bars_15m, "15m onay", n15 * 15)

    forming = _forming_as_bar()
    if forming:
        f_chg = _pct(forming["open"], forming["close"])
        pulse["forming_chg"] = round(f_chg, 3)
        pulse["line"] += f" | açık15m={f_chg:+.2f}%"
    else:
        pulse["forming_chg"] = 0.0

    s15 = state.structure_15m or "?"
    s1h = state.structure_1h or "?"
    struct_line = f"Yapı (swing): 15m={s15}  1h={s1h}"

    # Öncelik: grafikte gördüğünüz hareket = 1m nabız
    lead = pulse["bias"]
    confirm = timing["bias"]

    if lead == confirm and lead in ("UP", "DOWN"):
        align_status = "UYUMLU"
        align_note = f"Nabız ve 15m onay aynı: {lead}"
    elif lead in ("UP", "DOWN") and confirm == "RANGE":
        align_status = "ERKEN"
        align_note = (
            f"Grafikte {lead} (1m) var; 15m henüz net onaylamadı — "
            f"trade için yapı+ güç şartı ayrı"
        )
    elif lead == "RANGE" and confirm in ("UP", "DOWN"):
        align_status = "GECİKMELİ"
        align_note = f"15m {confirm} diyor; 1m nabız yatay — kısa vade sakin"
    elif lead != confirm and lead in ("UP", "DOWN") and confirm in ("UP", "DOWN"):
        align_status = "ÇELİŞKİ"
        align_note = f"1m {lead} vs 15m {confirm} — küçük TF önce döner"
    else:
        align_status = "YATAY"
        align_note = "Kısa ve orta vade belirsiz"

    struct_ok_up = s15 == "UP" and s1h == "UP"
    struct_ok_dn = s15 == "DOWN" and s1h == "DOWN"
    if lead == "UP" and not struct_ok_up:
        trade_hint = "LONG kapısı: yapı UP/UP değil" + (
            f" ({s15}/{s1h})" if s15 != "?" else ""
        )
    elif lead == "DOWN" and not struct_ok_dn:
        trade_hint = "SHORT kapısı: yapı DOWN/DOWN değil" + (
            f" ({s15}/{s1h})" if s15 != "?" else ""
        )
    elif lead in ("UP", "DOWN"):
        trade_hint = f"Yön {lead} — güç≥60 ve flow onayı trade için"
    else:
        trade_hint = "Yön yok — izleme"

    chart_lines = [
        pulse["line"],
        timing["line"],
        struct_line,
        f"Hizalama: {align_status} — {align_note}",
        trade_hint,
    ]

    return {
        "pulse_1m": pulse,
        "timing_15m": timing,
        "structure_15m": s15,
        "structure_1h": s1h,
        "lead_bias": lead,
        "confirm_bias": confirm,
        "align_status": align_status,
        "align_note": align_note,
        "trade_hint": trade_hint,
        "chart_lines": chart_lines,
        "chart_summary": " | ".join(chart_lines[:3]),
    }
