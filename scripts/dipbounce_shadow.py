"""scripts/dipbounce_shadow.py - DIP-BOUNCE paper-forward shadow (EMIR YOK).
Dogrulanmis (oturum 2026-11, kullanici'nin 'donus-noktasi ortak ozelligi' sezgisinden): aşiri-satim sicramasi.
Sinyal: RSI(14)<30 + close < SMA20*(1-0.05) (oversold + SMA'nin %5 alti) -> LONG, 5 gun tut.
TAM SINAV GECTI: slippage-dayanikli (+44 slip10), 4-ceyrek 3/4+, permutasyon p=0.0000, parametre-platosu (9/9+).
AMA mean-reversion: Q3 -296 = guclu-dususte DUSEN-BICAK riski (guard'lar duzeltmedi -> kucuk-boyut savunmasi).
VDPM'e TAMAMLAYICI: VDPM bull-trend'de, bu HER oversold'da (ayida da) ateşler = donus-sicramalarini yakalar.
Repaint'siz (RSI/SMA kapanmis gunluk bar, dedup), net COST=20bps. start_all+watchdog. EMIR YOK.
"""
from __future__ import annotations
import json, time, sqlite3, os
import urllib.request
import datetime as dt

REST = "https://fapi.binance.com"
COINS = ["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT","AVAXUSDT","LINKUSDT","DOGEUSDT","ADAUSDT","LTCUSDT",
         "DOTUSDT","ATOMUSDT","NEARUSDT","APTUSDT","ARBUSDT","OPUSDT","INJUSDT","FILUSDT"]
RSI_P = 14; RSI_MAX = 30.0; SMA_P = 20; DIST_MAX = -5.0; HOLD_DAYS = 5
HOLD_SEC = HOLD_DAYS * 86400
COST = 20.0   # bps net (fee ~10 + slippage ~5x2)
_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "dipbounce_shadow.db")


def _get(u, t=15):
    return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "dipb/1"}), timeout=t).read())

def _daily(sym, limit=60):
    try:
        r = _get(f"{REST}/fapi/v1/klines?symbol={sym}&interval=1d&limit={limit}")
        return [(int(x[0]) // 1000, float(x[4])) for x in r]   # (open_ts_sec, close)
    except Exception:
        return None

def _price(sym):
    try:
        return float(_get(f"{REST}/fapi/v1/ticker/price?symbol={sym}")["price"])
    except Exception:
        return 0.0

def _rsi(c, i, p=RSI_P):
    if i < p:
        return None
    g = lo = 0.0
    for j in range(i - p + 1, i + 1):
        d = c[j] - c[j - 1]; g += max(d, 0); lo += max(-d, 0)
    return 100 - 100 / (1 + (g / p) / ((lo / p) or 1e-9))


def _db():
    os.makedirs(os.path.dirname(_DB), exist_ok=True)
    c = sqlite3.connect(_DB, timeout=30)
    c.execute("""CREATE TABLE IF NOT EXISTS dipb (
        symbol TEXT, signal_ts INTEGER, signal_human TEXT, entry REAL, rsi REAL, dist REAL,
        hold_until INTEGER, close_ts INTEGER, exit REAL, pnl_bps REAL, status TEXT,
        PRIMARY KEY(symbol, signal_ts))""")
    c.commit(); return c


def process():
    c = _db(); now = int(time.time())
    # 1) CIKIS: 5 gun dolan paper pozisyonlari kapat
    for sym, sts, entry, hu in c.execute(
            "SELECT symbol, signal_ts, entry, hold_until FROM dipb WHERE status='OPEN'").fetchall():
        if now >= hu:
            px = _price(sym)
            if px <= 0:
                continue
            pnl = (px - entry) / entry * 1e4 - COST   # LONG net
            c.execute("UPDATE dipb SET close_ts=?, exit=?, pnl_bps=?, status='CLOSED' WHERE symbol=? AND signal_ts=?",
                      (now, round(px, 8), round(pnl, 1), sym, sts))
    c.commit()

    # 2) GIRIS: her coin, SON KAPANMIS gunluk barda oversold-dip sinyali
    for s in COINS:
        d = _daily(s)
        if not d or len(d) < SMA_P + RSI_P + 3:
            continue
        c_ = [x[1] for x in d]
        i = len(c_) - 2   # son KAPANMIS bar (sonuncu in-progress)
        r = _rsi(c_, i)
        if r is None:
            continue
        s20 = sum(c_[i - SMA_P + 1:i + 1]) / SMA_P
        dist = (c_[i] - s20) / s20 * 100
        if r < RSI_MAX and dist < DIST_MAX:
            sts = d[i][0]
            entry = c_[i]
            c.execute("INSERT OR IGNORE INTO dipb VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (s, sts, dt.datetime.fromtimestamp(sts).strftime("%Y-%m-%d"),
                       round(entry, 8), round(r, 1), round(dist, 1),
                       sts + HOLD_SEC, None, None, None, "OPEN"))
    c.commit()


def _summary(c):
    op = c.execute("SELECT COUNT(*) FROM dipb WHERE status='OPEN'").fetchone()[0]
    cl = c.execute("SELECT COUNT(*), COALESCE(AVG(pnl_bps),0), COALESCE(SUM(pnl_bps),0), "
                   "SUM(CASE WHEN pnl_bps>0 THEN 1 ELSE 0 END) FROM dipb WHERE status='CLOSED'").fetchone()
    wr = (100 * cl[3] / cl[0]) if cl[0] else 0
    return op, cl[0], cl[1], cl[2], wr


if __name__ == "__main__":
    import sys
    c0 = _db()
    if "--status" in sys.argv:
        op, cn, avg, tot, wr = _summary(c0)
        print(f"dipbounce_shadow (oversold-sicrama LONG, PAPER): {op} acik, {cn} kapali | "
              f"ort {avg:+.0f}bps | toplam {tot:+.0f}bps | win %{wr:.0f} | net-maliyet {COST}bps")
        sys.exit(0)
    print("dipbounce_shadow BASLADI (PAPER, EMIR YOK). RSI<30+SMA<-5% -> LONG 5g oversold-sicrama forward.")
    while True:
        t0 = time.time()
        try:
            process()
            op, cn, avg, tot, wr = _summary(c0)
            print(f"[{dt.datetime.now().strftime('%H:%M')}] {op} acik, {cn} kapali, ort {avg:+.0f}bps, win%{wr:.0f}")
        except Exception as e:
            print("dongu hatasi:", e)
        time.sleep(max(60.0, 3600 - (time.time() - t0)))   # ~saatlik dongu (gunluk sinyal, sik tarama gereksiz)
