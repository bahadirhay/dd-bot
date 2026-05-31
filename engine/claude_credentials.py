"""
engine/claude_credentials.py — Claude API anahtarı (dashboard + .env + dosya).
"""
from __future__ import annotations

import os
from pathlib import Path

from core.config import BASE_DIR, cfg

KEY_FILE = BASE_DIR / "data" / "claude_key.txt"
_runtime_key: str = ""


def get_key(override: str | None = None) -> str:
    """Öncelik: dashboard girişi → bellek → dosya → .env"""
    if override and override.strip():
        return override.strip()
    if _runtime_key:
        return _runtime_key
    if KEY_FILE.exists():
        k = KEY_FILE.read_text(encoding="utf-8").strip()
        if k:
            return k
    return cfg.ANTHROPIC_API_KEY or ""


def set_key(key: str) -> None:
    global _runtime_key
    key = (key or "").strip()
    _runtime_key = key
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    if key:
        KEY_FILE.write_text(key, encoding="utf-8")
    elif KEY_FILE.exists():
        KEY_FILE.unlink()


def is_configured(override: str | None = None) -> bool:
    return bool(get_key(override))


def mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "••••"
    return f"••••{key[-4:]}"


def status_text() -> str:
    k = get_key()
    if k:
        return f"Claude API: kayıtlı ({mask_key(k)}) — LLM ve grafik okuma kullanılabilir."
    return "Claude API anahtarı yok — alanı doldurup Kaydet'e basın veya .env ANTHROPIC_API_KEY ekleyin."
