"""bot.db retention: diagnostik log tablolarini (market_snapshots, market_events, v3_attribution)
N gunden eski satirlardan budar. Snapshot/event STRATEJI verisi DEGIL (backtest klines'i
Binance'ten cekiyoruz); sinirsiz buyuyup 13GB olmustu. WAL modu -> canli bot calisirken guvenli
(partiler halinde, her parti commit + checkpoint). VACUUM YAPMAZ (exclusive kilit ister; disk zaten
bol, dosya buyumez cunku bosalan sayfa yeniden kullanilir). start_all.ps1 her aciliste calistirir.
"""
import sqlite3, time, sys, os

KEEP_DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 7
TABLES = ("market_snapshots", "market_events", "v3_attribution")
DB = os.path.join(os.path.dirname(__file__), "..", "data", "bot.db")

def prune():
    cut = time.time() - KEEP_DAYS * 86400
    c = sqlite3.connect(DB, timeout=30)
    c.execute("PRAGMA busy_timeout=30000")
    total = 0
    for tbl in TABLES:
        # ts kolonu var mi?
        cols = [r[1] for r in c.execute("PRAGMA table_info(%s)" % tbl).fetchall()]
        if "ts" not in cols:
            continue
        n_tbl = 0
        while True:
            cur = c.execute(
                "DELETE FROM %s WHERE rowid IN (SELECT rowid FROM %s WHERE ts < ? LIMIT 100000)" % (tbl, tbl),
                (cut,))
            c.commit()
            n = cur.rowcount
            n_tbl += n
            if n == 0:
                break
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        if n_tbl:
            print("  %-20s %d eski satir budandi" % (tbl, n_tbl))
        total += n_tbl
    c.close()
    print("prune_db: %d satir budandi (son %d gun tutuldu)" % (total, KEEP_DAYS))

if __name__ == "__main__":
    print("=== bot.db retention (son %d gun) ===" % KEEP_DAYS)
    try:
        prune()
    except Exception as e:
        print("prune_db hata (bot mesgul olabilir, sonra tekrar):", e)
