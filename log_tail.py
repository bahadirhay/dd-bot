"""
log_tail.py — Bot log görüntüleyici

Kullanım:
    python log_tail.py          # son 50 satır
    python log_tail.py 100      # son 100 satır
    python log_tail.py 50 V3    # sadece V3Decision satırları
    python log_tail.py 50 ERROR # sadece hata satırları
    python log_tail.py watch    # canlı takip (Ctrl+C ile dur)
"""

from __future__ import annotations

import os
import sys
import time
import glob
from pathlib import Path
from datetime import datetime

# ─── Renk kodları ─────────────────────────────────────────────────────────────
def red(s):    return f"\033[91m{s}\033[0m"
def green(s):  return f"\033[92m{s}\033[0m"
def yellow(s): return f"\033[93m{s}\033[0m"
def cyan(s):   return f"\033[96m{s}\033[0m"
def bold(s):   return f"\033[1m{s}\033[0m"
def dim(s):    return f"\033[2m{s}\033[0m"

# ─── Log dosyasını bul ────────────────────────────────────────────────────────

def find_log_file() -> Path | None:
    """Bot klasöründeki en güncel log dosyasını bulur."""
    candidates = [
        "data/logs/bot.log",
        "data/bot.log",
        "botlog/bot.log",
        "bot.log",
    ]
    # Doğrudan yollar
    for c in candidates:
        p = Path(c)
        if p.exists():
            return p

    # Glob ile ara
    patterns = [
        "data/logs/*.log",
        "data/*.log",
        "botlog/*.log",
        "*.log",
    ]
    found = []
    for pattern in patterns:
        found.extend(glob.glob(pattern))

    if not found:
        return None

    # En son değiştirileni seç
    return Path(max(found, key=os.path.getmtime))


# ─── Satır renklendirme ───────────────────────────────────────────────────────

def colorize(line: str) -> str:
    if "CRITICAL" in line or "KRİTİK" in line:
        return red(bold(line))
    if "ERROR" in line or "HATA" in line:
        return red(line)
    if "WARNING" in line or "WARNING" in line:
        return yellow(line)
    if "karar=LONG" in line:
        return green(bold(line))
    if "karar=SHORT" in line:
        return cyan(bold(line))
    if "POZİSYON AÇILDI" in line or "POZISYON ACILDI" in line:
        return green(bold(line))
    if "POZİSYON KAPATILDI" in line or "POZISYON KAPATILDI" in line:
        if "PnL=+" in line:
            return green(line)
        return yellow(line)
    if "Tez bitti" in line:
        return yellow(line)
    if "[V3]" in line and "karar=WAIT" in line:
        return dim(line)
    if "[LEVELS]" in line or "[STRUCT]" in line:
        return dim(line)
    return line


# ─── Filtre ───────────────────────────────────────────────────────────────────

FILTER_PRESETS = {
    "V3":       ["[V3]", "V3Decision"],
    "TRADE":    ["POZİSYON", "POZISYON", "karar=LONG", "karar=SHORT", "Tez bitti"],
    "ERROR":    ["ERROR", "WARNING", "CRITICAL", "HATA"],
    "LEVELS":   ["[LEVELS]", "[STRUCT]", "LevelsV3", "StructV3"],
    "ENTRY":    ["[ENTRY]", "EntryV3", "ZONE_SCENARIO", "LIQ_GRAB"],
    "CVD":      ["CVD", "TradeFeed"],
    "SCENARIO": ["[SCENARIO]", "ScenarioV3"],
}


def should_show(line: str, filter_key: str) -> bool:
    if not filter_key:
        return True
    key = filter_key.upper()
    if key in FILTER_PRESETS:
        return any(kw in line for kw in FILTER_PRESETS[key])
    # Serbest arama
    return filter_key.lower() in line.lower()


# ─── Ana fonksiyonlar ─────────────────────────────────────────────────────────

