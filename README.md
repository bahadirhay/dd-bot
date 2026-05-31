# ETH/USDT Rejim + Orderflow Botu

## Kurulum
```bash
pip install -r requirements.txt
# api_key.csv — tek satır (mmbot3) veya başlıklı CSV:
#   api_key,api_secret
#   ANAHTAR,GIZLI
# Varsayılan: CANLI Futures (TESTNET=false). Testnet: .env → TESTNET=true
python main.py
```

## Yapı
- Leverage: 5x sabit
- Margin: ISOLATED sabit
- Risk: %1/trade
- SL/TP: mmbot3 `structure_analyzer` + `structure_levels` (invalidation, likidite TP2)
- TP1: %50 kapat → SL breakeven
- TP2: likidite kovası veya TP1×1.008 fallback

## Canlı öncesi test
Adım adım checklist: [TESTING.md](TESTING.md)

## Log analizi
Her 06:00, 12:00, 18:00, 00:00 UTC'de otomatik analiz yapılır
ve Telegram'a gönderilir. DB: data/bot.db
