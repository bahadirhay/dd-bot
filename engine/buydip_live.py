"""engine/buydip_live.py — buydip CANLI (KUCUK gercek-para testi). VARSAYILAN KAPALI (V3_BUYDIP_LIVE).

Kural (dondurulmus, buydip_shadow ile AYNI): LIKIT coin GUCLU-uptrend (60g>=%15) + son-3g dip 8-20%
(falling-knife >20% ATLA) -> MARKET BUY kucuk notional -> 10 gun tut -> MARKET SELL.

GUVENLIK (gercek para -> cok siki):
- PER_SIGNAL_USDT=8, LEVERAGE=1 (kaldirac YOK), MAX_OPEN=3 -> max ~$24 maruz kalma (hesap ~$99).
- STOP_EXPERIMENT: kumulatif gerceklesen pnl <= -$12 ise YENI GIRIS DURUR (deney biter).
- min-notional + stepSize precision (dumpfade_live'dan test-edilmis mekanik).
- Restart-guvenli: kendi DB (data/buydip_live.db) + borsa pozisyon reconcile.
- Tek-instance kilidi. cfg.V3_BUYDIP_LIVE=False -> HICBIR gercek emir gitmez.
Bu bir VALIDASYON testi (gercek dolum + favorable-olmayan kosul olcumu), kar bahsi degil.
"""
from __future__ import annotations
import json, time, hmac, hashlib, sqlite3, os, socket
import urllib.request, urllib.parse, urllib.error
import datetime as dt
from decimal import Decimal, ROUND_DOWN

from core.config import cfg
from core.logger import get_logger

log = get_logger("BuydipLive")

# ── strateji (shadow ile ayni, dondurulmus) ──
DROP = 8.0; DROP_CAP = 20.0; TREND_N = 60; TREND_MIN_PCT = 15.0; HOLD_DAYS = 10
MIN_QVOL = 25e6; MIN_DAYS = TREND_N + 5; TOPN = 100
# ── PIYASA-FILTRESI (eklendi 10-16, kullanici onayi) ──
# BTC 10-gun trendi < 0 ise YENI GIRIS YOK (dusen piyasada dip-al'i durdur). Cikislar normal devam.
# Test (60 likit coin): FILTRESIZ +470bps vs FILTRELI(BTC>=0) +676bps vs ATLANAN(BTC<0) +222bps.
# Dusen-piyasa dipleri zayif (+222); saglikli-piyasa dipleri guclu (+676). "Piyasayla inatlasma."
MKT_SYMBOL = "BTCUSDT"; MKT_TREND_N = 10; MKT_TREND_MIN_PCT = 0.0
# ── GUVENLIK limitleri (gercek para) ──
PER_SIGNAL_USDT = 12.0       # islem basi notional. $8 COK KUCUKTU (pre-flight: AVAX min-emir $11 > $8 ->
                             # sinyal KACIYORDU). $12 AVAX+mid-cap'leri yakalar; pahali major'lar (BTC min
                             # $86, ETH/LINK $20) zaten kucuk-hesaba alinmaz (nadir dip-sinyal verir).
LEVERAGE = 1                 # KALDIRAC YOK
MAX_OPEN = 5                 # ayni anda max pozisyon -> max ~$60 maruz kalma (hesap $99'un ~%60'i). 3'ten
                             # 5'e (kullanici karari 10-07): sim 1161-sinyal, 3-slot %77 sinyali kacirir +
                             # -$11.3 (GUVENILMEZ, kucuk ornek); 5-slot +$21.2 (edge gorunur, $39 tampon).
                             # Sinyaller kumelenir (en yogun gun 35 sinyal) -> az slot edge'i kaciriyordu.
STOP_EXPERIMENT_USDT = -35.0 # 5-slot (daha cok eszamanli) -> normal-swing buyur; -28 erken keserdi.
                             # -35 = buyuk-kitapta normal-drawdown'i kesmez, sadece cokmus-edge'de tetiklenir.
# ── FELAKET-STOP (eklendi 10-26, kullanici onayi). Test (327 sinyal, 4-ceyrek): getiriye NOTR (+306->+318),
# 55 buyuk-kazananin HEPSI korunur (stop kaybeden coker uzerine tetiklenir, kazanan degil), en-kotu kuyruk
# -52%->-38% kesilir. Kar artirmaz = TAIL/sermaye-koruma sigortasi. Girişte DROP_CAP=20 (dip<=-20 alma);
# tutarken -25'e inerse cik (QUSDT -52% felaketini onler). Cekirdek sinyale (uptrend+dip+10g) dokunmaz.
CATASTROPHE_STOP_PCT = -25.0
STOP_EFFECTIVE_TS = 1793017117  # 2026-10-26 15:18. SADECE bu tarihten SONRA acilan pozisyonlara uygulanir.
                                # Mevcut QUSDT(-52%)/BTW grandfathered: -25 penceresi gecti, dibe zorla satma
                                # (5.5g kaldi, sonuc tahmin-edilemez); 10-gun kurali kapatir. Stop isini ileride yapar.

