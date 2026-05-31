"""
botlog/db_backup.py
───────────────────
bot.db için otomatik günlük yedekleme.

Kullanım (run_scheduled_analysis içine ekle):
    from botlog.db_backup import maybe_backup
    await maybe_backup()

Yedekler: data/backups/bot_YYYY-MM-DD.db
7 günden eski yedekler otomatik silinir.
"""

from __future__ import annotations

import os
import shutil
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from core.logger import get_logger

log = get_logger("DBBackup")

DB_PATH    = Path("data/bot.db")
BACKUP_DIR = Path("data/backups")
KEEP_DAYS  = 7

# Son backup zamanını tutan basit flag (process içi)
_last_backup_day: int = 0


async def maybe_backup() -> bool:
    """
    Bugün henüz backup alınmamışsa alır.
    True → backup alındı, False → atlandı veya hata.
    """
    global _last_backup_day

    today = _today_int()
    if _last_backup_day == today:
        return False  # zaten alındı

    success = _do_backup()
    if success:
        _last_backup_day = today
        _cleanup_old_backups()
    return success


def _do_backup() -> bool:
    try:
        if not DB_PATH.exists():
            log.warning("DBBackup: bot.db bulunamadı, yedek alınamadı")
            return False

        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        dest = BACKUP_DIR / f"bot_{date_str}.db"

        if dest.exists():
            log.debug(f"DBBackup: {dest.name} zaten var, atlanıyor")
            return True

        shutil.copy2(DB_PATH, dest)
        size_kb = dest.stat().st_size / 1024
        log.info(f"DBBackup: yedek alındı → {dest.name} ({size_kb:.1f} KB)")
        return True

    except Exception as e:
        log.error(f"DBBackup: yedekleme hatası: {e}")
        return False


def _cleanup_old_backups() -> None:
    """KEEP_DAYS günden eski yedekleri siler."""
    try:
        cutoff = time.time() - KEEP_DAYS * 86400
        for f in BACKUP_DIR.glob("bot_*.db"):
            if f.stat().st_mtime < cutoff:
                f.unlink()
                log.info(f"DBBackup: eski yedek silindi → {f.name}")
    except Exception as e:
        log.warning(f"DBBackup cleanup hatası: {e}")


def _today_int() -> int:
    now = datetime.now(timezone.utc)
    return now.year * 10000 + now.month * 100 + now.day
