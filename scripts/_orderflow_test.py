"""scripts/_orderflow_test.py — ORDER-FLOW edge FORWARD testi (KURALLAR DONDURULDU 2026-09-13).

AMAC: orderflow_shadow.db'de biriken OBI + taker-akis, kisa-vadeli fiyat hareketini NET
(maliyet-dahil) ONGORUYOR MU? Bunu, kendimizi bir daha kandirmayacak sekilde test etmek.

=== NEDEN BU DOSYA SIMDI YAZILDI (dondurma) ===
Aylardir hatamiz: once test edip sonra "iyi gorunen" esigi/pencereyi secmek (overfit). Backtest
ve dar-pencere forward bizi surekli sahte-pozitif ile kandirdi (G +9075->canli -48; dumpfade
+28064->forward -4000). BU SEFER kurallari VERI OLGUNLASMADAN once yaziyoruz ki 15-Ekim'de
sonuca gore esnetemeyelim. Asagidaki sabitler ve GECME-KURALI DEGISTIRILEMEZ.

=== DONDURULMUS GECME-KURALI (hepsi ayni anda saglanmali) ===
Bir (coin, ozellik, ufuk, yon) hipotezi ancak sunlarin HEPSINDE gecerse "edge adayi"dir:
  1. NET beklenti > 0     : maliyet-dahil (spread + fee) ortalama islem-getirisi pozitif.
  2. IKI-YARI TUTARLI     : zaman ilk-yari ve ikinci-yari NET beklenti AYNI ISARET (+).
  3. PERMUTASYON p < 0.05  : sinyal-getiri iliskisi karistirilmis-etikete gore anlamli.
  4. FDR (Benjamini-Hochberg) sonrasi p hala < 0.05 : coklu-hipotez duzeltmesi
     (bant x coin x ufuk x yon = onlarca test; biri sansen gecer, FDR bunu keser).
CANLIYA GECIS: yalniz bu 4 kurali gecen + BAGIMSIZ tekrarlanan (per-coin degil coin-KESITINDE
tutarli) bir sinyal icin, once kucuk maker-shadow fill-rate ile, sonra kullanici onayiyla.
HICBIRI gecmezse: order-flow da olu -> canli yon-yapisi YOK, sermaye korunur.

=== SABITLER (DONDURULDU) ===
"""
import sqlite3
import random
import statistics
import bisect
import numpy as np

DB = "file:data/orderflow_shadow.db?mode=ro"
HORIZONS = [1, 3, 5, 10]        # dk ileri getiri ufuklari
FEATURES = ["obi5", "obi10", "obi25", "cvd_delta"]
COST_BPS = 7.0                  # muhafazakar round-trip: ~ort spread + maker fee (net icin dusulur)
Q = 0.20                        # sinyal esigi: ozellik dagiliminin ust/alt %20'si -> long/short
PERM_N = 2000                   # permutasyon tekrari
MIN_EVENTS = 200                # bir hipotezin degerlendirilebilmesi icin min bagimsiz olay
SEED = 20260913
random.seed(SEED)

# BTC/ETH gecmis verisinde DEPTH_LIMIT=100 iken obi10/obi25 bant-cokmesi vardi (2026-09-13 duzeltildi
# depth=1000). Test, her (coin,ozellik) icin cokme oranini olcup >%20 ise o ozelligi ATLAR (kirli veri).


def _load(c, sym):
    return c.execute(
        "SELECT ts,mid,spread_bps,obi5,obi10,obi25,cvd_delta FROM of_snap "
        "WHERE symbol=? ORDER BY ts", (sym,)).fetchall()


