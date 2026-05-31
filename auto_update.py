"""
auto_update.py
──────────────
dd-bot otomatik güncelleme scripti.

Kullanım:
    python auto_update.py          # güncelleme kontrol et ve uygula
    python auto_update.py --check  # sadece kontrol et, uygulama
    python auto_update.py --force  # versiyon kontrolü olmadan zorla uygula

Bot çalışırken de güvenle çalışır — sadece yeni dosyaları ekler,
mevcut dosyaları DEĞİŞTİRMEZ.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

# ─── Ayarlar ─────────────────────────────────────────────────────────────────

REPO_RAW = "https://raw.githubusercontent.com/bahadirhay/dd-bot/master"
VERSION_URL = f"{REPO_RAW}/.update_version"
VERSION_FILE = Path(".update_version")
BACKUP_DIR = Path("data/update_backups")

# İndirilecek yeni dosyalar: (kaynak URL path'i, hedef yerel path)
UPDATE_FILES = [
    ("botlog/performance_context.py", "botlog/performance_context.py"),
    ("botlog/db_backup.py",           "botlog/db_backup.py"),
    ("core/daily_loss_guard.py",      "core/daily_loss_guard.py"),
    ("core/error_handler.py",         "core/error_handler.py"),
    ("engine/v3_guard.py",            "engine/v3_guard.py"),
    ("engine/adaptive_risk.py",       "engine/adaptive_risk.py"),
]

# Bu dosyalar GitHub'da YOK — sadece lokalde oluşturulur
LOCAL_ONLY_FILES = {
    "botlog/performance_context.py",
    "botlog/db_backup.py",
    "core/daily_loss_guard.py",
    "core/error_handler.py",
    "engine/v3_guard.py",
    "engine/adaptive_risk.py",
}

# ─── Renkli çıktı ────────────────────────────────────────────────────────────

def green(s):  return f"\033[92m{s}\033[0m"
def yellow(s): return f"\033[93m{s}\033[0m"
def red(s):    return f"\033[91m{s}\033[0m"
def bold(s):   return f"\033[1m{s}\033[0m"
def cyan(s):   return f"\033[96m{s}\033[0m"


# ─── Yardımcı fonksiyonlar ───────────────────────────────────────────────────

def _fetch(url: str, timeout: int = 15) -> bytes | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    except Exception as e:
        print(red(f"  İndirme hatası: {url}\n  {e}"))
        return None


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()[:16]


def _read_local_version() -> str:
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text().strip()
    return "0"


def _write_local_version(version: str) -> None:
    VERSION_FILE.write_text(version)


def _backup_file(path: Path) -> None:
    """Mevcut dosyayı timestamp'li yedekle."""
    if not path.exists():
        return
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / f"{path.name}.{ts}.bak"
    shutil.copy2(path, dest)


def _bot_running() -> bool:
    """Bot prosesinin çalışıp çalışmadığını kontrol et."""
    try:
        import subprocess
        result = subprocess.run(
            ["tasklist" if sys.platform == "win32" else "pgrep", "-f", "main.py"],
            capture_output=True, text=True
        )
        return "main.py" in result.stdout
    except Exception:
        return False


# ─── Ana mantık ──────────────────────────────────────────────────────────────

