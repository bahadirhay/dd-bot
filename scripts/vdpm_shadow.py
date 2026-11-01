"""scripts/vdpm_shadow.py - VDPM trend-beta LONG-ONLY paper-forward shadow (EMIR YOK).
Dogrulanmis sistem (bkz oturum 2026-11): 4h direnç-breakout + delta-oppose + BTC-rejim(10g up) + breadth(>=55%).
Backtest: +163bps/islem, Sharpe +4.0, maxDD -20k, permutasyon p=0.0000, slippage-dayanikli. AMA trend-beta
(bull'da kumelenir, ayida susar) + survivorship-supheli (tek-rejim ornek) -> FORWARD DOGRULAMA sart.
Bu modul GERCEK-ZAMAN, repaint'siz (pivot i+5'te onaylanir, breakout ancak sonra), gercek dolumla olcer.
HICBIR emir vermez; data/vdpm_shadow.db'ye paper kaydeder. buydip_shadow sablonu. start_all[2i]+watchdog.
"""
from __future__ import annotations
import json, time, sqlite3, os
import urllib.request
import datetime as dt

REST = "https://fapi.binance.com"
COINS = ["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT","AVAXUSDT","LINKUSDT","DOGEUSDT","ADAUSDT","LTCUSDT"]
LB = 5              # pivot swing (sag+sol) -> i+LB'de onaylanir
HOLD_BARS = 12      # 12 x 4h = 48h tutus
HOLD_SEC = HOLD_BARS * 4 * 3600
BRN = 30            # breadth lookback (4h bar): coin close>close[-30] mi
BR_MIN = 0.55       # breadth esigi (universe yukari-orani)
BTC_TREND_DAYS = 10 # BTC gunluk rejim
COST = 30.0         # bps net (fee ~10 + slippage ~20, breakout gercekci)
_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "vdpm_shadow.db")


def _get(u, t=15):
    return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "vdpm/1"}), timeout=t).read())

