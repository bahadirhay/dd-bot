"""
execution/account_sync.py — Binance Futures hesap + pozisyon (mmbot3 uyumlu).
"""
from __future__ import annotations

import time
from typing import Any

from core.config import cfg, is_paper_mode
from core.state import state
from core.logger import get_logger

log = get_logger("AccountSync")

_last_sync_ts: float = 0.0
_flat_streak: int = 0
FLAT_CONFIRM_READS: int = 2


def _f(row: dict, *keys: str, default: float = 0.0) -> float:
    for k in keys:
        if k in row and row[k] is not None and row[k] != "":
            try:
                return float(row[k])
            except (TypeError, ValueError):
                pass
    return default


def _normalize_list(data: Any) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if data.get("symbol") and "positionAmt" in data:
            return [data]
        for k in ("orders", "data", "positionRisk", "assets"):
            v = data.get(k)
            if isinstance(v, list):
                return v
    return []


def pick_symbol_position(data: Any, symbol: str | None = None) -> dict | None:
    """
    positionRisk yanıtından en büyük mutlak miktarlı satırı seç.
    Hedge modda boş SHORT + dolu LONG satırı olabilir — ilk satır yanlış olur.
    """
    sym = symbol or cfg.SYMBOL
    best: dict | None = None
    best_amt = 0.0
    for row in _normalize_list(data):
        if not isinstance(row, dict) or row.get("symbol") != sym:
            continue
        amt = abs(_f(row, "positionAmt"))
        if amt > best_amt:
            best_amt = amt
            best = row
    return best if best_amt >= 1e-9 else None


async def fetch_position_row() -> dict | None:
    """Borsadaki aktif ETH pozisyon satırı (yoksa None)."""
    from execution.executor import _signed_request

    pr_raw = await _signed_request(
        "GET", "/fapi/v2/positionRisk", {"symbol": cfg.SYMBOL}
    )
    row = pick_symbol_position(pr_raw)
    if not row:
        pr_all = await _signed_request("GET", "/fapi/v2/positionRisk", {})
        row = pick_symbol_position(pr_all)
    return row


async def cancel_orphan_exchange_orders(reason: str = "startup_flat") -> int:
    """Pozisyon yokken borsadaki açık SL/TP/limit emirlerini sil."""
    from execution.protection_orders import cancel_all_open_protection_orders

    return await cancel_all_open_protection_orders(reason)


