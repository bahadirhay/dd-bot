"""core/logger.py"""
import logging, sys
from logging.handlers import RotatingFileHandler
from core.config import cfg
import os; os.makedirs(cfg.LOG_DIR, exist_ok=True)

_FMT = logging.Formatter("%(asctime)s  %(levelname)-7s  [%(name)-18s]  %(message)s",
                         datefmt="%H:%M:%S")


class SafeRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler + COK-PROCESS guvenligi. Birden fazla Python sureci (main.py + dashboard
    scriptleri + tek-seferlik test scriptleri) ayni bot.log'u actiginda, rollover anindaki os.rename
    Windows'ta digerinin acik-kolu yuzunden PermissionError (WinError 32) verebilir. Standart
    RotatingFileHandler bunu YUTMAZ: dosya stream'ini None birakip patlar -> emit() her seferinde
    ayni hatayi tekrar tekrar loglar (stderr'i "Logging error" ile doldurur) VE o satirlar hic
    diske yazilmaz (2026-07-30: bot.log 3 gundur bu yuzden buyumemis, sessizce veri kaybediyordu).
    Burada hatayi yutup stream'i yeniden aciyoruz: rollover o an basarisiz olsa da satir kaybolmaz,
    dosya bir sonraki musait ana kadar (digerinin kolu kapaninca) buyumeye devam eder."""

    def doRollover(self):
        try:
            super().doRollover()
        except (OSError, PermissionError):
            if self.stream is None:
                try:
                    self.stream = self._open()
                except Exception:
                    pass


# TEK paylasilan handler seti. Her logger kendi handler'ini yaratirsa (onceki hali)
# ayni bot.log'a birden fazla acik dosya kolu olur; RotatingFileHandler rollover
# Windows'ta rename yaparken WinError 32 (dosya kullanimda) verir. Tek instance ile
# yalnizca bir acik kol var -> rollover sorunsuz (AYNI PROCESS icin; farkli process'lerin
# kendi handler'lari icin SafeRotatingFileHandler yukarida ayrica korur).
_STREAM = logging.StreamHandler(sys.stdout)
_STREAM.setFormatter(_FMT)
# Rotasyon: bot.log sinirsiz buyumesin (2.7GB olmustu -> disk/bellek baskisi).
# 50MB x 5 backup = ~250MB tavan.
_FILE = SafeRotatingFileHandler(f"{cfg.LOG_DIR}/bot.log", maxBytes=50 * 1024 * 1024,
                                backupCount=5, encoding="utf-8")
_FILE.setFormatter(_FMT)

def get_logger(name: str) -> logging.Logger:
    log = logging.getLogger(name)
    if log.handlers: return log
    log.setLevel(logging.INFO)
    log.addHandler(_STREAM)
    log.addHandler(_FILE)
    return log
