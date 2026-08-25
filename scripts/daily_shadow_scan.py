"""scripts/daily_shadow_scan.py — AKTIF shadow/paper stratejilerin gunluk forward-dogrulama taramasi.

Amac: retired-shadows-jul2026 hafizasindaki "AKTIF KALAN (izlenecek)" listesini (dumpfade, funding,
tmom, ftsm/F) + D cok-coin forward-shadow'u (d_multicoin_shadow.db) her gun yeni gelen veriyle
kontrol eder. YENI HIPOTEZ URETMEZ — sadece zaten test edilip aktif birakilmis olanlarin net'i
zaman icinde nasil gidiyor onu olcer (7g / 30g / onceki-30g / tum-zaman), sign-flip veya
belirgin cokus varsa isaretler. Elenmis (config=false) stratejiler burada YOK, onlar retired-shadows'ta.

Kullanim: python scripts/daily_shadow_scan.py
Cikti: stdout'a markdown tablo (skill bunu rapor dosyasina yazar).
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOT_DB = os.path.join(ROOT, "data", "bot.db")
D_DB = os.path.join(ROOT, "data", "d_multicoin_shadow.db")
DAY = 86400.0

# (label, db_path, table, ts_col, status_col, status_vals, pnl_col, extra_where)
TARGETS = [
    ("dumpfade (D+1 likidite-fade)", BOT_DB, "dumpfade_paper", "open_ts", "filled", (1,), "pnl_bps", ""),
    ("funding (kontraryan)", BOT_DB, "funding_paper", "close_ts", "status", ("CLOSED",), "pnl_bps", ""),
    ("tmom (1h momentum)", BOT_DB, "tmom_paper", "close_ts", "status", ("CLOSED",), "pnl_bps", ""),
    ("ftsm / F (gunluk trend)", BOT_DB, "ftsm_paper", "close_ts", "status", ("CLOSED",), "pnl_bps", ""),
]


def refresh_d_multicoin() -> None:
    """D cok-coin forward-shadow DB'sini gunceller (Binance'ten kline cekip yeni islemleri ekler)."""
    try:
        subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "d_multicoin_shadow.py")],
                        cwd=ROOT, timeout=120, capture_output=True, text=True)
    except Exception as e:
        print(f"  [uyari] D cok-coin shadow yenilenemedi: {e}")


def window_stats(rows: list[tuple[float, float]], now: float, since_days: float, until_days: float = 0.0) -> dict:
    lo = now - since_days * DAY
    hi = now - until_days * DAY
    win = [pnl for ts, pnl in rows if lo <= ts < hi]
    n = len(win)
    net = sum(win)
    wr = (sum(1 for p in win if p > 0) / n * 100.0) if n else 0.0
    return {"n": n, "net": net, "avg": (net / n if n else 0.0), "wr": wr}


def scan_table(label: str, db_path: str, table: str, ts_col: str, status_col: str,
                status_vals: tuple, pnl_col: str, extra_where: str) -> None:
    if not os.path.exists(db_path):
        print(f"### {label}\n  DB yok: {db_path}\n")
        return
    try:
        c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        ph = ",".join("?" * len(status_vals))
        q = f"SELECT {ts_col}, {pnl_col} FROM {table} WHERE {status_col} IN ({ph}) {extra_where}"
        rows = [(float(r[0]), float(r[1] or 0.0)) for r in c.execute(q, status_vals).fetchall() if r[0] is not None]
    except Exception as e:
        print(f"### {label}\n  sorgu hatasi: {e}\n")
        return
    if not rows:
        print(f"### {label}\n  henuz islem yok\n")
        return
    now = time.time()
    w7 = window_stats(rows, now, 7, 0)
    w30 = window_stats(rows, now, 30, 0)
    prev30 = window_stats(rows, now, 60, 30)
    allt_net = sum(p for _, p in rows)
    allt_n = len(rows)
    print(f"### {label}  (tum-zaman: {allt_n} islem, net {allt_net:+.0f}bps)")
    print(f"  son 7g : n={w7['n']:3d}  net={w7['net']:+8.0f}bps  avg={w7['avg']:+6.1f}  win%={w7['wr']:5.1f}")
    print(f"  son 30g: n={w30['n']:3d}  net={w30['net']:+8.0f}bps  avg={w30['avg']:+6.1f}  win%={w30['wr']:5.1f}")
    print(f"  onceki30g: n={prev30['n']:3d}  net={prev30['net']:+8.0f}bps  avg={prev30['avg']:+6.1f}  win%={prev30['wr']:5.1f}")
    flags = []
    if prev30["n"] >= 5 and w30["n"] >= 5:
        if prev30["net"] > 0 and w30["net"] < 0:
            flags.append("SIGN-FLIP: onceki30g pozitifti, son30g negatif")
        elif prev30["net"] > 0 and w30["net"] < prev30["net"] * 0.5:
            flags.append(f"COKUS: net son30g onceki30g'nin <%50'sine dustu ({prev30['net']:+.0f}->{w30['net']:+.0f})")
    if w7["n"] >= 3 and w7["net"] < 0 and w30["net"] > 0:
        flags.append("son 7g negatife donmus (30g hala pozitif, izle)")
    if flags:
        for f in flags:
            print(f"  [!] {f}")
    print()


def scan_d_multicoin() -> None:
    label = "D cok-coin forward (SOL/LINK/ETH)"
    if not os.path.exists(D_DB):
        print(f"### {label}\n  DB yok: {D_DB}\n")
        return
    c = sqlite3.connect(f"file:{D_DB}?mode=ro", uri=True)
    for sym in ("SOLUSDT", "LINKUSDT", "ETHUSDT"):
        try:
            rows = [(float(r[0]), float(r[1] or 0.0)) for r in c.execute(
                "SELECT exit_ts, pnl_bps FROM d_paper WHERE symbol=? AND status='CLOSED' AND exit_ts IS NOT NULL",
                (sym,)).fetchall()]
        except Exception as e:
            print(f"### {label} / {sym}\n  sorgu hatasi: {e}\n")
            continue
        if not rows:
            print(f"### {label} / {sym}\n  henuz islem yok\n")
            continue
        now = time.time()
        w7 = window_stats(rows, now, 7, 0)
        w30 = window_stats(rows, now, 30, 0)
        allt_net = sum(p for _, p in rows)
        print(f"### {label} / {sym}  (tum-zaman: {len(rows)} islem, net {allt_net:+.0f}bps)")
        print(f"  son 7g : n={w7['n']:3d}  net={w7['net']:+8.0f}bps  win%={w7['wr']:5.1f}")
        print(f"  son 30g: n={w30['n']:3d}  net={w30['net']:+8.0f}bps  win%={w30['wr']:5.1f}")
        print()


def main() -> None:
    print(f"=== Gunluk shadow-forward taramasi | {time.strftime('%Y-%m-%d %H:%M')} ===\n")
    print("(Bu tarama YENI hipotez uretmez; sadece halihazirda AKTIF birakilmis stratejilerin")
    print(" gercek forward performansini izler. Elenmisler icin memory:retired-shadows'a bakin.)\n")
    refresh_d_multicoin()
    for label, db_path, table, ts_col, status_col, status_vals, pnl_col, extra_where in TARGETS:
        scan_table(label, db_path, table, ts_col, status_col, status_vals, pnl_col, extra_where)
    scan_d_multicoin()


if __name__ == "__main__":
    main()
