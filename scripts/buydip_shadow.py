"""scripts/buydip_shadow.py — YUKSELISTE DIP-AL forward-paper SHADOW. GERCEK EMIR YOK.

Bulgu (2026-09-24, survivorship-testli): LIKIT coin UPTREND'de (close>close[-40]) VE son 3-gun <=-8% dip
-> LONG, 10 gun tut. 496-coin evreninde LIKIT alt-kume 4-ceyrek HEPSI+ (net +313bps, win %45 asimetrik).
Kucuk-cap'te tutarsiz (survivorship). Bu shadow FORWARD dogrular: gecmis + canli ortusur mu.

Gunluk tarama (dumpfade gibi): likit evren (24h-vol top + 30g-ort>=25M) -> sinyal -> paper LONG ->
10 gun sonra kapat. GERCEK EMIR YOK. Maliyet 10bps (likit taker r/t). start_all + watchdog ile surekli.
"""
import os, sys, json, time, sqlite3
import urllib.request
import datetime as dt

REST = "https://fapi.binance.com"
_DATADIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
DB = os.path.join(_DATADIR, "buydip_shadow.db")
DROP = 8.0            # son 3-gun getiri <= -bu% -> dip (saglikli geri-cekilme baslangici)
DROP_CAP = 20.0       # <= -bu% ise ATLA (falling-knife/cokme, dip degil - ONE -38%/AKE -29% gibi toparlamaz)
TREND_N = 60          # uptrend tanimi (close>close[-N]). 40->60 (09-27 lookback testi): N=20/40 Q3-negatif
                      # tutarsiz; N=60/90 4-ceyrek HEPSI+ (60: net+441 win%49 en robust). 40 makuldu ama 60 saglam.
TREND_MIN_PCT = 15.0  # 'AZ AMA TAM' (kullanici felsefesi 09-27, veriyle dogrulandi): 40g-trend >= bu%.
                      # Test (likit 98coin): zayif-uptrend(0-15%) dipi +180/win%42 vs guclu(>15%) +448/win%48.
                      # Sadece GUCLU yukselisteki dipi al = daha az sinyal, daha yuksek isabet.
HOLD_DAYS = 10        # tut suresi
FEE = 10.0            # likit taker round-trip bps
# SHORT-RIP (ayna, 10-12 eklendi, kullanici: short'u forward-kanitla). Guclu DUSUS + rip -> paper SHORT.
# Tarihsel Q3/Q4 negatifti (rejim-bagimli); forward izlenecek. GERCEK EMIR YOK, ayni paper surec.
RIP = 8.0             # son 3-gun getiri >= +bu% -> rip (pump)
RIP_CAP = 20.0        # >= +bu% ise ATLA (parabolik, fade degil)
TREND_MAX = -15.0     # 60g-trend <= bu% (guclu dusus)
MIN_QVOL = 25e6       # 30-gun ort quote-vol (likit)
MIN_DAYS = TREND_N + 5
TOPN = 100            # 24h-vol aday listesi
MAX_OPEN = 25         # ayni anda max acik paper pozisyon (patlamayi onle)
_last_day = 0


def _get(u):
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(u, headers={"User-Agent": "buydip/1.0"}), timeout=15).read())


def init_db():
    os.makedirs(_DATADIR, exist_ok=True)
    c = sqlite3.connect(DB, timeout=30)
    c.execute("""CREATE TABLE IF NOT EXISTS buydip (
        symbol TEXT, open_ts INTEGER, open_human TEXT, entry REAL, drop3 REAL,
        close_ts INTEGER, exit REAL, pnl_bps REAL, status TEXT,
        PRIMARY KEY (symbol, open_ts))""")
    c.execute("""CREATE TABLE IF NOT EXISTS riprshort (
        symbol TEXT, open_ts INTEGER, open_human TEXT, entry REAL, rip3 REAL,
        close_ts INTEGER, exit REAL, pnl_bps REAL, status TEXT,
        PRIMARY KEY (symbol, open_ts))""")
    c.commit()
    return c


def _candidates():
    try:
        t = _get(f"{REST}/fapi/v1/ticker/24hr")
        rows = [(x["symbol"], float(x.get("quoteVolume", 0) or 0)) for x in t
                if str(x.get("symbol", "")).endswith("USDT")]
        rows.sort(key=lambda z: -z[1])
        return [s for s, _ in rows[:TOPN]]
    except Exception:
        return []


