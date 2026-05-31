"""
core/instance_lock.py — Tek main.py örneği (çift bot = WS kopması / aggTrade donması).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from core.config import BASE_DIR

LOCK_PATH = BASE_DIR / "data" / "bot.pid"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError, SystemError):
        # Windows: geçersiz/eski PID bazen WinError 87 → SystemError
        return False
    return True


def acquire() -> None:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    if LOCK_PATH.exists():
        try:
            old = int(LOCK_PATH.read_text(encoding="utf-8").strip())
        except ValueError:
            old = 0
        if old and _pid_alive(old) and old != os.getpid():
            print(
                f"\n[HATA] Bot zaten çalışıyor (PID {old}).\n"
                f"  Önce kapatın: taskkill /PID {old} /F\n"
                f"  veya Görev Yöneticisi → python.exe\n"
                f"  Sonra: python main.py\n",
                file=sys.stderr,
            )
            sys.exit(1)
    LOCK_PATH.write_text(str(os.getpid()), encoding="utf-8")


def release() -> None:
    try:
        if LOCK_PATH.exists():
            cur = int(LOCK_PATH.read_text(encoding="utf-8").strip() or "0")
            if cur == os.getpid():
                LOCK_PATH.unlink(missing_ok=True)
    except Exception:
        pass
