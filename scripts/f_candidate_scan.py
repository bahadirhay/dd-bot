"""scripts/f_candidate_scan.py — F (gunluk TSM trend-takip) ADAY KESIF TARAMASI.

G'nin aylik funding-taramasinin F-karsiligi: TUM likit perp'lerde gunluk-TSM (N=40 flip-only) walk-forward.
Saglam standardi (G ile ayni mantik): iki-yari-pozitif Sharpe + permutasyon p<0.05 + likidite>=$10M.
Permutasyon: gunluk getirileri karistir, TSM Sharpe'i yeniden hesapla -> gercek Sharpe sans-ustunde mi
(trend-persistansi gercek mi). Sonuc: reports/f_scan_YYYY-MM-DD.txt + yeni saglam aday. EMIR YOK.
"""
import os
import json
import time
import math
import random
import urllib.request
import datetime as dt

random.seed(13)
REST = "https://fapi.binance.com"
N = 40
NPERM = 500
MIN_VOL_M = 10.0
# mevcut F basket + G canli (kiyas icin isaretlenir)
F_BASKET = {"BNB", "LINK", "ADA", "LTC", "INJ", "ETH"}
G_LIVE = {"ETH", "AVAX", "XRP", "ETC"}


def _get(u):
    return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "fscan/1.0"}), timeout=20).read())


def build_universe():
    t = _get(f"{REST}/fapi/v1/ticker/24hr")
    out = []
    for x in t:
        s = x.get("symbol", "")
        if not s.endswith("USDT"):
            continue
        try:
            vol_m = float(x.get("quoteVolume", 0)) / 1e6
        except Exception:
            continue
        if vol_m >= MIN_VOL_M:
            out.append((s, vol_m))
    return sorted(out, key=lambda z: -z[1])


def kd(sym, limit=450):
    try:
        r = _get(f"{REST}/fapi/v1/klines?symbol={sym}&interval=1d&limit={limit}")
    except Exception:
        return None
    return [float(x[4]) for x in r]


def tsm_rets(cl):
    """flip-only TSM gunluk getiri serisi + flip sayisi."""
    rets = []
    flips = 0
    pos = 0
    for t in range(N, len(cl) - 1):
        side = 1 if cl[t] > cl[t - N] else -1
        if side != pos and pos != 0:
            flips += 1
        pos = side
        rets.append(side * (cl[t + 1] / cl[t] - 1))
    return rets, flips


def sharpe(a):
    if len(a) < 10:
        return 0.0
    m = sum(a) / len(a)
    sd = (sum((x - m) ** 2 for x in a) / len(a)) ** 0.5
    return m / sd * math.sqrt(365) if sd > 0 else 0.0


def main():
    uni = build_universe()
    lines = []
    def P(s):
        print(s); lines.append(s)
    P("=== F ADAY KESIF (gunluk TSM N=%d, flip-only) | %s | %d likit coin (>=$%.0fM) ===" %
      (N, dt.datetime.now().strftime("%Y-%m-%d %H:%M"), len(uni), MIN_VOL_M))
    P("  saglam = iki-yari-Sharpe>0 + permut p<0.05 + Sharpe>0.5 + likit. Permut: getiri-karistir.")
    P("")
    robust = []
    weak = []
    for sym, vol in uni:
        cl = kd(sym)
        if not cl or len(cl) < N + 80:
            continue
        rets, flips = tsm_rets(cl)
        if len(rets) < 60:
            continue
        sh = sharpe(rets)
        h = len(rets) // 2
        s1, s2 = sharpe(rets[:h]), sharpe(rets[h:])
        # permutasyon: getirileri karistir, TSM'i ayni close-serisinden DEGIL, getiri-serisinden yeniden kur
        # (trend-persistansi testi): shuffled getiriyle kumulatif seri -> TSM Sharpe
        obs = sh
        ge = 0
        base = rets[:]
        for _ in range(NPERM):
            sh_r = base[:]
            random.shuffle(sh_r)
            # shuffled getiriden fiyat serisi kur, TSM uygula
            px = [1.0]
            for r in sh_r:
                px.append(px[-1] * (1 + r))
            pr, _ = tsm_rets(px)
            if sharpe(pr) >= obs:
                ge += 1
        p = ge / NPERM
        name = sym.replace("USDT", "")
        mark = "[BASKET]" if name in F_BASKET else ("[G-canli]" if name in G_LIVE else "[YENI]")
        row = (name, len(rets), sh, s1, s2, p, flips, vol, mark)
        if s1 > 0 and s2 > 0 and sh > 0.5 and p < 0.05:
            robust.append(row)
        elif p < 0.05 and sh > 0.3:
            weak.append(row)
    robust.sort(key=lambda z: -z[2])
    P("=== SAGLAM (iki-yari+ Sharpe>0.5 permut p<0.05 likit) ===")
    for (nm, n, sh, s1, s2, p, fl, vol, mark) in robust:
        P("  %-8s Sh=%+.2f 1y=%+.2f 2y=%+.2f p=%.3f flip=%d hac=%5.0fM %s" % (nm, sh, s1, s2, p, fl, vol, mark))
    yeni = [r for r in robust if r[8] == "[YENI]"]
    P("")
    P("  >>> YENI SAGLAM ADAY (basket/canli disi) -> ftsm_shadow'a ekle, forward tut: %s" %
      (", ".join(r[0] for r in yeni) if yeni else "YOK"))
    P("")
    P("=== zayif-gecen (p<0.05 ama Sharpe/iki-yari zayif -> tuzak) ===")
    for (nm, n, sh, s1, s2, p, fl, vol, mark) in weak[:12]:
        P("  %-8s Sh=%+.2f 1y=%+.2f 2y=%+.2f p=%.3f %s" % (nm, sh, s1, s2, p, mark))
    P("")
    P("  NOT: kripto korele + kisa ornek (~1.2yil). YENI cikarsa once ftsm_shadow (paper) forward, sonra karar.")
    os.makedirs("reports", exist_ok=True)
    fn = os.path.join("reports", "f_scan_%s.txt" % dt.date.today().isoformat())
    with open(fn, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n-> rapor: %s" % fn)


if __name__ == "__main__":
    main()
