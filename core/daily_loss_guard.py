"""
core/daily_loss_guard.py
────────────────────────
Günlük maksimum kayıp limitini takip eder.
Limit aşılırsa botu durdurur ve Telegram'a bildirim gönderir.

Kullanım:
    from core.daily_loss_guard import DailyLossGuard
    guard = DailyLossGuard()

    # Her trade kapanışında:
    guard.record_trade_pnl(pnl_pct=-1.2)

    # execute_entry öncesinde:
    if not guard.can_trade():
        return  # günlük limit aşıldı
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from core.logger import get_logger

log = get_logger("DailyGuard")

# Varsayılan limit — .env ile override edilebilir
DEFAULT_MAX_DAILY_LOSS_PCT = 3.0   # günlük %3
DEFAULT_MAX_TRADES_PER_DAY = 10    # maksimum günlük işlem sayısı


class DailyLossGuard:
    """
    Thread-safe değil; asyncio döngüsünde tek thread'den çağrılır.
    Gün sıfırlama UTC 00:00'da otomatik yapılır.
    """

    def __init__(
        self,
        max_loss_pct: float | None = None,
        max_trades: int | None = None,
    ):
        from core.config import cfg  # geç import — circular import önlemi

        self._max_loss_pct = max_loss_pct or float(
            getattr(cfg, "MAX_DAILY_LOSS_PCT", DEFAULT_MAX_DAILY_LOSS_PCT)
        )
        self._max_trades = max_trades or int(
            getattr(cfg, "MAX_DAILY_TRADES", DEFAULT_MAX_TRADES_PER_DAY)
        )

        self._day: int = self._today()
        self._cumulative_pnl: float = 0.0
        self._trade_count: int = 0
        self._halted: bool = False
        self._halt_reason: str = ""

        log.info(
            f"DailyGuard başlatıldı: max_loss={self._max_loss_pct}% "
            f"max_trades={self._max_trades}"
        )

    # ── Yardımcı ──────────────────────────────────────────────────────────────

    @staticmethod
    def _today() -> int:
        """UTC günü integer olarak (YYYYMMDD)."""
        now = datetime.now(timezone.utc)
        return now.year * 10000 + now.month * 100 + now.day

    def _check_day_rollover(self) -> None:
        today = self._today()
        if today != self._day:
            log.info(
                f"DailyGuard: yeni gün → sıfırlanıyor "
                f"(önceki PnL={self._cumulative_pnl:.2f}% "
                f"trade={self._trade_count})"
            )
            self._day = today
            self._cumulative_pnl = 0.0
            self._trade_count = 0
            self._halted = False
            self._halt_reason = ""

    # ── Public API ────────────────────────────────────────────────────────────

    def can_trade(self) -> bool:
        """
        True → trade açılabilir.
        False → günlük limit aşıldı, trade açılmaz.
        """
        self._check_day_rollover()

        if self._halted:
            log.warning(f"DailyGuard: trade engellendi — {self._halt_reason}")
            return False

        # Kayıp kontrolü
        if self._cumulative_pnl <= -self._max_loss_pct:
            self._halt("loss_limit", self._cumulative_pnl)
            return False

        # Trade sayısı kontrolü
        if self._trade_count >= self._max_trades:
            self._halt("trade_count", self._trade_count)
            return False

        return True

    def record_trade_open(self) -> None:
        """Pozisyon açılınca çağrılır."""
        self._check_day_rollover()
        self._trade_count += 1
        log.debug(f"DailyGuard: trade açıldı ({self._trade_count}/{self._max_trades})")

    def record_trade_pnl(self, pnl_pct: float) -> None:
        """
        Trade kapanınca çağrılır.
        pnl_pct: yüzde cinsinden (örn. -1.2 veya +0.8)
        """
        self._check_day_rollover()
        self._cumulative_pnl = round(self._cumulative_pnl + pnl_pct, 4)

        log.info(
            f"DailyGuard: trade PnL={pnl_pct:+.2f}% "
            f"günlük toplam={self._cumulative_pnl:+.2f}%"
        )

        # Limit aşıldı mı?
        if self._cumulative_pnl <= -self._max_loss_pct and not self._halted:
            self._halt("loss_limit", self._cumulative_pnl)

    def status(self) -> dict:
        """Dashboard / Telegram özeti için."""
        self._check_day_rollover()
        return {
            "halted": self._halted,
            "halt_reason": self._halt_reason,
            "cumulative_pnl_pct": self._cumulative_pnl,
            "trade_count": self._trade_count,
            "max_loss_pct": self._max_loss_pct,
            "max_trades": self._max_trades,
            "remaining_loss_pct": round(
                self._max_loss_pct + self._cumulative_pnl, 4
            ),
        }

    # ── İç yardımcı ───────────────────────────────────────────────────────────

    def _halt(self, reason: str, value: float) -> None:
        self._halted = True
        if reason == "loss_limit":
            self._halt_reason = (
                f"Günlük kayıp limiti aşıldı: {value:.2f}% "
                f"(limit: -{self._max_loss_pct}%)"
            )
        else:
            self._halt_reason = (
                f"Günlük işlem limiti aşıldı: {int(value)} işlem "
                f"(limit: {self._max_trades})"
            )

        log.critical(f"DailyGuard DURDURULDU: {self._halt_reason}")
        self._send_telegram_alert()

    def _send_telegram_alert(self) -> None:
        """Telegram'a acil uyarı — sync wrapper."""
        try:
            import asyncio
            from core.config import cfg

            if not getattr(cfg, "TELEGRAM_BOT_TOKEN", "") or not getattr(
                cfg, "TELEGRAM_CHAT_ID", ""
            ):
                return

            msg = (
                f"🛑 *BOT DURDURULDU*\n"
                f"Sebep: {self._halt_reason}\n"
                f"Günlük PnL: `{self._cumulative_pnl:+.2f}%`\n"
                f"İşlem sayısı: `{self._trade_count}`"
            )

            # Eğer çalışan loop varsa task olarak ekle
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_send_tg(msg))
            except RuntimeError:
                pass  # loop yok, atla

        except Exception as e:
            log.warning(f"DailyGuard Telegram uyarı hatası: {e}")


async def _send_tg(message: str) -> None:
    """Telegram mesajı gönderir."""
    try:
        import aiohttp
        from core.config import cfg

        token = cfg.TELEGRAM_BOT_TOKEN
        chat_id = cfg.TELEGRAM_CHAT_ID
        url = f"https://api.telegram.org/bot{token}/sendMessage"

        async with aiohttp.ClientSession() as session:
            await session.post(
                url,
                json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"},
                timeout=aiohttp.ClientTimeout(total=10),
            )
    except Exception as e:
        log.warning(f"Telegram gönderim hatası: {e}")


# ── Singleton ────────────────────────────────────────────────────────────────
# main.py'de bir kez oluşturulur, diğer modüller import eder

_guard: DailyLossGuard | None = None


def get_guard() -> DailyLossGuard:
    global _guard
    if _guard is None:
        _guard = DailyLossGuard()
    return _guard
