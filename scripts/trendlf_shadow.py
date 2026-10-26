"""scripts/trendlf_shadow.py — COK-COIN "LONG-veya-NAKIT" gunluk trend-takip SHADOW. GERCEK EMIR YOK.

Kural (kullanici karari 09-15, veriyle secildi f_longflat testi):
  Her coin, her gun: close[bugun] > close[40 gun once] ISE -> LONG, degilse -> NAKIT (short YOK).
  Esit-agirlik cok-coin. Gunluk kontrol; sinyal donunce long<->nakit cevir. 6 bps/flip.

NEDEN bu kural (basari etkenleri):
  1) SHORT YOK: F testinde short'lar 8/10 coinde kaybediyordu -> dususte kenara cekil, short'lama.
  2) COK-COIN cesitlendirme: tek-coin gurultusunu keser.
  3) DUSUS-KORUMASI: trend asagi donunce NAKIT -> buyuk cokuslerde disarda (asil deger burada).
  4) GUNLUK olcek: maliyet ihmal edilebilir (nadir flip), intraday-gurultu yok (VR~1 rastgele-yuruyus).
  Basari SARTI: kripto TREND etsin (surekli yon). Yatay/whipsaw -> flip-maliyeti ile kucuk kayip.
  'Trend genisligi' (kac coin LONG) = rejim gostergesi; genis=riskli-al, dar=nakite-cekil.

Not: EDGE DEGIL, disiplinli+dusus-korumali kripto-trend maruz kalmasi. Forward izle, sonra karar.
Calistirma: python scripts/trendlf_shadow.py   |   tek-tur: --once
"""
import os, sys, json, time, sqlite3, bisect
import urllib.request
import datetime as dt

REST = "https://fapi.binance.com"
_DATADIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
DB = os.path.join(_DATADIR, "trendlf_shadow.db")
COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "LINKUSDT"]
N = 40          # gunluk geri-bakis (trend tanimi)
FEE = 6.0       # flip basina bps
STOP_PCT = 10.0     # SERT-STOP: pozisyon giristen bu kadar %% dususte cik (trendden bagimsiz).
                    # Test (09-16, 10coin): stop -8..-15% DUZ N40'i her metrikte hafif gecti (getiri+,
                    # maxDD-, tail -1962->-1545bps). Trend-takipte stop YARDIMCI (G/D MR'da kanatiyordu).
COOLDOWN_DAYS = 10  # stop sonrasi bu kadar gun yeni giris YOK (churn/whipsaw onlemi).
BREADTH_K = 10      # YAPI-KONTROLU: piyasa-geneli breadth = coin'lerin close>close[-K] orani.
BREADTH_THR = 0.4   # breadth < bu ise PIYASA YAPISI DONDU -> yeni long YOK + acikari nakite cek.
                    # Test (09-22, 10coin 4-ceyrek WF): breadth-kapisi maxDD'yi HER ceyrekte azaltti
                    # (-7757->-6126, %21 dusuk), getiri ~ayni = saglam RISK-AZALTICI (kullanici 'yapi-kontrolu'
                    # sezgisi dogru; tek-coin pullback breadth'i bozmaz, piyasa-donusu breadth'i coker).


def _get(u):
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(u, headers={"User-Agent": "trendlf/1.0"}), timeout=15).read())


