"""
engine/decision_v3_patch.py
───────────────────────────
decision_v3.py'e retest senaryosu ekler.
Bu dosya doğrudan import edilir, decision_v3.py'i override ETMEZ.

Kullanım (decision_v3.py içinde update_decision() sonuna ekle):
    from engine.decision_v3_patch import apply_retest_override
    apply_retest_override()
"""

from __future__ import annotations

from core.logger import get_logger

log = get_logger("DecisionPatch")


def apply_retest_override() -> None:
    """
    Retest aktifse mevcut WAIT kararını LONG/SHORT'a çevirir.
    Retest en güçlü setup — kırılım onaylı, eski seviye yeni destek/direnç.
    """
    try:
        from core.state import state
        from engine.levels_v3 import get_levels

        levels = get_levels()

        if not levels.get("retest_active"):
            return

        retest_side = levels.get("retest_side", "")
        if not retest_side:
            return

        current_decision = getattr(state, "v3_decision", "WAIT")

        # Sadece WAIT ise override et — zaten LONG/SHORT ise dokunma
        if current_decision != "WAIT":
            return

        if retest_side == "LONG_RETEST":
            state.v3_decision = "LONG"
            state.v3_scenario = "RETEST_LONG"
            broken_r = levels.get("last_broken_resistance", 0)
            log.info(
                f"DecisionPatch: RETEST_LONG → karar WAIT'ten LONG'a çevrildi "
                f"(eski direnç={broken_r:.2f} şimdi destek)"
            )

        elif retest_side == "SHORT_RETEST":
            state.v3_decision = "SHORT"
            state.v3_scenario = "RETEST_SHORT"
            broken_s = levels.get("last_broken_support", 0)
            log.info(
                f"DecisionPatch: RETEST_SHORT → karar WAIT'ten SHORT'a çevrildi "
                f"(eski destek={broken_s:.2f} şimdi direnç)"
            )

    except Exception as e:
        log.warning(f"DecisionPatch retest override hatası: {e}")


def get_retest_context() -> dict:
    """
    execute_entry'e iletilecek retest detayları.
    Daha sıkı SL için kırılan seviye kullanılır.
    """
    try:
        from engine.levels_v3 import get_levels
        levels = get_levels()

        if not levels.get("retest_active"):
            return {}

        side = levels.get("retest_side", "")
        if side == "LONG_RETEST":
            broken_level = levels.get("last_broken_resistance", 0)
            return {
                "retest": True,
                "retest_side": "LONG",
                "invalidation": broken_level * 0.997,  # kırılan seviyenin %0.3 altı SL
                "scenario_label": "RETEST_LONG",
                "priority": "HIGH",
            }
        elif side == "SHORT_RETEST":
            broken_level = levels.get("last_broken_support", 0)
            return {
                "retest": True,
                "retest_side": "SHORT",
                "invalidation": broken_level * 1.003,  # kırılan seviyenin %0.3 üstü SL
                "scenario_label": "RETEST_SHORT",
                "priority": "HIGH",
            }
    except Exception as e:
        log.warning(f"DecisionPatch get_retest_context hatası: {e}")

    return {}
