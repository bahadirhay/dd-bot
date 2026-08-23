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

# G coinleri. ETH/AVAX canli-pozitif (ETH +51bps, AVAX +158bps/isl).
# 2026-08-16 EKLENDI: XRP + ETC — FORWARD-shadow pozitif + islem-basi ETH/AVAX seviyesinde
# (XRP forward +840/14isl/+60bps, ETC +731/18isl/+41bps). Kucuk boyut -> downside sinirli; canlida gor.
# DISCIPLIN: INJ gibi kaybederse HIZLA CIKAR (coin-basi karar). Eklenmeyenler: AKE/BR/VELVET/RE=varyans
# tuzagi (absurt +300..1573bps=volatilite), TAO/AIO=ince edge, forward-negatifler (INJ/SUI/CL/BZ/LINK).
COINS = ["ETHUSDT", "AVAXUSDT", "XRPUSDT", "ETCUSDT"]
W = 120           # rolling funding penceresi (40 gun)
PCT = 0.15        # uc yuzdelik
HOLD_H = 24       # tutus (backtest ile ayni)
FRESH_MAX_MIN = 60  # TAZELIK: funding ancak son 60 dk icinde aciklandiysa gir (backtest funding-ani
                    # girisine sadik). Daha eski=bayat -> atla, sonraki taze aciklamayi bekle. 8h dongunun
                    # cok altinda; startup + 24h-kapanis-sonrasi re-entry'de gec/hareketli fiyattan girisi onler.
# SL KALDIRILDI (2026-08-08 kullanici: backtest'le birebir). Backtest'te stop YOK; pozisyon
# tam HOLD_H tutulur. Felaket-SL de yok artik -> risk daha yuksek, bilincli tercih.
FEE_EST = 12.0    # log icin (giris+cikis taker + slippage tahmini); backtest FEE=12 ile ayni
TREND_N = 10      # TREND-ALIGN: trend lookback (gun). Contrarian giris SADECE trendle ayni yonde.
                  # N=10: donuslere daha duyarli (N=40 dun tepeyi kacirdi). Plato N=3-40 (8/8 coin,
                  # iki-yari+) -> N=10 GUVENLI, uc degil. N=10: +84bps/isl, %61 win, denge 0.93.
                  # Kisa-N=daha cok momentum/whipsaw ama backtest net-pozitif. Permut p=0.0000.
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