def daily(s, limit=N + 5):
    r = _get(f"{REST}/fapi/v1/klines?symbol={s}&interval=1d&limit={limit}")
    return [(int(x[0]) // 1000, float(x[4])) for x in r]   # (gun_ts, close)


def init_db():
    os.makedirs(_DATADIR, exist_ok=True)
    c = sqlite3.connect(DB, timeout=30)
    c.execute("""CREATE TABLE IF NOT EXISTS trendlf (
        symbol TEXT, open_ts INTEGER, open_human TEXT, entry REAL,
        close_ts INTEGER, exit REAL, pnl_bps REAL, status TEXT,
        PRIMARY KEY (symbol, open_ts))""")
    try:
        c.execute("ALTER TABLE trendlf ADD COLUMN reason TEXT")   # 'trend' / 'stop'
    except Exception:
        pass
    c.commit()
    return c


def signal(cl):
    """LONG (1) eger close[-1] > close[-1-N], degilse NAKIT (0)."""
    if len(cl) < N + 1:
        return None
    return 1 if cl[-1][1] > cl[-1 - N][1] else 0


def process(c, verbose=False):
    now = int(time.time())
    breadth = 0; live = 0
    def _close(s, ots, entry, px, reason):
        pnl = (px - entry) / entry * 1e4 - FEE
        c.execute("UPDATE trendlf SET close_ts=?, exit=?, pnl_bps=?, status='CLOSED', reason=? "
                  "WHERE symbol=? AND open_ts=?", (now, round(px, 6), round(pnl, 1), reason, s, ots))
        if verbose:
            tag = "STOP-CIK " if reason == "stop" else "NAKIT-CEK"
            print("  %s %-8s @ %.5g  pnl %+.0f bps" % (tag, s, px, pnl))

    # 1. GECIS: tum coin gunluk-veri + PIYASA BREADTH (yapi-kontrolu) hesapla
    cache = {}
    up_count = 0; valid = 0
    for s in COINS:
        try:
            cl = daily(s, max(N, BREADTH_K) + 5)
            if len(cl) < max(N, BREADTH_K) + 1:
                continue
            cache[s] = cl
            valid += 1
            if cl[-1][1] > cl[-1 - BREADTH_K][1]:            # KFAST-trend yukari mi (breadth icin)
                up_count += 1
        except Exception as e:
            if verbose:
                print("  %-8s HATA(fetch): %s" % (s, e))
    market_breadth = (up_count / valid) if valid else 1.0
    struct_ok = market_breadth >= BREADTH_THR
    if verbose:
        print("  PIYASA BREADTH: %.0f%% (%d/%d yukari) -> yapi %s"
              % (market_breadth * 100, up_count, valid, "SAGLAM" if struct_ok else "DONDU=NAKIT"))

    # 2. GECIS: her coin karar (breadth-kapisi dahil)
    for s in COINS:
        try:
            cl = cache.get(s)
            if cl is None:
                continue
            sig = signal(cl)
            if sig is None:
                continue
            px = cl[-1][1]
            if sig == 1:
                breadth += 1
            open_row = c.execute(
                "SELECT open_ts, entry FROM trendlf WHERE symbol=? AND status='OPEN'", (s,)).fetchone()
            if open_row:
                ots, entry = open_row
                dd = (px - entry) / entry * 100.0
                if dd <= -STOP_PCT:
                    _close(s, ots, entry, px, "stop")        # SERT-STOP (trendden bagimsiz)
                elif not struct_ok:
                    _close(s, ots, entry, px, "breadth")     # YAPI DONDU (piyasa-geneli) -> nakit
                elif sig == 0:
                    _close(s, ots, entry, px, "trend")       # kendi trendi nakite dondu
                else:
                    live += 1                                # pozisyon devam
            elif sig == 1 and struct_ok:
                # cooldown: son STOP kapanisi COOLDOWN_DAYS icinde ise yeni giris YOK
                last_stop = c.execute(
                    "SELECT close_ts FROM trendlf WHERE symbol=? AND reason='stop' "
                    "ORDER BY close_ts DESC LIMIT 1", (s,)).fetchone()
                if last_stop and last_stop[0] and (now - last_stop[0]) < COOLDOWN_DAYS * 86400:
                    if verbose:
                        print("  COOLDOWN  %-8s (stop sonrasi bekleme)" % s)
                else:
                    c.execute("INSERT OR IGNORE INTO trendlf "
                              "(symbol,open_ts,open_human,entry,close_ts,exit,pnl_bps,status,reason) "
                              "VALUES (?,?,?,?,?,?,?,?,?)",
                              (s, now, dt.datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M"),
                               round(px, 6), None, None, None, "OPEN", None))
                    live += 1
                    if verbose:
                        print("  LONG-AC  %-8s @ %.5g" % (s, px))
        except Exception as e:
            if verbose:
                print("  %-8s HATA: %s" % (s, e))
    c.commit()
    return breadth, live


def summary(c):
    rows = c.execute("SELECT pnl_bps FROM trendlf WHERE status='CLOSED' AND pnl_bps IS NOT NULL").fetchall()
    n = len(rows); net = sum(r[0] for r in rows)
    w = 100 * sum(1 for r in rows if r[0] > 0) // n if n else 0
    # acik pozisyonlarin anlik durumu
    opens = c.execute("SELECT symbol, entry FROM trendlf WHERE status='OPEN'").fetchall()
    unreal = 0.0
    for s, entry in opens:
        try:
            px = float(_get(f"{REST}/fapi/v1/ticker/price?symbol={s}")["price"])
            unreal += (px - entry) / entry * 1e4
        except Exception:
            pass
    print("  KAPANAN: %d islem, net %+.0f bps (win %d%%)" % (n, net, w))
    print("  ACIK: %d pozisyon, kagit-uzeri %+.0f bps" % (len(opens), unreal))
    print("  >>> TREND GENISLIGI: %d/%d coin LONG (basari etkeni: genis+trend=iyi, dar=nakit-koru)"
          % (len(opens), len(COINS)))


def main():
    c = init_db()
    if "--once" in sys.argv:
        print("=== trendlf_shadow TEK-TUR (long-veya-nakit, N=%d) ===" % N)
        b, l = process(c, verbose=True)
        summary(c)
        return
    print("trendlf_shadow BASLADI: %d coin, gunluk long-veya-nakit trend-takip (paper)" % len(COINS))
    while True:
        t0 = time.time()
        try:
            process(c)
        except Exception as e:
            print("dongu hatasi:", e)
        time.sleep(max(1.0, 3600 - (time.time() - t0)))   # saatte bir kontrol (gunluk sinyal ama sik bak)


if __name__ == "__main__":
    main()
