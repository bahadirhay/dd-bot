"""Maker deneyi durum raporu — tek komut.
Kullanim: python scripts/maker_status.py
Karar kurali: gercek fill >= %90 -> kalici | < %85 -> V3_D_MAKER_ENTRY=false ile geri al
"""
import sqlite3
import statistics

DB = 'data/bot.db'
c = sqlite3.connect('file:%s?mode=ro' % DB, uri=True)
c.row_factory = sqlite3.Row

rows = list(c.execute("SELECT * FROM maker_fill ORDER BY id"))
n = len(rows)
print('=' * 62)
print('  MAKER DENEYI — GERCEK DOLUM OLCUMU')
print('=' * 62)
if n == 0:
    print('\n  Henuz kayit yok. Ilk D sinyalinde limit konacak.')
    print('  (D ~1.9 islem/gun -> 10 sinyal ~5 gun, 20-30 sinyal ~10-15 gun)')
else:
    filled = [r for r in rows if r['filled']]
    missed = [r for r in rows if not r['filled']]
    rate = len(filled) / n * 100
    print('\n  Sinyal      : %d' % n)
    print('  DOLDU       : %d' % len(filled))
    print('  KACTI       : %d' % len(missed))
    print('  FILL ORANI  : %%%.1f' % rate)
    if filled:
        w = [r['waited_sec'] / 60 for r in filled]
        print('  ort dolum suresi: %.1f dk (medyan %.1f)' % (statistics.mean(w), statistics.median(w)))
    print('\n  --- KARAR ---')
    if n < 10:
        print('  ORNEKLEM YETERSIZ (%d/10) — olcmeye devam' % n)
    elif rate >= 90:
        print('  fill %%%.1f >= %%90 -> MAKER KALICI YAP' % rate)
    elif rate < 85:
        print('  fill %%%.1f < %%85 -> GERI AL (V3_D_MAKER_ENTRY=false)' % rate)
        print('  (backtest: bu bolgede TRAIN negatife doner)')
    else:
        print('  fill %%%.1f — ARADA (85-90), olcmeye devam' % rate)
    if n >= 10:
        print('\n  not: karar icin 20-30 sinyal hedeflendi (su an %d)' % n)
    print('\n  son kayitlar:')
    for r in rows[-8:]:
        st = 'DOLDU @%.2f' % r['fill_px'] if r['filled'] else 'KACTI'
        print('    %s %-5s limit=%8.2f  %-16s (%.0f dk)' % (
            r['ts_human'], r['direction'], r['limit_px'], st, (r['waited_sec'] or 0) / 60))
print('=' * 62)
