"""engine/g_live.py — STRATEJI G (funding-konumlanma kontraryan) GERCEK EMIR. VARSAYILAN KAPALI.

Sikı test gecti (permutasyon p=0.0245, fee/4-ceyrek/pct/hold/W platolari; bkz
[[funding-positioning-validated-aug2026]]). Fiyat-DISI edge: kalabalik funding uctayken (asiri-long
-> yuksek funding / asiri-short -> negatif) TERSINE gir. Kalan tek bilinmeyen: gercek fill/slippage.
Bu modul KUCUK boyutla ($10 x3) onu olcer.

TASARIM (dumpfade_live.py sablonu):
- Ana bottan (D) TAMAMEN BAGIMSIZ: kendi thread, kendi DB tablosu (g_live), kendi gunluk-zarar
  guard'i, kendi imzali istekleri (saf urllib+hmac). D'yi ETKILEMEZ.
- ETH/AVAX (likit — fill guvenli). Funding uc = rolling coin-basi %15 (look-ahead yok, backtest ile ayni).
- Giris MARKET (backtest funding-ani fiyatina sadik; likit coinde slippage kucuk, fee12'ye dahil).
- Cikis: 24h tutus (backtest ile ayni) + felaket-SL (-10%%, guvenlik; backtest'te yoktu ama nadir teter).
- Boyut: V3_G_MARGIN_USD x V3_G_LEVERAGE (10x3=$30 notional). config-gate V3_G_LIVE=false varsayilan.
- Tek-instance kilit (port 57602): ayri script import edip live_tick tetiklerse IKINCI emir-thread'i olmaz.
GERCEK EMIR — kullanicinin acik onayi + V3_G_LIVE=true olmadan HICBIR emir vermez.
"""
from __future__ import annotations
import json, time, hmac, hashlib, urllib.request, urllib.parse, urllib.error, sqlite3, threading, socket
from decimal import Decimal, ROUND_DOWN

from core.config import cfg
from core.logger import get_logger

log = get_logger("G-Live")

COINS = ["ETHUSDT", "AVAXUSDT"]
W = 120           # rolling funding penceresi (40 gun)
PCT = 0.15        # uc yuzdelik
HOLD_H = 24       # tutus (backtest ile ayni)
STOP_PCT = -10.0  # felaket-SL (fiyat, aleyhte %); backtest'te yoktu, nadir teter
FEE_EST = 12.0    # log icin (giris+cikis taker + slippage tahmini)
MAX_DAILY_LOSS_PCT = 15.0   # kendi gunluk-zarar limiti
LOCK_PORT = 57602

_open: dict = {}          # symbol -> pozisyon kaydi
_lock = threading.Lock()
_thread_started = False
_thread_lock = threading.Lock()
_lock_socket = None
_filters: dict = {}
_last_funding_done: dict = {}   # symbol -> son islenen fundingTime


# ───────── DB (kendi tablosu, dogrudan sqlite) ─────────
def _db():
    c = sqlite3.connect(cfg.DB_PATH, timeout=15)
    c.execute("PRAGMA busy_timeout=15000")
    c.execute("""CREATE TABLE IF NOT EXISTS g_live(
        id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, open_ts INTEGER, open_human TEXT,
        side TEXT, funding REAL, entry_px REAL, qty REAL, order_id TEXT,
        close_ts INTEGER, exit_px REAL, pnl_bps REAL, pnl_usd REAL, reason TEXT, status TEXT)""")
    return c

def _log_open(sym, side, funding, entry, qty, oid) -> int:
    c = _db()
    cur = c.execute("INSERT INTO g_live(symbol,open_ts,open_human,side,funding,entry_px,qty,order_id,status)"
                    " VALUES(?,?,?,?,?,?,?,?,'OPEN')",
                    (sym, int(time.time()), time.strftime("%Y-%m-%d %H:%M"), side, funding, entry, qty, str(oid)))
    c.commit(); rid = cur.lastrowid; c.close(); return rid

def _log_close(rid, exit_px, pnl_bps, pnl_usd, reason):
    c = _db()
    c.execute("UPDATE g_live SET close_ts=?,exit_px=?,pnl_bps=?,pnl_usd=?,reason=?,status='CLOSED' WHERE id=?",
              (int(time.time()), exit_px, round(pnl_bps, 1), round(pnl_usd, 4), reason, rid))
    c.commit(); c.close()

def _get_open_db():
    c = _db()
    rows = c.execute("SELECT id,symbol,side,entry_px,qty,open_ts,funding FROM g_live WHERE status='OPEN'").fetchall()
    c.close(); return rows

