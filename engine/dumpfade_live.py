"""
engine/dumpfade_live.py — Cross-sectional DUMP-FADE GERCEK EMIR (kucuk boyut, daraltilmis likit evren).

KULLANICI ONAYIYLA acilan ILK canli-emir fazi. Amac: engine/dumpfade_paper.py'nin forward-dogrulanmis
edge'inin (bkz memory dumpfade-liquidity-jul2026: 07-15-sonrasi dogru filtreyle 23/24 islem net +239bps/
islem, backtest +276bps'e uyuyor) GERCEK fill-rate/slippage'ini olcmek — hala kucuk boyutla. Ayni zamanda
scripts/_dumpfade_stop_wf.py'de 246-sembol/~520-gun/731-islem 4-pencereli walk-forward ile bulunan -%15
per-islem stop-loss'u (TEK esik: 4/4 pencere pozitif, stopsuz'a gore +%18 net) canliya tasir.

TASARIM KARARLARI (neden boyle):
- Ana bottan (core/state.py, cfg.SYMBOL=ETHUSDT tek-pozisyon mimarisi) TAMAMEN BAGIMSIZ: kendi
  bellek-ici state'i (sembol-keyed dict), kendi DB tablosu (dumpfade_live), kendi thread'i. Ana botun
  execution/executor.py'daki PAYLASILAN global aiohttp session'ini KULLANMAZ (farkli event-loop'ta
  reuse edilirse cakisir) — bu yuzden asyncio/aiohttp DEGIL, saf senkron urllib+hmac (dumpfade_paper.py
  ile ayni stil, + imzali istekler icin kucuk bir ek).
- Daraltilmis evren (~20 taninan likit major, SYN/EVAA/LAB tarzi exotic/micro-cap YOK): ilk-kez
  cok-sembollu gercek-emir kodunda tick-size/precision edge-case riskini azaltir.
- $10 margin x5 kaldirac = ana bot ile AYNI (cfg.TRADE_MARGIN_USD/LEVERAGE/MARGIN) — kullanicinin
  "zaten kucuk oynuyoruz" talebi.
- KENDI gunluk zarar limiti (DF_MAX_DAILY_LOSS_PCT, varsayilan %15) — D'nin core/daily_loss_guard.py
  guard'indan KASITLI olarak BAGIMSIZ. dumpfade kotu giderse SADECE dumpfade durur, D'nin yeni giris
  acmasi ASLA engellenmez (kullanici karari, 2026-07-25).
- Restart-guvenli: ilk live_tick() cagrisinda DB'den acik/bekleyen kayitlar geri yuklenir (bellek-ici
  _open kaybolursa bot restart sonrasi izlenmeyen acik pozisyon KALMAZ).

cfg.V3_DUMPFADE_LIVE=False VARSAYILAN (core/config.py). Acmadan once bu dosyayi ve memory/
dumpfade-liquidity-jul2026.md'yi oku.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import ROUND_DOWN, Decimal

from core.config import cfg, is_paper_mode
from core.logger import get_logger

log = get_logger("DumpFadeLive")

# Daraltilmis likit-major evren — exotic/micro-cap YOK (bkz modul docstring).
UNIVERSE = [
    "SOLUSDT", "LINKUSDT", "AVAXUSDT", "DOGEUSDT", "ADAUSDT", "DOTUSDT", "LTCUSDT", "BCHUSDT",
    "ATOMUSDT", "NEARUSDT", "APTUSDT", "ARBUSDT", "OPUSDT", "SUIUSDT", "XRPUSDT", "XLMUSDT",
    "UNIUSDT", "FILUSDT", "INJUSDT", "TRXUSDT",
]

DUMP_PCT = 12.5
DUMP_CAP = 30.0
OFFSET_BPS = 300.0
STOP_PCT = -15.0          # scripts/_dumpfade_stop_wf.py: 4/4 pencere pozitif, +%18 net vs stopsuz
FEE_BPS_EST = 10.0        # giris maker + cikis (gun-sonu maker VARSAYIM / stop taker GERCEK - kalibre degil)
MIN_QVOL = 25e6
MIN_DAYS = 60
MAX_CONCURRENT = 3
POLL_SEC = 45

# KENDI gunluk zarar limiti — D'nin core/daily_loss_guard.py guard'indan KASITLI olarak AYRI ve
# BAGIMSIZ (kullanici karari: dumpfade'in kotu gunu D'nin yeni giris acmasini ASLA engellemesin).
# equity $144'te iken tek -%15 stop ~-%5.2; bu limit ~2 tam stop'a izin verir, sonra SADECE
# dumpfade durur, D etkilenmez.
DF_MAX_DAILY_LOSS_PCT = 15.0

_open: dict[str, dict] = {}
_lock = threading.Lock()
_filters_cache: dict[str, tuple[float, float, float]] = {}
_last_day = -1
_thread_started = False
_thread_lock = threading.Lock()
_recovered = False


# KENDI guard'i BILEREK stateless/DB-turetilmis: hafiza-ici sayac TUTMAZ (bot restart olsa da
# gunun gerceklesmis zarari kaybolmaz; ayni fonksiyon dashboard'dan (AYRI process) cagrilsa da
# DOGRU sonuc verir — in-memory bir sayac iki process arasinda paylasilamazdi).

def _df_day_key_utc() -> int:
    from datetime import datetime, timezone
    n = datetime.now(timezone.utc)
    return n.year * 10000 + n.month * 100 + n.day


def _utc_day_bounds(day_key: int) -> tuple[float, float]:
    from datetime import datetime, timezone
    y, m, d = day_key // 10000, (day_key // 100) % 100, day_key % 100
    start = datetime(y, m, d, tzinfo=timezone.utc).timestamp()
    return start, start + 86400.0


def _today_realized_dollar_pnl() -> float:
    """Bugun (UTC) KAPANMIS dumpfade_live islemlerinin gerceklesmis dolar pnl toplami — DB'den."""
    import sqlite3
    try:
        start, end = _utc_day_bounds(_df_day_key_utc())
        c = sqlite3.connect(f"file:{cfg.DB_PATH}?mode=ro", uri=True)
        rows = c.execute(
            "SELECT qty, entry_px, exit_px FROM dumpfade_live WHERE status='CLOSED' "
            "AND close_ts >= ? AND close_ts < ?", (start, end)).fetchall()
        return sum((r[2] - r[1]) * r[0] for r in rows if r[0] and r[1] and r[2])
    except Exception:
        return 0.0


