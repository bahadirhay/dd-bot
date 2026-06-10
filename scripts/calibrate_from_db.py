"""
scripts/calibrate_from_db.py — DB'den kalibrasyon/backtest (offline, canlı bota dokunmaz).

Kayıtlı kararları (v3_attribution) sonraki fiyat hareketiyle (market_snapshots)
etiketler ve şunları raporlar:
  • RR kovalarına göre gerçek kazanç oranı (geometri edge'i gerçek mi?)
  • prob (pS/pL) kalibrasyonu: skor %X derken gerçekte %? kazanıyor
  • trend-hizalı vs ters setup performansı

Kullanım:  python scripts/calibrate_from_db.py [saat]   (varsayılan 48h, fwd=30dk)
"""
import sqlite3
import re
import sys
import time
import bisect

DB = "data/bot.db"
HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 48.0
FWD_MIN = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0
WIN_THR = 0.10  # lehte sayılması için min % hareket


def main():
    c = sqlite3.connect(DB)
    cur = c.cursor()
    ser = cur.execute(
        "select ts, price from market_snapshots where price>0 order by ts"
    ).fetchall()
    ts_arr = [r[0] for r in ser]
    px_arr = [r[1] for r in ser]

    def fwd(ts, mins):
        j = bisect.bisect_left(ts_arr, ts + mins * 60)
        return px_arr[j] if j < len(px_arr) else None

    rows = cur.execute(
        "select ts, price, reason_text, reject_reason, entered, action "
        "from v3_attribution where ts>=?",
        (time.time() - HOURS * 3600,),
    ).fetchall()
    c.close()

    recs = []  # (side, rr, prob, move_in_favor, rej, entered)
    for ts, px, rt, rej, ent, act in rows:
        rt = rt or ""
        side = "SHORT" if "SHORT" in rt else ("LONG" if "LONG" in rt else "")
        if not side:
            continue
        mR = re.search(r"RR=([0-9.]+)", rt)
        mP = re.search(r"p%s=([0-9.]+)%%" % ("S" if side == "SHORT" else "L"), rt)
        if not mR:
            continue
        rr = float(mR.group(1))
        prob = float(mP.group(1)) if mP else 0.0
        f = fwd(ts, FWD_MIN)
        if not f or px <= 0:
            continue
        move = (px - f) / px * 100 if side == "SHORT" else (f - px) / px * 100
        recs.append((side, rr, prob, move, rej or "", int(ent or 0)))

    print(f"=== Kalibrasyon: son {HOURS:.0f}h, ileri-bakis {FWD_MIN:.0f}dk, n={len(recs)} ===\n")

    def report(label, sub):
        if not sub:
            return
        w = sum(1 for r in sub if r[3] > WIN_THR)
        avg = sum(r[3] for r in sub) / len(sub)
        print(f"{label:<34} n={len(sub):>4}  kazanc={100*w/len(sub):>3.0f}%  ort={avg:+.2f}%")

    for side in ("SHORT", "LONG"):
        ss = [r for r in recs if r[0] == side]
        print(f"--- {side} ---")
        report("tum tezler", ss)
        report("RR>=2.0", [r for r in ss if r[1] >= 2.0])
        report("RR>=2.5", [r for r in ss if r[1] >= 2.5])
        for lo, hi in [(40, 48), (48, 52), (52, 56), (56, 60), (60, 100)]:
            report(f"  prob {lo}-{hi}% (RR>=2)",
                   [r for r in ss if r[1] >= 2.0 and lo <= r[2] < hi])
        print()


if __name__ == "__main__":
    main()
