"""
bot_status.py — Anlık bot durum özeti

Kullanım:
    python bot_status.py          # tam özet
    python bot_status.py short    # tek satır özet
    python bot_status.py watch    # her 5s güncelle
"""

from __future__ import annotations

import json
import os
import sys
import time
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

def green(s):  return f"\033[92m{s}\033[0m"
def red(s):    return f"\033[91m{s}\033[0m"
def yellow(s): return f"\033[93m{s}\033[0m"
def cyan(s):   return f"\033[96m{s}\033[0m"
def bold(s):   return f"\033[1m{s}\033[0m"
def dim(s):    return f"\033[2m{s}\033[0m"

DB_PATH      = Path("data/bot.db")
LEVELS_PATH  = Path("data/v3_active_levels.json")
PID_PATH     = Path("data/bot.pid")


# ─── Bot çalışıyor mu? ────────────────────────────────────────────────────────

def bot_running() -> bool:
    if PID_PATH.exists():
        try:
            pid = int(PID_PATH.read_text().strip())
            if sys.platform == "win32":
                import subprocess
                r = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}"],
                    capture_output=True, text=True
                )
                return str(pid) in r.stdout
            else:
                os.kill(pid, 0)
                return True
        except Exception:
            pass
    # PID yoksa process adından ara
    try:
        import subprocess
        r = subprocess.run(
            ["tasklist"] if sys.platform == "win32" else ["pgrep", "-f", "main.py"],
            capture_output=True, text=True
        )
        return "python" in r.stdout.lower() and "main" in r.stdout.lower()
    except Exception:
        return False


# ─── Seviyeler ────────────────────────────────────────────────────────────────

def read_levels() -> dict:
    if not LEVELS_PATH.exists():
        return {}
    try:
        return json.loads(LEVELS_PATH.read_text())
    except Exception:
        return {}


# ─── Son trade ────────────────────────────────────────────────────────────────

def read_last_trades(n: int = 5) -> list[dict]:
    if not DB_PATH.exists():
        return []
    try:
        conn = sqlite3.connect(DB_PATH, timeout=3)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(trades)")
        cols = {r["name"] for r in cur.fetchall()}
        if "pnl_pct" not in cols:
            conn.close()
            return []
        exit_col = "exit_price" if "exit_price" in cols else None
        where = f"WHERE {exit_col} IS NOT NULL AND {exit_col} > 0" if exit_col else ""
        cur.execute(f"""
            SELECT side, COALESCE(entry_price,0) AS ep,
                   COALESCE(pnl_pct,0) AS pnl,
                   COALESCE(entry_mode,'?') AS mode,
                   ts
            FROM trades {where}
            ORDER BY ts DESC LIMIT {n}
        """)
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception:
        return []


def read_daily_pnl() -> float:
    if not DB_PATH.exists():
        return 0.0
    try:
        conn = sqlite3.connect(DB_PATH, timeout=3)
        cur = conn.cursor()
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).timestamp()
        cur.execute(
            "SELECT SUM(pnl_pct) FROM trades WHERE ts >= ? AND exit_price > 0",
            (today_start,)
        )
        result = cur.fetchone()
        conn.close()
        return float(result[0] or 0)
    except Exception:
        return 0.0


# ─── Log'dan son karar ────────────────────────────────────────────────────────

