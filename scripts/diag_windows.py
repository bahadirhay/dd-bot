"""
Bot hangi zaman dilimine bakıyor? — özet tablo.
PYTHONPATH=. python scripts/diag_windows.py
"""
from __future__ import annotations

from core.config import cfg

TREND_BARS = getattr(cfg, "TREND_BARS_15M", 12)

rows = [
    ("Üst panel RANGE/DOWN/UP + Güç%", f"son {TREND_BARS} kapalı 15m mum", TREND_BARS * 15, "trend.py"),
    ("CVD tutarlılık (trend skoru)", f"son {cfg.CVD_BARS} adet 15m kapanış delta", cfg.CVD_BARS * 15, "trend.py + cvd_bars"),
    ("CVD 5m (üst sayı)", "aggTrade son 5 dk", 5, "trade_feed"),
    ("Yapı 15m UP/DOWN/UNCLEAR", f"son 96 mum, swing LB={cfg.SWING_LB_15M}", 96 * 15, "structure.py"),
    ("Yapı 1h", f"son 48 mum, swing LB={cfg.SWING_LB_1H}", 48 * 60, "structure.py"),
    ("Dashboard grafik 15m", "96 mum REST", 96 * 15, "binance_chart"),
    ("Dashboard grafik 1h", "48 mum REST", 48 * 60, "binance_chart"),
    ("Dashboard grafik 1m", "120 mum REST", 120, "binance_chart"),
    ("Grafikten açıkla — DB", "±15 dk journal", 30, "explain_context"),
    ("Grafikten açıkla — tek mum", "seçilen 15m mumu", 15, "explain_live"),
    ("Grafikten açıkla — yapı incelemesi", "seçilen ana kadar 96×15m + 48×1h", 24 * 60, "structure_explain"),
    ("Trade kapısı 1h+15m", "anlık structure_15m/1h etiketi", -1, "REQUIRE_HTF_ALIGN"),
]

print("=" * 72)
print("BOT ZAMAN PENCERELERI (ETH 15m)")
print("=" * 72)
for name, what, minutes, module in rows:
    if minutes < 0:
        t = "yapı: ~24h+48h veri, etiket anlık"
        print(f"\n{name}")
        print(f"  Ne: {what}")
        print(f"  Süre: {t}")
        print(f"  Kod: {module}")
        continue
    if minutes >= 60:
        t = f"{minutes/60:.1f} saat" if minutes < 1440 else f"{minutes/1440:.1f} gün"
    else:
        t = f"{minutes} dk"
    print(f"\n{name}")
    print(f"  Ne: {what}")
    print(f"  Süre: ~{t}")
    print(f"  Kod: {module}")

print("\n" + "=" * 72)
print(f"ÖNEMLİ: 'Güç %27 / RANGE' SADECE ilk satırdaki {TREND_BARS} mumdan gelir.")
print(f"Yapı UNCLEAR ise 24 saatlik swing analizinden gelir — 12 mum ile karıştırma.")
print("=" * 72)