def df_guard_status() -> dict:
    """Bagimsiz kendi guard'inin GUNCEL durumu — her cagrida DB+equity'den yeniden hesaplanir."""
    dollar_pnl = _today_realized_dollar_pnl()
    equity = _account_equity()
    pct = (dollar_pnl / equity * 100.0) if equity > 0 else 0.0
    return {"halted": pct <= -DF_MAX_DAILY_LOSS_PCT, "cumulative_pnl_pct": pct, "max_loss_pct": DF_MAX_DAILY_LOSS_PCT}


def _df_can_trade() -> bool:
    return not df_guard_status()["halted"]


# ───────────────────────── HTTP / imza ─────────────────────────

def _get(u: str, timeout: int = 15):
    return json.loads(urllib.request.urlopen(u, timeout=timeout).read())


def _now_ms() -> int:
    return int(time.time() * 1000)


def _signed(method: str, path: str, params: dict | None = None, timeout: int = 15) -> dict | list:
    if not cfg.API_KEY or not cfg.API_SECRET:
        return {}
    p = dict(params or {})
    p["timestamp"] = _now_ms()
    p["recvWindow"] = 5000
    qs = urllib.parse.urlencode(p)
    sig = hmac.new(cfg.API_SECRET.encode("utf-8"), qs.encode("utf-8"), hashlib.sha256).hexdigest()
    url = f"{cfg.REST}{path}?{qs}&signature={sig}"
    req = urllib.request.Request(url, method=method, headers={"X-MBX-APIKEY": cfg.API_KEY})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read())
        except Exception:
            body = {"code": e.code, "msg": str(e)}
        log.warning(f"[DUMPFADE-LIVE] {method} {path}: {body}")
        return body
    except Exception as ex:
        log.warning(f"[DUMPFADE-LIVE] {method} {path}: {ex}")
        return {}


def _round_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    v = Decimal(str(value))
    s = Decimal(str(step))
    return float((v / s).quantize(Decimal("1"), rounding=ROUND_DOWN) * s)


