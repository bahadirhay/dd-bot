"""
git_sync.py — Otomatik GitHub sync

Kullanım:
    python git_sync.py              # değişiklikleri push et
    python git_sync.py watch        # her 60s otomatik push
    python git_sync.py watch 30     # her 30s otomatik push
    python git_sync.py status       # sadece durum göster

Sadece kod dosyalarını push eder — .db, .log, api_key.csv gibi
hassas/büyük dosyaları kesinlikle göndermez.
"""

from __future__ import annotations

import os
import sys
import time
import subprocess
from pathlib import Path
from datetime import datetime, timezone

def green(s):  return f"\033[92m{s}\033[0m"
def red(s):    return f"\033[91m{s}\033[0m"
def yellow(s): return f"\033[93m{s}\033[0m"
def cyan(s):   return f"\033[96m{s}\033[0m"
def bold(s):   return f"\033[1m{s}\033[0m"
def dim(s):    return f"\033[2m{s}\033[0m"

# ─── Sadece bu uzantılar push edilir ─────────────────────────────────────────
ALLOWED_EXTENSIONS = {".py", ".md", ".txt", ".json", ".env.example", ".gitignore"}

# ─── Kesinlikle push edilmeyecek dosyalar ─────────────────────────────────────
BLOCKED_PATTERNS = [
    "api_key.csv", ".env", "bot.db", "trades.db",
    "*.log", "*.pid", "*.bak", "__pycache__",
    "dd-bot-improvements/", "dd-bot-patch/", "dd-bot-fix/",
]


def run(cmd: list[str], cwd: str = ".") -> tuple[int, str, str]:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def is_git_repo() -> bool:
    code, _, _ = run(["git", "rev-parse", "--is-inside-work-tree"])
    return code == 0


def get_changed_files() -> list[str]:
    """Sadece değişmiş .py ve izin verilen dosyaları döner."""
    _, out, _ = run(["git", "status", "--porcelain"])
    files = []
    for line in out.splitlines():
        if len(line) < 4:
            continue
        status = line[:2].strip()
        path = line[3:].strip().strip('"')
        if not path:
            continue
        # Engelli mi?
        blocked = False
        for pat in BLOCKED_PATTERNS:
            if pat.replace("*", "") in path or path.endswith(pat.replace("*", "")):
                blocked = True
                break
        if blocked:
            continue
        # Uzantı kontrolü
        ext = Path(path).suffix.lower()
        name = Path(path).name
        if ext not in ALLOWED_EXTENSIONS and name not in {".gitignore", ".env.example"}:
            continue
        files.append(path)
    return files


def get_status() -> dict:
    _, branch, _ = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    _, ahead_behind, _ = run(["git", "rev-list", "--left-right", "--count", "HEAD...@{u}"])
    ahead = behind = 0
    if ahead_behind:
        parts = ahead_behind.split()
        if len(parts) == 2:
            ahead, behind = int(parts[0]), int(parts[1])
    _, last_commit, _ = run(["git", "log", "--oneline", "-1"])
    changed = get_changed_files()
    return {
        "branch": branch,
        "ahead": ahead,
        "behind": behind,
        "last_commit": last_commit,
        "changed_files": changed,
    }


def print_status():
    if not is_git_repo():
        print(red("Bu klasör bir git reposu değil!"))
        return
    s = get_status()
    print(bold(cyan(f"\n{'─'*50}")))
    print(bold(f"Git Durum — branch: {s['branch']}"))
    print(f"  Son commit  : {dim(s['last_commit'])}")
    print(f"  Ahead/Behind: {s['ahead']} ahead, {s['behind']} behind")

    if s["changed_files"]:
        print(f"\n  Push edilecek {len(s['changed_files'])} dosya:")
        for f in s["changed_files"]:
            print(f"    {green('+')} {f}")
    else:
        print(f"\n  {green('Tüm dosyalar güncel.')}")
    print(bold(cyan(f"{'─'*50}\n")))


def do_sync(message: str = "") -> bool:
    """Değişen dosyaları push eder. True = başarılı."""
    if not is_git_repo():
        print(red("Git reposu bulunamadı!"))
        return False

    changed = get_changed_files()
    if not changed:
        print(dim(f"[{_now()}] Değişiklik yok, push atlandı."))
        return True

    # Dosyaları stage et
    for f in changed:
        code, _, err = run(["git", "add", f])
        if code != 0:
            print(yellow(f"  git add {f}: {err}"))

    # Commit mesajı
    if not message:
        files_short = ", ".join(Path(f).name for f in changed[:3])
        if len(changed) > 3:
            files_short += f" +{len(changed)-3}"
        message = f"auto: {files_short} [{_now()}]"

    # Commit
    code, out, err = run(["git", "commit", "-m", message])
    if code != 0:
        if "nothing to commit" in (out + err):
            print(dim(f"[{_now()}] Commit edilecek değişiklik yok."))
            return True
        print(red(f"Commit hatası: {err or out}"))
        return False

    # Push
    code, out, err = run(["git", "push", "origin", "HEAD"])
    if code != 0:
        print(red(f"Push hatası: {err or out}"))
        return False

    print(green(f"[{_now()}] ✓ Push edildi: {message}"))
    for f in changed:
        print(f"  {green('→')} {f}")
    return True


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def watch_mode(interval: int = 60):
    print(bold(cyan(f"Otomatik sync başladı (her {interval}s) — Ctrl+C ile dur\n")))
    try:
        while True:
            do_sync()
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nDurduruldu.")


def main():
    args = sys.argv[1:]

    if not is_git_repo():
        print(red("Hata: Bu klasör bir git reposu değil."))
        print(yellow("Bot klasöründe çalıştırın: cd C:\\Users\\BH\\Desktop\\bot"))
        sys.exit(1)

    if not args or args[0].lower() == "push":
        msg = " ".join(args[1:]) if len(args) > 1 else ""
        do_sync(msg)
        return

    if args[0].lower() == "status":
        print_status()
        return

    if args[0].lower() == "watch":
        interval = int(args[1]) if len(args) > 1 and args[1].isdigit() else 60
        watch_mode(interval)
        return

    # Mesaj olarak kullan
    do_sync(" ".join(args))


if __name__ == "__main__":
    main()
