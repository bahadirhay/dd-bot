"""scripts/rangefade_shadow.py - RANGE-FADE + IKINCI-TEYIT paper-forward shadow (EMIR YOK).
Kullanici fikri (2026-11), oturumun EN GUCLU adayı. Pivot(5,5) aralik-fade + DONUS-TEYITI:
  - DESTEGE deginca -> PENDING LONG. Fiyat S*(1+%0.5)'e SICRARSA (teyit) -> LONG ac (hedef=DIRENC, stop=S*0.97).
    Teyit gelmeden S*0.97'ye duserse -> IPTAL (dusen-bicak elendi).
  - DIRENCE deginca -> PENDING SHORT. Fiyat R*(1-%0.5)'e KIRILIRSA -> SHORT ac (hedef=DESTEK, stop=R*1.03).
TAM SINAV GECTI (teyitli, saf-fade'den guclu): +231bps win70, 4-CEYREK 4/4+, permut p=0.0000,
slippage-dayanikli (+203@%1). TEYIT ~720 falling-knife setup'ini eledi (win %56->70). AMA backtest + mean-
reversion (guclu trendde zorlanir) + tek-rejim -> FORWARD sart. Repaint'siz, coin-basi TEK (pending VEYA open),
gercek-fiyat cikis (slippage dogal), net COST=10. start_all[2k]+watchdog[4d]. EMIR YOK.
"""
from __future__ import annotations
import json, time, sqlite3, os
import urllib.request
import datetime as dt

REST = "https://fapi.binance.com"
COINS = ["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT","AVAXUSDT","LINKUSDT","DOGEUSDT","ADAUSDT","LTCUSDT"]
LB = 5
STOP_PCT = 3.0       # stop seviye otesi %
CONF_PCT = 0.5       # donus-teyit %: seviyeden bu kadar donunce gir
MAX_PEND_DAYS = 20   # teyit/iptal gelmezse pending iptal
MAX_HOLD_DAYS = 120  # acik pozisyon guvenlik
COST = 10.0
_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "rangefade_shadow.db")


def _get(u, t=15):
    return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "rf/2"}), timeout=t).read())

