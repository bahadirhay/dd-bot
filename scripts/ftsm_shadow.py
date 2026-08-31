"""scripts/ftsm_shadow.py — F (gunluk TSM trend-takip) FORWARD PAPER-SHADOW.

F = gunluk Time-Series-Momentum: side = sign(close[t] - close[t-N]); flip-only (trend donunce ters cevir).
Dogrulama (cok-coin WF): ETH/BNB/LINK/ADA/LTC/INJ robust (iki-yari+ Sharpe>0.5). ETH = G'de kaldi (cakisma),
o yuzden F CANLI-ADAY seti = ayrik: BNB/LINK/ADA/LTC/INJ (+ETH referans). Ayrik set -> G ile tek hesapta
netlesme YOK.

Bu shadow: her coin icin flip-only paper islemler + forward P&L. Kendi DB (data/ftsm_shadow.db), EMIR YOK,
canli bota SIFIR dokunus. Haftalik calistir. Deploy/forward isareti: 2026-08-31.

Calistir:  python scripts/ftsm_shadow.py
"""
import os
import json
import time
import sqlite3
import urllib.request
import datetime as dt

REST = "https://fapi.binance.com"
_DATADIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
DB = os.path.join(_DATADIR, "ftsm_shadow.db")

N = 40            # gunluk geri-bakis (dogrulanmis optimum)
FEE_BPS = 6.0     # flip basina ~6 bps (2 bacak)
FWD = time.mktime(time.strptime("2026-08-31", "%Y-%m-%d"))  # bu tarih sonrasi = gercek forward

# F canli-aday seti (G ile AYRIK) + ETH referans (G'de canli, burada sadece kiyas)
LIVE = ["BNBUSDT", "LINKUSDT", "ADAUSDT", "LTCUSDT", "INJUSDT"]
REF = ["ETHUSDT"]
COINS = LIVE + REF


def kd(sym, limit=450):
    u = f"{REST}/fapi/v1/klines?symbol={sym}&interval=1d&limit={limit}"
    r = json.loads(urllib.request.urlopen(u, timeout=20).read())
    return [(int(x[0]) // 1000, float(x[4])) for x in r]  # (ts, close)


def ensure():
    os.makedirs(_DATADIR, exist_ok=True)
    c = sqlite3.connect(DB, timeout=30)
    c.execute("""CREATE TABLE IF NOT EXISTS ftsm_shadow (
        symbol TEXT, open_ts INTEGER, open_human TEXT, side INTEGER,
        entry REAL, close_ts INTEGER, exit REAL, pnl_bps REAL, status TEXT,
        PRIMARY KEY (symbol, open_ts))""")
    c.commit()
    return c


def trades(sym):
    """Flip-only TSM: trend donunce pozisyon cevir. Her pozisyon bir 'trade'."""
    d = kd(sym)
    if len(d) < N + 5:
        return []
    ts = [t for t, _ in d]
    cl = [p for _, p in d]
    out = []
    pos = 0
    ent_i = None
    for i in range(N, len(cl)):
        side = 1 if cl[i] > cl[i - N] else -1
        if side != pos:
            if pos != 0 and ent_i is not None:   # eski pozisyonu kapat
                p0, p1 = cl[ent_i], cl[i]
                pnl = pos * (p1 - p0) / p0 * 1e4 - FEE_BPS
                out.append((ts[ent_i], pos, p0, ts[i], p1, round(pnl, 1), "CLOSED"))
            pos = side
            ent_i = i
    if pos != 0 and ent_i is not None:   # acik pozisyon
        out.append((ts[ent_i], pos, cl[ent_i], None, None, None, "OPEN"))
    return out


def main():
    c = ensure()
    print("=== F (gunluk TSM N=%d, flip-only) FORWARD-SHADOW ===" % N)
    print("  canli-aday (G-AYRIK): %s | referans: %s" % (",".join(x.replace("USDT", "") for x in LIVE),
                                                          ",".join(x.replace("USDT", "") for x in REF)))
    print()
    print("  %-5s %-4s %18s   %18s" % ("coin", "", "TUM (net bps)", "FORWARD >=08-31"))
    for sym in COINS:
        tr = trades(sym)
        for (ots, side, ent, cts, ex, pnl, st) in tr:
            c.execute("INSERT OR REPLACE INTO ftsm_shadow VALUES (?,?,?,?,?,?,?,?,?)",
                      (sym, ots, dt.datetime.fromtimestamp(ots).strftime("%Y-%m-%d"),
                       side, round(ent, 5), cts, round(ex, 5) if ex else None, pnl, st))
        closed = [t for t in tr if t[6] == "CLOSED"]
        allnet = sum(t[5] for t in closed)
        fwd = [t for t in closed if t[3] and t[3] > FWD]
        fnet = sum(t[5] for t in fwd)
        opos = [t for t in tr if t[6] == "OPEN"]
        opd = ("acik:%s" % ("LONG" if opos[0][1] == 1 else "SHORT")) if opos else "-"
        tag = "REF" if sym in REF else "aday"
        print("  %-5s [%s] n=%2d net=%+7.0f   n=%2d net=%+6.0f   %s"
              % (sym.replace("USDT", ""), tag, len(closed), allnet, len(fwd), fnet, opd))
    c.commit()
    print()
    print("  NOT: FORWARD sutunu 08-31 sonrasi = gercek OOS (henuz ~bos, bugun basladi). Haftalarca biriktir.")
    print("  ETH=REF (G'de canli, F'de degil). Aday set G ile AYRIK -> tek hesapta yan yana calisir.")


if __name__ == "__main__":
    main()
