"""scripts/g_reverse_shadow.py — G-TERS (reversed) FORWARD paper-shadow.

Kullanici iddiasi: G "short" dediginde LONG acsak (ters), gercek canli islemler +%28 olurdu (78 islem).
Backtest full-history TERSI diyor (-18675) ama kullanici backtest'e guvenmiyor (canli G'yi yanlisladi).
COZUM: geçmis degil GELECEK karar versin. Bu shadow, G'nin GERCEK sinyallerinde (funding uc + 12h-trend)
TERS pozisyonu paper kaydeder. Canli-G (data/bot.db g_live) ile forward kiyas: birkac hafta sonra
ters-G kazanirsa kullanici hakli -> G ters cevrilir. GERCEK EMIR YOK.

Not: G'nin gercek mantigiyla AYNI tetik (settled funding %85/%15 + 12h-trend filtre + 24h), sadece YON ters.
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
DB = os.path.join(_DATADIR, "g_reverse_shadow.db")
COINS = ["ETHUSDT", "AVAXUSDT", "XRPUSDT", "ETCUSDT"]   # canli G coinleri
W = 120; PCT = 0.15; TREND_H = 12; HOLD = 24; FEE = 12.0
FRESH_MIN = 90   # sadece son 90 dk'da aciklanan funding (canli G gibi taze giris)


def _get(u):
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(u, headers={"User-Agent": "grev/1.0"}), timeout=15).read())


def fu(s):
    return [(int(x["fundingTime"]) // 1000, float(x["fundingRate"]))
            for x in _get(f"{REST}/fapi/v1/fundingRate?symbol={s}&limit={W + 5}")]


def px(s):
    r = _get(f"{REST}/fapi/v1/klines?symbol={s}&interval=1h&limit=200")
    return {int(x[0]) // 1000: float(x[4]) for x in r}


def init_db():
    os.makedirs(_DATADIR, exist_ok=True)
    c = sqlite3.connect(DB, timeout=30)
    c.execute("""CREATE TABLE IF NOT EXISTS g_reverse (
        symbol TEXT, ftime INTEGER, open_human TEXT, g_side INTEGER, rev_side INTEGER,
        entry REAL, close_ts INTEGER, exit REAL, pnl_bps REAL, status TEXT,
        PRIMARY KEY (symbol, ftime))""")
    c.commit()
    return c


def process(c, verbose=False):
    now = time.time()
    opened = 0
    for s in COINS:
        try:
            fr = fu(s)
            if len(fr) < W + 1:
                continue
            ts, rate = fr[-1]
            past = sorted(r for _, r in fr[-W - 1:-1])
            hi = past[int(W * (1 - PCT))]; lo = past[int(W * PCT)]
            g_side = -1 if rate >= hi else (1 if rate <= lo else 0)   # G'nin diyecegi yon
            if not g_side:
                continue
            if now - ts > FRESH_MIN * 60:     # taze degil -> atla (canli G gibi)
                continue
            p = px(s); ks = sorted(p)
            def pat(t):
                i = bisect.bisect_right(ks, t) - 1
                return p[ks[i]] if i >= 0 else None
            p0 = pat(ts); ptr = pat(ts - TREND_H * 3600)
            if not p0 or not ptr or p0 <= 0:
                continue
            trend = 1 if p0 > ptr else -1
            if g_side != trend:              # 12h-filtre: G bu sinyali ALMAZ -> biz de almayiz
                continue
            if c.execute("SELECT 1 FROM g_reverse WHERE symbol=? AND ftime=?", (s, ts)).fetchone():
                continue
            rev_side = -g_side               # TERS pozisyon
            c.execute("INSERT OR IGNORE INTO g_reverse VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (s, ts, dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M"),
                       g_side, rev_side, round(p0, 6), None, None, None, "OPEN"))
            opened += 1
            if verbose:
                print("  TERS-ACIK %-5s G-derdi:%s -> biz:%s @ %.5g"
                      % (s, "SHORT" if g_side == -1 else "LONG",
                         "SHORT" if rev_side == -1 else "LONG", p0))
        except Exception as e:
            if verbose:
                print("  %-5s HATA: %s" % (s, e))
            continue
    # kapanis 24h
    closed = 0
    for row in c.execute("SELECT symbol,ftime,rev_side,entry FROM g_reverse WHERE status='OPEN'").fetchall():
        s, ft, rside, entry = row
        if now - ft < HOLD * 3600:
            continue
        try:
            p = px(s); ks = sorted(p)
            i = bisect.bisect_right(ks, ft + HOLD * 3600) - 1
            ex = p[ks[i]] if i >= 0 else None
            if not ex:
                continue
            pnl = rside * (ex - entry) / entry * 1e4 - FEE
            c.execute("UPDATE g_reverse SET close_ts=?, exit=?, pnl_bps=?, status='CLOSED' "
                      "WHERE symbol=? AND ftime=?",
                      (int(ft + HOLD * 3600), round(ex, 6), round(pnl, 1), s, ft))
            closed += 1
        except Exception:
            continue
    c.commit()
    return opened, closed


def summary(c):
    rows = c.execute("SELECT pnl_bps FROM g_reverse WHERE status='CLOSED' AND pnl_bps IS NOT NULL").fetchall()
    n = len(rows); net = sum(r[0] for r in rows)
    w = 100 * sum(1 for r in rows if r[0] > 0) // n if n else 0
    op = c.execute("SELECT COUNT(*) FROM g_reverse WHERE status='OPEN'").fetchone()[0]
    print("  TERS-G forward: %d kapali (net %+.0f bps, win %d%%), %d acik" % (n, net, w, op))


def main():
    c = init_db()
    if "--once" in sys.argv:
        print("=== g_reverse_shadow TEK-TUR ===")
        o, cl = process(c, verbose=True)
        print("  -> %d ters-giris, %d kapanis" % (o, cl))
        summary(c)
        return
    print("g_reverse_shadow BASLADI: %d coin, G-sinyalinin TERSI paper (forward kiyas)" % len(COINS))
    while True:
        t0 = time.time()
        try:
            process(c)
        except Exception as e:
            print("dongu hatasi:", e)
        time.sleep(max(1.0, 900 - (time.time() - t0)))


if __name__ == "__main__":
    main()