def _today_realized_usd() -> float:
    day0 = int(time.time() // 86400) * 86400
    c = _db()
    r = c.execute("SELECT COALESCE(SUM(pnl_usd),0) FROM g_live WHERE status='CLOSED' AND close_ts>=?", (day0,)).fetchone()
    c.close(); return float(r[0] or 0)


# ───────── HTTP / imza ─────────
def _get(u, timeout=12):
    return json.loads(urllib.request.urlopen(u, timeout=timeout).read())

def _signed(method, path, params=None, timeout=12):
    if not cfg.API_KEY or not cfg.API_SECRET:
        return {}
    p = dict(params or {}); p["timestamp"] = int(time.time() * 1000); p["recvWindow"] = 5000
    qs = urllib.parse.urlencode(p)
    sig = hmac.new(cfg.API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    req = urllib.request.Request(f"{cfg.REST}{path}?{qs}&signature={sig}", method=method,
                                 headers={"X-MBX-APIKEY": cfg.API_KEY})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try: body = json.loads(e.read())
        except Exception: body = {"code": e.code, "msg": str(e)}
        log.warning(f"[G-LIVE] {method} {path}: {body}"); return body
    except Exception as ex:
        log.warning(f"[G-LIVE] {method} {path}: {ex}"); return {}

def _round_step(v, step):
    if step <= 0: return v
    return float((Decimal(str(v)) / Decimal(str(step))).quantize(Decimal("1"), rounding=ROUND_DOWN) * Decimal(str(step)))

def _fmt(v, step):
    v = _round_step(v, step); s = f"{step:.10f}".rstrip("0")
    d = len(s.split(".")[1]) if "." in s else 0
    return f"{v:.{d}f}"

def _load_filters():
    if _filters: return
    try: info = _get(f"{cfg.REST}/fapi/v1/exchangeInfo")
    except Exception: return
    for s in info.get("symbols", []):
        if s.get("symbol") not in COINS: continue
        step, tick, minn = 0.001, 0.01, 5.0
        for f in s.get("filters", []):
            if f.get("filterType") == "LOT_SIZE": step = float(f.get("stepSize", step))
            elif f.get("filterType") == "MIN_NOTIONAL": minn = float(f.get("notional", f.get("minNotional", minn)))
        _filters[s["symbol"]] = (step, minn)

def _mark(sym):
    try: return float(_get(f"{cfg.REST}/fapi/v1/ticker/price?symbol={sym}").get("price", 0) or 0)
    except Exception: return 0.0

def _equity():
    r = _signed("GET", "/fapi/v2/balance", {})
    if isinstance(r, list):
        for a in r:
            if a.get("asset") == "USDT": return float(a.get("balance", 0) or 0)
    return 0.0


# ───────── funding sinyal (backtest ile ayni) ─────────
def _funding_signal(sym):
    """Donus: (side, funding_rate, funding_time) veya None. side: -1 SHORT / +1 LONG (kontraryan)."""
    try:
        fr = [(int(x["fundingTime"]) // 1000, float(x["fundingRate"]))
              for x in _get(f"{cfg.REST}/fapi/v1/fundingRate?symbol={sym}&limit={W + 2}")]
    except Exception:
        return None
    if len(fr) < W + 1:
        return None
    ts, rate = fr[-1]
    past = sorted(r for _, r in fr[-W - 1:-1])
    hi = past[int(W * (1 - PCT))]; lo = past[int(W * PCT)]
    if rate >= hi:   return (-1, rate, ts)   # kalabalik asiri-long -> SHORT
    if rate <= lo:   return (1, rate, ts)    # kalabalik asiri-short -> LONG
    return (0, rate, ts)


# ───────── guard ─────────
def _can_trade():
    eq = _equity()
    if eq <= 0: return True
    pct = _today_realized_usd() / eq * 100.0
    if pct <= -MAX_DAILY_LOSS_PCT:
        log.warning(f"[G-LIVE] KENDI gunluk-zarar limiti ({pct:.1f}%) — bugun yeni giris yok (D etkilenmedi)")
        return False
    return True


# ───────── emir ─────────
def _set_lev(sym):
    _signed("POST", "/fapi/v1/marginType", {"symbol": sym, "marginType": cfg.MARGIN})
    r = _signed("POST", "/fapi/v1/leverage", {"symbol": sym, "leverage": int(cfg.V3_G_LEVERAGE)})
    return isinstance(r, dict) and "leverage" in r

def _enter(sym, side, funding, ftime):
    if not _set_lev(sym): return
    _load_filters()
    step, minn = _filters.get(sym, (0.001, 5.0))
    px = _mark(sym)
    if px <= 0: return
    notional = float(cfg.V3_G_MARGIN_USD) * float(cfg.V3_G_LEVERAGE)
    qty = _round_step(notional / px, step)
    if qty <= 0 or qty * px < minn:
        log.info(f"[G-LIVE] {sym} miktar/min-notional yetersiz, atlandi"); return
    order_side = "BUY" if side == 1 else "SELL"
    r = _signed("POST", "/fapi/v1/order", {"symbol": sym, "side": order_side, "type": "MARKET",
                                           "quantity": _fmt(qty, step), "positionSide": "BOTH"})
    if not isinstance(r, dict) or not r.get("orderId"):
        log.warning(f"[G-LIVE] {sym} giris emri basarisiz: {r}"); return
    entry_px = float(r.get("avgPrice") or 0) or px
    rid = _log_open(sym, "LONG" if side == 1 else "SHORT", funding, entry_px, qty, r.get("orderId"))
    with _lock:
        _open[sym] = {"db_id": rid, "side": side, "entry_px": entry_px, "qty": qty,
                      "open_ts": int(time.time()), "funding_time": ftime}
        _last_funding_done[sym] = ftime
    log.warning(f"[G-LIVE] GIRIS {sym} {'LONG' if side==1 else 'SHORT'} @{entry_px:.4g} "
                f"qty={qty:g} funding={funding*100:+.4f}% (${notional:.0f} notional)")

def _exit(sym, rec, reason):
    step, _ = _filters.get(sym, (0.001, 5.0))
    close_side = "SELL" if rec["side"] == 1 else "BUY"
    r = _signed("POST", "/fapi/v1/order", {"symbol": sym, "side": close_side, "type": "MARKET",
                                           "quantity": _fmt(rec["qty"], step), "reduceOnly": "true",
                                           "positionSide": "BOTH"})
    ex = float(r.get("avgPrice") or 0) if isinstance(r, dict) else 0.0
    if ex <= 0: ex = _mark(sym)
    ent = rec["entry_px"]
    raw = ((ex - ent) if rec["side"] == 1 else (ent - ex)) / ent * 1e4
    pnl_bps = raw - FEE_EST
    pnl_usd = raw / 1e4 * (rec["qty"] * ent)   # notional bazli yaklasik
    _log_close(rec["db_id"], ex, pnl_bps, pnl_usd, reason)
    with _lock:
        _open.pop(sym, None)
    log.warning(f"[G-LIVE] CIKIS {sym} ({reason}) @{ex:.4g} = {pnl_bps:+.0f}bps (${pnl_usd:+.3f})")


# ───────── dongu ─────────
def _recover():
    for row in _get_open_db():
        rid, sym, side, ent, qty, ots, funding = row
        with _lock:
            _open[sym] = {"db_id": rid, "side": 1 if side == "LONG" else -1, "entry_px": ent,
                          "qty": qty, "open_ts": ots, "funding_time": 0}
    if _open:
        log.info(f"[G-LIVE] restart-restore: {len(_open)} acik pozisyon geri yuklendi ({list(_open)})")

def _tick():
    # 1) acik pozisyonlari yonet (SL + 24h)
    with _lock:
        syms = list(_open.keys())
    for sym in syms:
        with _lock:
            rec = dict(_open.get(sym) or {})
        if not rec: continue
        px = _mark(sym)
        if px <= 0: continue
        cur_pct = ((px - rec["entry_px"]) if rec["side"] == 1 else (rec["entry_px"] - px)) / rec["entry_px"] * 100
        held_h = (time.time() - rec["open_ts"]) / 3600.0
        if cur_pct <= STOP_PCT:
            _exit(sym, rec, "sl")
        elif held_h >= HOLD_H:
            _exit(sym, rec, "24h")
    # 2) yeni giris (funding uc + guard + pozisyon yoksa)
    if not _can_trade():
        return
    for sym in COINS:
        with _lock:
            if sym in _open: continue
        sig = _funding_signal(sym)
        if not sig: continue
        side, funding, ftime = sig
        if side == 0: continue
        if _last_funding_done.get(sym) == ftime:   # bu funding-donemi zaten islendi
            continue
        _last_funding_done[sym] = ftime
        _enter(sym, side, funding, ftime)


def _run_forever():
    log.warning(f"[G-LIVE] GERCEK EMIR AKTIF — ETH/AVAX, margin=${cfg.V3_G_MARGIN_USD} x{cfg.V3_G_LEVERAGE}, "
                f"funding-uc %{int(PCT*100)}, tutus {HOLD_H}h, SL {STOP_PCT}%")
    while True:
        try:
            _tick()
        except Exception as ex:
            log.error(f"[G-LIVE] dongu hatasi: {ex}")
        time.sleep(60)


def _acquire_lock() -> bool:
    global _lock_socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", LOCK_PORT)); s.listen(1); _lock_socket = s; return True
    except OSError:
        s.close(); return False


def live_tick() -> None:
    """decision_v3'ten cagrilir; ilk cagrida kalici thread baslatir. config-gate KAPALIYSA no-op."""
    global _thread_started
    from core.config import cfg as _c
    if not bool(getattr(_c, "V3_G_LIVE", False)):
        return
    try:
        from core.config import is_paper_mode
        if is_paper_mode(): return
    except Exception:
        pass
    with _thread_lock:
        if _thread_started: return
        _thread_started = True
    if not _acquire_lock():
        log.warning("[G-LIVE] BASKA PROCESS AKTIF (tek-instance kilit) — emir-thread BASLATILMADI")
        return
    _recover()
    threading.Thread(target=_run_forever, name="g-live", daemon=True).start()
