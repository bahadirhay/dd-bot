"""
execution/paper.py — İzleme modu: gerçek emir yok, tam pozisyon simülasyonu.
TP1 / TP2 / SL / breakeven fiyat ile tetiklenir; sonuçlar DB'ye yazılır.
"""
from __future__ import annotations

import time
from core.config import cfg
from core.state import state
from core.logger import get_logger
from execution.risk import Plan
from botlog.db import log_trade_open, log_trade_close

log = get_logger("Paper")

_trade_id: int = 0
_partial_pnl: float = 0.0


def init_paper_session():
    """İzleme oturumu bakiyesi."""
    state.paper_balance = float(getattr(cfg, "PAPER_BALANCE_USD", 10_000.0))
    state.paper_mode = True
    state.api_ok = True
    state.api_error = ""
    state.wallet_balance = state.paper_balance
    state.available_balance = state.paper_balance
    state.equity_balance = state.paper_balance
    state.real_balance = state.paper_balance
    state.real_balance_ts = time.time()
    state.exchange_position = {}
    global _trade_id, _partial_pnl
    _trade_id = 0
    _partial_pnl = 0.0


def paper_balance() -> float:
    return float(getattr(state, "paper_balance", 0.0) or cfg.PAPER_BALANCE_USD)


def _fill_price(plan: Plan) -> float:
    if plan.direction == "LONG":
        return state.ask or state.price or plan.entry
    return state.bid or state.price or plan.entry


def _mark_unrealized():
    if not state.in_position:
        state.unrealized_pnl = 0.0
        state.exchange_position = {}
        state.equity_balance = paper_balance()
        return
    px = state.price or state.mark_price or state.pos_entry
    sign = 1 if state.pos_side == "LONG" else -1
    state.unrealized_pnl = round((px - state.pos_entry) * state.pos_qty * sign, 4)
    bal = paper_balance()
    state.wallet_balance = bal
    state.available_balance = max(bal - state.pos_margin, 0.0)
    state.equity_balance = round(bal + state.unrealized_pnl, 4)
    state.real_balance = state.equity_balance
    state.real_balance_ts = time.time()
    notional = state.pos_qty * px if px > 0 else 0
    state.exchange_position = {
        "side": state.pos_side,
        "entry": round(state.pos_entry, 2),
        "qty": state.pos_qty,
        "mark": round(px, 2),
        "size": round(state.pos_margin, 2),
        "size_lev": round(notional, 1),
        "pnl": round(state.unrealized_pnl, 2),
        "sl": round(state.pos_sl, 2),
        "tp1": round(state.pos_tp1, 2),
        "tp2": round(state.pos_tp2, 2),
        "tp1_hit": state.pos_tp1_hit,
        "leverage": cfg.LEVERAGE,
        "live_from_api": False,
        "paper": True,
        "equity": state.equity_balance,
        "wallet": round(bal, 2),
        "available": round(state.available_balance, 2),
        "opened_at": time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(state.pos_open_ts)
        )
        if state.pos_open_ts
        else "—",
    }


def _apply_pnl_to_balance(pnl: float):
    state.paper_balance = round(paper_balance() + pnl, 4)
    state.real_balance = state.paper_balance
    state.real_balance_ts = time.time()


