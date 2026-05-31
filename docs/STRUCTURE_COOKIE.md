# Güncel çerez — destek / direnç (S/R odaklı strateji)

**Motor:** `engine/structure_cookie.py`  
**Bağlantı:** `engine/breakout.py` → `_resolve_trading_levels()`  
**Range:** `engine/range_trade.py` — edge×flow skor, HTF archetype

## Katmanlar

| Katman | Mum | Süre | Rol |
|--------|-----|------|-----|
| **Makro** | 96 | ~24h | İşlem bandı R/S (`STRUCTURE_COOKIE_BARS_MACRO`) |
| **Meso** | 32 / 16 trend | ~8h / ~4h | Destek rafı ince ayarı |
| **1m** | 20 | — | Mikro teyit |

## Direnç

1. Makro pencerede tepe dokunuşları (+ yerel tepe).
2. Kümele (`CLUSTER_MUL` × bar_noise).
3. Min `MIN_TOUCHES` dokunuş.
4. Üstteki en güçlü küme → medyan.
5. **Failed break** sayısı (üstünde kapanış altında) → `resistance_confidence`.
6. 1m refine.

## Destek katmanları

1. **`range_support`** = ana taban shelf  
   96×15m içinde alt acceptance katmanından seçilir. Stratejinin ana destek çizgisi budur.
2. **`micro_support`** = fiyata en yakın acceptance shelf  
   Son yükselişte oluşan yakın pullback zemini; takip / mikro bağlam.
3. **`deep_support`** = ikinci alt shelf / impuls tabanı  
   Daha derin savunma zemini; invalidasyon yakın akraba.

Kurallar:

- destek = yalnızca wick low değil, **close/body ile kabul görmüş raf**
- 15m macro body/close kümeleri + 1h anchor body/close adayları birlikte taranır
- **çoklu impuls** (`IMPULSE_TOP_N`) seviyesi hâlâ hesaplanır ama daha çok `deep_support` tarafında kullanılır
- **failed breakdown** — altına inip kapanış üstünde (`MIN_FAILED_BREAKS`)
- `support_confidence` = seçilen ana support için acceptance/impulse dokunuş + failed break + kalite
- meso pencerede yakın acceptance rafı ile ince ayar + 1m refine

## Kalite

- `ok` / `kısmi` / `dar` / `zayıf_*` — `min(S_conf, R_conf) >= MIN_EDGE_CONF`.
- Çerez geçerliyse swing ile band **genişletilmez** (`breakout` önce çerez).

## Range girişi (ayrı)

- Skor: **edge_p × flow_p** (toplama + HTF +10 yok).
- 15m DOWN → LONG yalnızca **alt red / absorption** (`RANGE_ARCHETYPE_STRICT`).
- Eşik: `range_min_score(chop, edge_confidence)` — dar bant tek başına ceza değil.

## Env

```env
STRUCTURE_COOKIE_BARS_MACRO=96
STRUCTURE_COOKIE_BARS=32
STRUCTURE_COOKIE_BARS_TREND=16
STRUCTURE_COOKIE_IMPULSE_TOP_N=3
STRUCTURE_COOKIE_MIN_FAILED_BREAKS=2
STRUCTURE_COOKIE_MIN_EDGE_CONF=0.5
STRUCTURE_COOKIE_MIN_TOUCHES=3
STRUCTURE_COOKIE_CLUSTER_MUL=2.5
STRUCTURE_COOKIE_1M_ENABLED=true
STRUCTURE_COOKIE_1M_LOOKBACK=20
RANGE_ARCHETYPE_STRICT=true
RANGE_MIN_EDGE_P=0.52
RANGE_MIN_FLOW_P=0.45
```
