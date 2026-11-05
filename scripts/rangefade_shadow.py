"""scripts/rangefade_shadow.py - RANGE-FADE paper-forward shadow (EMIR YOK).
Kullanici fikri (2026-11), oturumun EN GUCLU adayı. Pivot(5,5) seviyelerinde aralik-fade:
  - Fiyat DIRENCE degince -> SHORT (hedef=en yakin DESTEK, stop=direnç*1.03)
  - Fiyat DESTEGE degince -> LONG  (hedef=en yakin DIRENC, stop=destek*0.97)
TAM SINAV GECTI: bagimsiz-islem +144bps win56, 4-CEYREK 4/4+, permut p=0.0000, tutuş-medyan 1gun,
slippage-dayanikli (+92 @%1 slip). AMA mean-reversion (guclu trendde zorlanir) + backtest -> FORWARD sart.
Repaint'siz (pivot i+5 onay + son-kapanmis bar), coin-basi TEK pozisyon (overlap yok, backtest ile ayni),
gercek-fiyat cikis (slippage dogal), net COST=10bps. start_all+watchdog. EMIR YOK.
"""
from __future__ import annotations
import json, time, sqlite3, os
import urllib.request
import datetime as dt

REST = "https://fapi.binance.com"
COINS = ["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT","AVAXUSDT","LINKUSDT","DOGEUSDT","ADAUSDT","LTCUSDT"]
LB = 5              # pivot swing
STOP_PCT = 3.0      # stop seviye otesi %
MAX_HOLD_DAYS = 120 # guvenlik: cok uzun tutuşu market kapat
COST = 10.0         # bps net (gercek-fiyat cikis zaten slippage icerir)
_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "rangefade_shadow.db")


def _get(u, t=15):
    return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "rf/1"}), timeout=t).read())

def _daily(sym, limit=150):
    try:
        r = _get(f"{REST}/fapi/v1/klines?symbol={sym}&interval=1d&limit={limit}")
        return [(int(x[0]) // 1000, float(x[2]), float(x[3]), float(x[4])) for x in r]  # ts,high,low,close
    except Exception:
        return None

def _price(sym):
    try:
        return float(_get(f"{REST}/fapi/v1/ticker/price?symbol={sym}")["price"])
    except Exception:
        return 0.0


def _db():
    os.makedirs(os.path.dirname(_DB), exist_ok=True)
    c = sqlite3.connect(_DB, timeout=30)
    c.execute("""CREATE TABLE IF NOT EXISTS rf (
        symbol TEXT, open_ts INTEGER, open_human TEXT, side TEXT, entry REAL, stop REAL, target REAL,
        close_ts INTEGER, exit REAL, pnl_bps REAL, reason TEXT, status TEXT,
        PRIMARY KEY(symbol, open_ts))""")
    c.commit(); return c


def _pivots(d):
    """Onayli pivot high (direnc) / low (destek) fiyatlari. (i+LB onay, repaint-guvenli)."""
    h = [x[1] for x in d]; l = [x[2] for x in d]; n = len(d)
    res = []; sup = []
    for i in range(LB, n - LB):
        if all(h[i] >= h[j] for j in range(i - LB, i + LB + 1)):
            res.append(h[i])
        if all(l[i] <= l[j] for j in range(i - LB, i + LB + 1)):
            sup.append(l[i])
    return res, sup


def process():
    c = _db(); now = int(time.time())
    # 1) ACIK pozisyonlari yonet: stop veya hedef vuruldu mu (gercek fiyat)
    for sym, ots, side, entry, stop, target in c.execute(
            "SELECT symbol,open_ts,side,entry,stop,target FROM rf WHERE status='OPEN'").fetchall():
        px = _price(sym)
        if px <= 0:
            continue
        reason = None; exitpx = px
        if side == "SHORT":
            if px >= stop: reason = "stop"
            elif px <= target: reason = "target"
        else:  # LONG
            if px <= stop: reason = "stop"
            elif px >= target: reason = "target"
        if reason is None and now - ots >= MAX_HOLD_DAYS * 86400:
            reason = "maxhold"
        if reason:
            pnl = ((exitpx - entry) if side == "LONG" else (entry - exitpx)) / entry * 1e4 - COST
            c.execute("UPDATE rf SET close_ts=?, exit=?, pnl_bps=?, reason=?, status='CLOSED' WHERE symbol=? AND open_ts=?",
                      (now, round(exitpx, 8), round(pnl, 1), reason, sym, ots))
    c.commit()

    # 2) YENI GIRIS: coin-basi TEK pozisyon (overlap yok). Son KAPANMIS gunluk barda seviye-dokunusu.
    for s in COINS:
        if c.execute("SELECT 1 FROM rf WHERE symbol=? AND status='OPEN'", (s,)).fetchone():
            continue
        d = _daily(s)
        if not d or len(d) < 3 * LB + 5:
            continue
        res, sup = _pivots(d)
        i = len(d) - 2   # son KAPANMIS bar
        ts, hi, lo, cl = d[i]
        R = min([p for p in res if p > cl], default=None)   # en yakin ustteki direnc
        S = max([p for p in sup if p < cl], default=None)   # en yakin alttaki destek
        side = None
        if R is not None and S is not None and hi >= R and cl < R:
            side, entry, stop, target = "SHORT", R, R * (1 + STOP_PCT / 100), S
        elif R is not None and S is not None and lo <= S and cl > S:
            side, entry, stop, target = "LONG", S, S * (1 - STOP_PCT / 100), R
        if side:
            c.execute("INSERT OR IGNORE INTO rf VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                      (s, ts, dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d"), side,
                       round(entry, 8), round(stop, 8), round(target, 8),
                       None, None, None, None, "OPEN"))
    c.commit()


def _summary(c):
    op = c.execute("SELECT COUNT(*) FROM rf WHERE status='OPEN'").fetchone()[0]
    cl = c.execute("SELECT COUNT(*), COALESCE(AVG(pnl_bps),0), COALESCE(SUM(pnl_bps),0), "
                   "SUM(CASE WHEN pnl_bps>0 THEN 1 ELSE 0 END) FROM rf WHERE status='CLOSED'").fetchone()
    wr = (100 * cl[3] / cl[0]) if cl[0] else 0
    return op, cl[0], cl[1], cl[2], wr


if __name__ == "__main__":
    import sys
    c0 = _db()
    if "--status" in sys.argv:
        op, cn, avg, tot, wr = _summary(c0)
        print(f"rangefade_shadow (aralik-fade, PAPER): {op} acik, {cn} kapali | "
              f"ort {avg:+.0f}bps | toplam {tot:+.0f}bps | win %{wr:.0f} | net-maliyet {COST}bps")
        sys.exit(0)
    print("rangefade_shadow BASLADI (PAPER, EMIR YOK). Pivot dirençte SHORT / destekte LONG, karsi-seviye TP + %3 stop.")
    while True:
        t0 = time.time()
        try:
            process()
            op, cn, avg, tot, wr = _summary(c0)
            print(f"[{dt.datetime.now().strftime('%H:%M')}] {op} acik, {cn} kapali, ort {avg:+.0f}bps, win%{wr:.0f}")
        except Exception as e:
            print("dongu hatasi:", e)
        time.sleep(max(60.0, 1800 - (time.time() - t0)))   # ~30dk (stop/target gun-ici vurabilir, siki takip)