def _fmt(value: float, step: float) -> str:
    v = _round_step(value, step)
    s = f"{step:.10f}".rstrip("0")
    decimals = len(s.split(".")[1]) if "." in s else 0
    return f"{v:.{decimals}f}"


def _ensure_filters_loaded() -> None:
    if _filters_cache:
        return
    try:
        info = _get(f"{cfg.REST}/fapi/v1/exchangeInfo")
    except Exception as ex:
        log.warning(f"[DUMPFADE-LIVE] exchangeInfo alinamadi: {ex}")
        return
    for s in info.get("symbols", []):
        sym = s.get("symbol")
        if sym not in UNIVERSE:
            continue
        step, tick, min_n = 0.001, 0.01, 5.0
        for f in s.get("filters", []):
            ft = f.get("filterType")
            if ft == "LOT_SIZE":
                step = float(f.get("stepSize", step))
            elif ft == "PRICE_FILTER":
                tick = float(f.get("tickSize", tick))
            elif ft == "MIN_NOTIONAL":
                min_n = float(f.get("notional", f.get("minNotional", min_n)))
        _filters_cache[sym] = (step, tick, min_n)


def _mark_price(symbol: str) -> float:
    try:
        r = _get(f"{cfg.REST}/fapi/v1/ticker/price?symbol={symbol}")
        return float(r.get("price", 0) or 0)
    except Exception:
        return 0.0


def _account_equity() -> float:
    try:
        r = _signed("GET", "/fapi/v2/balance", {})
        if isinstance(r, list):
            for a in r:
                if a.get("asset") == "USDT":
                    return float(a.get("balance", 0) or 0)
    except Exception:
        pass
    return 0.0


def _daily(sym: str, limit: int = 66):
    try:
        r = _get(f"{cfg.REST}/fapi/v1/klines?symbol={sym}&interval=1d&limit={limit}")
        return [(int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[7])) for x in r]
    except Exception:
        return None


def _day_key() -> int:
    return int(time.time() // 86400)


# ───────────────────────── state / restart-recovery ─────────────────────────

def _recover_from_db() -> None:
    global _recovered
    if _recovered:
        return
    _recovered = True
    try:
        from botlog.db import get_open_dumpfade_live
        rows = get_open_dumpfade_live()
    except Exception as ex:
        log.warning(f"[DUMPFADE-LIVE] DB restore hatasi: {ex}")
        return
    with _lock:
        for row in rows:
            sym = row["symbol"]
            _open[sym] = {
                "db_id": row["id"], "order_id": row["order_id"] or "", "day_key": row["day_key"],
                "limit_px": row["limit_px"], "status": row["status"],
                "qty": row["qty"] or 0.0, "entry_px": row["entry_px"] or 0.0,
            }
    if _open:
        log.info(f"[DUMPFADE-LIVE] restart-restore: {len(_open)} acik/bekleyen kayit geri yuklendi ({list(_open)})")


# ───────────────────────── emir yonetimi ─────────────────────────

def _ensure_leverage_margin(symbol: str) -> bool:
    r1 = _signed("POST", "/fapi/v1/marginType", {"symbol": symbol, "marginType": cfg.MARGIN})
    code = int(r1.get("code", 0) or 0) if isinstance(r1, dict) else 0
    if code not in (0, 200, -4046):
        log.debug(f"[DUMPFADE-LIVE] {symbol} marjin-tipi yaniti: {r1}")
    r2 = _signed("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": int(cfg.LEVERAGE)})
    if not (isinstance(r2, dict) and "leverage" in r2):
        log.error(f"[DUMPFADE-LIVE] {symbol} kaldirac ayarlanamadi: {r2}")
        return False
    return True