async def restore_live_position_from_exchange(
    row: dict | None = None,
) -> bool:
    """
    Borsada açık pozisyon varken yerel state/DB'yi yeniden bağla.
    Market kapatma yapmaz.
    """
    if is_paper_mode() or not cfg.API_KEY:
        return False

    from execution.executor import is_position_opening

    if is_position_opening():
        log.debug("Açılış sürüyor — exchange restore atlandı (çift DB önleme)")
        return False

    from botlog.db import get_trade_levels, reconcile_open_trades_with_exchange

    if row is None:
        row = await fetch_position_row()
    if not row:
        return False

    amt = _f(row, "positionAmt")
    if abs(amt) < 0.0001:
        return False

    entry = _f(row, "entryPrice")
    side = "LONG" if amt > 0 else "SHORT"
    qty = round(abs(amt), 4)

    state.in_position = True
    state.pos_side = side
    state.pos_entry = entry
    state.pos_qty = qty
    if not state.pos_open_ts:
        state.pos_open_ts = time.time()

    import execution.executor as ex

    ex._trade_id = reconcile_open_trades_with_exchange(side, entry, qty)

    lv = get_trade_levels(ex._trade_id)
    if lv:
        if lv.get("sl", 0) > 0:
            state.pos_sl = lv["sl"]
            state.pos_sl_initial = lv["sl"]
        if lv.get("tp1", 0) > 0:
            state.pos_tp1 = lv["tp1"]
        if lv.get("tp2", 0) > 0:
            state.pos_tp2 = lv["tp2"]
        if lv.get("tp1_hit"):
            state.pos_tp1_hit = True
        q = lv.get("qty", 0) or qty
        if lv.get("qty_tp1", 0) > 0:
            state.pos_qty_tp1 = lv["qty_tp1"]
        else:
            state.pos_qty_tp1 = round(q * cfg.TP1_PCT, 4)
        if lv.get("qty_tp2", 0) > 0:
            state.pos_qty_tp2 = lv["qty_tp2"]
        else:
            state.pos_qty_tp2 = round(max(q - state.pos_qty_tp1, 0), 4)

    if getattr(cfg, "STRATEGY_V3_ENABLED", False):
        fallback_s = 0.0
        fallback_r = 0.0
        try:
            from core.state import effective_price
            from engine.levels_v3 import get_levels_snapshot

            px = float(effective_price() or state.mark_price or entry or 0)
            snap = get_levels_snapshot(px) if px > 0 else {}
            fallback_s = float(snap.get("active_support") or 0)
            fallback_r = float(snap.get("active_resistance") or 0)
            if fallback_s <= 0:
                fallback_s = float((snap.get("support") or {}).get("price", 0) or 0)
            if fallback_r <= 0:
                fallback_r = float((snap.get("resistance") or {}).get("price", 0) or 0)
        except Exception:
            pass

        from engine.position_manager_v2 import restore_entry_anchors_from_db

        entry_support, entry_resistance = restore_entry_anchors_from_db(
            ex._trade_id,
            fallback_support=fallback_s,
            fallback_resistance=fallback_r,
        )
        state.position_breakout = {
            "strategy": "v3",
            "entry_mode": "v3",
            "direction": side,
            "scenario": "RESTORED_POSITION",
            "trigger": "RESTORE",
            "break_level": 0.0,
            "active_support": entry_support or fallback_s,
            "active_resistance": entry_resistance or fallback_r,
            "entry_support": entry_support,
            "entry_resistance": entry_resistance,
            "tp1": float(state.pos_tp1 or 0),
            "tp2": float(state.pos_tp2 or 0),
        }
        from engine.thesis_v3 import rebuild_thesis_from_position

        state.position_breakout["thesis"] = rebuild_thesis_from_position(
            state.position_breakout, side, entry
        )
        th = state.position_breakout["thesis"]
        if th:
            log.info(
                f"Tez restore: {th.get('scenario')} key={th.get('key_level'):.2f} "
                f"invalidation={th.get('invalidation_price'):.2f}"
            )
        if entry_support > 0:
            log.info(
                f"Giris destegi restore: S={entry_support:.2f} "
                f"R={entry_resistance:.2f} (trade_id={ex._trade_id})"
            )
    else:
        from engine.position_sl import restore_position_context

        restore_position_context(ex._trade_id)
    # Seviye haritası chart backfill sonrası main.py içinde refresh_levels ile güncellenir

    from execution.protection_orders import ensure_protection_orders

    try:
        await ensure_protection_orders()
    except Exception as ex_err:
        log.warning(f"Koruma emirleri restore: {ex_err}")

    log.info(
        f"Pozisyon geri yüklendi — {side} {qty:.4f} ETH @ {entry:.2f} "
        f"(trade_id={ex._trade_id})"
    )
    return True


async def reconcile_exchange_flat() -> None:
    """Borsa gerçekten düz: yerel state, DB OPEN ve yetim SL/TP temizle."""
    global _flat_streak

    n_orders = await cancel_orphan_exchange_orders("exchange_flat_sync")
    clear_local_position_state()
    from botlog.db import close_orphan_open_trades

    n_db = close_orphan_open_trades("exchange_flat_sync")
    _flat_streak = 0
    log.warning(
        f"Borsa düz (onaylı) — {n_orders} koruma emri iptal, "
        f"{n_db} DB OPEN kayıt kapatıldı (bot market kapatmadı)"
    )


def clear_local_position_state() -> None:
    """Borsa düz — yerel pozisyon bayrakları (DB ayrı)."""
    state.in_position = False
    state.pos_side = ""
    state.pos_entry = 0.0
    state.pos_qty = 0.0
    state.pos_qty_tp1 = 0.0
    state.pos_qty_tp2 = 0.0
    state.pos_sl = 0.0
    state.pos_tp1 = 0.0
    state.pos_tp2 = 0.0
    state.pos_tp1_hit = False
    state.pos_be_active = False
    state.unrealized_pnl = 0.0
    state.exchange_position = {}
    state.position_breakout = {}