def _today_realized_usd(sym=None) -> float:
    """Bugunku realized PnL. sym verilirse SADECE o coin (coin-basi guard icin)."""
    day0 = int(time.time() // 86400) * 86400
    c = _db()
    if sym:
        r = c.execute("SELECT COALESCE(SUM(pnl_usd),0) FROM g_live WHERE status='CLOSED' AND close_ts>=? AND symbol=?",
                      (day0, sym)).fetchone()
    else:
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


_dtrend_cache: dict = {}   # sym -> (trend, fetch_ts); gunluk trend saatte bir tazelenir (nadir degisir)

def _daily_trend(sym):
    """Gunluk trend yonu: +1 (yukselis) / -1 (dusus) / 0 (veri az). close[bugun] vs close[TREND_N gun once].
    TREND-ALIGN filtresi bunu kullanir: contrarian giris sadece bu yonle AYNI ise alinir."""
    now = time.time()
    c = _dtrend_cache.get(sym)
    if c and now - c[1] < 3600:
        return c[0]
    try:
        r = _get(f"{cfg.REST}/fapi/v1/klines?symbol={sym}&interval=1d&limit={TREND_N + 3}")
        closes = [float(x[4]) for x in r]
    except Exception:
        return 0
    if len(closes) < TREND_N + 1:
        return 0
    trend = 1 if closes[-1] > closes[-1 - TREND_N] else -1
    _dtrend_cache[sym] = (trend, now)
    return trend


# GUARD KALDIRILDI (2026-08-08 kullanici: backtest'le BIREBIR). Backtest'te gunluk-zarar limiti
# yoktu -> her uc funding-doneminde kosulsuz girilir. Hicbir non-backtest fren kalmadi.


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
    pos_side = "LONG" if side == 1 else "SHORT"   # HEDGE mod: long/short ayri defter (backtest birebir)
    r = _signed("POST", "/fapi/v1/order", {"symbol": sym, "side": order_side, "type": "MARKET",
                                           "quantity": _fmt(qty, step), "positionSide": pos_side})
    if not isinstance(r, dict) or not r.get("orderId"):
        log.warning(f"[G-LIVE] {sym} giris emri basarisiz: {r}"); return
    entry_px = float(r.get("avgPrice") or 0) or px
    rid = _log_open(sym, "LONG" if side == 1 else "SHORT", funding, entry_px, qty, r.get("orderId"))
    with _lock:
        # _open POZISYON-BASI (db_id ile) — ayni coinde UST USTE birden fazla olabilir (backtest gibi).
        # Borsa one-way modda bunlari netler; yazilim her mantiksal pozisyonu ayri takip eder
        # (kendi giris fiyati + kendi 24h sayaci), cikista kendi qty'siyle reduceOnly kapatir.
        _open[rid] = {"db_id": rid, "sym": sym, "side": side, "entry_px": entry_px, "qty": qty,
                      "open_ts": int(time.time()), "funding_time": ftime}
        _last_funding_done[sym] = ftime
        n_sym = sum(1 for p in _open.values() if p["sym"] == sym)
    log.warning(f"[G-LIVE] GIRIS {sym} {'LONG' if side==1 else 'SHORT'} @{entry_px:.4g} "
                f"qty={qty:g} funding={funding*100:+.4f}% (${notional:.0f} notional) [coinde {n_sym} pozisyon]")

def _exit(rec, reason):
    sym = rec["sym"]
    step, _ = _filters.get(sym, (0.001, 5.0))
    close_side = "SELL" if rec["side"] == 1 else "BUY"
    pos_side = "LONG" if rec["side"] == 1 else "SHORT"   # HEDGE: kendi defterinden kapat (reduceOnly YOK)
    r = _signed("POST", "/fapi/v1/order", {"symbol": sym, "side": close_side, "type": "MARKET",
                                           "quantity": _fmt(rec["qty"], step), "positionSide": pos_side})
    ex = float(r.get("avgPrice") or 0) if isinstance(r, dict) else 0.0
    if ex <= 0: ex = _mark(sym)
    ent = rec["entry_px"]
    raw = ((ex - ent) if rec["side"] == 1 else (ent - ex)) / ent * 1e4
    pnl_bps = raw - FEE_EST
    pnl_usd = raw / 1e4 * (rec["qty"] * ent)   # notional bazli yaklasik
    _log_close(rec["db_id"], ex, pnl_bps, pnl_usd, reason)
    with _lock:
        _open.pop(rec["db_id"], None)
    log.warning(f"[G-LIVE] CIKIS {sym} ({reason}) @{ex:.4g} = {pnl_bps:+.0f}bps (${pnl_usd:+.3f})")


# ───────── dongu ─────────
def _funding_time_at_or_before(sym, ts):
    """sym funding gecmisinden ts'den onceki (<=) son settlement zamani."""
    try:
        fr = [int(x["fundingTime"]) // 1000 for x in _get(f"{cfg.REST}/fapi/v1/fundingRate?symbol={sym}&limit=10")]
    except Exception:
        return None
    prev = [t for t in fr if t <= ts]
    return prev[-1] if prev else None

def _recover():
    for row in _get_open_db():
        rid, sym, side, ent, qty, ots, funding = row
        with _lock:
            _open[rid] = {"db_id": rid, "sym": sym, "side": 1 if side == "LONG" else -1,
                          "entry_px": ent, "qty": qty, "open_ts": ots, "funding_time": 0}
    # RESTART-DUPLIKASYONUNU ONLE: _last_funding_done'i geri kur. Her coin icin, en son acik pozisyonun
    # girdigi funding-donemini (open_ts'den onceki son settlement) 'islendi' say. Boylece restart AYNI
    # donemde ikinci pozisyon ACMAZ; yalniz GERCEK yeni funding-doneminde ust-uste acar (backtest gibi).
    for sym in set(p["sym"] for p in _open.values()):
        latest_ots = max(p["open_ts"] for p in _open.values() if p["sym"] == sym)
        ft = _funding_time_at_or_before(sym, latest_ots)
        if ft:
            _last_funding_done[sym] = ft
    if _open:
        log.info(f"[G-LIVE] restart-restore: {len(_open)} acik pozisyon + funding-done geri yuklendi ({_last_funding_done})")

def _tick():
    # 1) acik pozisyonlari yonet — SADECE 24h cikis (backtest gibi; SL YOK). Her pozisyon BAGIMSIZ.
    with _lock:
        recs = [dict(p) for p in _open.values()]
    for rec in recs:
        if (time.time() - rec["open_ts"]) / 3600.0 >= HOLD_H:
            _exit(rec, "24h")
    # 2) yeni giris — HER taze uc funding-doneminde (UST USTE izinli; 'pozisyon yoksa' KOSULU YOK).
    #    Boylece backtest gibi: funding uctayken her donem (8h) ayri 24h-pozisyon acilir (coinde max 3).
    for sym in COINS:
        sig = _funding_signal(sym)
        if not sig: continue
        side, funding, ftime = sig
        if side == 0: continue
        if _last_funding_done.get(sym) == ftime:   # bu funding-donemi zaten islendi
            continue
        # TAZELIK PENCERESI: yalniz YENI aciklanan (son FRESH_MAX_MIN dk) funding'e gir.
        # Bayatsa (startup'ta veya 24h-kapanis coin-i donem-ortasinda serbest biraktiginda) ATLA,
        # 'gordum' isaretle, sonraki taze aciklamayi bekle -> backtest'in funding-ani girisine sadik.
        if time.time() - ftime > FRESH_MAX_MIN * 60:
            if _last_funding_done.get(sym) != ftime:
                log.info(f"[G-LIVE] {sym} bayat funding ({int((time.time()-ftime)/60)}dk once) atlandi — "
                         f"taze aciklama beklenir")
            _last_funding_done[sym] = ftime
            continue
        _last_funding_done[sym] = ftime
        # TREND-ALIGN FILTRESI (N=TREND_N gunluk): contrarian yon gunluk-trendle AYNI degilse ATLA.
        # Backtest: trende-karsi -32bps/isl (kaybeden), trend-uyumlu +46bps/isl (permut p=0.0000).
        trend = _daily_trend(sym)
        if trend != 0 and side != trend:
            log.info(f"[G-LIVE] {sym} TREND-ALIGN atla: {'LONG' if side==1 else 'SHORT'} sinyali "
                     f"gunluk-trend {'UP' if trend==1 else 'DOWN'} ile TERS (trende-karsi=backtest kaybeden)")
            continue
        _enter(sym, side, funding, ftime)


def _run_forever():
    log.warning(f"[G-LIVE] GERCEK EMIR AKTIF — {','.join(c.replace('USDT','') for c in COINS)}, margin=${cfg.V3_G_MARGIN_USD} x{cfg.V3_G_LEVERAGE}, "
                f"funding-uc %{int(PCT*100)}, tutus {HOLD_H}h, UST-USTE izinli (coinde max 3), SL YOK (backtest birebir)")
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
    # G, ana botun PAPER_MODE'undan BAGIMSIZ: yalnizca acik V3_G_LIVE anahtariyla + API anahtari
    # varsa canli. Boylece ana bot paper'a alinip (test-edilmemis V3 motoru susturulup) yalniz
    # test-edilmis G canli tutulabilir. (Eskiden is_paper_mode() gate'liydi -> paper'da G de susardi.)
    if not (getattr(_c, "API_KEY", "") and getattr(_c, "API_SECRET", "")):
        return
    with _thread_lock:
        if _thread_started: return
        _thread_started = True
    if not _acquire_lock():
        log.warning("[G-LIVE] BASKA PROCESS AKTIF (tek-instance kilit) — emir-thread BASLATILMADI")
        return
    _recover()
    threading.Thread(target=_run_forever, name="g-live", daemon=True).start()
