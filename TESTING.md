# ETH Bot — Canlı Öncesi Test Rehberi

Yayına almadan önce her maddeyi işaretleyin. Bot **API key varken gerçek Binance emirleri** gönderir; küçük bakiye ile test edin.

---

## 0. Hazırlık

| # | Kontrol | Nerede bakılır |
|---|---------|----------------|
| 0.1 | `api_key.csv` dolu, `your_key_here` değil | Dosya |
| 0.2 | API: **Futures** yetkisi, IP whitelist (varsa) doğru | Binance → API Management |
| 0.3 | Başlangıç logu: `API : ✓ Bağlı` ve bakiye doğru | `data/logs/bot.log` veya terminal |
| 0.4 | Dashboard: **🟢 API \| CANLI** (veya TESTNET bilinçli seçildiyse) | http://localhost:8050 |
| 0.5 | Risk anlayışı: trade başına ~%1 bakiye, max ~%20 marjin | `core/config.py` |

```powershell
cd c:\Users\BH\Desktop\bot
python main.py
```

---

## 1. Bağlantı testi (emir yok)

| # | Beklenen log / ekran | Binance UI |
|---|----------------------|------------|
| 1.1 | `Binance zaman offset: … ms` | — |
| 1.2 | `API bağlantısı OK \| Bakiye: $… USDT` | Futures cüzdan bakiyesi aynı |
| 1.3 | `aggTrade stream aktif`, `Kline stream aktif` | — |
| 1.4 | Dashboard fiyat güncelleniyor (🟢 Canlı veri) | — |

---

## 2. Sinyal ve plan (giriş öncesi)

Sinyal **15m mum kapanışında** üretilir; giriş **1m onayı** sonrası.

| # | Beklenen | Log anahtarı |
|---|----------|--------------|
| 2.1 | Rejim skoru ≥ 3/4 olmadan trade yok | `Rejim geçmedi` veya skor < 3 |
| 2.2 | Sinyal gelince SL/TP1/TP2 yazılır (mmbot3 yapı) | `SİNYAL` + `inv=` + `tp1_tgt=` + `R:R1` / `R:R2` |
| 2.3 | R:R TP1 ≥ 1.5 değilse giriş yok | `R:R=… yetersiz` |
| 2.4 | Giriş bekleme | `Giriş bekleniyor: … max 5 adet 1m mum` |
| 2.5 | Risk planı | `Risk Planı:` + miktar, marjin, liq |

**Binance:** Bu aşamada henüz pozisyon/emir olmamalı.

---

## 3. Pozisyon açılışı (gerçek emirler)

| # | Beklenen log | Binance → Positions / Open Orders |
|---|--------------|-----------------------------------|
| 3.1 | `POZİSYON AÇILDI: LONG/SHORT … ETH @ …` | Pozisyon açık, miktar ≈ plan |
| 3.2 | `SL : … id=…` | 1× STOP_MARKET (close position) |
| 3.3 | `TP1: … (qty_tp1) id=…` | 1× TAKE_PROFIT_MARKET, qty ≈ %50 |
| 3.4 | `TP2: … (qty_tp2) id=…` | 1× TAKE_PROFIT_MARKET, kalan qty |
| 3.5 | Marjin ISOLATED 5x | Position: Isolated, leverage 5 |

Telegram açıksa: `POZİSYON AÇILDI` mesajı.

---

## 4. TP1 + breakeven testi

| # | Beklenen log | Binance |
|---|--------------|---------|
| 4.1 | Fiyat TP1’e gelince | TP1 emri FILLED, pozisyon ~%50 azalır |
| 4.2 | `TP1 DOLDU @ … → BE aktifleşiyor` | — |
| 4.3 | `TP1 sonrası kalan: … ETH (borsa)` | Position qty ≈ qty_tp2 |
| 4.4 | `BREAKEVEN aktif: SL → …` | Eski SL iptal, yeni SL ≈ giriş |
| 4.5 | Dashboard: TP1 ✅, BE ✅ | — |

Telegram: `TP1 DOLDU` + kısmi PnL.

**Manuel doğrulama:** TP1 dolmadan erken çıkış yapmayın; sadece TP1 senaryosunu izleyin.

---

## 5. TP2 veya tam kapanış

| # | Senaryo | Beklenen |
|---|---------|----------|
| 5.1 | Fiyat TP2’ye gelir | TP2 emri dolar, pozisyon 0 |
| 5.2 | Log | `Borsada pozisyon yok → TP2/SL dolmuş` (senkron) |
| 5.3 | Dashboard | Pozisyon YOK |
| 5.4 | DB / tablo | Trade CLOSED, `tp1_hit` / `be_activated` doğru |

---

## 6. Erken çıkış testleri (isteğe bağlı, ayrı oturum)

Küçük pozisyonla tek tek deneyin:

| Sebep | Tetikleyici | Log |
|--------|-------------|-----|
| `regime_break` | Rejim RANGE, skor ≤ 1 | `Rejim bozuldu` |
| `cvd_reverse` | LONG + CVD < -200 (SHORT tersi) | `CVD sert …` |
| `stale_data` | WS 10+ sn kesilir | `Veri 10sn'den eski` |

Her birinde: `POZİSYON KAPATILDI`, tüm açık emirler iptal, Telegram `KAPATILDI`.

---

## 7. Miktar senkronu (kod düzeltmesi)

| # | Kontrol |
|---|---------|
| 7.1 | TP1 sonrası `close_position` / erken çıkış **kalan qty** ile kapatır (tam qty değil) |
| 7.2 | Borsa TP2’yi doldurduğunda bot state’i sıfırlar (`EXCHANGE_CLOSED`) |
| 7.3 | Borsa TP1’i fiyattan önce doldurursa `Borsa TP1 dolu görünüyor → breakeven` |

---

## 8. Yayın öncesi son checklist

- [ ] En az **1 tam döngü**: giriş → TP1 + BE → TP2 (veya planlı erken çıkış)
- [ ] Binance **Trade History** PnL ile log PnL tutarlı (≈ komisyon farkı)
- [ ] `data/bot.db` → trades tablosu kayıt dolu
- [ ] İzleme modu (`API key yok`) ile canlı mod karışık test edilmedi
- [ ] Bakiye ve risk boyutu kabul edilebilir
- [ ] `.env` / `TESTNET` bilinçli seçildi (canlı key ↔ CANLI)

---

## Hızlı log arama (PowerShell)

```powershell
Select-String -Path "data\logs\bot.log" -Pattern "SİNYAL|POZİSYON AÇILDI|TP1 DOLDU|BREAKEVEN|KAPATILDI|API bağlantısı"
```

---

## Sorun giderme

| Belirti | Olası neden |
|---------|-------------|
| `API key yok → İzleme modu` | Botu yeniden başlat; `api_key.csv` kontrol |
| `Hata -2015` | TESTNET=true ama canlı key (veya tersi) |
| `Hata -1022` | Secret yanlış (artık imza düzeltmesi var; yine de key/secret eşleşsin) |
| TP1 oldu, SL hâlâ eski | BE logu yok → `move_to_breakeven` hatası; Binance open orders |
| Kapatma “quantity” hatası | Eski bug; güncel kod borsadan qty çeker |

---

*Son güncelleme: TP1 sonrası borsa senkronu + `TESTING.md` ile birlikte.*
