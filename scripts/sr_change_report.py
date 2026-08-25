"""
scripts/sr_change_report.py — S/R aktif kenar degisim audit raporu.

Amac: gereksiz (px'e uzak, kucuk, savrulan) S/R degisimlerini bulmak.
Kullanim:
    python -m scripts.sr_change_report            # son 1000 degisim
    python -m scripts.sr_change_report --hours 6  # son 6 saat
"""
from __future__ import annotations

import argparse
import sqlite3
import time
from collections import Counter, defaultdict

from core.config import cfg


def _rows(hours: float, limit: int) -> list[dict]:
    c = sqlite3.connect(cfg.DB_PATH)
    c.row_factory = sqlite3.Row
    if hours > 0:
        cutoff = time.time() - hours * 3600
        q = "SELECT * FROM sr_changes WHERE ts > ? ORDER BY ts ASC"
        rows = c.execute(q, (cutoff,)).fetchall()
    else:
        q = "SELECT * FROM (SELECT * FROM sr_changes ORDER BY ts DESC LIMIT ?) ORDER BY ts ASC"
        rows = c.execute(q, (limit,)).fetchall()
    return [dict(r) for r in rows]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=0.0)
    ap.add_argument("--limit", type=int, default=1000)
    ap.add_argument("--far-pt", type=float, default=8.0,
                    help="px'e bu mesafeden uzak degisim = supheli")
    ap.add_argument("--tiny-pt", type=float, default=2.0,
                    help="bu pt altindaki degisim = kucuk/gureksiz aday")
    args = ap.parse_args()

    rows = _rows(args.hours, args.limit)
    if not rows:
        print("sr_changes bos. Bot calistikca veri birikir.")
        return

    n = len(rows)
    touched = sum(1 for r in rows if r.get("touched"))
    untouched = n - touched

    reason_cnt: Counter = Counter()
    tiny = 0
    far = 0
    # flicker: ayni kenar deger A->B->A geri donus
    s_seq = [r["new_support"] for r in rows if r.get("new_support")]
    r_seq = [r["new_resistance"] for r in rows if r.get("new_resistance")]

    def flicker_count(seq: list[float]) -> int:
        f = 0
        for i in range(2, len(seq)):
            if seq[i] is not None and seq[i - 2] is not None:
                if abs(seq[i] - seq[i - 2]) < 0.05 and abs(seq[i] - seq[i - 1]) > 0.05:
                    f += 1
        return f

    # en cok oynayan seviyeler (yuvarlanmis)
    level_hits: Counter = Counter()
    for r in rows:
        for rs, key in ((r.get("support_reason"), "S"), (r.get("resistance_reason"), "R")):
            if rs:
                reason_cnt[f"{key}:{rs}"] += 1
        for d in (r.get("d_support"), r.get("d_resistance")):
            if d is not None:
                if d < args.tiny_pt:
                    tiny += 1
        ds, dr = r.get("dist_support_pt"), r.get("dist_resistance_pt")
        chg_dists = []
        if r.get("d_support") is not None and ds is not None:
            chg_dists.append(ds)
        if r.get("d_resistance") is not None and dr is not None:
            chg_dists.append(dr)
        if chg_dists and min(chg_dists) > args.far_pt:
            far += 1
        for lv in (r.get("new_support"), r.get("new_resistance")):
            if lv:
                level_hits[round(lv)] += 1

    span_h = (rows[-1]["ts"] - rows[0]["ts"]) / 3600 if n > 1 else 0.0
    print(f"=== S/R DEGISIM RAPORU  (n={n}, {span_h:.1f} saat) ===")
    print(f"degisim/saat       : {n / span_h:.1f}" if span_h > 0 else "degisim/saat: -")
    print(f"dokunmali (touched): {touched} (%{touched / n * 100:.0f})")
    print(f"dokunmasiz         : {untouched} (%{untouched / n * 100:.0f})  <- supheli")
    print(f"kucuk (<{args.tiny_pt}pt)      : {tiny}")
    print(f"uzak  (>{args.far_pt}pt)      : {far}  <- px'ten uzakta degisen kenar")
    print(f"flicker S (A->B->A): {flicker_count(s_seq)}")
    print(f"flicker R (A->B->A): {flicker_count(r_seq)}")
    print("\n--- sebep dagilimi ---")
    for k, v in reason_cnt.most_common(15):
        print(f"  {v:4d}  {k}")
    print("\n--- en cok oynayan seviyeler (yuvarlanmis fiyat) ---")
    for lv, v in level_hits.most_common(10):
        print(f"  {v:4d}  ~{lv}")


if __name__ == "__main__":
    main()
