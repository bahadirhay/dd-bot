"""
engine/explain_decision.py — Bot kurallarina gore yapı etiketi ve trade karari.
"""
from __future__ import annotations

from core.config import cfg
from botlog.db import _conn

# Kullaniciya Turkce etiketler (bot icinde UP/DOWN/RANGE)
LABEL_SHORT = "SHORT"
LABEL_LONG = "LONG (BUY)"
LABEL_FLAT = "YATAY"

VISUAL_DOWN = "DÜŞÜŞ"
VISUAL_UP = "YÜKSELİŞ"
VISUAL_FLAT = "YATAY"


def _normalize_signal(sig) -> dict:
    """Journal payload: signal çoğu zaman 'FLAT'/'LONG' string."""
    if isinstance(sig, dict):
        return sig
    if isinstance(sig, str) and sig.upper() in ("LONG", "SHORT"):
        return {"direction": sig.upper()}
    return {}


def _normalize_trend(tv) -> dict:
    if isinstance(tv, dict):
        return tv
    return {}


def structure_label(s15: str | None, s1h: str | None) -> dict:
    """
    1h+15m yapı → SHORT / LONG / YATAY (bot kurallari).
    """
    s15 = (s15 or "?").upper()
    s1h = (s1h or "?").upper()

    if s15 == "DOWN" and s1h == "DOWN":
        return {
            "label": LABEL_SHORT,
            "confidence": "yuksek",
            "rule": "15m ve 1h yapı aşağı (HH/HL bozulmuş) → ana SHORT yapısı.",
            "trade_gate": "SHORT adayı açılabilir (orderflow onayı gerekir).",
        }
    if s15 == "UP" and s1h == "UP":
        return {
            "label": LABEL_LONG,
            "confidence": "yuksek",
            "rule": "15m ve 1h yapı yukarı (LH/HL yukarı) → ana LONG yapısı.",
            "trade_gate": "LONG adayı açılabilir (orderflow onayı gerekir).",
        }
    if s15 == "DOWN" and s1h != "DOWN":
        return {
            "label": LABEL_SHORT,
            "confidence": "zayif",
            "rule": "15m aşağı ama 1h henüz aşağı değil → counter-trend / düzeltme.",
            "trade_gate": "REQUIRE_HTF_ALIGN=true ise SHORT AÇILMAZ (15m tek başına yetmez).",
        }
    if s15 == "UP" and s1h != "UP":
        return {
            "label": LABEL_LONG,
            "confidence": "zayif",
            "rule": "15m yukarı ama 1h henüz yukarı değil → counter-trend riski.",
            "trade_gate": "REQUIRE_HTF_ALIGN=true ise LONG AÇILMAZ.",
        }
    if s15 in ("RANGE", "FLAT", "?") and s1h in ("RANGE", "FLAT", "?"):
        return {
            "label": LABEL_FLAT,
            "confidence": "orta",
            "rule": "Yapı net değil veya yatay bant.",
            "trade_gate": "Trend trade kapalı — yatay rejim.",
        }
    return {
        "label": LABEL_FLAT,
        "confidence": "dusuk",
        "rule": f"15m={s15} 1h={s1h} karışık — net trend yok.",
        "trade_gate": "Trade için 1h+15m aynı yön şart.",
    }


