# IZLEME PLANI (2026-09-16 -> ~15 Ekim)

Kullanici karari: birkac hafta forward izle, DATA + ANALIZI KACIRMA. Canlida yon-riski YOK,
sermaye ~$98.87 korunuyor (PAPER_MODE=True). Bu dosya = izleme referansi.

## AKTIF TOPLAYICILAR (reboot-dayanikli: Startup VBS -> start_all + supervisor)
| surec | ne topluyor | DB | reboot |
|---|---|---|---|
| main.py | D maker-shadow (poc_maker) + bot | data/bot.db | supervisor + start_all |
| orderflow_shadow.py | OBI 3-bant + taker-akis + mid (dakika) | data/orderflow_shadow.db | start_all [2b] |
| trendlf_shadow.py | ASIL: gunluk long-veya-nakit trend + -10% stop | data/trendlf_shadow.db | start_all [2e] |
| dumpfade_paper (main icinde) | illikit-dump fade (NEGATIF cikti, guvenme) | data/bot.db | main |
| funding/ftsm/d-multicoin | gunluk task GBot_ShadowUpdate 06:00 | ayri DB'ler | Windows task |

DURDURULDU (yanlislandi, start_all'dan cikarildi): g_reverse_shadow, g_early_shadow (rejim donunce ters kaybetti).

### VERI-KAYBI KORUMASI (3 katman, 2026-09-18):
1. Supervisor (run_bot.ps1): main.py cokerse saniyeler icinde geri getirir.
2. Startup VBS -> start_all: reboot/logon'da hepsini ayaga kaldirir (idempotent).
3. GBot_Watchdog (YENI, 30dk'da bir scripts/watchdog.ps1): standalone shadow'lar (orderflow/trendlf)
   cokerse REBOOT beklemeden geri getirir -> log data/logs/watchdog.log. Kontrol: 4318/4320 slot son-3g, 0 bosluk.

## ASIL IZLENEN: trendlf (senin + benim fikrinin ortak, dogrulanmis yapisi)
Kural: 5 coin (BTC/ETH/SOL/BNB/LINK) esit-agirlik, gunluk close>close[40g] ISE LONG degilse NAKIT,
SHORT YOK, -10% sert stop + 10g cooldown. EDGE DEGIL = cost-verimli, dusuk-churn trend-beta.
Basari etkenleri (izle): (1) trend-genisligi=kac coin LONG, (2) flip-sikligi, (3) max-dusus (stop calisiyor mu),
(4) coin dagilimi. Su an (09-16): 5/5 LONG, kagit ~-1400bps (pullback yiyor, tasarim geregi), gercek para dokunulmadi.

## HER "DURUM NE" DENDIGINDE BAKILACAK (analiz komutlari)
```
# trendlf durum + kagit K/Z + trend genisligi
python scripts/trendlf_shadow.py --once
# order-flow tazelik/bosluk kontrolu -> data/logs + DB son ts
# maker A/B (yavas birikiyor)
python scripts/maker_status.py
```

## ~15 EKIM KARARI (dondurulmus, degistirilemez)
Order-flow: `python scripts/_orderflow_test.py` (numpy, kurallar dondurulmus).
Klimaks-fade: `python scripts/_orderflow_climax_test.py` (on-bakis EDGE DEGIL, tekrar bak).
GECME-KURALI (hepsi ayni anda): gercekci-dolum + NET+ maliyet-dahil + iki-yari tutarli + permut p<0.05 +FDR
+ cok-coin cogunlugu 3+/4 ceyrek. GECEN yoksa -> canliya HICBIR sey, basabas'ta kal.

## TARANMIS-ELENMIS (TEKRAR ACMA -- hepsi test edildi, edge degil)
S/R fade/breakout/stop, Fibonacci, Fib+S/R confluence+TP/SL, pivot-yon, spike-fade, balina-takibi (top-trader),
klimaks-fade, MA-cross sabit (felaket) + adaptif-intraday (churn yer) + adaptif-N gunluk (overfit). Rejim-tespit
(otokorelasyon) persistence ~0 = ongorulemez. Detay: memory dosyalari + reports/DURUM_2026-09-13.md.

## SINIR (durust)
- Makine kapali/uykuda -> toplayici durur (ev-makinesi siniri). Logon'da start_all otomatik yakalar.
- trendlf trend-BETA: kripto trend ederse +, dusus/yatayda kaybeder (rejim-bagimli, edge degil).
- Beklenti: order-flow muhtemelen sub-cost (15 Ekim dogrular). Gercek deger = kaybetmemek + trende disiplinli maruz kalmak.
