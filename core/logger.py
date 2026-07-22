"""core/logger.py"""
import logging, sys
from logging.handlers import RotatingFileHandler
from core.config import cfg
import os; os.makedirs(cfg.LOG_DIR, exist_ok=True)

_FMT = logging.Formatter("%(asctime)s  %(levelname)-7s  [%(name)-18s]  %(message)s",
                         datefmt="%H:%M:%S")

# TEK paylasilan handler seti. Her logger kendi handler'ini yaratirsa (onceki hali)
# ayni bot.log'a birden fazla acik dosya kolu olur; RotatingFileHandler rollover
# Windows'ta rename yaparken WinError 32 (dosya kullanimda) verir. Tek instance ile
# yalnizca bir acik kol var -> rollover sorunsuz.
_STREAM = logging.StreamHandler(sys.stdout)
_STREAM.setFormatter(_FMT)
# Rotasyon: bot.log sinirsiz buyumesin (2.7GB olmustu -> disk/bellek baskisi).
# 50MB x 5 backup = ~250MB tavan.
_FILE = RotatingFileHandler(f"{cfg.LOG_DIR}/bot.log", maxBytes=50 * 1024 * 1024,
                            backupCount=5, encoding="utf-8")
_FILE.setFormatter(_FMT)

def get_logger(name: str) -> logging.Logger:
    log = logging.getLogger(name)
    if log.handlers: return log
    log.setLevel(logging.INFO)
    log.addHandler(_STREAM)
    log.addHandler(_FILE)
    return log
