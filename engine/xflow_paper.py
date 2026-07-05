"""
engine/xflow_paper.py — EXTREME-FLOW SHORT SHADOW. GERCEK EMIR YOK.

Bulgu (bu seans, ~41g market_snapshots, walk-forward TRAIN/OOS + esik-plato + yon-ayrimi):
  15m forming delta_sum <= ~-3500 (EXTREME satis akisi) -> maker-limit SHORT, kisa tut -> net-POZITIF
  (OOS plato -3000/-4000, mh8: +702..+1063). Iki thread birlesti: kullanicinin extreme-flow gozlemi
  ("-4500 -> dusecek") + seansin maker-execution bulgusu. YALNIZ execution-farkiyla pozitif (taker
  marjinaldi, maker net-pozitif).

ASIMETRIK (onemli): LONG tarafi (extreme ALIS -> long) hicbir ufuk/esikte robust DEGILDI -> KURULMADI.
  Kripto asagi kaskad (zorunlu satis/likidasyon) yapar; yukari FOMO-tepesi. 5m-cvd de OOS-cokuyordu ->
  15m dogru pencere. Bu yuzden shadow: SADECE SHORT, SADECE 15m-delta, esik ~-3500.

KALIBRE: 41g / tek rejim / ~95 short islem / IDEALIZE maker fill. DOGRULANMADI. Bu shadow CANLI forward'da
  olcer: gercek fill-rate + net OOS-pozitifligini koruyor mu. Tutarsa sonraki adim tartisilir. Emir YOK.

Durum makinesi: PENDING(limit kondu) -> FILLED(deger)/MISSED(pencere doldu) -> CLOSED(cikis).
"""
from __future__ import annotations

import json
import time
import urllib.request

from core.config import cfg
from core.logger import get_logger
from core.state import state

log = get_logger("XFlowPaper")

SYMBOL = "ETHUSDT"
THR = 3500.0              # 15m forming delta_sum bu deger-alti (extreme satis) -> SHORT (yalniz eksi taraf)
OFFSET = 5.0             # limit, kapanistan bu kadar bps USTUNE (SHORT maker)
FILL_WINDOW = 3          # bar; dolmazsa MISS
MHOLD = 8               # bar; backtest sweet-spot ufku (mh8)
SL = 90.0               # bps ters
COST = 8.0             # ~maker giris + taker cikis + slip (taker-round-trip ~12 idi)

_pending: dict | None = None   # {id,side,delta,lim,waited}
_pos: dict | None = None       # {id,side,fill,held}
_bar = 0
_bar_delta = 0.0               # su anki 15m barin son gorulen birikmis delta'si
_restored = False
_cache: tuple = (0, [])


def _bar_id() -> int:
    return int(time.time() // 900)


def _klines(limit: int = 20) -> list | None:
    global _cache
    day = _bar_id()
    if _cache[0] == day and _cache[1]:
        return _cache[1]
    try:
        u = ("https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=15m&limit=%d" % (SYMBOL, limit))
        r = json.loads(urllib.request.urlopen(u, timeout=12).read())
        bars = [(float(x[2]), float(x[3]), float(x[4])) for x in r]  # h,l,c
        closed = bars[:-1]
        _cache = (day, closed)
        return closed
    except Exception as ex:
        log.warning(f"[XFLOW] klines: {ex}")
        return _cache[1] or None


def _restore() -> None:
    global _pending, _pos, _restored
    if _restored:
        return
    _restored = True
    try:
        from botlog.db import get_open_xflow
        row = get_open_xflow(SYMBOL)
        if not row:
            return
        if row["status"] == "PENDING":
            _pending = {"id": row["id"], "side": row["side"], "delta": row["delta"], "lim": row["lim"], "waited": 0}
        elif row["status"] == "FILLED":
            _pos = {"id": row["id"], "side": row["side"], "fill": row["fill"] or row["lim"], "held": 0}
        log.info(f"[XFLOW] restore: {row['status']} {row['side']} (id={row['id']})")
    except Exception as ex:
        log.warning(f"[XFLOW] restore: {ex}")


def paper_tick() -> None:
    global _pending, _pos, _bar, _bar_delta
    if not bool(getattr(cfg, "V3_XFLOW_PAPER", True)):
        return
    _restore()

    # HER tick: su anki 15m barin birikmis delta'sini yakala (bar-kapanista final degeri gerekir)
    f = state.forming_15m or {}
    d = f.get("delta_sum")
    cur = _bar_id()
    if cur == _bar:
        if d is not None:
            _bar_delta = float(d)
        return

    # YENI BAR: onceki barin final delta'si _bar_delta'da; onceki bar KAPANDI.
    prev_delta = _bar_delta
    _bar = cur
    _bar_delta = float(d) if d is not None else 0.0

    from botlog.db import log_xflow_open, update_xflow_status
    bars = _klines()
    if not bars or len(bars) < 2:
        return
    i = len(bars) - 1
    h, l, c = bars[i][0], bars[i][1], bars[i][2]
    if c <= 0:
        return

    # 1) ACIK pozisyon -> cikis (SHORT: SL ters yukari, yoksa maxhold)
    if _pos:
        side = _pos["side"]; fill = _pos["fill"]; _pos["held"] += 1
        cur_bps = (fill - c) / fill * 1e4          # SHORT kar = fill - fiyat
        adv = (h - fill) / fill * 1e4              # ters = yukari
        if adv >= SL:
            update_xflow_status(_pos["id"], "CLOSED", exit_px=fill * (1 + SL / 1e4),
                                pnl_bps=round(-SL - COST, 1), reason="sl")
            log.info(f"[XFLOW] SL SHORT -{SL:.0f}bps")
            _pos = None
        elif _pos["held"] >= MHOLD:
            update_xflow_status(_pos["id"], "CLOSED", exit_px=c, pnl_bps=round(cur_bps - COST, 1), reason="maxhold")
            log.info(f"[XFLOW] maxhold SHORT +{cur_bps:.0f}bps (net {cur_bps-COST:+.0f})")
            _pos = None
        return

    # 2) BEKLEYEN limit -> doldu mu / kacti mi (SHORT limit ustte -> yukari deger dolar)
    if _pending:
        lim = _pending["lim"]; _pending["waited"] += 1
        if h >= lim:
            update_xflow_status(_pending["id"], "FILLED", fill_px=lim)
            _pos = {"id": _pending["id"], "side": "SHORT", "fill": lim, "held": 0}
            log.info(f"[XFLOW] FILLED SHORT @{lim:.2f}")
            _pending = None
        elif _pending["waited"] >= FILL_WINDOW:
            update_xflow_status(_pending["id"], "MISSED")
            log.info(f"[XFLOW] MISSED SHORT (limit {lim:.2f} {FILL_WINDOW} barda dolmadi)")
            _pending = None
        return

    # 3) FLAT -> extreme satis akisi? (YALNIZ SHORT, esik ~-3500, 15m forming delta)
    if prev_delta <= -THR:
        lim = c * (1 + OFFSET / 1e4)
        rid = log_xflow_open(SYMBOL, "SHORT", prev_delta, c, lim)
        _pending = {"id": rid, "side": "SHORT", "delta": prev_delta, "lim": lim, "waited": 0}
        log.info(f"[XFLOW] LIMIT kondu SHORT sig={c:.2f} lim={lim:.2f} delta={prev_delta:.0f} (extreme satis)")