def _rankdata(a):
    """Ortalama-rank (baglar icin), numpy."""
    a = np.asarray(a, dtype=float)
    order = a.argsort(kind="mergesort")
    ranks = np.empty(len(a), dtype=float)
    sa = a[order]
    i = 0
    n = len(a)
    while i < n:
        j = i
        while j + 1 < n and sa[j + 1] == sa[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0
        i = j + 1
    return ranks


def _centered_unit(r):
    """Rank vektorunu ortala + birim-norma indirge -> Spearman = nokta-carpim."""
    rc = r - r.mean()
    nrm = np.sqrt((rc * rc).sum())
    return rc / nrm if nrm > 0 else rc


def _spearman(a, b):
    return float(_centered_unit(_rankdata(a)).dot(_centered_unit(_rankdata(b))))


def _perm_p(sig, ret, obs):
    """Karistirilmis-etiket permutasyon (VEKTORIZE): rank'lar SABIT, sadece ret-rank karistirilir.
    Spearman = centered-unit nokta-carpimi -> her permutasyon O(n) tek matris carpimi."""
    su = _centered_unit(_rankdata(sig))       # sabit
    ru = _centered_unit(_rankdata(ret))       # karistirilacak
    rng = np.random.default_rng(SEED)
    n = len(su)
    cnt = 1
    obs = abs(obs)
    # blok-blok permutasyon (bellek dengesi)
    done = 0
    while done < PERM_N:
        b = min(500, PERM_N - done)
        M = np.array([rng.permutation(ru) for _ in range(b)])   # b x n
        rhos = np.abs(M.dot(su))                                 # b
        cnt += int((rhos >= obs).sum())
        done += b
    return cnt / (PERM_N + 1)


def _bh_fdr(pairs, alpha=0.05):
    """Benjamini-Hochberg: [(key,p),...] -> gecen key kumesi."""
    m = len(pairs)
    if not m:
        return set()
    ordered = sorted(pairs, key=lambda kp: kp[1])
    passed = set()
    for i, (k, p) in enumerate(ordered, start=1):
        if p <= alpha * i / m:
            passed = set(kk for kk, _ in ordered[:i])
    return passed


def evaluate():
    c = sqlite3.connect(DB, uri=True)
    syms = [r[0] for r in c.execute("SELECT DISTINCT symbol FROM of_snap")]
    span = c.execute("SELECT MIN(ts),MAX(ts) FROM of_snap").fetchone()
    days = (span[1] - span[0]) / 86400
    print("=== ORDER-FLOW FORWARD TESTI (dondurulmus kurallar) ===")
    print("veri: %.1f gun, %d coin. maliyet=%.0fbps, esik=%.0f%%, permut=%d\n" % (
        days, len(syms), COST_BPS, Q * 100, PERM_N))
    if days < 25:
        print("!! UYARI: veri < 25 gun -> DUSUK GUC. Bu bir on-bakis; asil karar ~15-Ekim.\n")

    hypotheses = []   # (key, net, net_h1, net_h2, spearman, p, n)
    for sym in syms:
        rows = _load(c, sym)
        if len(rows) < MIN_EVENTS * 2:
            continue
        ts = [r[0] for r in rows]
        mid = [r[1] for r in rows]
        feat_idx = {"obi5": 3, "obi10": 4, "obi25": 5, "cvd_delta": 6}
        # ts->index (dk hizali degilse en-yakin-onceki)
        for feat in FEATURES:
            fi = feat_idx[feat]
            fvals_all = [r[fi] for r in rows]
            # kirli-veri kapisi: bant-cokme (obi10/obi25 obi5'e esitse) >%20 ise atla
            if feat in ("obi10", "obi25"):
                coll = sum(1 for r in rows if r[fi] == r[3]) / len(rows)
                if coll > 0.20:
                    print("  %-9s %-9s ATLANDI (bant-cokme %%%.0f, kirli gecmis veri)" % (sym, feat, coll * 100))
                    continue
            for H in HORIZONS:
                sig = []
                ret = []
                i = 0
                n = len(rows)
                while i < n:
                    # t+H dk sonraki mid (en-yakin-onceki bar, bosluk toleransi)
                    tgt = ts[i] + H * 60
                    j = bisect.bisect_right(ts, tgt) - 1
                    if j <= i or ts[j] - tgt > 120 or mid[i] <= 0:
                        i += H
                        continue
                    gross = (mid[j] - mid[i]) / mid[i] * 1e4
                    sig.append(fvals_all[i])
                    ret.append(gross)
                    i += H          # BAGIMSIZ olay: ufuk kadar atla (ortusme yok)
                if len(sig) < MIN_EVENTS:
                    continue
                # yonlu NET beklenti: ust-%Q long, alt-%Q short, maliyet dus
                ss = sorted(sig)
                hi = ss[int(len(ss) * (1 - Q))]
                lo = ss[int(len(ss) * Q)]
                trades = []
                thalf = ts[0] + (ts[-1] - ts[0]) / 2
                t_of = []
                # sig/ret ile ayni sirada olay-zamanlarini yakala
                # (yeniden gez: hizali kalmasi icin ayni dongu mantigi)
                k = 0
                i = 0
                while i < n and k < len(sig):
                    tgt = ts[i] + H * 60
                    j = bisect.bisect_right(ts, tgt) - 1
                    if j <= i or ts[j] - tgt > 120 or mid[i] <= 0:
                        i += H
                        continue
                    s = sig[k]
                    r_ = ret[k]
                    if s >= hi:
                        trades.append((ts[i], r_ - COST_BPS))
                    elif s <= lo:
                        trades.append((ts[i], -r_ - COST_BPS))
                    k += 1
                    i += H
                if len(trades) < MIN_EVENTS:
                    continue
                net = statistics.mean(t[1] for t in trades)
                h1 = [t[1] for t in trades if t[0] < thalf]
                h2 = [t[1] for t in trades if t[0] >= thalf]
                if len(h1) < 30 or len(h2) < 30:
                    continue
                n1 = statistics.mean(h1)
                n2 = statistics.mean(h2)
                sp = _spearman(sig, ret)
                p = _perm_p(sig, ret, sp)
                key = "%s|%s|H%d" % (sym, feat, H)
                hypotheses.append((key, net, n1, n2, sp, p, len(trades)))

    if not hypotheses:
        print("degerlendirilecek yeterli veri yok.")
        return
    # FDR (tum permutasyon p'leri uzerinden)
    fdr_pass = _bh_fdr([(h[0], h[5]) for h in hypotheses])
    print("\n%-22s %8s %8s %8s %7s %7s %6s %s" % (
        "hipotez", "NET", "1yari", "2yari", "rho", "p", "n", "VERDICT"))
    winners = []
    for key, net, n1, n2, sp, p, n in sorted(hypotheses, key=lambda x: x[5]):
        r1 = net > 0
        r2 = (n1 > 0 and n2 > 0)
        r3 = p < 0.05
        r4 = key in fdr_pass
        ok = r1 and r2 and r3 and r4
        flags = "".join(["N" if r1 else "-", "H" if r2 else "-",
                         "P" if r3 else "-", "F" if r4 else "-"])
        verdict = "*** GECTI ***" if ok else flags
        print("%-22s %+8.2f %+8.2f %+8.2f %+7.3f %7.4f %6d %s" % (
            key, net, n1, n2, sp, p, n, verdict))
        if ok:
            winners.append(key)
    print("\nGECME-KURALI: N=net+ H=iki-yari+ P=permut<.05 F=FDR-gecti (DORDU birden = GECTI)")
    if winners:
        print("GECEN adaylar (%d): %s" % (len(winners), ", ".join(winners)))
        print("-> SIRADAKI: coin-kesitinde tutarli mi? maker fill-rate shadow, sonra KULLANICI ONAYI.")
    else:
        print("GECEN YOK -> order-flow (bu veri/cozunurlukte) olu. Canli yon-yapisi ACMA, sermaye koru.")


if __name__ == "__main__":
    evaluate()