def _place_entry(symbol: str, day_key: int, dump: float, day_open: float, limit_px: float) -> None:
    from botlog.db import log_dumpfade_live_pending

    if not _ensure_leverage_margin(symbol):
        return
    step, tick, min_n = _filters_cache.get(symbol, (0.001, 0.01, 5.0))
    notional = float(cfg.TRADE_MARGIN_USD) * float(cfg.LEVERAGE)
    qty = _round_step(notional / limit_px, step)
    if qty <= 0:
        log.info(f"[DUMPFADE-LIVE] {symbol}: miktar adima gore sifir, atlandi")
        return
    if qty * limit_px < min_n:
        log.info(f"[DUMPFADE-LIVE] {symbol}: notional min-notional altinda, atlandi")
        return
    r = _signed("POST", "/fapi/v1/order", {
        "symbol": symbol, "side": "BUY", "type": "LIMIT", "timeInForce": "GTX",
        "quantity": _fmt(qty, step), "price": _fmt(limit_px, tick), "positionSide": "BOTH",
    })
    if not isinstance(r, dict) or not r.get("orderId"):
        log.warning(f"[DUMPFADE-LIVE] {symbol} giris emri basarisiz: {r}")
        return
    st = str(r.get("status", ""))
    if st in ("REJECTED", "EXPIRED"):
        log.info(f"[DUMPFADE-LIVE] {symbol} GTX reddedildi (caprazlardi) — atlandi")
        return
    rid = log_dumpfade_live_pending(symbol, day_key, dump, day_open, limit_px, str(r.get("orderId")))
    with _lock:
        _open[symbol] = {"db_id": rid, "order_id": str(r.get("orderId")), "day_key": day_key,
                          "limit_px": limit_px, "status": "PENDING", "qty": qty, "entry_px": 0.0}
    log.info(f"[DUMPFADE-LIVE] {symbol} dump{dump:.1f}% -> LIMIT BUY {qty:g} @ {limit_px:.6g} kondu")


def _daily_scan_and_enter(dk: int) -> None:
    from botlog.db import dumpfade_live_seen

    if is_paper_mode():
        return
    if not _df_can_trade():
        log.warning("[DUMPFADE-LIVE] KENDI gunluk zarar limiti — bugun yeni giris yok (D etkilenmedi)")
        return
    _ensure_filters_loaded()
    for sym in UNIVERSE:
        try:
            with _lock:
                n_open = len(_open)
                already = sym in _open
            if n_open >= MAX_CONCURRENT:
                log.info(f"[DUMPFADE-LIVE] max concurrent ({MAX_CONCURRENT}) doldu, tarama durduruldu")
                break
            if already or dumpfade_live_seen(sym, dk):
                continue
            bars = _daily(sym, 66)
            if not bars or len(bars) < MIN_DAYS + 2:
                continue
            vols = [b[5] for b in bars[-31:-1] if b[5] > 0]
            if not vols or (sum(vols) / len(vols)) < MIN_QVOL:
                continue
            D = bars[-2]                       # son TAMAMLANMIS gun = dump gunu
            D1 = bars[-1]                       # bugun (forming) = giris gunu, open zaten belli
            o_d, c_d = D[1], D[4]
            if o_d <= 0:
                continue
            dump = (c_d - o_d) / o_d * 100
            if dump > -DUMP_PCT or dump <= -DUMP_CAP:
                continue
            o1 = D1[1]
            if o1 <= 0:
                continue
            lim = o1 * (1 - OFFSET_BPS / 1e4)
            _place_entry(sym, dk, dump, o1, lim)
        except Exception as ex:
            log.debug(f"[DUMPFADE-LIVE] {sym} tarama hatasi: {ex}")