async def paper_open(plan: Plan, signal_id: int = 0) -> bool:
    global _trade_id, _partial_pnl
    if not plan.ok():
        return False

    from execution.executor import same_direction_position_open

    blocked, reason = await same_direction_position_open(plan.direction)
    if blocked:
        log.warning(f"[PAPER] Aynı yönde ek pozisyon yok: {reason}")
        state.no_entry_reason = reason
        return False

    fill = round(_fill_price(plan), 2)
    _partial_pnl = 0.0

    log.info(
        f"\n{'═'*54}\n"
        f"  [PAPER] POZİSYON AÇILDI: {plan.direction}  "
        f"{plan.qty_total:.4f} ETH @ {fill:.2f}\n"
        f"  [PAPER] SL={plan.sl:.2f}  TP1={plan.tp1:.2f}  TP2={plan.tp2:.2f}  "
        f"(simüle — borsaya emir yok)\n"
        f"  Marjin(sim): {plan.margin_req:.2f} USDT  "
        f"({cfg.MARGIN} {cfg.LEVERAGE}x)  Liq={plan.liq_price:.2f}\n"
        f"  Paper bakiye: ${paper_balance():,.2f}\n"
        f"{'═'*54}"
    )

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
    state.pos_sl_id = "paper-sl"
    state.pos_tp1_id = "paper-tp1"
    state.pos_tp2_id = "paper-tp2"
    state.pos_tp1_hit = False
    state.pos_be_active = False
    state.pos_open_ts = time.time()
    _mark_unrealized()

    _trade_id = log_trade_open(
        {
            "signal_id": signal_id,
            "order_id": f"PAPER-{int(time.time())}",
            "direction": plan.direction,
            "entry_price": fill,
            "qty": plan.qty_total,
            "qty_tp1": plan.qty_tp1,
            "qty_tp2": plan.qty_tp2,
            "sl": plan.sl,
            "tp1": plan.tp1,
            "tp2": plan.tp2,
            "liq_price": plan.liq_price,
            "margin": plan.margin_req,
            "leverage": cfg.LEVERAGE,
            "margin_type": cfg.MARGIN,
            "open_ts": state.pos_open_ts,
            "regime_at_open": state.regime,
            "cvd_at_open": state.cvd_5m,
            "notes": "paper",
        }
    )
    try:
        from botlog.db import update_trade_open_features
        from botlog.trade_features import collect_open_features

        update_trade_open_features(
            _trade_id,
            collect_open_features(plan.direction, fill, plan.tp1, plan.sl),
        )
    except Exception:
        pass
    try:
        from engine.attribution_v3 import attach_attribution_to_trade

        attach_attribution_to_trade(_trade_id)
    except Exception:
        pass
    try:
        from engine.execution_brain_v3 import snapshot_trade_brain

        snapshot_trade_brain()
    except Exception:
        pass
    return True


async def paper_replace_sl(new_sl: float, reason: str = "") -> bool:
    if not state.in_position or new_sl <= 0:
        return False
    from engine.position_sl import _sl_tighter

    old = float(state.pos_sl or 0)
    if old > 0 and (abs(new_sl - old) < 0.5 or not _sl_tighter(state.pos_side, new_sl, old)):
        return False
    state.pos_sl = new_sl
    state.pos_sl_id = "paper-trail-sl"
    tag = reason or "güncelleme"
    log.info(f"[PAPER] SL sıkılaştırıldı ({tag}): {old:.2f} → {new_sl:.2f}")
    return True


async def paper_move_to_breakeven() -> bool:
    if state.pos_be_active or not state.in_position:
        return False
    from engine.position_sl import initial_trail_sl_at_tp1, _mark

    mark = _mark() or state.pos_entry
    new_sl = initial_trail_sl_at_tp1(state.pos_side, state.pos_tp1, mark)
    if new_sl <= 0:
        m = state.pos_entry * 0.0005
        new_sl = round(
            state.pos_entry + m if state.pos_side == "LONG" else state.pos_entry - m,
            2,
        )
    state.pos_sl = new_sl
    state.pos_sl_id = "paper-trail-sl"
    state.pos_be_active = True
    pb = dict(state.position_breakout or {})
    pb["sl_stage"] = "trail_15m"
    state.position_breakout = pb
    log.info(f"[PAPER] TP1 sonrası SL=TP1: → {new_sl:.2f}")
    return True


