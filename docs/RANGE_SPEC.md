# Kanal (Range) modu — v2 (bps + 1m + CVD eğimi)

## Aktivasyon

```env
ENTRY_MODE=range
# veya
ENTRY_MODE=hybrid
```

## Hibrit = iki ayrı sistem

| Sistem | Ne zaman | Giriş |
|--------|----------|--------|
| **Kanal (range)** | Fiyat **S–R arasında**, seviyeye ≤35 bps | Fade + skor ≥65 |
| **Kırılım (break)** | Fiyat **band dışı** (destek altı / direnç üstü) | 2s tutma + CVD/taker + max 120 bps uzaklık |

Band içinde kırılım motoru **çalışmaz** (sadece range). Band dışında range **çalışmaz**.

### Yapısal kırılım (zorunlu)

Destek **altına** veya direnç **üstüne** çıkınca (`BREAK_STRUCTURAL_MANDATORY=true`):

- **1 sn** tutma (`BREAK_STRUCTURAL_HOLD_SEC`)
- Sonra **SHORT/LONG açılır** — CVD, taker, HTF, OI, cooldown, uzaklık limiti **yok**
- Log: `KIRILIM ZORUNLU`

## Yön seçimi (v3 — hesaplanan seviyeler)

Sabit bps gevşetme yok. Mesafe eşiği:

`prox_eff = RANGE_PROXIMITY_BPS × (kanal_genişliği_bps / RANGE_REF_WIDTH_BPS)`

En yakın seviye = min mesafe:

1. Dış **S / R** (15m swing kanal)
2. **1m mikro raf** — son N mum cluster high/low
3. **Kanal içi 15m swing** — S<R arası tepe/dip
4. **Bölge red** — Q75/Q25 (çeyrek) içinde 1m üst/alt fitil red; fiyat geri çekilse bile

| Durum | Kural |
|--------|--------|
| **LONG** | En yakın alt seviye `≤ prox_eff` |
| **SHORT** | En yakın üst seviye `≤ prox_eff` veya üst çeyrekte yakın red |
| **CHOP** | İki tarafa da yakın |
| **BEKLE** | Ortada; salınım≥2 ise dashboard’da not |

`band_pct` giriş tetikleyici değil; salınım/red bilgisi için kullanılır.

## Skor (≥65, HTF cezalı)

| Katman | Max |
|--------|-----|
| Mesafe (yakınlık) | ~22 |
| 1m red (fitil, engulf, S/R dokunuş) | 30 |
| 15×1m nabız | 18 |
| CVD 90sn eğim + taker | 30 |
| fail / OI | bonus |

## TP

- TP1 = mid
- TP2 = karşı band − buffer
- SL = band dışı 8 bps

## Env

`RANGE_PROXIMITY_BPS`, `RANGE_CHOP_DIFF_BPS`, `RANGE_CVD_SLOPE_SEC`, `RANGE_1M_LOOKBACK`, `RANGE_WICK_MIN`
