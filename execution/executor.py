"""
execution/executor.py — Binance Futures emir motoru
mmbot3 imza/zaman senkronu: query string URL'de, aiohttp params yok.
"""
import asyncio
import hashlib
import hmac
import socket
import time
from urllib.parse import urlencode

import aiohttp

from core.config import cfg, is_paper_mode
from core.state import state
from core.logger import get_logger
from execution.risk import Plan
from botlog.db import log_trade_open, log_trade_close, log_error

log = get_logger("Executor")

_trade_id: int = 0
_signal_id: int = 0
_time_offset_ms: int = 0
_position_open_lock = asyncio.Lock()
_opening_direction: str = ""
_api_http_session: aiohttp.ClientSession | None = None
_api_http_connector: aiohttp.TCPConnector | None = None


def is_position_opening() -> bool:
    """Market girişi + koruma emirleri sürerken True (çift açılış önleme)."""
    return bool(_opening_direction)


def get_opening_direction() -> str:
    return _opening_direction


def _build_api_connector() -> aiohttp.TCPConnector:
    return aiohttp.TCPConnector(
        ttl_dns_cache=300,
        family=socket.AF_INET,
        limit=20,
        limit_per_host=10,
        enable_cleanup_closed=True,
    )


async def _get_api_http_session() -> aiohttp.ClientSession:
    global _api_http_session, _api_http_connector
    if _api_http_session is None or _api_http_session.closed:
        _api_http_connector = _build_api_connector()
        _api_http_session = aiohttp.ClientSession(connector=_api_http_connector)
    return _api_http_session


async def close_api_http_session() -> None:
    global _api_http_session, _api_http_connector
    if _api_http_session is not None and not _api_http_session.closed:
        await _api_http_session.close()
    _api_http_session = None
    _api_http_connector = None


async def _http_json(
    method: str,
    url: str,
    *,
    headers: dict | None = None,
    timeout: aiohttp.ClientTimeout | None = None,
):
    sess = await _get_api_http_session()
    async with sess.request(method.upper(), url, headers=headers, timeout=timeout) as r:
        return await r.json(content_type=None)


def set_signal_id(sid: int):
    global _signal_id
    _signal_id = sid


def _now_ms() -> int:
    return int(time.time() * 1000) + _time_offset_ms


async def _sync_time():
    global _time_offset_ms
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            data = await _http_json(
                "GET",
                f"{cfg.REST}/fapi/v1/time",
                timeout=aiohttp.ClientTimeout(total=8),
            )
            server_ms = int(data.get("serverTime", 0))
            local_ms = int(time.time() * 1000)
            if server_ms > 0:
                _time_offset_ms = server_ms - local_ms
                log.info(f"Binance zaman offset: {_time_offset_ms} ms")
            return
        except Exception as e:
            last_err = e
            if attempt < 2:
                await asyncio.sleep(2)
    await close_api_http_session()
    log.warning(f"Zaman senkronu başarısız: {last_err}")
    _time_offset_ms = 0


