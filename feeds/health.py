"""feeds/health.py — Feed sağlık izleme (takılma tespiti)."""
import asyncio
import time

from core.state import state, effective_price, mid_price
from core.shutdown import is_stopping
from core.async_sleep import stoppable_sleep
from core.logger import get_logger

log = get_logger("Health")

_WARN_SEC = 90


async def run_watchdog():
    while not is_stopping():
        try:
            await stoppable_sleep(60)
        except asyncio.CancelledError:
            break
        if is_stopping():
            break
        now = time.time()
        trade_age = now - state.trade_last_update if state.trade_last_update else -1
        book_age = now - state.book_last_update if state.book_last_update else -1
        kline_age = now - state.kline_last_update if state.kline_last_update else -1

        issues = []
        if trade_age < 0 or trade_age > _WARN_SEC:
            issues.append(f"aggTrade {trade_age:.0f}s" if trade_age >= 0 else "aggTrade hiç")
        if book_age < 0 or book_age > _WARN_SEC:
            issues.append(f"book {book_age:.0f}s" if book_age >= 0 else "book hiç")
        if kline_age < 0 or kline_age > 180:
            issues.append(f"kline {kline_age:.0f}s" if kline_age >= 0 else "kline hiç")

        if issues:
            log.warning(
                f"Veri akışı yavaş/kapalı: {', '.join(issues)} | "
                f"gösterim={effective_price():.2f} mid={mid_price():.2f} "
                f"cvd={state.cvd_5m:+.0f} rejim={state.regime} "
                f"(REST recovery grace/cooldown kurallariyla calisiyor)"
            )
        else:
            log.debug(
                f"Feed OK | fiyat={effective_price():.2f} cvd={state.cvd_5m:+.0f} "
                f"rejim={state.regime} {state.regime_score}/4"
            )