async def refresh_account_snapshot(*, force: bool = False) -> bool:
    """Canlı: bakiye (equity) + pozisyon + algo SL/TP → state."""
    global _last_sync_ts, _flat_streak

    if is_paper_mode() or not cfg.API_KEY:
        return False

    now = time.time()
    if not force and (now - _last_sync_ts) < 2.0:
        return state.api_ok

    from execution.executor import _signed_request
    from execution.protection_orders import (
        get_open_algo_orders,
        _parse_algo_orders,
        ensure_protection_orders,
        sync_protection_ids_from_exchange,
    )

    try:
        wallet = avail = cross_pnl = margin_bal = 0.0

        acct = await _signed_request("GET", "/fapi/v2/account")
        if isinstance(acct, dict):
            wallet = _f(acct, "totalWalletBalance")
            margin_bal = _f(acct, "totalMarginBalance")
            cross_pnl = _f(acct, "totalUnrealizedProfit")
            avail = _f(acct, "availableBalance")
            if wallet <= 0:
                wallet = _f(acct, "totalCrossWalletBalance")

        if wallet <= 0 and margin_bal <= 0:
            bal_raw = await _signed_request("GET", "/fapi/v2/balance")
            if isinstance(bal_raw, list):
                for asset in bal_raw:
                    if asset.get("asset") != "USDT":
                        continue
                    wallet = _f(asset, "balance", "crossWalletBalance")
                    avail = _f(asset, "availableBalance")
                    cross_pnl = _f(asset, "crossUnPnl")
                    if margin_bal <= 0 and wallet > 0:
                        margin_bal = wallet + cross_pnl
                    break

        # Binance UI "Margin Balance" = totalMarginBalance (cüzdan + tüm uPnL)
        if margin_bal > 0:
            equity = margin_bal
        elif wallet > 0:
            equity = wallet + cross_pnl
        else:
            equity = 0.0

        state.wallet_balance = wallet
        state.available_balance = avail
        state.unrealized_pnl = round(cross_pnl, 4)
        state.equity_balance = round(equity, 4)
        state.real_balance = state.equity_balance
        state.real_balance_ts = now
        state.api_ok = True
        state.api_error = ""

        pr_raw = await _signed_request(
            "GET", "/fapi/v2/positionRisk", {"symbol": cfg.SYMBOL}
        )
        row = pick_symbol_position(pr_raw)
        if not row:
            pr_all = await _signed_request("GET", "/fapi/v2/positionRisk", {})
            row = pick_symbol_position(pr_all)

        algos = await get_open_algo_orders()
        sl_price = tp1 = tp2 = 0.0
        tp1_hit = False
        state.exchange_position = {}
        amt = 0.0

        if row:
            amt = _f(row, "positionAmt")
            if abs(amt) >= 0.0001:
                _flat_streak = 0
                if not state.in_position:
                    from execution.executor import is_position_opening

                    if is_position_opening():
                        log.debug(
                            "Borsada pozisyon var ama açılış sürüyor — restore bekletildi"
                        )
                    else:
                        log.warning(
                            "Borsada pozisyon var, yerel state boş — geri yükleniyor"
                        )
                        await restore_live_position_from_exchange(row)
                side = "LONG" if amt > 0 else "SHORT"
                entry = _f(row, "entryPrice")
                mark = _f(row, "markPrice") or state.mark_price or state.price
                lev = int(_f(row, "leverage") or cfg.LEVERAGE) or cfg.LEVERAGE
                qty = round(abs(amt), 4)
                notional = qty * max(mark, 1e-9)
                margin_usd = notional / max(lev, 1)
                pos_upnl = _f(row, "unRealizedProfit", "unrealizedProfit")
                # Gösterim: pozisyon uPnL (panel ile aynı); equity hesabı API margin
                upnl = pos_upnl if pos_upnl != 0 else cross_pnl

                prev_tp1_hit = bool(state.pos_tp1_hit)
                sl_price, tp1, tp2, tp1_hit = _parse_algo_orders(algos, side)
                from execution.protection_orders import sync_tp1_hit_state

                await sync_tp1_hit_state(qty)
                tp1_hit = bool(state.pos_tp1_hit)
                await sync_protection_ids_from_exchange(algos)
                if sl_price > 0:
                    state.pos_sl = sl_price
                elif state.pos_sl <= 0:
                    sl_price = state.pos_sl
                else:
                    sl_price = state.pos_sl
                if tp1 <= 0:
                    tp1 = state.pos_tp1
                if tp2 <= 0:
                    tp2 = state.pos_tp2

                state.in_position = True
                state.pos_side = side
                state.pos_entry = entry
                state.pos_qty = qty
                state.unrealized_pnl = round(upnl, 4)
                state.equity_balance = round(equity, 4)
                state.real_balance = state.equity_balance
                state.pos_tp1_hit = bool(state.pos_tp1_hit or tp1_hit)
                if state.pos_tp1_hit and not prev_tp1_hit:
                    state.pos_tp1_id = ""
                if state.pos_margin <= 0:
                    state.pos_margin = round(margin_usd, 2)

                state.exchange_position = {
                    "side": side,
                    "entry": round(entry, 2),
                    "qty": qty,
                    "mark": round(mark, 2),
                    "size": round(margin_usd, 2),
                    "size_lev": round(notional, 1),
                    "pnl": round(upnl, 2),
                    "sl": round(sl_price, 2) if sl_price else 0.0,
                    "tp1": round(tp1, 2) if tp1 else 0.0,
                    "tp2": round(tp2, 2) if tp2 else 0.0,
                    "tp1_hit": tp1_hit,
                    "leverage": lev,
                    "live_from_api": True,
                    "equity": state.equity_balance,
                    "wallet": round(wallet, 2),
                    "margin_balance": round(margin_bal or equity, 2),
                    "available": round(avail, 2),
                    "opened_at": (
                        time.strftime(
                            "%Y-%m-%d %H:%M:%S",
                            time.localtime(state.pos_open_ts),
                        )
                        if state.pos_open_ts
                        else "—"
                    ),
                }

                need_tp = not state.pos_tp1_hit and tp1 <= 0
                if bool(getattr(cfg, "SEND_TP2_ORDER", False)):
                    need_tp = need_tp and tp2 <= 0
                if sl_price <= 0 or need_tp:
                    from execution.protection_orders import ensure_protection_orders

                    await ensure_protection_orders()
                    algos = await get_open_algo_orders()
                    sl_price, tp1, tp2, tp1_hit = _parse_algo_orders(algos, side)
                    state.exchange_position["sl"] = round(sl_price, 2) if sl_price else 0
                    state.exchange_position["tp1"] = round(tp1, 2) if tp1 else 0
                    state.exchange_position["tp2"] = round(tp2, 2) if tp2 else 0
                    state.exchange_position["tp1_hit"] = tp1_hit

                if tp1_hit and not prev_tp1_hit:
                    try:
                        state.pos_tp1_hit = True
                        from execution.executor import schedule_runner_sl_after_tp1

                        await schedule_runner_sl_after_tp1()
                        log.info("Account sync: TP1 dolu gorundu -> runner SL planlandi")
                    except Exception as ex:
                        log.warning(f"Account sync TP1 runner SL: {ex}")

        if abs(amt) < 0.0001:
            _flat_streak += 1
            if _flat_streak >= FLAT_CONFIRM_READS:
                if state.in_position:
                    await reconcile_exchange_flat()
                else:
                    from botlog.db import count_open_trades, close_orphan_open_trades

                    if count_open_trades() > 0:
                        n_db = close_orphan_open_trades("exchange_flat_db_only")
                        if n_db:
                            log.info(
                                f"Borsa düz, DB'de {n_db} OPEN kayıt kapatıldı"
                            )
                    state.exchange_position = {}
                    _flat_streak = 0
            elif state.in_position:
                log.debug(
                    f"Borsa düz okuma ({_flat_streak}/{FLAT_CONFIRM_READS}) — "
                    "henüz state temizlenmedi"
                )
            else:
                state.exchange_position = {}
            if not state.pos_qty:
                state.unrealized_pnl = 0.0
            state.equity_balance = round(
                margin_bal if margin_bal > 0 else wallet, 4
            )
            state.real_balance = state.equity_balance

        state.account_sync_ts = now
        _last_sync_ts = now

        if state.in_position:
            if not getattr(cfg, "STRATEGY_V3_ENABLED", False):
                try:
                    from engine.breakout import refresh_levels

                    refresh_levels()
                except Exception:
                    pass
            try:
                from execution.protection_orders import manage_position_sl

                await manage_position_sl()
            except Exception as ex:
                log.warning(f"SL yönetimi hata: {ex}")
        return True

    except Exception as e:
        state.api_error = str(e)[:120]
        log.warning(f"Hesap senkronu: {state.api_error}")
        return False