def _signed_url(endpoint: str, params: dict | None = None) -> str:
    """İmza — aiohttp param sıralamasını bypass et (mmbot3)."""
    p = dict(params or {})
    p["timestamp"] = _now_ms()
    p["recvWindow"] = 5000
    qs = urlencode(p)
    sig = hmac.new(
        cfg.API_SECRET.encode("utf-8"),
        qs.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{cfg.REST}{endpoint}?{qs}&signature={sig}"


async def _signed_request(method: str, endpoint: str, params: dict | None = None):
    if not cfg.API_KEY:
        return {}
    headers = {"X-MBX-APIKEY": cfg.API_KEY}
    timeout = aiohttp.ClientTimeout(total=10)
    last = {}
    for attempt in range(2):
        url = _signed_url(endpoint, params or {})
        try:
            last = await _http_json(
                method,
                url,
                headers=headers,
                timeout=timeout,
            )
        except Exception as e:
            await close_api_http_session()
            if attempt == 0:
                log.warning(f"[{method} {endpoint}] HTTP exception: {e}")
                continue
            raise
        if isinstance(last, dict) and last.get("code") is not None and int(last.get("code")) < 0:
            if int(last.get("code")) == -1021 and attempt == 0:
                await _sync_time()
                continue
            log.error(f"[{method} {endpoint}] {last}")
            log_error("Executor", str(last), f"{method} {endpoint}")
        return last
    return last


async def _req(method: str, path: str, params: dict):
    return await _signed_request(method, path, params)


async def _resolve_fill_price(order: dict, plan: Plan) -> float:
    """MARKET emir yanıtında avgPrice çoğu zaman 0 — order sorgusu ile doldur."""
    px = float(order.get("avgPrice") or 0)
    if px > 0:
        return px

    oid = order.get("orderId")
    if oid:
        q = await _req(
            "GET",
            "/fapi/v1/order",
            {"symbol": cfg.SYMBOL, "orderId": int(oid)},
        )
        if isinstance(q, dict):
            px = float(q.get("avgPrice") or 0)
            if px > 0:
                return px
            executed = float(q.get("executedQty") or 0)
            cum = float(q.get("cumQuote") or 0)
            if executed > 0 and cum > 0:
                return round(cum / executed, 2)

    fallback = float(plan.entry or 0)
    if fallback > 0:
        return fallback
    px = state.mark_price or state.price or state.bid or state.ask
    return float(px) if px and px > 0 else 0.0


async def restore_exchange_position_on_startup() -> None:
    """Restart sonrası borsa pozisyonu + DB trade kaydı senkronu."""
    from execution.account_sync import reconcile_startup_exchange

    await reconcile_startup_exchange()


def _set_api_state(ok: bool, error: str = "", balance: float | None = None):
    state.api_ok = ok
    state.api_error = error[:120] if error else ""
    if balance is not None:
        state.real_balance = balance
        state.real_balance_ts = time.time()


async def fetch_balance() -> bool:
    """Binance bakiye + API durumu (dashboard ve main loop)."""
    if is_paper_mode():
        from execution.paper import init_paper_session, paper_balance
        init_paper_session()
        return True

    if not cfg.API_KEY:
        _set_api_state(False, "api_key.csv boş veya eksik")
        return False

    bt = max(8.0, float(cfg.BALANCE_API_TIMEOUT_SEC or 18.0))
    bal_timeout = aiohttp.ClientTimeout(total=bt, connect=min(12.0, bt))
    headers = {"X-MBX-APIKEY": cfg.API_KEY}

    try:
        url = _signed_url("/fapi/v2/balance")
        data = await _http_json(
            "GET",
            url,
            headers=headers,
            timeout=bal_timeout,
        )

        if isinstance(data, list):
            for asset in data:
                if asset.get("asset") == "USDT":
                    bal = float(asset.get("availableBalance", 0))
                    _set_api_state(True, "", bal)
                    log.debug(f"Bakiye OK: ${bal:.4f} USDT")
                    return True
            _set_api_state(False, "USDT bulunamadı")
            return False

        if isinstance(data, dict):
            code = int(data.get("code", 0))
            msg = data.get("msg", "")
            if code == -1021:
                await _sync_time()
                url = _signed_url("/fapi/v2/balance")
                data2 = await _http_json(
                    "GET",
                    url,
                    headers=headers,
                    timeout=bal_timeout,
                )
                if isinstance(data2, list):
                    for asset in data2:
                        if asset.get("asset") == "USDT":
                            bal = float(asset.get("availableBalance", 0))
                            _set_api_state(True, "", bal)
                            return True
                if isinstance(data2, dict):
                    code = int(data2.get("code", code))
                    msg = data2.get("msg", msg)
            _set_api_state(False, f"Hata {code}: {msg}")
            log.warning(f"Binance API: {state.api_error}")
            return False

        _set_api_state(False, f"Beklenmedik yanıt: {type(data)}")
        return False

    except asyncio.TimeoutError:
        _set_api_state(False, f"Zaman aşımı ({bt:.0f}s)")
        log.warning(state.api_error)
        return False
    except Exception as e:
        await close_api_http_session()
        _set_api_state(False, str(e)[:120])
        log.warning(f"Bakiye hatası: {state.api_error}")
        return False


async def refresh_balance():
    await fetch_balance()
    if cfg.API_KEY and not is_paper_mode():
        from execution.account_sync import refresh_account_snapshot

        await refresh_account_snapshot()


async def setup_api() -> bool:
    if is_paper_mode():
        from execution.paper import init_paper_session, paper_balance
        init_paper_session()
        log.info(f"İzleme modu (paper) | Simüle bakiye: ${paper_balance():,.2f} USDT")
        return True

    if not cfg.API_KEY:
        _set_api_state(False, "api_key.csv boş veya eksik")
        log.warning("API key yok")
        return False
    await _sync_time()
    ok = await fetch_balance()
    if ok:
        log.info(f"API bağlantısı OK | Bakiye: ${state.real_balance:.4f} USDT")
    else:
        log.warning(f"API bağlantısı başarısız: {state.api_error}")
    return ok


async def get_equity_for_risk() -> float:
    """Risk üst sınırı (MAX_MARGIN_PCT) için equity — kullanılabilir değil."""
    if is_paper_mode():
        from execution.paper import paper_balance
        return paper_balance()
    eq = getattr(state, "equity_balance", 0.0) or state.real_balance
    if eq > 0:
        return eq
    await get_balance()
    return getattr(state, "equity_balance", 0.0) or state.real_balance


async def get_balance() -> float:
    """Genel bakiye okuma (API durumu)."""
    if is_paper_mode():
        from execution.paper import paper_balance
        return paper_balance()
    if state.api_ok and state.real_balance_ts > 0:
        return getattr(state, "equity_balance", 0.0) or state.real_balance
    if await fetch_balance():
        from execution.account_sync import refresh_account_snapshot

        await refresh_account_snapshot(force=True)
        if getattr(state, "available_balance", 0) > 0:
            return state.available_balance
        return state.real_balance
    return 0.0


async def _setup():
    """Isolated margin + leverage 5x"""
    if is_paper_mode():
        return True

    if not state.api_ok:
        await setup_api()
    if not state.api_ok:
        return False

    r1 = await _req("POST", "/fapi/v1/marginType", {"symbol": cfg.SYMBOL, "marginType": cfg.MARGIN})
    code = int(r1.get("code", 0) or 0) if isinstance(r1, dict) else 0
    if code == -4067:
        from execution.account_sync import cancel_orphan_exchange_orders, fetch_position_row

        row = await fetch_position_row()
        amt = abs(float(row.get("positionAmt", 0) or 0)) if row else 0.0
        if amt < 0.0001:
            n = await cancel_orphan_exchange_orders("margin_setup_orphans")
            log.warning(
                f"Marjin tipi engellendi (açık emir) — pozisyon yok, {n} emir iptal, tekrar deneniyor"
            )
            r1 = await _req(
                "POST", "/fapi/v1/marginType", {"symbol": cfg.SYMBOL, "marginType": cfg.MARGIN}
            )
            code = int(r1.get("code", 0) or 0) if isinstance(r1, dict) else 0
        else:
            log.warning(
                "Marjin tipi değiştirilemedi (açık emirler) — mevcut ayarla devam"
            )
            code = -4046
    if isinstance(r1, dict) and code not in (200, 0, -4046):
        log.error(f"Marjin tipi hatası: {r1}")
        return False

    r2 = await _req("POST", "/fapi/v1/leverage", {"symbol": cfg.SYMBOL, "leverage": cfg.LEVERAGE})
    if isinstance(r2, dict) and "leverage" in r2:
        log.info(f"Marjin: {cfg.MARGIN}  Kaldıraç: {cfg.LEVERAGE}x ✓")
        return True
    return False


async def open_position(plan: Plan, signal_id: int = 0) -> bool:
    async with _position_open_lock:
        if is_paper_mode():
            from execution import paper as _paper

            return await _paper.paper_open(plan, signal_id)
        return await _open_position_live(plan, signal_id)


async def _open_position_live(plan: Plan, signal_id: int = 0) -> bool:
    global _trade_id, _opening_direction

    if not plan.ok():
        log.error(f"Plan geçersiz: {plan.warnings}")
        return False

    if not await _setup():
        return False

    blocked, reason = await same_direction_position_open(plan.direction)
    if blocked:
        log.warning(f"Aynı yönde ek pozisyon yok: {reason}")
        state.no_entry_reason = reason
        return False

    _opening_direction = plan.direction
    try:
        amt = await get_position_amt_signed()
        if abs(amt) >= 0.0001:
            await asyncio.sleep(0.35)
            amt = await get_position_amt_signed()
        if abs(amt) >= 0.0001:
            ex_side = _position_side_from_amt(amt)
            if ex_side == plan.direction:
                reason = f"Borsada zaten {ex_side} ({amt:+.4f} ETH)"
            else:
                reason = f"Borsada {ex_side} pozisyon var — {plan.direction} eklenmez"
            log.warning(f"{reason}")
            state.no_entry_reason = reason
            return False

        return await _execute_market_entry(plan, signal_id)
    finally:
        _opening_direction = ""


async def _execute_market_entry(plan: Plan, signal_id: int) -> bool:
    global _trade_id

    side = "BUY" if plan.direction == "LONG" else "SELL"
    cside = "SELL" if plan.direction == "LONG" else "BUY"

    from execution.protection_orders import format_qty, round_qty_float

    qty = await round_qty_float(plan.qty_total)
    if qty < 0.001:
        log.error(f"Miktar borsa adımına göre sıfır: plan={plan.qty_total}")
        return False
    plan.qty_total = qty
    plan.qty_tp1 = await round_qty_float(plan.qty_tp1)
    plan.qty_tp2 = await round_qty_float(plan.qty_total - plan.qty_tp1)
    if plan.qty_tp2 < 0.001:
        plan.qty_tp1 = qty
        plan.qty_tp2 = 0.0

    r = await _req(
        "POST",
        "/fapi/v1/order",
        {
            "symbol": cfg.SYMBOL,
            "side": side,
            "type": "MARKET",
            "quantity": await format_qty(qty),
            "positionSide": "BOTH",
        },
    )
    if not isinstance(r, dict) or r.get("status") not in ("FILLED", "NEW", "PARTIALLY_FILLED"):
        log.error(f"Giriş başarısız: {r}")
        return False

    fill = await _resolve_fill_price(r, plan)
    if fill <= 0:
        log.error("Giriş fiyatı alınamadı — emir iptal edilmiş sayılır")
        return False
    oid = str(r.get("orderId", ""))

    state.in_position = True
    state.pos_side = plan.direction
    state.pos_entry = fill
    state.pos_qty = plan.qty_total
    state.pos_qty_tp1 = plan.qty_tp1
    state.pos_qty_tp2 = plan.qty_tp2
    state.pos_sl = plan.sl
    state.pos_sl_initial = plan.sl
    state.pos_tp1 = plan.tp1
    state.pos_tp2 = plan.tp2
    state.pos_liq_price = plan.liq_price
    state.pos_margin = plan.margin_req
    state.pos_tp1_hit = False
    state.pos_be_active = False
    state.pos_open_ts = time.time()

    pos_value = round(plan.qty_total * fill, 2)
    margin_used = round(pos_value / max(cfg.LEVERAGE, 1), 2)
    log.info(
        f"\n{'═'*54}\n"
        f"  POZİSYON AÇILDI: {plan.direction}  "
        f"{plan.qty_total:.4f} ETH @ {fill:.2f}\n"
        f"  Pozisyon değeri: {pos_value:.2f} USDT  "
        f"(marjin ~{margin_used:.2f} USDT, {cfg.MARGIN} {cfg.LEVERAGE}x)\n"
        f"  Hedef marjin: {plan.margin_req:.2f} USDT  "
        f"Liq={plan.liq_price:.2f}\n"
        f"{'═'*54}"
    )

    from execution.protection_orders import place_position_protection

    sl_id, tp1_id, tp2_id, tp1_live, tp2_live = await place_position_protection(
        plan.direction,
        plan.qty_total,
        plan.qty_tp1,
        plan.qty_tp2,
        plan.sl,
        plan.tp1,
        plan.tp2,
        entry=fill,
    )
    if not sl_id:
        log.error(f"Algo SL gönderilemedi — seviye {plan.sl:.2f}")
    if plan.qty_tp1 >= 0.001 and not tp1_id:
        log.error(
            f"Algo TP1 gönderilemedi — plan={plan.tp1:.2f} "
            f"ayarlı={tp1_live:.2f}"
        )
    if not bool(getattr(cfg, "SEND_TP2_ORDER", False)):
        log.info(
            f"Koruma: SL + TP1 (%{cfg.TP1_PCT*100:.0f} kapat) — "
            f"TP2 emri yok, runner 15m trail SL"
        )

    state.pos_tp1 = tp1_live if tp1_live > 0 else plan.tp1
    state.pos_tp2 = tp2_live if tp2_live > 0 else plan.tp2
    state.pos_sl_id = sl_id
    state.pos_tp1_id = tp1_id
    state.pos_tp2_id = tp2_id if tp2_id else ""
    if not bool(getattr(cfg, "SEND_TP2_ORDER", False)):
        state.pos_tp2_id = ""

    from botlog.db import log_trade_open, update_trade_entry

    ex_qty = await get_position_qty()
    qty_db = round(ex_qty if ex_qty >= 0.001 else plan.qty_total, 4)
    state.pos_qty = qty_db

    _trade_id = log_trade_open(
        {
            "signal_id": signal_id,
            "order_id": oid,
            "direction": plan.direction,
            "entry_price": fill,
            "qty": qty_db,
            "qty_tp1": plan.qty_tp1,
            "qty_tp2": plan.qty_tp2,
            "sl": plan.sl,
            "tp1": state.pos_tp1,
            "tp2": state.pos_tp2,
            "liq_price": plan.liq_price,
            "margin": plan.margin_req,
            "leverage": cfg.LEVERAGE,
            "margin_type": cfg.MARGIN,
            "open_ts": state.pos_open_ts,
            "regime_at_open": state.regime,
            "cvd_at_open": state.cvd_5m,
            "notes": "canli",
        }
    )
    update_trade_entry(_trade_id, fill, qty_db)
    return True


async def get_position_qty() -> float:
    """Borsadaki açık pozisyon miktarı (ETH, mutlak değer)."""
    amt = await get_position_amt_signed()
    return abs(amt)


async def get_position_amt_signed() -> float:
    """Borsa pozisyon miktarı (+ LONG, − SHORT, 0 düz)."""
    if is_paper_mode():
        if state.in_position:
            q = float(state.pos_qty or 0)
            return q if state.pos_side == "LONG" else -q
        return 0.0
    if not cfg.API_KEY:
        return 0.0
    from execution.account_sync import pick_symbol_position

    r = await _signed_request("GET", "/fapi/v2/positionRisk", {"symbol": cfg.SYMBOL})
    pos = pick_symbol_position(r)
    if not pos:
        r = await _signed_request("GET", "/fapi/v2/positionRisk", {})
        pos = pick_symbol_position(r)
    if pos:
        return float(pos.get("positionAmt", 0) or 0)
    return 0.0


def _position_side_from_amt(amt: float) -> str:
    if amt > 0.0001:
        return "LONG"
    if amt < -0.0001:
        return "SHORT"
    return ""


async def same_direction_position_open(direction: str) -> tuple[bool, str]:
    """
    Aynı yönde zaten pozisyon var mı (borsa önce, sonra state, sonra DB).
    Döner: (engelle, sebep)
    """
    direction = (direction or "").upper()
    if not direction:
        return False, ""

    if _opening_direction:
        if _opening_direction == direction:
            return True, f"Aynı yönde açılış sürüyor ({direction})"
        return True, f"Başka yön açılıyor ({_opening_direction}) — {direction} bekletildi"

    if state.in_position and state.pos_side == direction:
        return True, f"Zaten {direction} açık (bot state)"

    amt = await get_position_amt_signed()
    ex_side = _position_side_from_amt(amt)
    if ex_side == direction:
        if not state.in_position or state.pos_side != direction:
            if not is_position_opening():
                from execution.account_sync import restore_live_position_from_exchange

                await restore_live_position_from_exchange()
        return True, f"Borsada zaten {direction} ({amt:+.4f} ETH)"

    if abs(amt) >= 0.0001 and ex_side and ex_side != direction:
        return True, f"Borsada {ex_side} açık — {direction} eklenmez"

    try:
        from botlog.db import close_orphan_open_trades, count_open_trades

        if count_open_trades(direction) > 0:
            if not state.in_position and ex_side == "" and not is_position_opening():
                n = close_orphan_open_trades("db_stale_no_exchange")
                if n:
                    log.info(
                        f"Borsa düz — {n} eski DB OPEN {direction} kaydı kapatıldı"
                    )
                return False, ""
            return True, f"DB'de açık {direction} kaydı var"
    except Exception:
        pass

    return False, ""


async def sync_position_state() -> bool:
    """
    Yerel state ↔ Binance pozisyonu.
    Döner: True = hâlâ açık pozisyon var, False = borsada kapandı (TP2/SL).
    """
    if is_paper_mode():
        from execution import paper as _paper
        return await _paper.paper_sync_position()

    if not state.in_position:
        return False

    if time.time() < getattr(state, "startup_grace_until", 0):
        return True

    ex_qty = await get_position_qty()
    if ex_qty < 0.001:
        log.info("Borsada pozisyon yok → TP2/SL dolmuş (poll)")
        from execution.position_lifecycle import async_finalize_position_closed

        mark = float(state.mark_price or state.price or 0)
        await async_finalize_position_closed(
            "exchange_closed_poll",
            source="sync",
            exit_price=mark,
        )
        return False

    if abs(ex_qty - state.pos_qty) > 0.0005:
        log.info(f"Miktar senkron: {state.pos_qty:.4f} → {ex_qty:.4f} ETH")
        state.pos_qty = round(ex_qty, 4)

    # Borsa TP1'i bot'tan önce doldurmuş olabilir
    if (
        not state.pos_tp1_hit
        and state.pos_qty_tp2 >= 0.001
        and abs(ex_qty - state.pos_qty_tp2) < 0.002
    ):
        log.info("Borsa TP1 dolu görünüyor → runner hazir")
        state.pos_tp1_hit = True
        if state.pos_tp1_id:
            await _req(
                "DELETE",
                "/fapi/v1/order",
                {"symbol": cfg.SYMBOL, "orderId": state.pos_tp1_id},
            )
            state.pos_tp1_id = ""
        await schedule_runner_sl_after_tp1()

    return True


async def on_tp1_hit() -> float:
    """
    TP1 tetiklendi: miktar senkronu, TP1 emri iptal, runner SL planla.
    Döner: kapanan TP1 miktarı (Telegram için).
    """
    if is_paper_mode():
        from execution import paper as _paper
        return await _paper.paper_on_tp1_hit()

    if state.pos_tp1_hit or not state.in_position:
        return 0.0

    qty_closed = state.pos_qty_tp1
    state.pos_tp1_hit = True
    try:
        from botlog.db import mark_open_trade_tp1_hit

        mark_open_trade_tp1_hit(_trade_id)
    except Exception:
        pass

    if state.pos_tp1_id:
        await _req(
            "DELETE",
            "/fapi/v1/order",
            {"symbol": cfg.SYMBOL, "orderId": state.pos_tp1_id},
        )
        state.pos_tp1_id = ""

    ex_qty = await get_position_qty()
    if ex_qty >= 0.001:
        state.pos_qty = round(ex_qty, 4)
        log.info(f"TP1 sonrası kalan: {state.pos_qty:.4f} ETH (borsa)")
    else:
        state.pos_qty = round(max(state.pos_qty_tp2, 0.0), 4)
        log.info(f"TP1 sonrası kalan (plan): {state.pos_qty:.4f} ETH")

    close_side = "SELL" if state.pos_side == "LONG" else "BUY"
    if state.pos_tp2_id:
        from execution.protection_orders import cancel_algo_order

        await cancel_algo_order(state.pos_tp2_id)
        state.pos_tp2_id = ""
    else:
        from execution.protection_orders import cancel_all_tp_algos

        await cancel_all_tp_algos(close_side)
        state.pos_tp1_id = ""

    await schedule_runner_sl_after_tp1()
    return qty_closed


async def schedule_runner_sl_after_tp1() -> bool:
    """TP1 sonrasi: SL ya hemen TP1'e (eski) ya da 15m kapanisa kadar korunur."""
    if getattr(cfg, "TP1_DEFER_SL_TO_15M", True):
        from execution.protection_orders import defer_runner_sl_to_15m

        return defer_runner_sl_to_15m()
    return await move_to_breakeven()


async def close_position(reason: str = "signal") -> float:
    if is_paper_mode():
        from execution import paper as _paper
        return await _paper.paper_close(reason)

    global _trade_id
    if time.time() < getattr(state, "startup_grace_until", 0):
        log.warning(f"Startup grace — pozisyon kapatma engellendi: {reason}")
        return 0.0
    if not state.in_position:
        return 0.0

    from execution.protection_orders import cancel_all_open_protection_orders

    await cancel_all_open_protection_orders(reason)

    qty = await get_position_qty()
    if qty >= 0.001:
        state.pos_qty = round(qty, 4)
    elif state.pos_qty < 0.001:
        log.warning("Kapatılacak miktar yok")
        from execution.position_lifecycle import async_finalize_position_closed

        await async_finalize_position_closed(reason, source="executor")
        return 0.0

    side = "SELL" if state.pos_side == "LONG" else "BUY"
    r = await _req(
        "POST",
        "/fapi/v1/order",
        {
            "symbol": cfg.SYMBOL,
            "side": side,
            "type": "MARKET",
            "quantity": state.pos_qty,
            "reduceOnly": "true",
            "positionSide": "BOTH",
        },
    )

    exit_plan = Plan(
        direction=state.pos_side,
        entry=state.pos_entry,
        sl=state.pos_sl,
        tp1=state.pos_tp1,
        tp2=state.pos_tp2,
        qty_total=state.pos_qty,
        qty_tp1=0,
        qty_tp2=0,
        risk_usdt=0,
        notional=0,
        margin_req=0,
        liq_price=0,
        rr_tp1=0,
        rr_tp2=0,
        warnings=[],
    )
    exit_px = await _resolve_fill_price(r if isinstance(r, dict) else {}, exit_plan)
    if exit_px <= 0:
        exit_px = float(state.price or state.mark_price or state.pos_entry)
    sign = 1 if state.pos_side == "LONG" else -1
    pnl = round((exit_px - state.pos_entry) * state.pos_qty * sign, 4)
    dur_min = round((time.time() - state.pos_open_ts) / 60, 1)

    log.info(
        f"POZİSYON KAPATILDI: {state.pos_side} @ {exit_px:.2f}  "
        f"PnL={pnl:+.4f} USDT  süre={dur_min}dk  sebep={reason}"
    )

    if _trade_id:
        log_trade_close(
            _trade_id,
            {
                "exit_price": exit_px,
                "pnl": pnl,
                "pnl_pct": round(pnl / (state.pos_entry * state.pos_qty) * 100, 3),
                "status": "CLOSED",
                "close_reason": reason,
                "close_ts": time.time(),
                "duration_min": dur_min,
                "tp1_hit": int(state.pos_tp1_hit),
                "be_activated": int(state.pos_be_active),
            },
        )

    from execution.position_lifecycle import async_finalize_position_closed

    await async_finalize_position_closed(reason, source="executor")

    try:
        from utils.notifier import notify_close
        await notify_close(reason, pnl)
    except Exception:
        pass

    return pnl


async def move_to_breakeven() -> bool:
    """TP1 sonrası: SL → TP1 seviyesi (TP1_DEFER_SL_TO_15M kapalıysa veya acil BE)."""
    if getattr(cfg, "TP1_DEFER_SL_TO_15M", True) and state.pos_tp1_hit:
        from execution.protection_orders import defer_runner_sl_to_15m

        return defer_runner_sl_to_15m()

    if is_paper_mode():
        from execution import paper as _paper
        return await _paper.paper_move_to_breakeven()

    if not state.in_position:
        return False
    from engine.position_sl import initial_trail_sl_at_tp1, _mark, mark_sl_managed
    from execution.protection_orders import replace_sl_algo

    mark = _mark()
    new_sl = initial_trail_sl_at_tp1(state.pos_side, state.pos_tp1, mark)
    if new_sl <= 0:
        return False
    if await replace_sl_algo(new_sl, "TP1 sonrası SL=TP1"):
        state.pos_be_active = True
        pb = dict(state.position_breakout or {})
        pb["sl_stage"] = "trail_15m"
        state.position_breakout = pb
        mark_sl_managed()
        return True
    return False


async def reverse_position(plan: Plan, signal_id: int = 0) -> bool:
    log.info(f"TERS: {state.pos_side} kapatılıyor → {plan.direction} açılıyor")
    await close_position(f"reverse_to_{plan.direction}")
    await asyncio.sleep(0.5)
    return await open_position(plan, signal_id)


def check_tp1_hit() -> bool:
    if state.pos_tp1_hit or not state.in_position:
        return False
    p = state.price
    if state.pos_side == "LONG" and p >= state.pos_tp1:
        return True
    if state.pos_side == "SHORT" and p <= state.pos_tp1:
        return True
    return False


async def _cancel_all():
    from execution.protection_orders import cancel_all_open_protection_orders

    await cancel_all_open_protection_orders("cancel_all")
