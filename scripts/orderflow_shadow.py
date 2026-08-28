"""scripts/orderflow_shadow.py — GERCEK-ZAMANLI ORDER-FLOW / OBI SHADOW toplayici.

Amac: fiyat-DISI, tarihsel-test-EDILEMEZ mikroyapiyi CANLI biriktir. GERCEK EMIR YOK, sadece kayit.
Kullanici sezgisi: "mumlarin ardindaki sey" = emir-defteri dengesizligi + gercek para/islem yonu.
Bunlari Binance depth-gecmisi vermedigi icin backtest edemedik -> canli toplamak TEK yol.

Her INTERVAL sn'de her coin icin kaydeder:
  - OBI (order book imbalance) 3 bantta: mid'in +-5/10/25 bps'indeki (bid_qty - ask_qty)/(toplam)
  - taker agresor akisi: son kapali 1m barin taker-buy vs taker-sell hacmi (para yonu, CVD-delta)
  - mid + spread (ileride t->t+dt forward-getiri etiketlemesi icin)

Haftalar sonra scripts/_orderflow_test.py: "OBI/akis kisa-vadeli hareketi ongoruyor mu?"
  -> WF + permutasyon + iki-yari. Fiyati URETEN mikroyapiyi yakalama denemesi (denenmemis tek aci).

Calistirma (surekli):  python scripts/orderflow_shadow.py
Tek-tur self-test:      python scripts/orderflow_shadow.py --once
"""
from __future__ import annotations

import json
import os
import sys
import time
import sqlite3
import urllib.request

# self-contained (bot core'a bagli degil) -> her yerden/detached calisir
REST = "https://fapi.binance.com"
_DATADIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
DB = os.path.join(_DATADIR, "orderflow_shadow.db")
COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT",
         "XRPUSDT", "AVAXUSDT", "LINKUSDT", "DOGEUSDT"]
INTERVAL = 60          # sn (dakika basi snapshot)
DEPTH_LIMIT = 100      # emir defteri seviye sayisi
BANDS_BPS = [5, 10, 25]  # OBI hesap bantlari


def _get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "ofshadow/1.0"})
    return json.loads(urllib.request.urlopen(req, timeout=8).read())


def init_db() -> sqlite3.Connection:
    os.makedirs(_DATADIR, exist_ok=True)
    c = sqlite3.connect(DB, timeout=30)
    c.execute("""
        CREATE TABLE IF NOT EXISTS of_snap (
            ts        INTEGER,   -- unix sn (snapshot ani)
            symbol    TEXT,
            mid       REAL,
            spread_bps REAL,
            obi5      REAL,      -- OBI +-5 bps  [-1,+1] (+ alis baskisi)
            obi10     REAL,      -- OBI +-10 bps
            obi25     REAL,      -- OBI +-25 bps
            bid_qty10 REAL,      -- +-10 bps bid hacmi
            ask_qty10 REAL,      -- +-10 bps ask hacmi
            tk_buy    REAL,      -- son 1m taker-BUY hacmi (agresor alis)
            tk_sell   REAL,      -- son 1m taker-SELL hacmi (agresor satis)
            cvd_delta REAL,      -- tk_buy - tk_sell (para yonu)
            PRIMARY KEY (ts, symbol)
        )""")
    c.commit()
    return c


def snap_depth(sym: str):
    """Emir defterinden OBI (3 bant) + mid + spread. -> dict veya None."""
    d = _get(f"{REST}/fapi/v1/depth?symbol={sym}&limit={DEPTH_LIMIT}")
    bids = [(float(p), float(q)) for p, q in d.get("bids", [])]
    asks = [(float(p), float(q)) for p, q in d.get("asks", [])]
    if not bids or not asks:
        return None
    best_bid, best_ask = bids[0][0], asks[0][0]
    mid = (best_bid + best_ask) / 2.0
    spread_bps = (best_ask - best_bid) / mid * 1e4
    out = {"mid": mid, "spread_bps": round(spread_bps, 3)}
    for b in BANDS_BPS:
        lo = mid * (1 - b / 1e4)
        hi = mid * (1 + b / 1e4)
        bq = sum(q for p, q in bids if p >= lo)
        aq = sum(q for p, q in asks if p <= hi)
        tot = bq + aq
        obi = (bq - aq) / tot if tot > 0 else 0.0
        out[f"obi{b}"] = round(obi, 4)
        if b == 10:
            out["bid_qty10"] = round(bq, 3)
            out["ask_qty10"] = round(aq, 3)
    return out


def snap_flow(sym: str):
    """Son KAPALI 1m barin taker agresor akisi (klines field 9 = taker-buy base hacmi)."""
    r = _get(f"{REST}/fapi/v1/klines?symbol={sym}&interval=1m&limit=2")
    if len(r) < 2:
        return None
    bar = r[-2]  # son kapali bar
    vol = float(bar[5])          # toplam base hacim
    tk_buy = float(bar[9])       # taker-buy base hacim
    tk_sell = vol - tk_buy
    return {"tk_buy": round(tk_buy, 3), "tk_sell": round(tk_sell, 3),
            "cvd_delta": round(tk_buy - tk_sell, 3)}


def collect_once(c: sqlite3.Connection, verbose: bool = False) -> int:
    ts = int(time.time())
    n = 0
    for sym in COINS:
        try:
            dp = snap_depth(sym)
            fl = snap_flow(sym)
            if not dp or not fl:
                continue
            c.execute(
                "INSERT OR REPLACE INTO of_snap "
                "(ts,symbol,mid,spread_bps,obi5,obi10,obi25,bid_qty10,ask_qty10,"
                "tk_buy,tk_sell,cvd_delta) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (ts, sym, dp["mid"], dp["spread_bps"], dp["obi5"], dp["obi10"],
                 dp["obi25"], dp["bid_qty10"], dp["ask_qty10"],
                 fl["tk_buy"], fl["tk_sell"], fl["cvd_delta"]))
            n += 1
            if verbose:
                print("  %-8s mid=%.4g spread=%.2fbps OBI(5/10/25)=%+.2f/%+.2f/%+.2f "
                      "CVD1m=%+.2f" % (sym, dp["mid"], dp["spread_bps"],
                                       dp["obi5"], dp["obi10"], dp["obi25"],
                                       fl["cvd_delta"]))
        except Exception as e:
            if verbose:
                print("  %-8s HATA: %s" % (sym, e))
            continue
    c.commit()
    return n


def main():
    once = "--once" in sys.argv
    c = init_db()
    if once:
        print("=== order-flow shadow TEK-TUR self-test ===")
        n = collect_once(c, verbose=True)
        tot = c.execute("SELECT COUNT(*) FROM of_snap").fetchone()[0]
        print("  -> %d coin kaydedildi. DB toplam satir: %d (%s)" % (n, tot, DB))
        return
    print("order-flow shadow BASLADI: %d coin, her %ds, DB=%s" % (len(COINS), INTERVAL, DB))
    while True:
        t0 = time.time()
        try:
            collect_once(c)
        except Exception as e:
            print("dongu hatasi:", e)
        # dakika basina hizala
        time.sleep(max(1.0, INTERVAL - (time.time() - t0)))


if __name__ == "__main__":
    main()