def read_last_decision() -> str:
    log_paths = [
        "data/logs/bot.log", "data/bot.log", "botlog/bot.log", "bot.log"
    ]
    import glob
    all_logs = []
    for p in log_paths:
        if Path(p).exists():
            all_logs.append(p)
    for pat in ["data/logs/*.log", "data/*.log"]:
        all_logs.extend(glob.glob(pat))

    if not all_logs:
        return "—"

    log_file = max(all_logs, key=os.path.getmtime)
    try:
        with open(log_file, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        # Son V3 karar satırını bul
        for line in reversed(lines):
            if "[V3]" in line and "karar=" in line:
                # karar=LONG/SHORT/WAIT
                parts = line.strip()
                # timestamp al
                ts_part = parts[:8] if len(parts) > 8 else ""
                # karar kısmını al
                if "karar=" in parts:
                    karar = parts.split("karar=")[1].split("|")[0].strip()
                    neden = ""
                    if "neden:" in parts:
                        neden = parts.split("neden:")[1].split("|")[0].strip()[:60]
                    return f"{karar} — {neden}"
        return "—"
    except Exception:
        return "—"


# ─── .env'den bazı ayarlar ────────────────────────────────────────────────────

def read_env_key(key: str, default: str = "?") -> str:
    env_path = Path(".env")
    if not env_path.exists():
        return default
    try:
        for line in env_path.read_text().splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return default


# ─── Tam özet ────────────────────────────────────────────────────────────────

def print_full_status():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    running = bot_running()
    levels = read_levels()
    trades = read_last_trades(5)
    daily_pnl = read_daily_pnl()
    last_decision = read_last_decision()

    # Seviyeleri oku
    active = (levels.get("active") or {})
    support_px = float((active.get("support") or {}).get("price", 0) or 0)
    resistance_px = float((active.get("resistance") or {}).get("price", 0) or 0)
    zone = str(active.get("zone") or "?")
    locked = bool(active.get("locked"))

    # .env ayarları
    paper = read_env_key("PAPER_MODE", "true").lower() == "true"
    strategy = read_env_key("ENTRY_MODE", "?")
    v3 = read_env_key("STRATEGY_V3_ENABLED", "false").lower() == "true"
    max_loss = read_env_key("MAX_DAILY_LOSS_PCT", "3.0")
    liq_ws = read_env_key("LIQ_WS_ENABLED", "false").lower() == "true"

    print(bold(cyan(f"\n{'═'*56}")))
    print(bold(cyan(f"  ETH/USDT Bot Durum Raporu — {now}")))
    print(bold(cyan(f"{'═'*56}")))

    # Bot durumu
    status_str = green("● ÇALIŞIYOR") if running else red("○ DURDU")
    mode_str = yellow("PAPER") if paper else green("CANLI")
    print(f"\n  Bot      : {status_str}  |  Mod: {mode_str}  |  Strateji: {strategy.upper()} {'V3' if v3 else ''}")

    # Seviyeler
    if support_px > 0 and resistance_px > 0:
        width = resistance_px - support_px
        width_pct = width / support_px * 100
        lock_tag = yellow(" [KİLİTLİ]") if locked else ""
        print(f"\n  Seviyeler: S={bold(f'{support_px:.2f}')}  R={bold(f'{resistance_px:.2f}')}  "
              f"Genişlik={width:.2f} ({width_pct:.1f}%){lock_tag}")
        print(f"  Zone     : {bold(zone)}")
    else:
        print(f"\n  Seviyeler: {yellow('Veri yok')}")

    # Son karar
    if "LONG" in last_decision:
        decision_colored = green(last_decision)
    elif "SHORT" in last_decision:
        decision_colored = cyan(last_decision)
    else:
        decision_colored = dim(last_decision)
    print(f"\n  Son Karar: {decision_colored}")

    # Günlük PnL
    pnl_str = f"{daily_pnl:+.4f} USDT"
    pnl_colored = green(pnl_str) if daily_pnl >= 0 else red(pnl_str)
    print(f"\n  Günlük PnL: {pnl_colored}  |  Max kayıp limiti: -{max_loss}%")

    # Son tradeler
    if trades:
        print(f"\n  Son {len(trades)} trade:")
        for t in trades:
            pnl = float(t.get("pnl", 0))
            side = t.get("side", "?")
            mode = t.get("mode", "?")
            ep = float(t.get("ep", 0))
            ts = float(t.get("ts", 0))
            dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%m/%d %H:%M") if ts > 0 else "?"
            pnl_s = f"{pnl:+.4f}%" 
            pnl_c = green(pnl_s) if pnl >= 0 else red(pnl_s)
            side_c = green(side) if side == "LONG" else cyan(side)
            print(f"    {dt}  {side_c:12}  @{ep:.2f}  PnL={pnl_c}  [{mode}]")
    else:
        print(f"\n  Son trade: {dim('Henüz yok')}")

    # Ekstra
    extras = []
    if liq_ws:
        extras.append(green("LIQ ✓"))
    else:
        extras.append(yellow("LIQ ✗"))
    if v3:
        extras.append(green("V3 ✓"))
    print(f"\n  Özellikler: {' | '.join(extras)}")

    print(bold(cyan(f"{'═'*56}\n")))


def print_short_status():
    """Tek satır özet — bana yapıştırmak için."""
    running = bot_running()
    levels = read_levels()
    active = (levels.get("active") or {})
    support_px = float((active.get("support") or {}).get("price", 0) or 0)
    resistance_px = float((active.get("resistance") or {}).get("price", 0) or 0)
    zone = str(active.get("zone") or "?")
    daily_pnl = read_daily_pnl()
    last_decision = read_last_decision()
    now = datetime.now(timezone.utc).strftime("%H:%M:%S")

    status = "ÇALIŞIYOR" if running else "DURDU"
    print(
        f"[{now}] Bot={status} | "
        f"S={support_px:.2f} R={resistance_px:.2f} zone={zone} | "
        f"Karar={last_decision[:40]} | "
        f"GünlükPnL={daily_pnl:+.4f}%"
    )


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]

    if args and args[0].lower() == "watch":
        interval = int(args[1]) if len(args) > 1 and args[1].isdigit() else 5
        print(bold(cyan(f"Canlı izleme (her {interval}s) — Ctrl+C ile dur\n")))
        try:
            while True:
                os.system("cls" if sys.platform == "win32" else "clear")
                print_full_status()
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\nDurduruldu.")
        return

    if args and args[0].lower() == "short":
        print_short_status()
        return

    print_full_status()


if __name__ == "__main__":
    main()