_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "buydip_live.db")
_filters = {}; _last_day = 0; _lock_sock = None


def _enabled() -> bool:
    return bool(getattr(cfg, "V3_BUYDIP_LIVE", False))


def _acquire_lock() -> bool:
    global _lock_sock
    try:
        _lock_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _lock_sock.bind(("127.0.0.1", 57613))   # ikinci instance bind edemez
        return True
    except OSError:
        return False


# ── HTTP / imza (dumpfade_live'dan) ──
def _get(u, t=15): return json.loads(urllib.request.urlopen(u, timeout=t).read())

def _signed(method, path, params=None, t=15):
    if not cfg.API_KEY or not cfg.API_SECRET: return {}
    p = dict(params or {}); p["timestamp"] = int(time.time()*1000); p["recvWindow"] = 5000
    qs = urllib.parse.urlencode(p)
    sig = hmac.new(cfg.API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    req = urllib.request.Request(f"{cfg.REST}{path}?{qs}&signature={sig}", method=method,
                                 headers={"X-MBX-APIKEY": cfg.API_KEY})
    try:
        with urllib.request.urlopen(req, timeout=t) as r: return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try: body = json.loads(e.read())
        except Exception: body = {"code": e.code, "msg": str(e)}
        log.warning(f"[BUYDIP-LIVE] {method} {path}: {body}"); return body
    except Exception as ex:
        log.warning(f"[BUYDIP-LIVE] {method} {path}: {ex}"); return {}

def _round_step(v, step):
    if step <= 0: return v
    return float((Decimal(str(v))/Decimal(str(step))).quantize(Decimal("1"), rounding=ROUND_DOWN)*Decimal(str(step)))

def _fmt(v, step):
    v = _round_step(v, step); s = f"{step:.10f}".rstrip("0")
    d = len(s.split(".")[1]) if "." in s else 0
    return f"{v:.{d}f}"

def _filt(sym):
    if sym in _filters: return _filters[sym]
    try:
        info = _get(f"{cfg.REST}/fapi/v1/exchangeInfo")
        for s in info.get("symbols", []):
            if s.get("symbol") != sym: continue
            step, mn = 0.001, 5.0
            for f in s.get("filters", []):
                if f.get("filterType") == "LOT_SIZE": step = float(f.get("stepSize", step))
                elif f.get("filterType") == "MIN_NOTIONAL": mn = float(f.get("notional", f.get("minNotional", mn)))
            _filters[sym] = (step, mn); return _filters[sym]
    except Exception: pass
    return (0.001, 5.0)

def _hedge_mode() -> bool:
    r = _signed("GET", "/fapi/v1/positionSide/dual", {})
    return bool(r.get("dualSidePosition")) if isinstance(r, dict) else False


# ── DB ──
def _db():
    os.makedirs(os.path.dirname(_DB), exist_ok=True)
    c = sqlite3.connect(_DB, timeout=30)
    c.execute("""CREATE TABLE IF NOT EXISTS buydip_live (
        symbol TEXT, open_ts INTEGER, open_human TEXT, entry REAL, qty REAL, drop3 REAL,
        close_ts INTEGER, exit REAL, pnl_usdt REAL, status TEXT, PRIMARY KEY(symbol,open_ts))""")
    c.commit(); return c

def _realized_total(c) -> float:
    r = c.execute("SELECT COALESCE(SUM(pnl_usdt),0) FROM buydip_live WHERE status='CLOSED'").fetchone()
    return float(r[0] or 0)


# ── evren / sinyal ──
def _candidates():
    try:
        t = _get(f"{cfg.REST}/fapi/v1/ticker/24hr")
        rows = [(x["symbol"], float(x.get("quoteVolume", 0) or 0)) for x in t if str(x.get("symbol","")).endswith("USDT")]
        rows.sort(key=lambda z: -z[1]); return [s for s,_ in rows[:TOPN]]
    except Exception: return []

def _daily(sym, limit=MIN_DAYS+3):
    try:
        r = _get(f"{cfg.REST}/fapi/v1/klines?symbol={sym}&interval=1d&limit={limit}")
        return [(int(x[0]), float(x[4]), float(x[7])) for x in r]
    except Exception: return None

def _market_trend() -> float | None:
    """BTC son MKT_TREND_N gun % degisim. None = veri alinamadi (fail-open: giris engellenmez)."""
    try:
        r = _get(f"{cfg.REST}/fapi/v1/klines?symbol={MKT_SYMBOL}&interval=1d&limit={MKT_TREND_N+3}")
        cl = [float(x[4]) for x in r]
        if len(cl) < MKT_TREND_N + 1 or cl[-1-MKT_TREND_N] <= 0: return None
        return (cl[-1] - cl[-1-MKT_TREND_N]) / cl[-1-MKT_TREND_N] * 100
    except Exception: return None


# ── emir ──
def _place_buy(c, sym, close, d3, hedge):
    step, mn = _filt(sym)
    px = _get(f"{cfg.REST}/fapi/v1/ticker/price?symbol={sym}")
    mark = float(px.get("price", 0) or 0)
    if mark <= 0: return
    qty = _round_step(PER_SIGNAL_USDT / mark, step)
    if qty <= 0 or qty*mark < mn:
        min_order = max(mark*step, mn)
        log.warning(f"[BUYDIP-LIVE] SINYAL ATLANDI {sym} dip{d3:.1f}%: butce ${PER_SIGNAL_USDT} < min-emir "
                    f"${min_order:.1f} (buyuk-hesap gerekir). Bu sinyali KACIRDIK."); return
    _signed("POST", "/fapi/v1/leverage", {"symbol": sym, "leverage": LEVERAGE})
    params = {"symbol": sym, "side": "BUY", "type": "MARKET", "quantity": _fmt(qty, step)}
    if hedge: params["positionSide"] = "LONG"
    r = _signed("POST", "/fapi/v1/order", params)
    if not isinstance(r, dict) or not r.get("orderId"):
        log.warning(f"[BUYDIP-LIVE] {sym} BUY basarisiz: {r}"); return
    now = int(time.time()); ap = float(r.get("avgPrice", 0) or 0) or mark
    c.execute("INSERT OR IGNORE INTO buydip_live VALUES (?,?,?,?,?,?,?,?,?,?)",
              (sym, now, dt.datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M"),
               round(ap,8), qty, round(d3,2), None, None, None, "OPEN"))
    c.commit()
    log.info(f"[BUYDIP-LIVE] GIRIS {sym} dip{d3:.1f}% MARKET BUY {qty:g} @ ~{ap:.6g} (~${qty*ap:.1f})")

def _close(c, sym, ots, qty, entry, hedge):
    step, mn = _filt(sym)
    params = {"symbol": sym, "side": "SELL", "type": "MARKET", "quantity": _fmt(qty, step), "reduceOnly": "true"}
    if hedge: params["positionSide"] = "LONG"; params.pop("reduceOnly", None)
    r = _signed("POST", "/fapi/v1/order", params)
    if not isinstance(r, dict) or not r.get("orderId"):
        log.warning(f"[BUYDIP-LIVE] {sym} SELL basarisiz: {r}"); return
    now = int(time.time()); ap = float(r.get("avgPrice", 0) or 0)
    if ap <= 0:
        px = _get(f"{cfg.REST}/fapi/v1/ticker/price?symbol={sym}"); ap = float(px.get("price",0) or 0)
    pnl = (ap - entry) * qty
    c.execute("UPDATE buydip_live SET close_ts=?, exit=?, pnl_usdt=?, status='CLOSED' WHERE symbol=? AND open_ts=?",
              (now, round(ap,8), round(pnl,4), sym, ots))
    c.commit()
    log.info(f"[BUYDIP-LIVE] CIKIS {sym} MARKET SELL @ ~{ap:.6g} pnl ${pnl:+.2f}")


def tick():
    """decision_v3 / supervisor'dan cagrilir. Gunde bir giris tarama, her cagri cikis-kontrol."""
    global _last_day
    if not _enabled():
        return
    if _lock_sock is None and not _acquire_lock():
        return
    try:
        c = _db(); now = int(time.time()); hedge = _hedge_mode()
        # 1) CIKIS (her cagri): 10 gun dolan + felaket-stop vuran pozisyonlari kapat
        for sym, ots, qty, entry in c.execute(
                "SELECT symbol,open_ts,qty,entry FROM buydip_live WHERE status='OPEN'").fetchall():
            if now - ots >= HOLD_DAYS*86400:
                _close(c, sym, ots, qty, entry, hedge); continue
            # FELAKET-STOP: sadece STOP_EFFECTIVE_TS'den sonra acilanlar (eskiler grandfathered)
            if ots >= STOP_EFFECTIVE_TS:
                try:
                    pxr = _get(f"{cfg.REST}/fapi/v1/ticker/price?symbol={sym}")
                    px = float(pxr.get("price", 0) or 0)
                    if px > 0 and (px-entry)/entry*100 <= CATASTROPHE_STOP_PCT:
                        log.warning(f"[BUYDIP-LIVE] FELAKET-STOP {sym} {(px-entry)/entry*100:+.1f}% "
                                    f"<= {CATASTROPHE_STOP_PCT}% -> CIK")
                        _close(c, sym, ots, qty, entry, hedge)
                except Exception as e:
                    log.warning(f"[BUYDIP-LIVE] {sym} felaket-stop fiyat alinamadi: {e}")
        # 2) GIRIS (gunde bir)
        dk = int(now // 86400)
        if dk == _last_day:
            return
        _last_day = dk
        if _realized_total(c) <= STOP_EXPERIMENT_USDT:
            log.warning(f"[BUYDIP-LIVE] STOP-EXPERIMENT: kumulatif ${_realized_total(c):.2f} <= ${STOP_EXPERIMENT_USDT} -> yeni giris YOK")
            return
        open_ct = c.execute("SELECT COUNT(*) FROM buydip_live WHERE status='OPEN'").fetchone()[0]
        if open_ct >= MAX_OPEN:
            return
        # PIYASA-FILTRESI: BTC dusuyorsa (10g trend < esik) yeni giris YOK. Cikislar yukarida zaten yapildi.
        # fail-open: trend None (veri yok) ise engelleme (eski davranis). Gunde bir loglanir.
        mkt = _market_trend()
        if mkt is not None and mkt < MKT_TREND_MIN_PCT:
            log.info(f"[BUYDIP-LIVE] PIYASA-FILTRESI: BTC 10g {mkt:+.1f}% < {MKT_TREND_MIN_PCT:.0f}% "
                     f"-> dusen piyasa, yeni giris YOK (mevcut pozisyonlar devam).")
            return
        # BORSA-RECONCILE (duplikasyon-koruma 10-12): DB'ye guvenme, borsada acik olan coinleri de atla.
        # Restart-yarisi/kriz DB'yi senkronsuz birakirsa bile ayni coine 2. emir GITMEZ.
        exch_open = set()
        try:
            for p in _signed("GET", "/fapi/v2/positionRisk", {}):
                if isinstance(p, dict) and abs(float(p.get("positionAmt", 0) or 0)) > 0:
                    exch_open.add(p["symbol"])
        except Exception as e:
            log.warning(f"[BUYDIP-LIVE] borsa-reconcile alinamadi, DB-only mod: {e}")
        for sym in _candidates():
            if open_ct >= MAX_OPEN: break
            if sym in exch_open:          # borsada zaten acik -> duplikasyon onle
                continue
            d = _daily(sym)
            if not d or len(d) < MIN_DAYS: continue
            vols = [x[2] for x in d[-31:-1] if x[2] > 0]
            if not vols or sum(vols)/len(vols) < MIN_QVOL: continue
            close = d[-1][1]; cN = d[-1-TREND_N][1]; c3 = d[-4][1]
            if cN <= 0 or c3 <= 0: continue
            tstr = (close-cN)/cN*100; d3 = (close-c3)/c3*100
            if not (tstr >= TREND_MIN_PCT and -DROP_CAP <= d3 <= -DROP): continue
            if c.execute("SELECT 1 FROM buydip_live WHERE symbol=? AND status='OPEN'", (sym,)).fetchone(): continue
            _place_buy(c, sym, close, d3, hedge); open_ct += 1
    except Exception as ex:
        log.error(f"[BUYDIP-LIVE] tick hatasi: {ex}")


def _status(c):
    op = c.execute("SELECT COUNT(*) FROM buydip_live WHERE status='OPEN'").fetchone()[0]
    cl = c.execute("SELECT COUNT(*),COALESCE(SUM(pnl_usdt),0) FROM buydip_live WHERE status='CLOSED'").fetchone()
    return op, cl[0], float(cl[1] or 0)


if __name__ == "__main__":
    import sys
    c0 = _db()
    if "--status" in sys.argv:
        op, cn, pnl = _status(c0)
        print(f"buydip_live: ENABLED={_enabled()} | {op} acik, {cn} kapali, gerceklesen ${pnl:+.2f} | "
              f"limit: ${PER_SIGNAL_USDT}/sinyal x max {MAX_OPEN} = ${PER_SIGNAL_USDT*MAX_OPEN} | stop ${STOP_EXPERIMENT_USDT}")
        sys.exit(0)
    if not _acquire_lock():
        print("buydip_live ZATEN calisiyor (kilit alinamadi) -> bu surec CIKIYOR (duplikasyon-onleme).")
        sys.exit(0)
    print(f"buydip_live BASLADI (ENABLED={_enabled()}). Her saat tick; flag kapaliyken HICBIR emir gitmez.")
    while True:
        t0 = time.time()
        try:
            tick()
        except Exception as e:
            print("dongu hatasi:", e)
        time.sleep(max(1.0, 3600 - (time.time() - t0)))