def why_no_trade(p_at: dict, tv: dict | None = None) -> list[str]:
    """Botun o an pozisyon açmama nedenleri (sıralı)."""
    tv = _normalize_trend(tv or p_at.get("trend"))
    reasons = []

    if p_at.get("in_position"):
        reasons.append(
            f"Zaten pozisyon vardı: {p_at.get('pos_side')} — yeni giriş yapılmaz."
        )
        return reasons

    s15 = p_at.get("structure_15m")
    s1h = p_at.get("structure_1h")
    struct = structure_label(s15, s1h)

    bias = tv.get("bias", "RANGE")
    strength = tv.get("strength", 0)
    trade_ok = tv.get("trade_ok", False)
    flow_ok = tv.get("flow_ok", False)
    aligned = tv.get("structure_aligned", False)
    phase = tv.get("phase", "")

    reasons.append(
        f"Yapı etiketi: {struct['label']} — {struct['rule']}"
    )

    if not cfg.AUTO_TRADE_ENABLED:
        reasons.append("AUTO_TRADE kapalı — bot otomatik emir göndermez.")

    if bias == "RANGE":
        reasons.append(
            f"Trend görünümü YATAY (güç {strength}/100) — trade eşiği geçilmedi."
        )
    elif not flow_ok:
        reasons.append(
            f"Orderflow/trend gücü yetersiz (güç {strength}/100, min ~60) — "
            f"CVD/taker onayı zayıf."
        )

    if cfg.REQUIRE_HTF_ALIGN and not aligned:
        reasons.append(
            f"1h+15m uyumsuz (15m={s15}, 1h={s1h}) — {struct['trade_gate']}"
        )

    if flow_ok and aligned and not trade_ok:
        reasons.append("trade_ok=false — iç kural seti girişe izin vermedi.")

    if trade_ok and phase not in ("drop", "rise", "downtrend", "uptrend"):
        if cfg.ENTRY_MODE == "trend" and cfg.IMPULSE_1M_TRADE:
            reasons.append(
                f"Impulse giriş: phase={phase} — ani drop/rise yok (düz downtrend/uptrend yetmez)."
            )

    cvd = p_at.get("cvd_5m", 0)
    taker = p_at.get("taker_ratio", 0.5)
    if struct["label"] == LABEL_SHORT and cvd > 100:
        reasons.append(
            f"CVD5m={cvd:+.0f} satışa ters — fiyat düşse bile agresif alım baskısı var."
        )
    if struct["label"] == LABEL_LONG and cvd < -100:
        reasons.append(
            f"CVD5m={cvd:+.0f} alıma ters — yükselişe rağmen satış akışı."
        )
    if struct["label"] == LABEL_SHORT and taker > 0.55:
        reasons.append(f"Taker alım {taker:.0%} — SHORT için satış dominant değil.")
    if struct["label"] == LABEL_LONG and taker < 0.45:
        reasons.append(f"Taker satış {1-taker:.0%} — LONG için alım dominant değil.")

    sig = _normalize_signal(p_at.get("signal"))
    if sig.get("no_entry_reason"):
        reasons.append(f"15m sinyal kaydı: {sig['no_entry_reason']}")

    if trade_ok and aligned:
        blockers = [r for r in reasons[1:] if "SAĞLAN" not in r]
        if not blockers:
            reasons.append(
                f"Ana kapılar AÇIK (bias={bias}, phase={phase}) — "
                "yine de giriş yoksa: impulse anı kaçırıldı, cooldown veya emir hatası."
            )

    return reasons


def load_nearby_signal(ts: float, window_sec: float = 1200) -> dict | None:
    with _conn() as db:
        row = db.execute(
            """
            SELECT direction, entered, no_entry_reason, regime, structure_15m,
                   structure_1h, cvd_5m, taker_ratio, price, ts_human
            FROM signals
            WHERE ts BETWEEN ? AND ?
            ORDER BY ABS(ts - ?) ASC
            LIMIT 1
            """,
            (ts - window_sec, ts + window_sec, ts),
        ).fetchone()
    return dict(row) if row else None


def load_nearby_trade(ts: float, window_sec: float = 3600) -> dict | None:
    with _conn() as db:
        row = db.execute(
            """
            SELECT direction, status, entry_price, open_ts, close_ts, close_reason
            FROM trades
            WHERE open_ts <= ? AND (close_ts IS NULL OR close_ts >= ?)
            ORDER BY open_ts DESC
            LIMIT 1
            """,
            (ts + window_sec, ts - window_sec),
        ).fetchone()
    return dict(row) if row else None


def _normalize_visual(raw: str) -> str:
    t = (raw or "").upper()
    if "DUS" in t or "DROP" in t or "SHORT" in t or "SAT" in t:
        return VISUAL_DOWN
    if "YUK" in t or "RISE" in t or "LONG" in t or "ALIM" in t or "BUY" in t:
        return VISUAL_UP
    return VISUAL_FLAT


def infer_visual_from_candle(ctx: dict) -> dict:
    """Görsel yokken o anki mumdan tahmini yön."""
    c = ctx.get("candle") or {}
    o, cl = c.get("open", 0), c.get("close", 0)
    if not o:
        return {"ok": False, "label": "", "source": "mum_verisi", "detail": "Mum verisi yok"}
    chg = (cl - o) / o * 100
    if chg < -0.08:
        label = VISUAL_DOWN
    elif chg > 0.08:
        label = VISUAL_UP
    else:
        label = VISUAL_FLAT
    return {
        "ok": True,
        "label": label,
        "source": "mum_verisi (DB)",
        "detail": f"Kayitli mum: {chg:+.2f}% (görsel değil, yaklaşık)",
    }


