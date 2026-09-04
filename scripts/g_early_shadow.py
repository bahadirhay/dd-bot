"""scripts/g_early_shadow.py — G ERKEN-GIRIS shadow (premium-index / predicted-funding).

Kullanici bulgusu: G settlement'i bekleyince GEC kaliyor -- reversion'un +83bps'i settlement ONCESI oluyor.
Cozum: settled-funding'i bekleme; GERCEK-ZAMANLI premium-index (predicted funding) uca ULASTIGI an gir.
Backtest (6 coin): EARLY +20.7/isl iki-yari-tutarli vs SETTLED +17.3 tutarsiz, 3x firsat (yanlis-sinyal dahil).

Bu shadow: premium-index'i periyodik izler, rolling-percentil ucunu CAPRAZladiginda + 12h-trend uyumluysa
paper-giris (24h tutus) kaydeder -> data/g_early_shadow.db. GERCEK EMIR YOK. Canli-settled-G ile forward kiyas.
Kanit: birkac hafta EARLY-shadow settled'i gecerse -> canli dusun.

Calistir (surekli):  python scripts/g_early_shadow.py
Tek-tur:             python scripts/g_early_shadow.py --once
"""
import os
import sys
import json
import time
import sqlite3
import bisect
import urllib.request
import datetime as dt

REST = "https://fapi.binance.com"
_DATADIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
DB = os.path.join(_DATADIR, "g_early_shadow.db")

COINS = ["ETHUSDT", "AVAXUSDT", "XRPUSDT", "ETCUSDT", "LINKUSDT", "BNBUSDT"]
Wp = 480            # rolling premium-percentil penceresi (saat) ~20 gun
PCT = 0.15          # %85/%15 uc
TREND_H = 12        # 12h-trend filtresi (canli G ile ayni)
HOLD = 24           # 24h tutus
FEE = 12.0          # bps (backtest ile ayni)
INTERVAL = 900      # sn (15 dk'da bir kontrol; premium saatlik degisir)


def _get(url):
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": "gearly/1.0"}), timeout=15).read())


def prem_kl(s, limit=520):
    r = _get(f"{REST}/fapi/v1/premiumIndexKlines?symbol={s}&interval=1h&limit={limit}")
    return [(int(x[0]) // 1000, float(x[4])) for x in r]  # (ts, premium close)


def price_kl(s, limit=200):
    r = _get(f"{REST}/fapi/v1/klines?symbol={s}&interval=1h&limit={limit}")
    return {int(x[0]) // 1000: float(x[4]) for x in r}


def init_db():
    os.makedirs(_DATADIR, exist_ok=True)
    c = sqlite3.connect(DB, timeout=30)
    c.execute("""CREATE TABLE IF NOT EXISTS g_early (
        symbol TEXT, open_ts INTEGER, open_human TEXT, side INTEGER,
        entry REAL, premium REAL, close_ts INTEGER, exit REAL, pnl_bps REAL, status TEXT,
        PRIMARY KEY (symbol, open_ts))""")
    c.commit()
    return c


def process(c, verbose=False):
    now = time.time()
    opened = 0
    for s in COINS:
        try:
            pm = prem_kl(s)
            if len(pm) < Wp + 3:
                continue
            # son KAPALI bar = pm[-2] (pm[-1] olusmakta)
            ts_bar, cur = pm[-2]
            prev = pm[-3][1]
            win = sorted(x[1] for x in pm[-2 - Wp:-2])
            hi = win[int(Wp * 0.85)]
            lo = win[int(Wp * 0.15)]
            cross_hi = cur >= hi and prev < hi
            cross_lo = cur <= lo and prev > lo
            side = -1 if cross_hi else (1 if cross_lo else 0)
            if not side:
                continue
            # 12h-trend (canli G ile ayni)
            px = price_kl(s)
            xks = sorted(px)
            def pat(t):
                p = bisect.bisect_right(xks, t) - 1
                return px[xks[p]] if p >= 0 else None
            p0 = pat(ts_bar)
            ptr = pat(ts_bar - TREND_H * 3600)
            if not p0 or not ptr or p0 <= 0:
                continue
            trend = 1 if p0 > ptr else -1
            if side != trend:
                continue
            # cooldown: bu coinde son 24h'te giris varsa atla
            last = c.execute("SELECT MAX(open_ts) FROM g_early WHERE symbol=?", (s,)).fetchone()[0]
            if last and now - last < HOLD * 3600:
                continue
            # ayni bara ikinci giris yok
            if c.execute("SELECT 1 FROM g_early WHERE symbol=? AND open_ts=?", (s, ts_bar)).fetchone():
                continue
            c.execute("INSERT OR IGNORE INTO g_early VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (s, ts_bar, dt.datetime.fromtimestamp(ts_bar).strftime("%Y-%m-%d %H:%M"),
                       side, round(p0, 6), round(cur, 6), None, None, None, "OPEN"))
            opened += 1
            if verbose:
                print("  ACIK %-8s %s premium=%+.4f%% trend=%s @ %.5g"
                      % (s, "SHORT" if side == -1 else "LONG", cur * 100,
                         "UP" if trend == 1 else "DOWN", p0))
        except Exception as e:
            if verbose:
                print("  %-8s HATA: %s" % (s, e))
            continue
    # KAPANIS: 24h dolan OPEN'lari kapat
    closed = 0
    for row in c.execute("SELECT symbol,open_ts,side,entry FROM g_early WHERE status='OPEN'").fetchall():
        s, ots, side, entry = row
        if now - ots < HOLD * 3600:
            continue
        try:
            px = price_kl(s)
            xks = sorted(px)
            p = bisect.bisect_right(xks, ots + HOLD * 3600) - 1
            exitpx = px[xks[p]] if p >= 0 else None
            if not exitpx:
                continue
            pnl = side * (exitpx - entry) / entry * 1e4 - FEE
            c.execute("UPDATE g_early SET close_ts=?, exit=?, pnl_bps=?, status='CLOSED' "
                      "WHERE symbol=? AND open_ts=?",
                      (int(ots + HOLD * 3600), round(exitpx, 6), round(pnl, 1), s, ots))
            closed += 1
        except Exception:
            continue
    c.commit()
    return opened, closed


def summary(c):
    rows = c.execute("SELECT pnl_bps FROM g_early WHERE status='CLOSED' AND pnl_bps IS NOT NULL").fetchall()
    n = len(rows)
    net = sum(r[0] for r in rows)
    op = c.execute("SELECT COUNT(*) FROM g_early WHERE status='OPEN'").fetchone()[0]
    w = 100 * sum(1 for r in rows if r[0] > 0) // n if n else 0
    print("  EARLY-shadow: %d kapali (net %+.0f bps, win %d%%), %d acik" % (n, net, w, op))


def main():
    c = init_db()
    if "--once" in sys.argv:
        print("=== g_early_shadow TEK-TUR ===")
        o, cl = process(c, verbose=True)
        print("  -> %d yeni giris, %d kapanis" % (o, cl))
        summary(c)
        return
    print("g_early_shadow BASLADI: %d coin, her %ds (premium-cross erken-giris)" % (len(COINS), INTERVAL))
    while True:
        t0 = time.time()
        try:
            process(c)
        except Exception as e:
            print("dongu hatasi:", e)
        time.sleep(max(1.0, INTERVAL - (time.time() - t0)))


if __name__ == "__main__":
    main()