def tail_lines(log_file: Path, n: int, filter_key: str = "") -> list[str]:
    """Dosyadan son n satırı okur, filtre uygular."""
    try:
        with open(log_file, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception as e:
        return [red(f"Log okunamadı: {e}")]

    if filter_key:
        lines = [l for l in lines if should_show(l, filter_key)]

    return lines[-n:]


def print_lines(lines: list[str]) -> None:
    for line in lines:
        print(colorize(line.rstrip()))


def watch_mode(log_file: Path, filter_key: str = "") -> None:
    """Canlı takip — dosyaya yeni satır gelince gösterir."""
    print(bold(cyan(f"Canlı takip: {log_file} (Ctrl+C ile dur)\n")))
    try:
        with open(log_file, encoding="utf-8", errors="replace") as f:
            f.seek(0, 2)  # dosya sonuna git
            while True:
                line = f.readline()
                if line:
                    if should_show(line, filter_key):
                        print(colorize(line.rstrip()))
                else:
                    time.sleep(0.2)
    except KeyboardInterrupt:
        print("\nDurduruldu.")


def print_summary(log_file: Path) -> None:
    """Son 500 satırdan özet istatistik çıkarır."""
    lines = tail_lines(log_file, 500)
    trades_opened = sum(1 for l in lines if "POZİSYON AÇILDI" in l or "POZISYON ACILDI" in l)
    trades_closed = sum(1 for l in lines if "POZİSYON KAPATILDI" in l or "POZISYON KAPATILDI" in l)
    errors = sum(1 for l in lines if " ERROR " in l)
    warnings = sum(1 for l in lines if " WARNING " in l)
    longs = sum(1 for l in lines if "karar=LONG" in l)
    shorts = sum(1 for l in lines if "karar=SHORT" in l)

    pnl_lines = [l for l in lines if "PnL=" in l and "KAPATILDI" in l]
    total_pnl = 0.0
    for l in pnl_lines:
        try:
            part = l.split("PnL=")[1].split(" ")[0]
            total_pnl += float(part.replace("USDT", "").strip())
        except Exception:
            pass

    size = log_file.stat().st_size / 1024
    print(bold(cyan(f"\n{'─'*50}")))
    print(bold(f"Log özeti: {log_file.name} ({size:.0f} KB)"))
    print(f"  Açılan pozisyon : {green(str(trades_opened))}")
    print(f"  Kapanan pozisyon: {str(trades_closed)}")
    print(f"  LONG kararı     : {green(str(longs))}")
    print(f"  SHORT kararı    : {cyan(str(shorts))}")
    pnl_str = f"{total_pnl:+.4f} USDT"
    print(f"  Toplam PnL      : {green(pnl_str) if total_pnl >= 0 else red(pnl_str)}")
    print(f"  Hata            : {red(str(errors)) if errors else '0'}")
    print(f"  Uyarı           : {yellow(str(warnings)) if warnings else '0'}")
    print(bold(cyan(f"{'─'*50}\n")))


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]

    log_file = find_log_file()
    if not log_file:
        print(red("Log dosyası bulunamadı!"))
        print(yellow("Aranan konumlar: data/logs/, data/, botlog/, ./ (*.log)"))
        sys.exit(1)

    # watch modu
    if args and args[0].lower() == "watch":
        filter_key = args[1] if len(args) > 1 else ""
        watch_mode(log_file, filter_key)
        return

    # özet modu
    if args and args[0].lower() == "summary":
        print_summary(log_file)
        return

    # satır sayısı ve filtre
    n = 50
    filter_key = ""

    if args:
        if args[0].isdigit():
            n = int(args[0])
            filter_key = args[1] if len(args) > 1 else ""
        else:
            filter_key = args[0]

    print(bold(cyan(f"\nLog: {log_file} | Son {n} satır"
                    + (f" | Filtre: {filter_key}" if filter_key else "") + "\n")))

    lines = tail_lines(log_file, n, filter_key)
    if not lines:
        print(yellow("Eşleşen satır bulunamadı."))
    else:
        print_lines(lines)

    print(dim(f"\n{len(lines)} satır gösterildi. "
              f"Canlı takip için: python log_tail.py watch {filter_key or ''}"))


if __name__ == "__main__":
    main()