def compare_visual_vs_bot(visual: dict, decision: dict) -> dict:
    """Grafikte gördüğünüz vs bot etiketi."""
    if not visual.get("ok"):
        return {
            "match": "BILINMIYOR",
            "summary": "Grafik yönü okunamadi — sadece bot etiketine bakin.",
        }

    v = visual.get("label", VISUAL_FLAT)
    bot = decision.get("structure_label", LABEL_FLAT)
    bias = decision.get("bias", "RANGE")

    if v == VISUAL_DOWN:
        if bot == LABEL_SHORT:
            m, s = "UYUMLU", (
                "Grafikte düşüş görüyorsunuz; bot da SHORT yapısı görüyor — "
                "algı uyumlu. Yine de pozisyon için orderflow + 1h kapısı gerekir."
            )
        elif bot == LABEL_FLAT:
            m, s = "UYUMSUZ", (
                "Grafikte düşüş var ama bot YATAY diyor — "
                "muhtemelen 1h uyumsuz veya trend gücü düşük; bu yüzden trade yok."
            )
        else:
            m, s = "UYUMSUZ", (
                "Grafikte düşüş var ama bot LONG (BUY) yapısı görüyor — "
                "büyük uyumsuzluk; farklı zaman veya bot farklı yapı okumuş olabilir."
            )
    elif v == VISUAL_UP:
        if bot == LABEL_LONG:
            m, s = "UYUMLU", (
                "Grafikte yükseliş görüyorsunuz; bot da LONG (BUY) yapısı görüyor."
            )
        elif bot == LABEL_FLAT:
            m, s = "UYUMSUZ", (
                "Grafikte yükseliş var ama bot YATAY — 1h+15m veya güç yetersiz."
            )
        else:
            m, s = "UYUMSUZ", (
                "Grafikte yükseliş var ama bot SHORT görüyor — yapı/timing farkı."
            )
    else:
        if bot == LABEL_FLAT or bias == "RANGE":
            m, s = "UYUMLU", "Grafik yatay/karışık; bot da YATAY — trade beklenmez."
        else:
            m, s = "KISMI", (
                f"Grafik yatay görünüyor ama bot {bot} etiketi veriyor — "
                "kısa süreli yapı veya sizin işaret ettiğiniz mum farklı olabilir."
            )

    if decision.get("bot_action") == "ACILMAZDI" and m == "UYUMLU" and v in (VISUAL_DOWN, VISUAL_UP):
        s += " Uyumlu yapı olsa bile bot o an POZİSYON AÇMADI (aşağıdaki maddelere bakın)."

    return {"match": m, "summary": s}


def format_visual_compare_block(visual: dict, decision: dict) -> list[str]:
    cmp = compare_visual_vs_bot(visual, decision)
    lines = [
        "--- SİZİN GRAFİK vs BOT ---",
        f"  Sizin gördüğünüz (grafik): {visual.get('label', '?')}",
        f"    Kaynak: {visual.get('source', '?')} — {visual.get('detail', '')}",
        f"  Bot etiketi (kurallar):   {decision.get('structure_label', '?')}",
        f"  Uyum: {cmp['match']}",
        f"  → {cmp['summary']}",
        "",
    ]
    return lines


def build_decision_block(p_at: dict, ts: float) -> dict:
    tv = _normalize_trend(p_at.get("trend"))
    s15, s1h = p_at.get("structure_15m"), p_at.get("structure_1h")
    struct = structure_label(s15, s1h)
    no_trade = why_no_trade(p_at, tv)
    nearby_sig = load_nearby_signal(ts)
    nearby_trade = load_nearby_trade(ts)

    would_open = bool(tv.get("trade_ok")) and not p_at.get("in_position")
    action = "ACILABILIRDI" if would_open else "ACILMAZDI"

    return {
        "structure_label": struct["label"],
        "structure_confidence": struct["confidence"],
        "structure_rule": struct["rule"],
        "trade_gate": struct["trade_gate"],
        "bot_action": action,
        "bias": tv.get("bias"),
        "phase": tv.get("phase"),
        "strength": tv.get("strength"),
        "trade_ok": tv.get("trade_ok"),
        "structure_aligned": tv.get("structure_aligned"),
        "why_no_trade": no_trade,
        "nearby_signal": nearby_sig,
        "nearby_trade": nearby_trade,
        "require_htf_align": cfg.REQUIRE_HTF_ALIGN,
        "auto_trade": cfg.AUTO_TRADE_ENABLED,
    }
