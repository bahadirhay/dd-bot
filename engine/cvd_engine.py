"""
engine/cvd_engine.py — CVD bar güncelleme + diverjans tespiti
"""
import time
from core.state  import state
from core.logger import get_logger

log = get_logger("CVD")


def on_bar_close(candle: dict):
    """
    15m mum kapandığında çağrılır.
    CVD bar geçmişine ekle + diverjans kontrol et.
    """
    delta = candle.get("delta", 0)
    bar   = {
        "ts"       : candle["ts"],
        "close"    : candle["close"],
        "delta"    : delta,
        "direction": 1 if delta > 0 else -1,
        "cvd_snap" : state.cvd_raw,
    }
    state.cvd_bars.append(bar)


def check_divergence(direction: str) -> bool:
    """
    Fiyat-CVD diverjansı var mı?

    Bullish diverjans: fiyat yeni dip yaptı ama CVD yapmadı → alım baskısı zayıflıyor
    Bearish diverjans: fiyat yeni zirve yaptı ama CVD yapmadı → satım baskısı zayıflıyor

    LONG açmak istiyoruz: bearish diverjans varsa girme (yukarı momentum zayıf)
    SHORT açmak istiyoruz: bullish diverjans varsa girme (aşağı momentum zayıf)
    """
    bars = list(state.cvd_bars)[-5:]
    if len(bars) < 3:
        return False

    prices = [b["close"] for b in bars]
    cvds   = [b["cvd_snap"] for b in bars]

    if direction == "LONG":
        # Fiyat yükseliyor ama CVD yükselmiyor → bearish div → girme
        price_up = prices[-1] > prices[-3]
        cvd_flat = cvds[-1] <= cvds[-3]
        return price_up and cvd_flat

    if direction == "SHORT":
        # Fiyat düşüyor ama CVD düşmüyor → bullish div → girme
        price_dn = prices[-1] < prices[-3]
        cvd_flat = cvds[-1] >= cvds[-3]
        return price_dn and cvd_flat

    return False
