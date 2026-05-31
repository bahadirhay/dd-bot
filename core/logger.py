"""core/logger.py"""
import logging, sys
from core.config import cfg
import os; os.makedirs(cfg.LOG_DIR, exist_ok=True)

def get_logger(name: str) -> logging.Logger:
    log = logging.getLogger(name)
    if log.handlers: return log
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s  [%(name)-18s]  %(message)s",
                            datefmt="%H:%M:%S")
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(fmt)
    fh = logging.FileHandler(f"{cfg.LOG_DIR}/bot.log", encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(h)
    log.addHandler(fh)
    return log