class Updater:

    def __init__(self, force: bool = False, check_only: bool = False):
        self.force = force
        self.check_only = check_only
        self.errors: list[str] = []
        self.updated: list[str] = []
        self.skipped: list[str] = []

    def run(self) -> int:
        print(bold(cyan("\n╔══════════════════════════════════════╗")))
        print(bold(cyan("║   dd-bot Otomatik Güncelleme v1.0    ║")))
        print(bold(cyan("╚══════════════════════════════════════╝\n")))

        # Bot çalışıyor mu?
        if _bot_running():
            print(yellow("⚠  Bot şu an çalışıyor — yeni dosyalar eklenir, çalışma etkilenmez.\n"))

        # Versiyon kontrolü
        local_ver = _read_local_version()
        print(f"  Yerel versiyon : {bold(local_ver)}")

        remote_ver = self._fetch_remote_version()
        print(f"  Uzak versiyon  : {bold(remote_ver or '?')}\n")

        if not self.force and remote_ver and local_ver >= remote_ver:
            print(green("✓ Zaten güncel. Güncelleme gerekmiyor."))
            return 0

        if self.check_only:
            if remote_ver and local_ver < remote_ver:
                print(yellow(f"→ Güncelleme mevcut: {local_ver} → {remote_ver}"))
                print("  Uygulamak için: python auto_update.py")
            return 0

        # Dosyaları güncelle
        print(bold("Dosyalar güncelleniyor...\n"))
        self._update_files()

        # Versiyon yaz
        if remote_ver and not self.errors:
            _write_local_version(remote_ver)

        # Özet
        self._print_summary()
        return 1 if self.errors else 0

    def _fetch_remote_version(self) -> str | None:
        data = _fetch(VERSION_URL)
        if data:
            return data.decode().strip()
        # VERSION dosyası yoksa tarih bazlı versiyon kullan
        return datetime.now(timezone.utc).strftime("%Y%m%d")

    def _update_files(self) -> None:
        # LOCAL_ONLY_FILES için GitHub'dan indirme yok,
        # doğrudan paketten kopyala
        script_dir = Path(__file__).parent
        improvements_dir = script_dir / "dd-bot-improvements"

        for src_path, dest_path in UPDATE_FILES:
            dest = Path(dest_path)
            print(f"  {'→':2} {dest_path}", end=" ")

            # Klasörü oluştur
            dest.parent.mkdir(parents=True, exist_ok=True)

            # Önce iyileştirme paketinden bak
            local_src = improvements_dir / src_path
            if local_src.exists():
                content = local_src.read_bytes()
                self._write_file(dest, content, dest_path)
                continue

            # GitHub'dan indir
            url = f"{REPO_RAW}/{src_path}"
            content = _fetch(url)
            if content is None:
                print(yellow("(GitHub'da yok — atlandı)"))
                self.skipped.append(dest_path)
                continue

            self._write_file(dest, content, dest_path)

    def _write_file(self, dest: Path, content: bytes, label: str) -> None:
        # Mevcut dosyayla karşılaştır
        if dest.exists():
            existing = dest.read_bytes()
            if existing == content:
                print(green("✓ (değişmemiş)"))
                self.skipped.append(label)
                return
            # Değişti — yedekle
            _backup_file(dest)

        try:
            dest.write_bytes(content)
            size_kb = len(content) / 1024
            print(green(f"✓ güncellendi ({size_kb:.1f} KB)"))
            self.updated.append(label)
        except Exception as e:
            print(red(f"✗ HATA: {e}"))
            self.errors.append(f"{label}: {e}")

    def _print_summary(self) -> None:
        print()
        print(bold("─" * 42))
        print(bold("Özet"))
        print(f"  Güncellenen : {green(str(len(self.updated)))} dosya")
        print(f"  Atlanan     : {str(len(self.skipped))} dosya")
        print(f"  Hata        : {red(str(len(self.errors))) if self.errors else '0'}")

        if self.updated:
            print(bold("\nGüncellenen dosyalar:"))
            for f in self.updated:
                print(f"  {green('+')} {f}")

        if self.errors:
            print(bold(red("\nHatalar:")))
            for e in self.errors:
                print(f"  {red('✗')} {e}")

        if not self.errors and self.updated:
            print(green(bold("\n✓ Güncelleme tamamlandı!")))
            if _bot_running():
                print(yellow("  Bot çalışıyor — yeni özellikler bir sonraki restart'ta aktif olur."))
            else:
                print("  Botu yeniden başlatın: python main.py")

        print()


# ─── Zamanlanmış kontrol (arka planda) ───────────────────────────────────────

def schedule_auto_check():
    """
    Bot startup'ında arka planda güncelleme kontrolü yapar.
    main.py'e eklenecek:
        from auto_update import schedule_auto_check
        schedule_auto_check()
    """
    import threading

    def _check():
        try:
            import time
            time.sleep(30)  # bot başladıktan 30sn sonra kontrol et
            updater = Updater(check_only=False)
            result = updater.run()
            if result == 0:
                pass  # zaten güncel
        except Exception:
            pass  # güncelleme hatası botu durdurmamalı

    t = threading.Thread(target=_check, daemon=True, name="auto-update")
    t.start()


# ─── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="dd-bot otomatik güncelleme")
    parser.add_argument("--check", action="store_true", help="Sadece kontrol et")
    parser.add_argument("--force", action="store_true", help="Zorla güncelle")
    args = parser.parse_args()

    updater = Updater(force=args.force, check_only=args.check)
    sys.exit(updater.run())
