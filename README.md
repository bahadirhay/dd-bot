# dd-bot İyileştirme Paketi
## Uygulama Rehberi

---

## Yeni Dosyalar (projenize kopyalayın)

| Dosya | Nereye | Ne Yapar |
|-------|--------|----------|
| `botlog/performance_context.py` | `botlog/` klasörüne | SQLite'tan geçmiş okur, cfg'ye adaptif parametre yazar |
| `botlog/db_backup.py` | `botlog/` klasörüne | Günlük otomatik yedek alır, 7 günden eskiyi siler |
| `core/daily_loss_guard.py` | `core/` klasörüne | Günlük %3 kayıp veya 10 işlem limitinde botu durdurur |
| `core/error_handler.py` | `core/` klasörüne | silent/warn/error/critical hata hiyerarşisi |
| `engine/v3_guard.py` | `engine/` klasörüne | V3 update başarısızsa stale flag, işlem engeli |
| `engine/adaptive_risk.py` | `engine/` klasörüne | UNCLEAR'da %40-60 risk, LIQ yakınlık filtresi |

---

## main.py Değişiklikleri

### Adım 1 — Import ekle (dosya başı)
```python
from botlog.performance_context import load_performance_context
from botlog.db_backup import maybe_backup
from core.daily_loss_guard import get_guard
from core.error_handler import guard as err
from engine.v3_guard import v3_update_safe, is_v3_stale
from engine.adaptive_risk import get_effective_risk, liq_filter_ok
```

### Adım 2 — _main_loop içinde setup_api sonrasına ekle
```python
await load_performance_context()  # geçmiş performansı oku
daily_guard = get_guard()          # günlük guard başlat
```

### Adım 3 — _on_1h fonksiyonunu değiştir
```python
# ESKİ:
try:
    update_levels(); update_structure(); update_decision()
except Exception as e:
    log.warning(f"V3 1h guncelleme: {e}")

# YENİ:
await v3_update_safe("1h")
```

### Adım 4 — _on_entry_confirmed başına kontroller ekle
```python
daily_guard = get_guard()
if not daily_guard.can_trade():
    return

if getattr(cfg, "STRATEGY_V3_ENABLED", False) and is_v3_stale():
    return

effective_risk, min_rr = get_effective_risk()
details["risk_pct_override"] = effective_risk

daily_guard.record_trade_open()
```

### Adım 5 — botlog/analyzer.py içine backup ekle
```python
from botlog.db_backup import maybe_backup
await maybe_backup()
```

---

## .env Güncellemesi
```
MAX_DAILY_LOSS_PCT=3.0
MAX_DAILY_TRADES=10
LIQ_WS_ENABLED=true
RANGE_CVD_SLOPE_SEC=180
RANGE_CVD_SLOPE_MIN=100
```

---

## Öncelik Sırası

1. `daily_loss_guard.py` + main.py entegrasyonu → **hemen uygula**
2. `v3_guard.py` → **hemen uygula**
3. `performance_context.py` → bir sonraki restart'ta uygula
4. `db_backup.py` → bu hafta uygula
5. `adaptive_risk.py` → test ettikten sonra uygula
6. `.env` güncellemesi (LIQ + CVD) → hemen uygula, sıfır kod değişikliği

---

## Test Etme

```bash
# Performance context testi (bot.db varsa)
python -c "
import asyncio
from botlog.performance_context import load_performance_context
ctx = asyncio.run(load_performance_context())
print(f'win_rate={ctx.win_rate:.0%} risk_mult={ctx.suggested_risk_multiplier}')
print(f'warnings={ctx.warnings}')
"

# Daily guard testi
python -c "
from core.daily_loss_guard import DailyLossGuard
g = DailyLossGuard(max_loss_pct=3.0, max_trades=5)
g.record_trade_open()
g.record_trade_pnl(-1.5)
g.record_trade_pnl(-1.8)
print(g.status())
print('can_trade:', g.can_trade())  # False beklenir
"
```
