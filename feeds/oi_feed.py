"""feeds/oi_feed.py — OI + Funding REST poller (10sn)"""
import asyncio, aiohttp, time
from core.config import cfg
from core.state  import state
from core.shutdown import is_stopping
from core.async_sleep import stoppable_sleep
from core.logger import get_logger

log = get_logger("OIFeed")
_last_warn_ts = 0.0


def _warn_throttled(msg: str) -> None:
    global _last_warn_ts
    now = time.time()
    if now - _last_warn_ts >= 60:
        log.warning(msg)
        _last_warn_ts = now

async def run():
    log.info("OI+Funding poller başladı")
    async with aiohttp.ClientSession() as sess:
        while not is_stopping():
            try:
                # OI
                async with sess.get(
                    f"{cfg.REST}/fapi/v1/openInterest",
                    params={"symbol": cfg.SYMBOL},
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as r:
                    d = await r.json(content_type=None)
                    if r.status != 200 or not isinstance(d, dict):
                        _warn_throttled(f"OI gecici HTTP {r.status}: {d}")
                        await stoppable_sleep(cfg.OI_POLL)
                        continue
                    oi = float(d.get("openInterest", 0))
                    if oi:
                        state.oi_history.append({"ts": time.time(), "oi": oi})
                        state.oi_current = oi
                        # Son 3 ölçüm artan mı?
                        hist = list(state.oi_history)[-cfg.OI_LOOKBACK:]
                        if len(hist) >= 2:
                            state.oi_rising = all(
                                hist[i]["oi"] > hist[i-1]["oi"]
                                for i in range(1, len(hist))
                            )

                # Funding
                async with sess.get(
                    f"{cfg.REST}/fapi/v1/premiumIndex",
                    params={"symbol": cfg.SYMBOL},
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as r:
                    d = await r.json(content_type=None)
                    if r.status != 200 or not isinstance(d, dict):
                        _warn_throttled(f"Funding gecici HTTP {r.status}: {d}")
                        await stoppable_sleep(cfg.OI_POLL)
                        continue
                    state.funding_rate = float(d.get("lastFundingRate", 0))
                    state.mark_price   = float(d.get("markPrice", state.mark_price))
                    f = state.funding_rate
                    if   f >  0.0005: state.funding_signal = "LONG_CROWD"
                    elif f < -0.0005: state.funding_signal = "SHORT_CROWD"
                    else:             state.funding_signal = "NEUTRAL"

            except Exception as e:
                _warn_throttled(f"OI/Funding gecici veri hatasi: {e!r}")

            if is_stopping():
                break
            await stoppable_sleep(cfg.OI_POLL)
