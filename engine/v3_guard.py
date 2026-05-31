"""
engine/v3_guard.py
──────────────────
V3 strateji güncellemesi başarısız olursa state'i 'stale' işaretler
ve execute_entry'nin stale durumda işlem açmasını engeller.

Mevcut main.py'deki bu pattern'ı REPLACE eder:

    ÖNCE (tehlikeli):
        try:
            update_levels(); update_structure(); update_decision()
        except Exception as e:
            log.warning(f"V3 1h guncelleme: {e}")   # eski kararla devam!

    SONRA (güvenli):
        await v3_update_safe("1h")
        # hata → state.v3_stale = True → execute_entry'de WAIT'e zorla
"""

from __future__ import annotations

import time
from core.logger import get_logger

log = get_logger("V3Guard")

# Kaç saniye boyunca stale kabul edilir (bu süreden sonra işlem engellenir)
V3_STALE_TIMEOUT_SEC = 90


async def v3_update_safe(trigger: str = "1h") -> bool:
    """
    V3 engine'i güvenli şekilde günceller.

    Returns:
        True  → güncelleme başarılı
        False → hata oluştu, state.v3_stale = True set edildi
    """
    try:
        from core.config import cfg
        if not getattr(cfg, "STRATEGY_V3_ENABLED", False):
            return True  # V3 kapalı, sorun yok

        from engine.levels_v3 import update_levels
        from engine.structure_v3 import update_structure
        from engine.cvd_v3 import update_cvd_snapshot
        from engine.decision_v3 import update_decision

        update_levels()
        update_structure()
        update_cvd_snapshot()
        update_decision()

        # Başarılı → stale bayrağı temizle
        _set_stale(False, trigger)
        return True

    except Exception as e:
        log.error(
            f"V3Guard: {trigger} güncellemesi BAŞARISIZ → "
            f"state.v3_stale = True | hata: {e}"
        )
        _set_stale(True, trigger)
        return False


def is_v3_stale() -> bool:
    """
    execute_entry çağrılmadan önce kontrol edilmeli.
    True dönerse işlem açılmamalı.
    """
    try:
        from core.state import state
        if not getattr(state, "v3_stale", False):
            return False

        # Timeout dolmuşsa hâlâ stale
        last_ok = getattr(state, "v3_last_ok_ts", 0.0) or 0.0
        elapsed = time.time() - last_ok

        if elapsed > V3_STALE_TIMEOUT_SEC:
            log.warning(
                f"V3Guard: V3 son {elapsed:.0f}s'dir güncellenemiyor "
                f"→ işlem engellendi"
            )
            return True
        return False

    except Exception:
        return False  # state erişilemiyorsa engelleme


def _set_stale(stale: bool, trigger: str) -> None:
    try:
        from core.state import state
        state.v3_stale = stale
        if not stale:
            state.v3_last_ok_ts = time.time()
    except Exception as e:
        log.warning(f"V3Guard state yazma hatası: {e}")
