"""
scripts/backup_db.py — DB yedekleme (WAL-guvenli, bot calisirken de calisir).

Iki mod:
  light (varsayilan): yalniz islem gecmisi tablolari (trades/signals/v3_attribution/
                      regime_log/sr_changes) -> KUCUK, yeri doldurulamaz veri.
  full              : tum DB (market_snapshots dahil, ~3GB) -> gzip.

Kullanim:
  python -m scripts.backup_db            # light yedek
  python -m scripts.backup_db full       # tam yedek (buyuk)
  python -m scripts.backup_db light "D:/yedek"   # hedef klasor

Cikti: <hedef>/bot_<mod>_YYYYMMDD_HHMM.db(.gz)
Eski yedekleri otomatik budama: her mod icin son KEEP adet tutulur.
"""
from __future__ import annotations

import gzip
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime

KEEP = 14  # her mod icin tutulacak yedek sayisi
LIGHT_TABLES = ["trades", "signals", "v3_attribution", "regime_log", "sr_changes",
                "market_events", "errors"]


def _src_db() -> str:
    try:
        from core.config import cfg
        return cfg.DB_PATH
    except Exception:
        return os.path.join(os.path.dirname(__file__), "..", "data", "bot.db")


def _prune(dest_dir: str, mode: str) -> None:
    files = sorted(
        f for f in os.listdir(dest_dir)
        if f.startswith(f"bot_{mode}_")
    )
    for f in files[:-KEEP]:
        try:
            os.remove(os.path.join(dest_dir, f))
        except OSError:
            pass


def backup(mode: str = "light", dest_dir: str = "") -> str:
    src = _src_db()
    if not os.path.exists(src):
        raise SystemExit(f"DB bulunamadi: {src}")
    dest_dir = dest_dir or os.path.join(os.path.dirname(src), "backups")
    os.makedirs(dest_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    t0 = time.time()

    # Kaynaktan tutarli okuma (WAL): online backup API
    src_con = sqlite3.connect(src, timeout=30.0)

    if mode == "full":
        out = os.path.join(dest_dir, f"bot_full_{stamp}.db")
        dst_con = sqlite3.connect(out)
        with dst_con:
            src_con.backup(dst_con)  # tutarli anlik kopya (kilitlemez)
        dst_con.close()
        src_con.close()
        gz = out + ".gz"
        with open(out, "rb") as fi, gzip.open(gz, "wb", compresslevel=6) as fo:
            shutil.copyfileobj(fi, fo)
        os.remove(out)
        _prune(dest_dir, "full")
        sz = os.path.getsize(gz) / 1e6
        print(f"FULL yedek: {gz}  ({sz:.0f} MB, {time.time()-t0:.1f}s)")
        return gz

    # light: yalniz islem gecmisi tablolari -> bos yeni DB'ye kopyala
    out = os.path.join(dest_dir, f"bot_light_{stamp}.db")
    if os.path.exists(out):
        os.remove(out)
    dst_con = sqlite3.connect(out)
    existing = {r[0] for r in src_con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    with dst_con:
        for t in LIGHT_TABLES:
            if t not in existing:
                continue
            ddl = src_con.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (t,)).fetchone()
            if not ddl or not ddl[0]:
                continue
            dst_con.execute(ddl[0])
            rows = src_con.execute(f"SELECT * FROM {t}").fetchall()
            if rows:
                ph = ",".join("?" * len(rows[0]))
                dst_con.executemany(f"INSERT INTO {t} VALUES ({ph})", rows)
    dst_con.close()
    src_con.close()
    gz = out + ".gz"
    with open(out, "rb") as fi, gzip.open(gz, "wb", compresslevel=6) as fo:
        shutil.copyfileobj(fi, fo)
    os.remove(out)
    _prune(dest_dir, "light")
    sz = os.path.getsize(gz) / 1e6
    print(f"LIGHT yedek: {gz}  ({sz:.1f} MB, {time.time()-t0:.1f}s)")
    return gz


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "light"
    dest = sys.argv[2] if len(sys.argv) > 2 else ""
    backup(mode, dest)