def _kl4h(sym, limit=150):
    try:
        r = _get(f"{REST}/fapi/v1/klines?symbol={sym}&interval=4h&limit={limit}")
        return [(int(x[0])//1000, float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5])) for x in r]
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
    c.execute("""CREATE TABLE IF NOT EXISTS vdpm (
        symbol TEXT, signal_ts INTEGER, signal_human TEXT, entry REAL, delta REAL, breadth REAL,
        hold_until INTEGER, close_ts INTEGER, exit REAL, pnl_bps REAL, status TEXT,
        PRIMARY KEY(symbol, signal_ts))""")
    c.commit(); return c


def _pivots(k):
    """Onayli pivot high'lar: (conf_bar_idx, level_px, delta). conf_bar = i+LB (repaint-guvenli)."""
    n = len(k); out = []
    for i in range(LB, n - LB):
        if not all(k[i][2] >= k[j][2] for j in range(i - LB, i + LB + 1)):
            continue
        d = 0.0
        for j in range(i - LB, i + LB + 1):
            rng = k[j][2] - k[j][3]
            d += k[j][5] * ((k[j][4] - k[j][3]) / rng * 2 - 1) if rng > 0 else (k[j][5] if k[j][4] >= k[j][1] else -k[j][5])
        out.append((i + LB, k[i][2], d))   # resistance level
    return out


def _btc_up():
    """BTC gunluk 10g trend up mu?"""
    k = _get(f"{REST}/fapi/v1/klines?symbol=BTCUSDT&interval=1d&limit={BTC_TREND_DAYS + 3}")
    cl = [float(x[4]) for x in k]
    if len(cl) < BTC_TREND_DAYS + 1 or cl[-1 - BTC_TREND_DAYS] <= 0:
        return None
    return cl[-1] > cl[-1 - BTC_TREND_DAYS]


def _breadth(series_by_ts, ts):
    d = series_by_ts.get(ts)
    return (sum(1 for v in d.values() if v) / len(d)) if d else None


def process():
    c = _db(); now = int(time.time())
    # 1) CIKIS: 48h dolan paper pozisyonlari kapat
    for sym, sts, entry, hu in c.execute(
            "SELECT symbol, signal_ts, entry, hold_until FROM vdpm WHERE status='OPEN'").fetchall():
        if now >= hu:
            px = _price(sym)
            if px <= 0:
                continue
            pnl = (px - entry) / entry * 1e4 - COST   # LONG net
            c.execute("UPDATE vdpm SET close_ts=?, exit=?, pnl_bps=?, status='CLOSED' WHERE symbol=? AND signal_ts=?",
                      (now, round(px, 8), round(pnl, 1), sym, sts))
    c.commit()

    # 2) GIRIS: rejim + breadth + yeni breakout-long sinyalleri
    bu = _btc_up()
    if bu is None or not bu:    # BTC-rejim yukari degil -> yeni LONG YOK (sadece sagliklı rejim)
        c.commit(); return
    kl = {}
    for s in COINS:
        k = _kl4h(s)
        if k and len(k) >= BRN + LB + 5:
            kl[s] = k
    # breadth serisi (ts -> {coin: up?})
    bts = {}
    for s, k in kl.items():
        for i in range(BRN, len(k)):
            bts.setdefault(k[i][0], {})[s] = k[i][4] > k[i - BRN][4]

    for s, k in kl.items():
        n = len(k)
        pvs = _pivots(k)
        # aktif direnç seviyeleri + ilk-kirilma tespiti (repaint'siz: breakout bar > conf_bar)
        for (cb, lvl, d) in pvs:
            if d <= 0:                     # delta-oppose: sadece BULLISH-delta direnç (gercek breakout)
                continue
            # cb'den sonra ilk 'close>=lvl' bari (ilk kirilma)
            for j in range(cb + 1, n):
                if k[j][4] >= lvl:
                    sts = k[j][0]
                    # cok eski sinyalleri atla (son ~5 gun) -> forward temiz
                    if now - sts > 5 * 86400:
                        break
                    br = _breadth(bts, sts)
                    if br is None or br < BR_MIN:
                        break
                    # kaydet (INSERT OR IGNORE -> dedup)
                    entry = k[j][4]
                    c.execute("INSERT OR IGNORE INTO vdpm VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                              (s, sts, dt.datetime.fromtimestamp(sts).strftime("%Y-%m-%d %H:%M"),
                               round(entry, 8), round(d, 1), round(br, 2),
                               sts + HOLD_SEC, None, None, None, "OPEN"))
                    break   # sadece ilk kirilma
    c.commit()


def _summary(c):
    op = c.execute("SELECT COUNT(*) FROM vdpm WHERE status='OPEN'").fetchone()[0]
    cl = c.execute("SELECT COUNT(*), COALESCE(AVG(pnl_bps),0), COALESCE(SUM(pnl_bps),0), "
                   "SUM(CASE WHEN pnl_bps>0 THEN 1 ELSE 0 END) FROM vdpm WHERE status='CLOSED'").fetchone()
    wr = (100 * cl[3] / cl[0]) if cl[0] else 0
    return op, cl[0], cl[1], cl[2], wr


if __name__ == "__main__":
    import sys
    c0 = _db()
    if "--status" in sys.argv:
        op, cn, avg, tot, wr = _summary(c0)
        print(f"vdpm_shadow (LONG-only trend-beta, PAPER): {op} acik, {cn} kapali | "
              f"ort {avg:+.0f}bps | toplam {tot:+.0f}bps | win %{wr:.0f} | net-maliyet {COST}bps")
        sys.exit(0)
    print("vdpm_shadow BASLADI (PAPER, EMIR YOK). 4h-breakout+delta+rejim+breadth LONG-only forward.")
    while True:
        t0 = time.time()
        try:
            process()
            op, cn, avg, tot, wr = _summary(c0)
            print(f"[{dt.datetime.now().strftime('%H:%M')}] {op} acik, {cn} kapali, ort {avg:+.0f}bps, win%{wr:.0f}")
        except Exception as e:
            print("dongu hatasi:", e)
        time.sleep(max(60.0, 1800 - (time.time() - t0)))   # ~30dk dongu