def _manage_open() -> None:
    from botlog.db import log_dumpfade_live_close, mark_dumpfade_live_cancelled, mark_dumpfade_live_filled

    if is_paper_mode():
        return
    dk = _day_key()
    with _lock:
        symbols = list(_open.keys())
    for symbol in symbols:
        with _lock:
            rec = dict(_open.get(symbol) or {})
        if not rec:
            continue
        try:
            if rec["status"] == "PENDING":
                r = _signed("GET", "/fapi/v1/order", {"symbol": symbol, "orderId": rec["order_id"]})
                st = str(r.get("status", "")) if isinstance(r, dict) else ""
                if st == "FILLED":
                    entry_px = float(r.get("avgPrice") or 0) or rec["limit_px"]
                    mark_dumpfade_live_filled(rec["db_id"], entry_px, rec["qty"],
                                               float(cfg.TRADE_MARGIN_USD), int(cfg.LEVERAGE))
                    with _lock:
                        if symbol in _open:
                            _open[symbol]["status"] = "OPEN"
                            _open[symbol]["entry_px"] = entry_px
                    log.info(f"[DUMPFADE-LIVE] {symbol} DOLDU @ {entry_px:.6g}")
                elif dk != rec["day_key"]:
                    _signed("DELETE", "/fapi/v1/order", {"symbol": symbol, "orderId": rec["order_id"]})
                    mark_dumpfade_live_cancelled(rec["db_id"])
                    with _lock:
                        _open.pop(symbol, None)
                    log.info(f"[DUMPFADE-LIVE] {symbol} gun bitti, dolmadi -> iptal")
            elif rec["status"] == "OPEN":
                px = _mark_price(symbol)
                entry_px = float(rec.get("entry_px") or 0)
                if px <= 0 or entry_px <= 0:
                    continue
                pct = (px - entry_px) / entry_px * 100.0
                reason = ""
                if pct <= STOP_PCT:
                    reason = "stop"
                elif dk != rec["day_key"]:
                    reason = "day_close"
                if not reason:
                    continue
                step, _tick, _minn = _filters_cache.get(symbol, (0.001, 0.01, 5.0))
                r = _signed("POST", "/fapi/v1/order", {
                    "symbol": symbol, "side": "SELL", "type": "MARKET",
                    "quantity": _fmt(rec["qty"], step), "reduceOnly": "true", "positionSide": "BOTH",
                })
                exit_px = float(r.get("avgPrice") or 0) if isinstance(r, dict) else 0.0
                if exit_px <= 0:
                    exit_px = px
                pnl_bps = (exit_px - entry_px) / entry_px * 1e4 - FEE_BPS_EST
                log_dumpfade_live_close(rec["db_id"], exit_px, pnl_bps, reason)
                # KENDI guard'i DB'den turer (_today_realized_dollar_pnl) — burada ayrica
                # kaydetmeye gerek yok, log_dumpfade_live_close zaten satiri yazdi.
                with _lock:
                    _open.pop(symbol, None)
                log.info(f"[DUMPFADE-LIVE] {symbol} KAPANDI ({reason}) @ {exit_px:.6g} = {pnl_bps:+.0f}bps")
        except Exception as ex:
            log.error(f"[DUMPFADE-LIVE] {symbol} yonetim hatasi: {ex}")


# ───────────────────────── giris noktasi ─────────────────────────

_lock_socket = None  # module-level: acik tutulur, GC/process-exit'e kadar portu isgal eder


def _acquire_single_instance_lock() -> bool:
    """SADECE bir process'te dumpfade-live gercek-emir thread'i calissin. dashboard/app.py gibi
    ayri bir script bu modulu import edip (dogrudan/dolayli) live_tick() tetiklerse, IKINCI bir
    gercek-emir thread'i baslamasin — sabit bir localhost porta bind ederek kilit kurulur; port
    zaten kullanimdaysa (baska process zaten kilidi tutuyor) False doner, thread BASLAMAZ."""
    global _lock_socket
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 57601))
        s.listen(1)
        _lock_socket = s
        return True
    except OSError:
        s.close()
        return False


def live_tick() -> None:
    """decision_v3.update_decision()'dan cagrilir (dumpfade_tick ile ayni desen). Ilk cagrida
    kalici bir izleme thread'i baslatir (day-gated tek-atim DEGIL — acik pozisyonlar gun boyu
    stop icin izlenmeli)."""
    global _thread_started
    if not bool(getattr(cfg, "V3_DUMPFADE_LIVE", False)):
        return
    if is_paper_mode():
        return
    with _thread_lock:
        if _thread_started:
            return
        _thread_started = True
    if not _acquire_single_instance_lock():
        log.warning("[DUMPFADE-LIVE] BASKA BIR PROCESS ZATEN AKTIF (tek-instance kilidi) — "
                    "bu process'te gercek-emir thread'i BASLATILMADI (guvenlik)")
        return
    _recover_from_db()
    threading.Thread(target=_run_forever, name="dumpfade-live", daemon=True).start()
    log.warning(f"[DUMPFADE-LIVE] GERCEK EMIR AKTIF — evren={len(UNIVERSE)} sembol, "
                f"margin=${cfg.TRADE_MARGIN_USD} x{cfg.LEVERAGE}, stop={STOP_PCT}%, max_pos={MAX_CONCURRENT}")


def _run_forever() -> None:
    global _last_day
    while True:
        try:
            dk = _day_key()
            if dk != _last_day:
                _last_day = dk
                _daily_scan_and_enter(dk)
            _manage_open()
        except Exception as ex:
            log.error(f"[DUMPFADE-LIVE] dongu hatasi: {ex}")
        time.sleep(POLL_SEC)
