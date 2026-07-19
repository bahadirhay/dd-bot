"""OBI shadow durum + analiz — tek komut.
Kullanim: python scripts/obi_status.py
Soru: OBI D-yonune KARSI (aligned=0) islemler HIZALI (aligned=1) olanlardan daha mi kotu?
Eger evetse -> OBI-filtresi ("karsi ise pas gec") D'yi iyilestirebilir.
"""
import sqlite3
import statistics

c = sqlite3.connect('file:data/bot.db?mode=ro', uri=True)
c.row_factory = sqlite3.Row
rows = list(c.execute("SELECT * FROM obi_paper WHERE status='CLOSED' AND pnl_bps IS NOT NULL"))
op = list(c.execute("SELECT * FROM obi_paper WHERE status='OPEN'"))

print('=' * 60)
print('  OBI MIKRO-YAPI SHADOW')
print('=' * 60)
print('  kapali islem: %d   | acik: %d' % (len(rows), len(op)))
if len(rows) < 8:
    print('\n  ORNEKLEM YETERSIZ (%d/8+). D ~2 sinyal/gun -> ~1-2 hafta.' % len(rows))
else:
    al = [r['pnl_bps'] for r in rows if r['aligned'] == 1]
    ct = [r['pnl_bps'] for r in rows if r['aligned'] == 0]
    tot = [r['pnl_bps'] for r in rows]
    print('\n  TUMU        : ort %+6.1f bps  (n=%d)' % (statistics.mean(tot), len(tot)))
    if al:
        print('  OBI-HIZALI  : ort %+6.1f bps  (n=%d)  <- OBI D-yonunu destekliyordu' % (statistics.mean(al), len(al)))
    if ct:
        print('  OBI-KARSI   : ort %+6.1f bps  (n=%d)  <- OBI D-yonune karsiydi' % (statistics.mean(ct), len(ct)))
    print('\n  --- VERDICT ---')
    if al and ct and len(al) >= 5 and len(ct) >= 5:
        diff = statistics.mean(al) - statistics.mean(ct)
        if diff > 10:
            print('  HIZALI, KARSI\'dan %+.1f bps IYI -> OBI-filtresi UMUT VERIYOR' % diff)
            print('  (canli 4-ceyrek + carpraz-coin dogrulama gerekir, tek-pencereye guvenme)')
        elif diff < -10:
            print('  KARSI daha iyi (%+.1f) -> OBI filtre TERS; filtreleme.' % diff)
        else:
            print('  fark kucuk (%+.1f bps) -> OBI AYIRMIYOR (cogu sey gibi).' % diff)
    else:
        print('  gruplardan biri hala kucuk (n<5), olcmeye devam.')
    print('\n  son kayitlar:')
    for r in rows[-8:]:
        print('    %s %-5s obi=%+.2f %-6s -> %+6.1f bps (%s)' % (
            r['open_human'], r['side'], r['obi'],
            'HIZALI' if r['aligned'] else 'KARSI', r['pnl_bps'], r['reason']))
print('=' * 60)
