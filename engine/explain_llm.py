"""
engine/explain_llm.py — Claude ile dogal Turkce rapor + grafik gorseli.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from core.config import cfg
from core.logger import get_logger
from engine.claude_credentials import get_key
from engine.explain_context import build_context, format_rule_report

log = get_logger("ExplainLLM")

UPLOAD_DIR = Path(__file__).resolve().parent.parent / "data" / "uploads"
ANTHROPIC_VERSION = "2023-06-01"


def _claude_post(api_key: str, body: dict) -> dict:
    url = "https://api.anthropic.com/v1/messages"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _text_from_response(out: dict) -> str:
    for block in out.get("content", []):
        if block.get("type") == "text":
            return (block.get("text") or "").strip()
    return ""


def _image_block(image_path: str) -> dict:
    with open(image_path, "rb") as f:
        data = base64.standard_b64encode(f.read()).decode("ascii")
    ext = Path(image_path).suffix.lower().lstrip(".") or "png"
    if ext == "jpg":
        ext = "jpeg"
    media = f"image/{ext}"
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media, "data": data},
    }


def save_upload(contents: str, filename: str) -> str:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    if "," in contents:
        contents = contents.split(",", 1)[1]
    raw = base64.b64decode(contents)
    safe = "".join(c for c in filename if c.isalnum() or c in "._-") or "chart.png"
    path = UPLOAD_DIR / safe
    path.write_bytes(raw)
    return str(path)


def vision_guess_time(image_path: str, api_key: str | None = None) -> str:
    key = get_key(api_key)
    if not key:
        return "Claude API anahtarı yok — dashboard'dan girin ve Kaydet'e basın."

    content = [
        {
            "type": "text",
            "text": (
                "Bu bir Binance Futures ETH/USDT mum grafigi ekran goruntusu. "
                "Alt eksendeki veya imlecin uzerindeki tarih-saati oku. "
                "Binance yerel saat gosteriyorsa UTC'ye cevir (Turkiye ise UTC+3). "
                "Sadece su formatta cevap ver, baska metin yazma: "
                "YYYY-MM-DD HH:MM UTC  veya BILINMIYOR"
            ),
        },
        _image_block(image_path),
    ]

    body = {
        "model": cfg.CLAUDE_MODEL,
        "max_tokens": 64,
        "messages": [{"role": "user", "content": content}],
    }
    try:
        out = _claude_post(key, body)
        text = _text_from_response(out)
        if not text or "BILINMIYOR" in text.upper():
            return "Grafikten saat okunamadi — tarihi elle girin veya grafige tiklayin."
        return text.replace("UTC", "").strip() + " UTC"
    except Exception as e:
        log.error(f"Claude vision hata: {e}")
        return f"Vision hata: {e}"


def vision_chart_bias(image_path: str, api_key: str | None = None) -> dict:
    """Binance görselinden kullanicinin gördüğü yön: DÜŞÜŞ / YÜKSELİŞ / YATAY."""
    key = get_key(api_key)
    if not key:
        return {"ok": False, "label": "", "source": "binance_gorsel", "detail": "API anahtari yok"}

    content = [
        {
            "type": "text",
            "text": (
                "Binance ETH/USDT mum grafigi. Kullanicinin isaret ettigi veya "
                "son belirgin hareket ne? Sadece tek kelime cevap ver: "
                "DUSUS veya YUKSELIS veya YATAY"
            ),
        },
        _image_block(image_path),
    ]
    body = {
        "model": cfg.CLAUDE_MODEL,
        "max_tokens": 20,
        "messages": [{"role": "user", "content": content}],
    }
    try:
        out = _claude_post(key, body)
        raw = _text_from_response(out)
        from engine.explain_decision import _normalize_visual
        label = _normalize_visual(raw)
        return {
            "ok": True,
            "label": label,
            "source": "Binance ekran görüntüsü (Claude)",
            "detail": f"Görsel okuma: {raw.strip()[:40]}",
            "raw": raw.strip(),
        }
    except Exception as e:
        return {"ok": False, "label": "", "source": "binance_gorsel", "detail": str(e)}


def explain_from_binance_screenshot(
    image_path: str,
    api_key: str | None = None,
    tz_mode: str = "tr",
) -> tuple[str, str | None]:
    """
    Binance ekran goruntusu → saat oku → DB + Claude aciklama.
    Returns (report_text, datetime_used or None).
    """
    when_guess = vision_guess_time(image_path, api_key=api_key)
    if (
        when_guess.startswith("Claude API")
        or when_guess.startswith("Vision hata")
        or "okunamadi" in when_guess.lower()
    ):
        return when_guess, None
    clean_utc = when_guess.replace(" UTC", "").strip()
    visual = vision_chart_bias(image_path, api_key=api_key)
    ctx = build_context(clean_utc, tz_mode="utc")
    if visual.get("ok"):
        ctx["visual"] = visual
    rule = format_rule_report(ctx)
    key = get_key(api_key)
    if not key:
        return rule + "\n\n[Claude kapali] Ust kisim kural raporu.", clean
    from engine.time_align import utc_to_binance_local, snap_15m_open
    from datetime import datetime, timezone

    ts = snap_15m_open(
        datetime.strptime(clean_utc, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp()
    )
    display_when = (
        utc_to_binance_local(ts).strftime("%Y-%m-%d %H:%M")
        if tz_mode == "tr"
        else clean_utc
    )

    claude = explain_narrative(
        clean_utc, image_path, api_key=api_key, ctx=ctx, prepend_rule=False, tz_mode="utc"
    )
    return rule + "\n\n" + claude, display_when


def explain_narrative(
    when: str | float,
    image_path: str | None = None,
    api_key: str | None = None,
    ctx: dict | None = None,
    prepend_rule: bool = True,
    tz_mode: str = "tr",
) -> str:
    ctx = ctx or build_context(when, tz_mode=tz_mode)
    if image_path and os.path.isfile(image_path) and not ctx.get("visual"):
        ctx["visual"] = vision_chart_bias(image_path, api_key=api_key)
    rule = format_rule_report(ctx)

    key = get_key(api_key)
    if not key:
        msg = "[Claude kapali] Dashboard'dan API anahtarı girin."
        return (rule + "\n\n" + msg) if prepend_rule else msg

    if not ctx.get("ok"):
        return rule if prepend_rule else ""

    ctx_json = json.dumps(
        {k: v for k, v in ctx.items() if k != "events"},
        ensure_ascii=False,
        default=str,
    )
    events_txt = json.dumps(ctx.get("events", []), ensure_ascii=False)

    dec = ctx.get("decision") or {}
    vis = ctx.get("visual") or {}
    cmp = {}
    if vis.get("ok"):
        from engine.explain_decision import compare_visual_vs_bot
        cmp = compare_visual_vs_bot(vis, dec)
    user_text = (
        f"Kullanici Binance grafigi yukledi ve soruyor:\n"
        f"'Bot bu hareketi neden gormedi / neden pozisyon acmadi?'\n"
        f"Zaman: {ctx['ts_human']}\n\n"
        f"ZORUNLU ETİKETLER (aynen kullan):\n"
        f"- Sizin grafik: {vis.get('label', '?')} ({vis.get('source', '')})\n"
        f"- Bot yapı: {dec.get('structure_label')}\n"
        f"- Uyum: {cmp.get('match', '?')} — {cmp.get('summary', '')}\n"
        f"- Bot karari: {dec.get('bot_action')}\n\n"
        f"Karar blogu:\n{json.dumps(dec, ensure_ascii=False, default=str)}\n\n"
        f"Tam veri:\n{ctx_json}\n\n"
        f"Olaylar:\n{events_txt}\n\n"
        f"Kural raporu:\n{rule}\n\n"
        "Gorev: Turkce 4 bolum yaz.\n"
        "1) SIZIN GRAFIK vs BOT: tablo gibi — Grafikte DUSUS/YUKSELIS/YATAY, Bot SHORT/LONG/YATAY, Uyum\n"
        "2) YAPI: 15m ve 1h (kurallara gore)\n"
        "3) NEDEN POZISYON YOK: why_no_trade maddeleri\n"
        "4) OZET: tek cumle — bot hakli miydi acmamaya\n"
        "Uydurma yok; DB yoksa acikca soyle."
    )

    content: list[dict] = [{"type": "text", "text": user_text}]
    if image_path and os.path.isfile(image_path):
        content = [
            {"type": "text", "text": "Kullanicinin yukledigi Binance/grafik ekran goruntusu:"},
            _image_block(image_path),
            {"type": "text", "text": user_text},
        ]

    body = {
        "model": cfg.CLAUDE_MODEL,
        "max_tokens": 1200,
        "system": (
            "Sen ETH/USDT futures bot danismanisin. "
            "Mutlaka SHORT, LONG (BUY) veya YATAY etiketi kullan. "
            "Ana soru: bot neden pozisyon acmadi? Veriye sadik kal."
        ),
        "messages": [{"role": "user", "content": content}],
    }

    try:
        out = _claude_post(key, body)
        narrative = _text_from_response(out)
        if not narrative:
            msg = "[Claude] Bos yanit."
            return (rule + "\n\n" + msg) if prepend_rule else msg
        claude_block = (
            f"=== Claude Analizi: {ctx['ts_human']} ===\n\n"
            f"{narrative}\n\n"
            f"---\n(Kaynak: bot DB + {'grafik gorseli' if image_path else 'veri'})"
        )
        return (rule + "\n\n" + claude_block) if prepend_rule else claude_block
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")[:400]
        msg = f"[Claude hata] {e.code}: {err}"
        return (rule + "\n\n" + msg) if prepend_rule else msg
    except Exception as e:
        msg = f"[Claude hata] {e}"
        return (rule + "\n\n" + msg) if prepend_rule else msg
