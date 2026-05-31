"""
engine/explain.py — Kayitli veriden "neden dustu/yukseldi?" aciklamasi.
"""
from __future__ import annotations

from engine.explain_context import build_context, format_rule_report


def explain_at(when: str | float, window_min: int = 15, tz_mode: str = "tr") -> str:
    """Verilen ana icin kural tabanli Turkce aciklama (tz: tr | utc)."""
    return format_rule_report(build_context(when, window_min, tz_mode))
