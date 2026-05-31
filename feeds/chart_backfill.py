"""feeds/chart_backfill.py — Başlangıçta 15m/1m kline (yapı + grafik)."""
import aiohttp

from core.config import cfg
from core.state import state, record_metrics_sample
from core.logger import get_logger
from engine.structure import add_bar_15m, add_bar_1h
from engine.cvd_engine import on_bar_close as cvd_on_bar

log = get_logger("ChartFill")


def _candle_from_row(row) -> dict:
    return {
        "ts": row[0] / 1000.0,
        "open": float(row[1]),
        "high": float(row[2]),
        "low": float(row[3]),
        "close": float(row[4]),
        "volume": float(row[5]),
        "buy_vol": float(row[9]) if len(row) > 9 else 0.0,
    }


async def _fetch_klines(interval: str, limit: int) -> list:
    url = f"{cfg.REST}/fapi/v1/klines"
    params = {"symbol": cfg.SYMBOL, "interval": interval, "limit": limit}
    async with aiohttp.ClientSession() as sess:
        async with sess.get(
            url, params=params, timeout=aiohttp.ClientTimeout(total=20)
        ) as r:
            data = await r.json()
    return data if isinstance(data, list) else []


async def backfill_15m_bars(bars: int = 96) -> int:
    """15m mum + yapı/CVD geçmişi (Binance 15m grafiği ile uyumlu)."""
    try:
        data = await _fetch_klines("15m", bars)
    except Exception as e:
        log.warning(f"15m backfill başarısız: {e}")
        return 0

    for row in data:
        c = _candle_from_row(row)
        c["sell_vol"] = c["volume"] - c["buy_vol"]
        c["delta"] = c["buy_vol"] - c["sell_vol"]
        add_bar_15m(c)
        cvd_on_bar(c)

    if data:
        last = _candle_from_row(data[-1])
        state.price = last["close"]
        state.kline_last_update = last["ts"]

    log.info(f"15m geçmiş yüklendi: {len(data)} mum  |  yapı 15m={state.structure_15m}")
    try:
        from dashboard.binance_chart import publish_bot_bars_to_cache

        publish_bot_bars_to_cache()
    except Exception:
        pass
    return len(data)


async def backfill_1h_bars(bars: int = 48) -> int:
    try:
        data = await _fetch_klines("1h", bars)
    except Exception as e:
        log.warning(f"1h backfill başarısız: {e}")
        return 0
    for row in data:
        c = _candle_from_row(row)
        c["sell_vol"] = c["volume"] - c["buy_vol"]
        c["delta"] = c["buy_vol"] - c["sell_vol"]
        add_bar_1h(c)
    log.info(f"1h geçmiş yüklendi: {len(data)} mum  |  yapı 1h={state.structure_1h}")
    return len(data)


async def backfill_price_history(hours: int | None = None) -> int:
    h = hours or cfg.CHART_HOURS
    limit = min(1500, max(60, h * 60))

    try:
        data = await _fetch_klines("1m", limit)
    except Exception as e:
        log.warning(f"1m backfill başarısız: {e}")
        return 0

    from engine.bars_1m import set_bars_1m

    candles = []
    state.price_history.clear()
    for row in data:
        ts = row[0] / 1000.0
        c = _candle_from_row(row)
        candles.append(c)
        state.price_history.append({"ts": ts, "price": c["close"]})

    set_bars_1m(candles[-max(cfg.PULSE_BARS_1M * 4, 120) :])

    if state.price_history:
        state.price = state.price_history[-1]["price"]

    record_metrics_sample()
    log.info(
        f"1m geçmiş: {len(candles)} mum (~{h}h) | nabız penceresi={cfg.PULSE_BARS_1M}dk"
    )
    return len(data)