async def reconcile_startup_exchange() -> bool:
    """
    Bot açılışında tek otorite: borsa pozisyonu yükle veya düzken yetim emirleri sil.
    Asla market emri ile pozisyon kapatmaz.
    Döner: True = borsada açık pozisyon var.
    """
    if is_paper_mode() or not cfg.API_KEY:
        state.exchange_reconciled = True
        return False

    from botlog.db import close_orphan_open_trades

    row = await fetch_position_row()
    amt = _f(row, "positionAmt") if row else 0.0

    if row and abs(amt) >= 0.0001:
        await restore_live_position_from_exchange(row)
        await refresh_account_snapshot(force=True)

        from execution.protection_orders import manage_position_sl, maybe_adjust_open_tp

        await manage_position_sl(force=True)
        try:
            await maybe_adjust_open_tp()
        except Exception:
            pass

        import execution.executor as ex

        log.info(
            f"Startup reconcile: pozisyon yüklendi — {state.pos_side} "
            f"{state.pos_qty:.4f} ETH @ {state.pos_entry:.2f}  "
            f"(trade_id={ex._trade_id})  SL={state.pos_sl:.2f} "
            f"TP1={state.pos_tp1:.2f}"
        )
        state.exchange_reconciled = True
        return True

    n_orders = await cancel_orphan_exchange_orders("startup_no_position")
    clear_local_position_state()
    n_db = close_orphan_open_trades("startup_flat")
    log.info(
        f"Startup reconcile: borsa düz — {n_orders} koruma emri iptal, "
        f"{n_db} DB OPEN kayıt kapatıldı"
    )
    state.exchange_reconciled = True
    return False
