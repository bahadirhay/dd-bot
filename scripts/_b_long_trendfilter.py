"""GERCEK canli LONG islemlerine SMA-align filtresini uygula (dogrulanmis filtre, gercek islem kumesi).
Soru: entry ANINDA px>SMA120 (uptrend-hizali) vs px<SMA120 (counter-trend) longlar nasil ayrisiyor?
Counter-trend longlar net-negatif + hizali longlar net-pozitif ise -> B/V3 longlarina da SMA-align ekle.
DURUST SINIR: tek donem (bir dusus trendi), n kucuk, post-hoc. Onerir, KANITLAMAZ (D icin coklu-pencere
zaten dogrulandi; bu onun B islem kumesine yansimasi)."""
import sqlite3, urllib.request, json, time, datetime as dt

SMA_LEN = 120  # 15m bar (canli D ile ayni)

def klines_range(sym, start_ms, end_ms):
    out = {}; end = end_ms
    while end > start_ms:
        u = ("https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=15m&limit=1500&endTime=%d" % (sym, end))
        try: r = json.loads(urllib.request.urlopen(u, timeout=20).read())
        except Exception: break
        if not r: break
        for x in r:
            out[int(x[0])] = float(x[4])  # close
        end = r[0][0] - 1
        if len(r) < 1500 or r[0][0] <= start_ms: break
    return out  # {open_ms: close}

def main():
    c = sqlite3.connect('file:data/bot.db?mode=ro', uri=True)
    rows = c.execute(
        "select open_ts, entry_price, pnl, notes from trades "
        "where direction='LONG' and pnl is not null and entry_price>0 and open_ts is not null"
    ).fetchall()
    # gercek canli islemler: phantom/restore/paper disla (pnl=0 & etiket)
    real = []
    for ts, ent, pnl, notes in rows:
        nt = (notes or '').lower()
        if any(k in nt for k in ('orphan', 'restored', 'paper', 'superseded')):
            continue
        real.append((ts, ent, pnl))
    if not real:
        print("gercek long yok"); return
    t0 = min(r[0] for r in real); t1 = max(r[0] for r in real)
    print("gercek canli LONG: %d islem  (%s -> %s)" % (
        len(real), dt.datetime.fromtimestamp(t0).strftime('%m-%d'), dt.datetime.fromtimestamp(t1).strftime('%m-%d')))
    # klines: SMA120 icin t0'dan ~2 gun oncesinden basla
    kl = klines_range("ETHUSDT", int((t0 - SMA_LEN * 900 - 86400) * 1000), int((t1 + 900) * 1000))
    ks = sorted(kl); closes = [kl[k] for k in ks]
    # her bar icin SMA120
    sma = {}
    for i in range(SMA_LEN, len(ks)):
        sma[ks[i]] = sum(closes[i - SMA_LEN:i]) / SMA_LEN
    import bisect
    def sma_at(ts_ms):
        p = bisect.bisect_right(ks, ts_ms) - 1
        while p >= SMA_LEN:
            if ks[p] in sma: return sma[ks[p]]
            p -= 1
        return None

    aligned, counter, nofilt = [], [], 0
    for ts, ent, pnl in real:
        s = sma_at(int(ts * 1000))
        if s is None:
            nofilt += 1; continue
        (aligned if ent > s else counter).append(pnl)

    def rep(name, v):
        if not v: print("  %-28s n=0" % name); return
        print("  %-28s n=%3d net=%+.3f USDT win=%d%% ort=%+.4f" % (
            name, len(v), sum(v), 100 * sum(1 for x in v if x > 0) // len(v), sum(v) / len(v)))

    print("\n=== SMA-align filtresi GERCEK longlarda (SMA%d, 15m) ===" % SMA_LEN)
    rep("HIZALI  (entry > SMA -> gecer)", aligned)
    rep("COUNTER (entry < SMA -> BLOK)", counter)
    print("  (SMA verisi yok: %d islem)" % nofilt)
    if aligned and counter:
        print("\n  YORUM:")
        print("  - Filtre uygulansaydi: COUNTER (%d islem, %+.3f USDT) ELENIRDI." % (len(counter), sum(counter)))
        print("  - Kalan (HIZALI): %d islem, %+.3f USDT." % (len(aligned), sum(aligned)))
        tot = sum(aligned) + sum(counter)
        print("  - Toplam simdi %+.3f -> filtreyle %+.3f (%+.3f iyilesme)" % (tot, sum(aligned), -sum(counter)))

if __name__ == "__main__":
    main()