def _daily(s, limit=MIN_DAYS + 3):
    try:
        r = _get(f"{REST}/fapi/v1/klines?symbol={s}&interval=1d&limit={limit}")
        return [(int(x[0]) // 1000, float(x[4]), float(x[7])) for x in r]  # ts, close, qvol
    except Exception:
        return None


def process(c, verbose=False, do_entries=True):
    now = int(time.time())
    # 1) ACIK pozisyonlari 10-gun'de kapat  (HER cagrida -> cikislar atlanmaz, bkz 10-05 bug)
    closed = 0
    for sym, ots, entry in c.execute(
            "SELECT symbol, open_ts, entry FROM buydip WHERE status='OPEN'").fetchall():
        if now - ots < HOLD_DAYS * 86400:
            continue
        d = _daily(sym, 5)
        if not d:
            continue
        px = d[-1][1]
        pnl = (px - entry) / entry * 1e4 - FEE
        c.execute("UPDATE buydip SET close_ts=?, exit=?, pnl_bps=?, status='CLOSED' "
                  "WHERE symbol=? AND open_ts=?", (now, round(px, 6), round(pnl, 1), sym, ots))
        closed += 1
        if verbose:
            print("  KAPAT %-10s pnl %+.0f bps" % (sym, pnl))
    # 1b) SHORT-RIP pozisyonlari 10-gun'de kapat (short pnl = -(px-entry)/entry)
    for sym, ots, entry in c.execute(
            "SELECT symbol, open_ts, entry FROM riprshort WHERE status='OPEN'").fetchall():
        if now - ots < HOLD_DAYS * 86400:
            continue
        d = _daily(sym, 5)
        if not d:
            continue
        px = d[-1][1]
        pnl = -(px - entry) / entry * 1e4 - FEE          # SHORT
        c.execute("UPDATE riprshort SET close_ts=?, exit=?, pnl_bps=?, status='CLOSED' "
                  "WHERE symbol=? AND open_ts=?", (now, round(px, 6), round(pnl, 1), sym, ots))
        if verbose:
            print("  SHORT-KAPAT %-10s pnl %+.0f bps" % (sym, pnl))
    c.commit()
    # 2) yeni sinyal tara (gunde bir -> do_entries)
    open_ct = c.execute("SELECT COUNT(*) FROM buydip WHERE status='OPEN'").fetchone()[0]
    opened = 0
    if do_entries and open_ct < MAX_OPEN:
        for sym in _candidates():
            if open_ct + opened >= MAX_OPEN:
                break
            d = _daily(sym)
            if not d or len(d) < MIN_DAYS:
                continue
            vols = [x[2] for x in d[-31:-1] if x[2] > 0]
            if not vols or sum(vols) / len(vols) < MIN_QVOL:
                continue                                   # likit degil -> atla
            close = d[-1][1]
            if close <= 0 or d[-1 - TREND_N][1] <= 0 or d[-4][1] <= 0:
                continue
            tstr = (close - d[-1 - TREND_N][1]) / d[-1 - TREND_N][1] * 100   # 40g trend gucu
            d3 = (close - d[-4][1]) / d[-4][1] * 100       # son 3-gun dip
            # LONG: guclu uptrend + 8-20% dip
            if tstr >= TREND_MIN_PCT and -DROP_CAP <= d3 <= -DROP:
                if not c.execute("SELECT 1 FROM buydip WHERE symbol=? AND status='OPEN'", (sym,)).fetchone():
                    c.execute("INSERT OR IGNORE INTO buydip VALUES (?,?,?,?,?,?,?,?,?)",
                              (sym, now, dt.datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M"),
                               round(close, 6), round(d3, 2), None, None, None, "OPEN"))
                    opened += 1
                    if verbose:
                        print("  DIP-AL %-10s uptrend+dip%.1f%% @ %.6g" % (sym, d3, close))
            # SHORT-RIP (ayna): guclu dusus + 8-20% rip
            elif tstr <= TREND_MAX and RIP <= d3 <= RIP_CAP:
                if not c.execute("SELECT 1 FROM riprshort WHERE symbol=? AND status='OPEN'", (sym,)).fetchone():
                    c.execute("INSERT OR IGNORE INTO riprshort VALUES (?,?,?,?,?,?,?,?,?)",
                              (sym, now, dt.datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M"),
                               round(close, 6), round(d3, 2), None, None, None, "OPEN"))
                    if verbose:
                        print("  RIP-SHORT %-10s dustrend+rip%.1f%% @ %.6g" % (sym, d3, close))
    c.commit()
    return opened, closed


def summary(c):
    rows = c.execute("SELECT pnl_bps FROM buydip WHERE status='CLOSED' AND pnl_bps IS NOT NULL").fetchall()
    n = len(rows); net = sum(r[0] for r in rows)
    w = 100 * sum(1 for r in rows if r[0] > 0) // n if n else 0
    op = c.execute("SELECT COUNT(*) FROM buydip WHERE status='OPEN'").fetchone()[0]
    print("  DIP-AL (LONG) forward: %d kapali (net %+.0f bps, avg %+.0f, win %d%%), %d acik"
          % (n, net, net / n if n else 0, w, op))
    srows = c.execute("SELECT pnl_bps FROM riprshort WHERE status='CLOSED' AND pnl_bps IS NOT NULL").fetchall()
    sn = len(srows); snet = sum(r[0] for r in srows)
    sw = 100 * sum(1 for r in srows if r[0] > 0) // sn if sn else 0
    sop = c.execute("SELECT COUNT(*) FROM riprshort WHERE status='OPEN'").fetchone()[0]
    print("  RIP-SHORT forward: %d kapali (net %+.0f bps, avg %+.0f, win %d%%), %d acik"
          % (sn, snet, snet / sn if sn else 0, sw, sop))


def main():
    c = init_db()
    if "--once" in sys.argv:
        print("=== buydip_shadow TEK-TUR (uptrend+dip%.0f%%, tut %dg) ===" % (DROP, HOLD_DAYS))
        o, cl = process(c, verbose=True)
        print("  -> %d yeni dip-al, %d kapanis" % (o, cl))
        summary(c)
        return
    print("buydip_shadow BASLADI: likit uptrend-dip-al paper (forward dogrulama)")
    global _last_day
    while True:
        t0 = time.time()
        try:
            dk = int(time.time() // 86400)
            do_entries = (dk != _last_day)     # GIRIS gunde bir; CIKIS her saat (bug-fix 10-05)
            process(c, do_entries=do_entries)
            if do_entries:
                _last_day = dk
        except Exception as e:
            print("dongu hatasi:", e)
        time.sleep(max(1.0, 3600 - (time.time() - t0)))


if __name__ == "__main__":
    main()
