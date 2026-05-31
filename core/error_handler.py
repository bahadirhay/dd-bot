"""
core/error_handler.py
─────────────────────
Merkezi hata yönetim yardımcısı.

Kullanım:
    from core.error_handler import guard

    # Kritik olmayan (dashboard, loglama) — sessiz geçiş
    with guard.silent("dashboard güncelle"):
        publish_bot_bars_to_cache()

    # Uyarı seviyesi — logla, devam et
    with guard.warn("journal yaz"):
        on_bar("1m", candle, label)

    # Kritik — logla + Telegram + gerekirse durdur
    with guard.critical("emir gönder", halt_on_error=False):
        await open_position(...)
"""

from __future__ import annotations

import traceback
from contextlib import contextmanager
from core.logger import get_logger

log = get_logger("ErrHandler")


class ErrorHandler:

    # ── Sessiz geçiş (dashboard, UI, cache) ──────────────────────────────────
    @contextmanager
    def silent(self, label: str):
        """Hata olursa log bile atmaz. Sadece UI/görselleştirme için."""
        try:
            yield
        except Exception:
            pass  # kasıtlı sessiz

    # ── Uyarı (loglama, journal, non-critical feed) ───────────────────────────
    @contextmanager
    def warn(self, label: str):
        """Hata olursa log.warning atar, devam eder."""
        try:
            yield
        except Exception as e:
            log.warning(f"[{label}] hata (devam ediliyor): {e}")

    # ── Hata seviyesi (strateji güncelleme, veri feed) ────────────────────────
    @contextmanager
    def error(self, label: str, fallback=None):
        """
        Hata olursa log.error atar.
        fallback değeri varsa yield yerine onu kullan — değer döndüren bloklar için.
        """
        try:
            yield
        except Exception as e:
            log.error(f"[{label}] hata: {e}")
            log.debug(traceback.format_exc())

    # ── Kritik (emir gönderme, pozisyon yönetimi) ─────────────────────────────
    @contextmanager
    def critical(self, label: str, halt_on_error: bool = False):
        """
        Hata olursa log.critical + Telegram uyarısı.
        halt_on_error=True: botu durdurur (nadir kullanılmalı).
        """
        try:
            yield
        except Exception as e:
            log.critical(f"[{label}] KRİTİK HATA: {e}")
            log.debug(traceback.format_exc())
            self._telegram_alert(label, str(e))
            if halt_on_error:
                self._halt(label)
            raise  # kritik hata yeniden fırlatılır — caller handle etmeli

    # ── Yardımcılar ───────────────────────────────────────────────────────────
    def _telegram_alert(self, label: str, error: str) -> None:
        try:
            import asyncio
            from core.config import cfg
            if not getattr(cfg, "TELEGRAM_BOT_TOKEN", ""):
                return

            msg = f"⚠️ *Bot kritik hata*\nModül: `{label}`\n`{error[:200]}`"
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_tg_send(msg))
            except RuntimeError:
                pass
        except Exception:
            pass

    def _halt(self, label: str) -> None:
        try:
            from core.shutdown import request_stop
            log.critical(f"ErrHandler: kritik hata nedeniyle bot durduruluyor [{label}]")
            request_stop()
        except Exception as e:
            log.error(f"ErrHandler halt hatası: {e}")


async def _tg_send(message: str) -> None:
    try:
        import aiohttp
        from core.config import cfg
        url = f"https://api.telegram.org/bot{cfg.TELEGRAM_BOT_TOKEN}/sendMessage"
        async with aiohttp.ClientSession() as s:
            await s.post(
                url,
                json={"chat_id": cfg.TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"},
                timeout=aiohttp.ClientTimeout(total=8),
            )
    except Exception:
        pass


# Singleton
guard = ErrorHandler()