def _daily(sym, limit=150):
    try:
        r = _get(f"{REST}/fapi/v1/klines?symbol={sym}&interval=1d&limit={limit}")
        return [(int(x[0]) // 1000, float(x[2]), float(x[3]), float(x[4])) for x in r]
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
        symbol TEXT, open_ts INTEGER, open_human TEXT, side TEXT, level REAL, conflvl REAL,
        entry REAL, stop REAL, target REAL, confirm_ts INTEGER,
        close_ts INTEGER, exit REAL, pnl_bps REAL, reason TEXT, status TEXT,
        PRIMARY KEY(symbol, open_ts))""")
    c.commit(); return c


def _pivots(d):
    h = [x[1] for x in d]; l = [x[2] for x in d]; n = len(d)
    res = []; sup = []
    for i in range(LB, n - LB):
        if all(h[i] >= h[j] for j in range(i - LB, i + LB + 1)): res.append(h[i])
        if all(l[i] <= l[j] for j in range(i - LB, i + LB + 1)): sup.append(l[i])
    return res, sup


def process():
    c = _db(); now = int(time.time())

    # 1) PENDING setuplari: iptal (bicak) veya teyit (gir)
    for sym, ots, side, level, conflvl, stop, target in c.execute(
            "SELECT symbol,open_ts,side,level,conflvl,stop,target FROM rf WHERE status='PENDING'").fetchall():
        px = _price(sym)
        if px <= 0:
            continue
        if side == "LONG":
            if px <= stop:
                c.execute("UPDATE rf SET status='CANCELLED', reason='bicak' WHERE symbol=? AND open_ts=?", (sym, ots))
            elif px >= conflvl:
                c.execute("UPDATE rf SET status='OPEN', entry=?, confirm_ts=? WHERE symbol=? AND open_ts=?",
                          (round(conflvl, 8), now, sym, ots))
            elif now - ots >= MAX_PEND_DAYS * 86400:
                c.execute("UPDATE rf SET status='CANCELLED', reason='teyit-yok' WHERE symbol=? AND open_ts=?", (sym, ots))
        else:  # SHORT
            if px >= stop:
                c.execute("UPDATE rf SET status='CANCELLED', reason='bicak' WHERE symbol=? AND open_ts=?", (sym, ots))
            elif px <= conflvl:
                c.execute("UPDATE rf SET status='OPEN', entry=?, confirm_ts=? WHERE symbol=? AND open_ts=?",
                          (round(conflvl, 8), now, sym, ots))
            elif now - ots >= MAX_PEND_DAYS * 86400:
                c.execute("UPDATE rf SET status='CANCELLED', reason='teyit-yok' WHERE symbol=? AND open_ts=?", (sym, ots))
    c.commit()

    # 2) ACIK pozisyonlari yonet: stop/hedef (gercek fiyat)
    for sym, ots, side, entry, stop, target, cts in c.execute(
            "SELECT symbol,open_ts,side,entry,stop,target,confirm_ts FROM rf WHERE status='OPEN'").fetchall():
        px = _price(sym)
        if px <= 0:
            continue
        reason = None
        if side == "LONG":
            if px <= stop: reason = "stop"
            elif px >= target: reason = "target"
        else:
            if px >= stop: reason = "stop"
            elif px <= target: reason = "target"
        if reason is None and cts and now - cts >= MAX_HOLD_DAYS * 86400:
            reason = "maxhold"
        if reason:
            pnl = ((px - entry) if side == "LONG" else (entry - px)) / entry * 1e4 - COST
            c.execute("UPDATE rf SET close_ts=?, exit=?, pnl_bps=?, reason=?, status='CLOSED' WHERE symbol=? AND open_ts=?",
                      (now, round(px, 8), round(pnl, 1), reason, sym, ots))
    c.commit()

    # 3) YENI SETUP: coin-basi TEK (pending veya open yoksa), son kapanmis barda seviye-dokunusu -> PENDING
    for s in COINS:
        if c.execute("SELECT 1 FROM rf WHERE symbol=? AND status IN ('PENDING','OPEN')", (s,)).fetchone():
            continue
        d = _daily(s)
        if not d or len(d) < 3 * LB + 5:
            continue
        res, sup = _pivots(d)
        i = len(d) - 2
        ts, hi, lo, cl = d[i]
        R = min([p for p in res if p > cl], default=None)
        S = max([p for p in sup if p < cl], default=None)
        side = None
        if S is not None and R is not None and lo <= S and cl > S:
            side = "LONG"; level = S; conflvl = S * (1 + CONF_PCT / 100); stop = S * (1 - STOP_PCT / 100); target = R
        elif R is not None and S is not None and hi >= R and cl < R:
            side = "SHORT"; level = R; conflvl = R * (1 - CONF_PCT / 100); stop = R * (1 + STOP_PCT / 100); target = S
        if side:
            c.execute("INSERT OR IGNORE INTO rf VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (s, ts, dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d"), side,
                       round(level, 8), round(conflvl, 8), None, round(stop, 8), round(target, 8),
                       None, None, None, None, None, "PENDING"))
    c.commit()


def _summary(c):
    pe = c.execute("SELECT COUNT(*) FROM rf WHERE status='PENDING'").fetchone()[0]
    op = c.execute("SELECT COUNT(*) FROM rf WHERE status='OPEN'").fetchone()[0]
    cn = c.execute("SELECT COUNT(*) FROM rf WHERE status='CANCELLED'").fetchone()[0]
    cl = c.execute("SELECT COUNT(*), COALESCE(AVG(pnl_bps),0), COALESCE(SUM(pnl_bps),0), "
                   "SUM(CASE WHEN pnl_bps>0 THEN 1 ELSE 0 END) FROM rf WHERE status='CLOSED'").fetchone()
    wr = (100 * cl[3] / cl[0]) if cl[0] else 0
    return pe, op, cn, cl[0], cl[1], cl[2], wr


if __name__ == "__main__":
    import sys
    c0 = _db()
    if "--status" in sys.argv:
        pe, op, cn, clo, avg, tot, wr = _summary(c0)
        print(f"rangefade_shadow (aralik-fade + TEYIT, PAPER): {pe} pending, {op} acik, {clo} kapali, {cn} iptal(bicak) | "
              f"ort {avg:+.0f}bps | toplam {tot:+.0f}bps | win %{wr:.0f}")
        sys.exit(0)
    print("rangefade_shadow BASLADI (PAPER + TEYIT, EMIR YOK). Seviyede PENDING, donus teyitlenince ac, %3 stop.")
    while True:
        t0 = time.time()
        try:
            process()
            pe, op, cn, clo, avg, tot, wr = _summary(c0)
            print(f"[{dt.datetime.now().strftime('%H:%M')}] {pe} pending, {op} acik, {clo} kapali, {cn} iptal, ort {avg:+.0f}bps, win%{wr:.0f}")
        except Exception as e:
            print("dongu hatasi:", e)
        time.sleep(max(60.0, 1800 - (time.time() - t0)))
