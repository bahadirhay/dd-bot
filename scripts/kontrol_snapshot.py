"""scripts/kontrol_snapshot.py - KONTROL NOKTASI anlik-durum dokumu (buydip canli + short-rip + filtre).
30-Ekim kontrol icin kuruldu (kullanici istegi): QUSDT acik + short-rip ilk kapanislari kacmasin.
Windows gorevi (GBot_Kontrol) ile calisir -> reports/KONTROL_<tarih>.md yazar. Read-only, emir YOK.
Elle de calistirilabilir: python scripts/kontrol_snapshot.py
"""
from __future__ import annotations
import sqlite3, time, json, urllib.request, os, datetime as dt

BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOLD_DAYS = 10
MKT_TREND_N = 10

def g(u, t=15):
    return json.loads(urllib.request.urlopen(u, timeout=t).read())

def _px(sym):
    try: return float(g(f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}")["price"])
    except Exception: return None

def _btc10():
    try:
        r = g(f"https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=1d&limit={MKT_TREND_N+3}")
        cl = [float(x[4]) for x in r]
        return (cl[-1] - cl[-1-MKT_TREND_N]) / cl[-1-MKT_TREND_N] * 100
    except Exception: return None

def main():
    L = []
    now = time.time()
    L.append(f"# KONTROL NOKTASI {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}\n")

    # buydip canli
    L.append("## buydip CANLI\n")
    try:
        c = sqlite3.connect(os.path.join(BOT, "data", "buydip_live.db"))
        cl = c.execute("SELECT COUNT(*),COALESCE(SUM(pnl_usdt),0),SUM(CASE WHEN pnl_usdt>0 THEN 1 ELSE 0 END) "
                       "FROM buydip_live WHERE status='CLOSED'").fetchone()
        wr = (100*cl[2]/cl[0]) if cl[0] else 0
        L.append(f"- KAPANAN: n={cl[0]} | gerceklesen ${cl[1]:+.2f} | kazanan {cl[2]}/{cl[0]} (%{wr:.0f})")
        L.append("- ACIK pozisyonlar:")
        tot = 0.0
        for r in c.execute("SELECT symbol,open_ts,open_human,entry,qty,drop3 FROM buydip_live WHERE status='OPEN'").fetchall():
            sym, ots, oh, entry, qty, d3 = r
            px = _px(sym)
            if px is None: L.append(f"    - {sym}: fiyat alinamadi"); continue
            pnl = (px-entry)*qty; pct = (px-entry)/entry*100; kalan = (ots+HOLD_DAYS*86400-now)/3600
            tot += pnl
            L.append(f"    - {sym}: giris {entry:.5g} (dip{d3:+.0f}%) simdi {px:.5g} {pct:+.1f}% = ${pnl:+.2f} | kapanmaya {kalan:.0f}s")
        L.append(f"  - ACIK toplam gerceklesmemis: ${tot:+.2f}")
    except Exception as e:
        L.append(f"- HATA: {e}")

    # filtre
    b = _btc10()
    L.append(f"\n## PIYASA / FILTRE\n- BTC 10g = {b:+.1f}% -> yeni giris " + ("ACIK" if (b or 0) >= 0 else "KAPALI (dusen piyasa)") if b is not None else "- BTC trend alinamadi")

    # short-rip + filtresiz shadow (A/B)
    L.append("\n## SHADOW (paper)\n")
    try:
        cs = sqlite3.connect(os.path.join(BOT, "data", "buydip_shadow.db"))
        op = cs.execute("SELECT COUNT(*) FROM riprshort WHERE status='OPEN'").fetchone()[0]
        cld = cs.execute("SELECT COUNT(*),COALESCE(SUM(pnl_bps),0),SUM(CASE WHEN pnl_bps>0 THEN 1 ELSE 0 END) "
                         "FROM riprshort WHERE status='CLOSED'").fetchone()
        wr = (100*cld[2]/cld[0]) if cld[0] else 0
        L.append(f"- short-rip (riprshort): {op} acik, {cld[0]} kapali | toplam {cld[1]:+.0f}bps | win %{wr:.0f}")
        for r in cs.execute("SELECT symbol,open_human,rip3,pnl_bps,status FROM riprshort ORDER BY open_ts DESC LIMIT 10").fetchall():
            tail = f"-> {r[3]:+.0f}bps" if r[3] is not None else "ACIK"
            L.append(f"    - {r[0]} {r[1]} ralli{r[2]:+.0f}% {tail}")
        bs = cs.execute("SELECT COUNT(*),AVG(pnl_bps),SUM(CASE WHEN pnl_bps>0 THEN 1 ELSE 0 END) "
                        "FROM buydip WHERE status='CLOSED'").fetchone()
        if bs[0]:
            L.append(f"- buydip FILTRESIZ shadow (A/B): n={bs[0]} avg={bs[1]:+.0f}bps win=%{100*bs[2]/bs[0]:.0f}")
    except Exception as e:
        L.append(f"- HATA: {e}")

    out = os.path.join(BOT, "reports", f"KONTROL_{dt.datetime.now().strftime('%Y-%m-%d')}.md")
    txt = "\n".join(L) + "\n"
    with open(out, "w", encoding="utf-8") as f:
        f.write(txt)
    print(txt)
    print(f"\n-> yazildi: {out}")

if __name__ == "__main__":
    main()