async def paper_on_tp1_hit() -> float:
    global _partial_pnl
    if state.pos_tp1_hit or not state.in_position:
        return 0.0

    qty_closed = state.pos_qty_tp1
    px = state.price or state.pos_tp1
    sign = 1 if state.pos_side == "LONG" else -1
    pnl_part = round((px - state.pos_entry) * qty_closed * sign, 4)
    _partial_pnl += pnl_part
    _apply_pnl_to_balance(pnl_part)

    state.pos_tp1_hit = True
    state.pos_qty = round(max(state.pos_qty_tp2, 0.0), 4)
    state.pos_tp1_id = ""

    from core.config import cfg

    if getattr(cfg, "TP1_DEFER_SL_TO_15M", True):
        from execution.protection_orders import defer_runner_sl_to_15m

        defer_runner_sl_to_15m()
    else:
        await paper_move_to_breakeven()
    log.info(
        f"[İZLEME] TP1 @ {px:.2f}  kapatılan={qty_closed:.4f} ETH  "
        f"kısmi PnL={pnl_part:+.4f} USDT  kalan={state.pos_qty:.4f} ETH"
    )
    return qty_closed


async def paper_close(reason: str = "signal") -> float:
    global _trade_id, _partial_pnl
    if not state.in_position:
        return 0.0

    exit_px = state.price or state.mark_price
    sign = 1 if state.pos_side == "LONG" else -1
    pnl_close = round((exit_px - state.pos_entry) * state.pos_qty * sign, 4)
    pnl_total = round(_partial_pnl + pnl_close, 4)
    dur_min = round((time.time() - state.pos_open_ts) / 60, 1)

    _apply_pnl_to_balance(pnl_close)

    notional = state.pos_entry * (state.pos_qty + (state.pos_qty_tp1 if state.pos_tp1_hit else 0))
    pnl_pct = round(pnl_total / max(notional, 1) * 100, 3) if notional else 0.0

    log.info(
        f"[İZLEME] POZİSYON KAPATILDI: {state.pos_side} @ {exit_px:.2f}  "
        f"PnL={pnl_total:+.4f} USDT (kısmi={_partial_pnl:+.4f})  "
        f"süre={dur_min}dk  sebep={reason}  "
        f"paper bakiye=${paper_balance():,.2f}"
    )

    if _trade_id:
        log_trade_close(
            _trade_id,
            {
                "exit_price": exit_px,
                "pnl": pnl_total,
                "pnl_pct": pnl_pct,
                "status": "CLOSED",
                "close_reason": reason,
                "close_ts": time.time(),
                "duration_min": dur_min,
                "tp1_hit": int(state.pos_tp1_hit),
                "be_activated": int(state.pos_be_active),
            },
        )

    from execution.position_lifecycle import finalize_position_closed

    finalize_position_closed(reason, source="paper", exit_px=exit_px)
    _partial_pnl = 0.0
    _trade_id = 0

    try:
        from utils.notifier import notify_close
        await notify_close(f"[İZLEME] {reason}", pnl_total)
    except Exception:
        pass

    return pnl_total


def _sl_hit() -> bool:
    if not state.in_position:
        return False
    p = state.price
    if state.pos_side == "LONG" and p <= state.pos_sl:
        return True
    if state.pos_side == "SHORT" and p >= state.pos_sl:
        return True
    return False


def _tp2_hit() -> bool:
    if not state.in_position or not state.pos_tp1_hit:
        return False
    p = state.price
    if state.pos_side == "LONG" and p >= state.pos_tp2:
        return True
    if state.pos_side == "SHORT" and p <= state.pos_tp2:
        return True
    return False


async def paper_sync_position() -> bool:
    """Paper: borsa yok; SL/TP2 fiyat kontrolü."""
    if not state.in_position:
        return False
    _mark_unrealized()
    if _sl_hit():
        await paper_close("runner_sl" if state.pos_tp1_hit else "sl_hit")
        return False
    from core.config import cfg

    if bool(getattr(cfg, "SEND_TP2_ORDER", False)) and _tp2_hit():
        await paper_close("tp2_hit")
        return False
    return True

