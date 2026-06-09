"""
execution/protection_orders.py — SL/TP algo emirleri (mmbot3: /fapi/v1/algoOrder).

Binance koşullu emirler çoğu hesapta yalnızca algo API ile kabul edilir.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal, ROUND_DOWN
from typing import Any

from core.config import cfg
from core.state import state
from core.logger import get_logger

log = get_logger("Protection")

_symbol_filters: dict[str, tuple[float, float, float]] = {}
_ensure_busy: bool = False


async def _signed(method: str, path: str, params: dict | None = None) -> Any:
    from execution.executor import _signed_request

    return await _signed_request(method, path, params or {})


async def _get_symbol_filters(symbol: str) -> tuple[float, float, float]:
    if symbol in _symbol_filters:
        return _symbol_filters[symbol]
    import aiohttp

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
        async with s.get(f"{cfg.REST}/fapi/v1/exchangeInfo") as r:
            info = await r.json(content_type=None)
    step, tick, min_n = 0.001, 0.01, 5.0
    for sdata in info.get("symbols", []):
        if sdata.get("symbol") != symbol:
            continue
        for f in sdata.get("filters", []):
            ft = f.get("filterType")
            if ft == "LOT_SIZE":
                step = float(f.get("stepSize", step))
            elif ft == "PRICE_FILTER":
                tick = float(f.get("tickSize", tick))
            elif ft == "MIN_NOTIONAL":
                min_n = float(f.get("notional", f.get("minNotional", min_n)))
        break
    _symbol_filters[symbol] = (step, tick, min_n)
    return _symbol_filters[symbol]


def _round_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    v = Decimal(str(value))
    s = Decimal(str(step))
    return float((v / s).quantize(Decimal("1"), rounding=ROUND_DOWN) * s)


async def format_price(price: float) -> str:
    p = await round_price_float(price)
    return f"{p:.8f}".rstrip("0").rstrip(".") or "0"


async def round_price_float(price: float) -> float:
    _, tick, _ = await _get_symbol_filters(cfg.SYMBOL)
    return _round_step(price, tick)


async def _current_mark() -> float:
    px = state.mark_price or state.price
    if px > 0:
        return px
    from core.futures_public_rest import get_premium_index

    d = await get_premium_index()
    if d and d.get("mark_price", 0) > 0:
        state.mark_price = d["mark_price"]
        return d["mark_price"]
    return 0.0


async def resolve_tp_levels(
    direction: str,
    entry: float,
    tp1: float,
    tp2: float,
    mark: float,
) -> tuple[float, float]:
    """
    SHORT: TP tetik fiyatı mark'ın ALTINDA olmalı (aksi halde -2021).
    LONG: mark'ın ÜSTÜNDE.
    Yapısal TP fiyat geçtiyse mark - buffer ile yeniden hesaplanır.
    """
    if mark <= 0:
        return tp1, tp2

    bps1 = float(cfg.PROTECTION_TP_MIN_BPS)
    bps2 = float(cfg.PROTECTION_TP2_EXTRA_BPS)
    _, tick, _ = await _get_symbol_filters(cfg.SYMBOL)
    step = max(tick * 2, tick) if tick > 0 else 0.01

    if direction == "SHORT":
        cap1 = mark * (1.0 - bps1 / 10000.0)
        cap2 = mark * (1.0 - bps2 / 10000.0)
        r1 = min(tp1, cap1) if tp1 > 0 else cap1
        if entry > 0 and r1 >= entry:
            r1 = min(cap1, entry - step)
        r2 = min(tp2, cap2) if tp2 > 0 else cap2
        if r2 >= r1:
            r2 = r1 - step
        if entry > 0 and r2 >= entry:
            r2 = min(cap2, entry - step * 2)
    else:
        floor1 = mark * (1.0 + bps1 / 10000.0)
        floor2 = mark * (1.0 + bps2 / 10000.0)
        r1 = max(tp1, floor1) if tp1 > 0 else floor1
        if entry > 0 and r1 <= entry:
            r1 = max(floor1, entry + step)
        r2 = max(tp2, floor2) if tp2 > 0 else floor2
        if r2 <= r1:
            r2 = r1 + step
        if entry > 0 and r2 <= entry:
            r2 = max(floor2, entry + step * 2)

    r1 = await round_price_float(r1)
    r2 = await round_price_float(r2)

    if tp1 > 0 and abs(r1 - tp1) > step * 0.5:
        log.warning(
            f"TP1 ayarlandı: plan={tp1:.2f} → {r1:.2f} "
            f"({direction} mark={mark:.2f} entry={entry:.2f})"
        )
    if tp2 > 0 and abs(r2 - tp2) > step * 0.5:
        log.warning(
            f"TP2 ayarlandı: plan={tp2:.2f} → {r2:.2f} "
            f"({direction} mark={mark:.2f})"
        )
    return r1, r2


async def round_qty_float(qty: float) -> float:
    """Borsa LOT_SIZE adımına göre miktar (aşağı yuvarla)."""
    step, _, _ = await _get_symbol_filters(cfg.SYMBOL)
    return _round_step(qty, step)


async def format_qty(qty: float) -> str:
    q = await round_qty_float(qty)
    if q <= 0:
        return "0"
    step, _, _ = await _get_symbol_filters(cfg.SYMBOL)
    if step >= 1:
        return str(int(q))
    return f"{q:.8f}".rstrip("0").rstrip(".") or "0"


def _normalize_list(data: Any) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("orders", "data", "rows"):
            v = data.get(k)
            if isinstance(v, list):
                return v
    return []


async def get_open_algo_orders() -> list[dict]:
    raw = await _signed("GET", "/fapi/v1/openAlgoOrders", {"symbol": cfg.SYMBOL})
    return [o for o in _normalize_list(raw) if isinstance(o, dict)]


def _is_close_position_stop(o: dict) -> bool:
    ot = str(o.get("orderType") or o.get("type") or "").upper()
    if "STOP" not in ot or "TAKE_PROFIT" in ot or "TRAILING" in ot:
        return False
    cp = o.get("closePosition")
    return str(cp).lower() in ("true", "1") or cp is True


def _collect_close_sl_algo_ids(
    algos: list[dict], close_side: str | None = None
) -> list[str]:
    ids: list[str] = []
    for o in algos:
        if not _is_close_position_stop(o):
            continue
        side = str(o.get("side") or "").upper()
        if close_side and side != close_side.upper():
            continue
        aid = str(o.get("algoId") or "")
        if aid and aid not in ids:
            ids.append(aid)
    return ids


def _parse_algo_orders(algos: list[dict], side: str) -> tuple[float, float, float, bool]:
    """SL, TP1, TP2 fiyatları + TP1 dolmuş mu."""
    sl = tp1 = tp2 = 0.0
    tp1_hit = False
    tps: list[float] = []

    for o in algos:
        ot = str(o.get("orderType") or o.get("type") or "").upper()
        trig = float(o.get("triggerPrice") or o.get("stopPrice") or 0)
        if trig <= 0:
            continue
        if _is_close_position_stop(o):
            sl = trig
        elif "TAKE_PROFIT" in ot:
            tps.append(trig)

    reverse = side == "SHORT"
    tps.sort(reverse=reverse)
    if len(tps) >= 2:
        tp1, tp2 = tps[0], tps[1]
    elif len(tps) == 1:
        # Tek TP = bekleyen TP1 emri (TP1 dolmamis sayilir; tp1_hit miktar/DB'den)
        tp1 = tps[0]
    return sl, tp1, tp2, tp1_hit


def infer_tp1_hit_from_qty(ex_qty: float | None = None) -> bool:
    """Borsa miktari TP1 sonrasi runner (~qty_tp2) ise TP1 zaten alinmistir."""
    if state.pos_tp1_hit:
        return True
    if not state.in_position:
        return False
    try:
        from botlog.db import get_open_trade_flags

        flags = get_open_trade_flags()
        if flags.get("tp1_hit"):
            return True
    except Exception:
        pass
    qty = float(ex_qty if ex_qty is not None else (state.pos_qty or 0))
    q1 = float(state.pos_qty_tp1 or 0)
    q2 = float(state.pos_qty_tp2 or 0)
    if q2 >= 0.001 and qty > 0 and abs(qty - q2) < 0.0025:
        return True
    if q1 >= 0.001 and q2 >= 0.001 and qty > 0:
        total = q1 + q2
        if total >= 0.002 and qty <= total * 0.55 + 0.001:
            return True
    return False


async def sync_tp1_hit_state(ex_qty: float | None = None) -> bool:
    """TP1 durumunu borsa miktari + DB ile hizala."""
    if infer_tp1_hit_from_qty(ex_qty):
        if not state.pos_tp1_hit:
            log.info(
                f"TP1 zaten alinmis (qty={float(ex_qty or state.pos_qty):.4f} "
                f"runner={float(state.pos_qty_tp2 or 0):.4f})"
            )
        state.pos_tp1_hit = True
        try:
            from botlog.db import mark_open_trade_tp1_hit
            import execution.executor as ex

            mark_open_trade_tp1_hit(int(getattr(ex, "_trade_id", 0) or 0))
        except Exception:
            pass
        return True
    return bool(state.pos_tp1_hit)


async def repair_runner_after_tp1(*, sl: float = 0.0, reason: str = "runner repair") -> bool:
    """
    TP1 sonrasi: tum TP emirlerini kaldir, yalnizca SL (istege bagli seviye).
  """
    from execution.executor import get_position_qty

    if not state.in_position:
        log.warning("repair_runner_after_tp1: pozisyon yok")
        return False

    ex_qty = await get_position_qty()
    if ex_qty >= 0.001:
        state.pos_qty = round(ex_qty, 4)

    await sync_tp1_hit_state(ex_qty)
    if not state.pos_tp1_hit:
        log.warning("repair_runner_after_tp1: TP1 henuz alinmamis gorunuyor")
        return False

    close_side = "SELL" if state.pos_side == "LONG" else "BUY"
    await cancel_all_tp_algos(close_side)
    state.pos_tp1_id = ""
    state.pos_tp2_id = ""

    target_sl = float(sl or 0)
    if target_sl <= 0:
        from engine.position_sl import initial_trail_sl_at_tp1, _mark

        target_sl = initial_trail_sl_at_tp1(
            state.pos_side, state.pos_tp1, _mark() or state.pos_entry
        )
    if target_sl <= 0:
        log.warning("repair_runner_after_tp1: SL seviyesi hesaplanamadi")
        return False

    ok = await replace_sl_algo(target_sl, reason, force=True)
    if ok:
        state.pos_be_active = True
        state.pos_sl = target_sl
        pb = dict(state.position_breakout or {})
        pb["sl_stage"] = "trail_15m"
        state.position_breakout = pb
        log.info(f"Runner onarildi: TP yok, SL={target_sl:.2f}")
    return ok


async def sync_protection_ids_from_exchange(
    algos: list[dict] | None = None,
) -> None:
    """Borsadaki açık algo emirlerinden SL/TP id'lerini state'e yazar (restart)."""
    if not state.in_position:
        return
    if algos is None:
        algos = await get_open_algo_orders()
    close_side = "SELL" if state.pos_side == "LONG" else "BUY"
    sl_ids = _collect_close_sl_algo_ids(algos, close_side)
    if sl_ids:
        state.pos_sl_id = sl_ids[0]
    sl_px, _, _, _ = _parse_algo_orders(algos, state.pos_side)
    if sl_px > 0:
        state.pos_sl = sl_px


async def _place_algo(params: dict) -> dict:
    r = await _signed("POST", "/fapi/v1/algoOrder", params)
    if isinstance(r, dict) and int(r.get("code", 0) or 0) < 0:
        log.error(f"Algo emir hata: {r}")
        return {}
    return r if isinstance(r, dict) else {}


async def cancel_all_open_protection_orders(reason: str = "") -> int:
    """
    Pozisyon kapandıktan sonra (SL/TP/bot/ters sinyal) tüm açık koruma emirlerini sil.
    Algo SL/TP + klasik openOrders.
    """
    from core.config import is_paper_mode

    if is_paper_mode() or not cfg.API_KEY:
        state.pos_sl_id = ""
        state.pos_tp1_id = ""
        state.pos_tp2_id = ""
        return 0

    tag = reason or "position_closed"
    n_algo = 0
    algos = await get_open_algo_orders()
    seen: set[str] = set()
    for o in algos:
        aid = str(o.get("algoId") or "")
        if not aid or aid in seen:
            continue
        seen.add(aid)
        if await cancel_algo_order(aid):
            n_algo += 1

    for known in (
        str(state.pos_sl_id or ""),
        str(state.pos_tp1_id or ""),
        str(state.pos_tp2_id or ""),
    ):
        if known and known not in seen:
            seen.add(known)
            if await cancel_algo_order(known):
                n_algo += 1

    r = await _signed("DELETE", "/fapi/v1/allOpenOrders", {"symbol": cfg.SYMBOL})
    ord_ok = True
    if isinstance(r, dict) and int(r.get("code", 0) or 0) < 0:
        code = int(r.get("code", 0) or 0)
        if code not in (-2011, -2013):
            log.warning(f"Tüm açık emirler iptal: {r}")
            ord_ok = False

    state.pos_sl_id = ""
    state.pos_tp1_id = ""
    state.pos_tp2_id = ""

    if n_algo > 0 or ord_ok:
        log.info(
            f"Açık koruma emirleri silindi: {n_algo} algo  "
            f"+ allOpenOrders ({tag})"
        )
    return n_algo


async def cancel_algo_order(algo_id: str) -> bool:
    if not algo_id:
        return False
    r = await _signed(
        "DELETE",
        "/fapi/v1/algoOrder",
        {"symbol": cfg.SYMBOL, "algoId": algo_id},
    )
    if isinstance(r, dict):
        code = int(r.get("code", 0) or 0)
        if code < 0:
            # Zaten yok / iptal edilmiş — yeni SL için engel değil
            if code in (-2011, -2013, -2022):
                return True
            log.warning(f"Algo iptal: {r}")
            return False
    return True


async def cancel_all_close_sl_algos(close_side: str) -> int:
    """
    Binance: aynı yönde yalnızca bir closePosition STOP (GTE) — SL güncellemeden önce hepsini iptal et.
    """
    algos = await get_open_algo_orders()
    ids = _collect_close_sl_algo_ids(algos, close_side)
    known = str(state.pos_sl_id or "")
    if known and known not in ids:
        ids.append(known)
    n = 0
    for aid in ids:
        if await cancel_algo_order(aid):
            n += 1
    if n:
        log.info(f"Algo SL iptal: {n} emir (closePosition {close_side})")
    return n


def _collect_tp_algo_ids(algos: list[dict], close_side: str) -> list[str]:
    ids: list[str] = []
    for o in algos:
        ot = str(o.get("orderType") or o.get("type") or "").upper()
        if "TAKE_PROFIT" not in ot:
            continue
        if str(o.get("side") or "").upper() != close_side.upper():
            continue
        aid = str(o.get("algoId") or "")
        if aid and aid not in ids:
            ids.append(aid)
    return ids


async def cancel_all_tp_algos(close_side: str) -> int:
    algos = await get_open_algo_orders()
    ids = _collect_tp_algo_ids(algos, close_side)
    for known in (str(state.pos_tp1_id or ""), str(state.pos_tp2_id or "")):
        if known and known not in ids:
            ids.append(known)
    n = 0
    for aid in ids:
        if await cancel_algo_order(aid):
            n += 1
    if n:
        log.info(f"Algo TP iptal: {n} emir ({close_side})")
    return n


def _tp_tighter(side: str, new_tp1: float, old_tp1: float) -> bool:
    if old_tp1 <= 0 or new_tp1 <= 0:
        return False
    if side == "SHORT":
        return new_tp1 > old_tp1
    return new_tp1 < old_tp1


def _tp_yakinlastirma_warranted(
    side: str, entry: float, old_tp1: float, new_tp1: float
) -> bool:
    """
    TP1 yalnizca mevcut hedef yapisal olarak acikca cok uzaksa yaklastirilir.
    Lokal S/R hedefi (demand/supply, swing) korunur; break_level formulu daha kotu
    (giriste yakin) onerse mevcut TP1'e dokunulmaz.
    """
    pb = dict(state.position_breakout or {})
    if str(pb.get("entry_mode") or pb.get("strategy") or "") == "v3":
        return False
    try:
        from engine.structure_levels import v3_zone_tp_targets

        z1, _ = v3_zone_tp_targets(side, entry)
        if z1 > 0 and old_tp1 > 0 and abs(old_tp1 - z1) <= max(2.0, entry * 0.0015):
            return False
    except Exception:
        pass
    if not _tp_tighter(side, new_tp1, old_tp1):
        return False
    if entry <= 0 or old_tp1 <= 0 or new_tp1 <= 0:
        return False

    from engine.structure_thresholds import tp1_max_distance_bps

    max_bps = float(tp1_max_distance_bps(entry, entry) or 0)
    old_dist = abs(old_tp1 - entry) / entry * 10000.0
    new_dist = abs(new_tp1 - entry) / entry * 10000.0
    min_local = float(getattr(cfg, "BREAK_TP1_LOCAL_MIN_BPS", 60) or 60)
    overshoot = float(getattr(cfg, "TP_ADJUST_MIN_OVERSHOOT_BPS", 300) or 300)
    local_ceiling = max(max_bps * 2.0, 250.0)

    if old_dist <= local_ceiling:
        return False
    if old_dist < overshoot:
        return False
    if new_dist < min_local:
        return False
    return True


def _infer_damaged_entry_tp1(state: Any) -> float:
    """
    Startup yanlis TP1 yakinlastirmasini tespit edip giris anindaki lokal hedefi tahmin et.
    Imza: mevcut TP ~= break_level*(1-ext) ve giriste cok yakin.
    """
    side = (state.pos_side or "").upper()
    entry = float(state.pos_entry or 0)
    cur = float(state.pos_tp1 or 0)
    if entry <= 0 or cur <= 0:
        return 0.0

    pb = dict(state.position_breakout or {})
    bl = float(
        pb.get("break_level")
        or pb.get("entry_support")
        or pb.get("active_support")
        or 0
    )
    if bl <= 0:
        return 0.0

    ext = float(getattr(cfg, "BREAK_TP1_EXTENSION_BPS", 30) or 30) / 10000.0

    if side == "SHORT":
        bad_sig = round(bl * (1.0 - ext), 2)
        if abs(cur - bad_sig) > 1.0:
            return 0.0
        cur_dist_bps = (entry - cur) / entry * 10000.0
        if cur_dist_bps > 90:
            return 0.0
        try:
            from core.state import effective_price
            from engine.levels_v3 import get_levels_snapshot

            px = float(effective_price() or entry)
            snap = get_levels_snapshot(px) if px > 0 else {}
            layers = (
                snap.get("zone_layers")
                or getattr(state, "v3_zone_layers", None)
                or {}
            )
            for key in ("demand_weak", "demand_liq"):
                band = layers.get(key) or {}
                zhi = float(band.get("high") or band.get("zone_high") or 0)
                if zhi > 0 and zhi < entry:
                    return round(zhi, 2)
        except Exception:
            pass
        from engine.structure_levels import nearest_swing_below

        sw = nearest_swing_below(entry, state.swing_lows_15m or [])
        if sw > 0:
            return round(sw, 2)
        return 0.0

    bad_sig = round(bl * (1.0 + ext), 2)
    if abs(cur - bad_sig) > 1.0:
        return 0.0
    cur_dist_bps = (cur - entry) / entry * 10000.0
    if cur_dist_bps > 90:
        return 0.0
    try:
        from core.state import effective_price
        from engine.levels_v3 import get_levels_snapshot

        px = float(effective_price() or entry)
        snap = get_levels_snapshot(px) if px > 0 else {}
        layers = (
            snap.get("zone_layers")
            or getattr(state, "v3_zone_layers", None)
            or {}
        )
        for key in ("supply_weak", "supply_mid", "supply_major"):
            band = layers.get(key) or {}
            zlo = float(band.get("low") or band.get("zone_low") or 0)
            if zlo > entry:
                return round(zlo, 2)
    except Exception:
        pass
    from engine.structure_levels import nearest_swing_above

    sw = nearest_swing_above(entry, state.swing_highs_15m or [])
    if sw > 0:
        return round(sw, 2)
    return 0.0


async def maybe_restore_entry_tp1(
    *, force: bool = False, reason: str = "TP1 giris onarimi"
) -> None:
    """Startup yanlis TP1 yakinlastirmasini giris anindaki hedefe geri yukler."""
    from core.config import is_paper_mode

    if is_paper_mode() or not cfg.API_KEY:
        return
    if not state.in_position or state.pos_tp1_hit:
        return

    import execution.executor as ex
    from botlog.db import (
        get_trade_levels,
        mark_trade_tp1_restored,
        notes_tp1_restored,
        parse_tp1_original_from_notes,
        update_open_trade_tps,
        update_trade_entry_tp1_original,
    )

    trade_id = int(getattr(ex, "_trade_id", 0) or 0)
    lv = get_trade_levels(trade_id) or {}
    notes = str(lv.get("notes") or "")
    entry = float(state.pos_entry or 0)
    cur = float(state.pos_tp1 or 0)
    side = (state.pos_side or "").upper()
    if entry <= 0 or cur <= 0:
        return

    target = parse_tp1_original_from_notes(notes)
    inferred = False
    if target <= 0:
        if notes_tp1_restored(notes):
            return
        target = _infer_damaged_entry_tp1(state)
        inferred = target > 0
        if target <= 0:
            return

    algos = await get_open_algo_orders()
    _, ex_tp1, _, _ = _parse_algo_orders(algos, side)
    ref = float(ex_tp1 or cur or 0)

    if side == "SHORT":
        if target >= ref - 0.25 or target >= entry:
            return
    elif side == "LONG":
        if target <= ref + 0.25 or target <= entry:
            return
    else:
        return

    if abs(ref - target) < 0.5:
        if abs(cur - target) < 0.5:
            return
        state.pos_tp1 = target
        return

    close_side = "SELL" if side == "LONG" else "BUY"
    mark = await _current_mark()
    tp2 = float(state.pos_tp2 or 0)
    tp1_adj, tp2_adj = await resolve_tp_levels(side, entry, target, tp2, mark)
    if side == "SHORT" and (tp1_adj <= 0 or tp1_adj >= entry):
        log.warning(f"TP1 onarimi atlandi: SHORT ayarli={tp1_adj:.2f} giris={entry:.2f}")
        return
    if side == "LONG" and (tp1_adj <= 0 or tp1_adj <= entry):
        log.warning(f"TP1 onarimi atlandi: LONG ayarli={tp1_adj:.2f} giris={entry:.2f}")
        return

    await cancel_all_tp_algos(close_side)
    await asyncio.sleep(0.25)
    tp1_id = ""
    if state.pos_qty_tp1 >= 0.001 and tp1_adj > 0:
        tp1_id = await place_tp_algo(
            side, close_side, tp1_adj, state.pos_qty_tp1
        )
    if not tp1_id:
        log.warning("TP1 onarimi basarisiz — emir gonderilemedi")
        return

    state.pos_tp1 = tp1_adj
    state.pos_tp2 = tp2_adj
    state.pos_tp1_id = tp1_id
    state.pos_tp2_id = ""
    pb = dict(state.position_breakout or {})
    if pb:
        pb["tp1"] = float(tp1_adj)
        pb["tp2"] = float(tp2_adj)
        state.position_breakout = pb
    update_open_trade_tps(trade_id, tp1=tp1_adj, tp2=tp2_adj)
    update_trade_entry_tp1_original(trade_id, tp1_adj)
    if inferred:
        mark_trade_tp1_restored(trade_id)
    diff_bps = abs(tp1_adj - ref) / entry * 10000.0
    log.info(
        f"{reason}: TP1 {ref:.2f} → {tp1_adj:.2f}  "
        f"(giris hedefi, Δ{diff_bps:.0f}bps)"
    )


async def maybe_refresh_v3_channel_tp1(
    *, force: bool = False, reason: str = "V3 kanal TP1 guncelleme"
) -> None:
    """V3 range: TP1 kanal icinde degilse (demand/band alti) yakin hedefe tasi."""
    from core.config import is_paper_mode
    from engine.structure_levels import v3_zone_tp_targets

    if is_paper_mode() or not cfg.API_KEY:
        return
    if not state.in_position or state.pos_tp1_hit:
        return
    pb = dict(state.position_breakout or {})
    if str(pb.get("entry_mode") or pb.get("strategy") or "") != "v3":
        return

    side = (state.pos_side or "").upper()
    entry = float(state.pos_entry or 0)
    old_tp1 = float(state.pos_tp1 or 0)
    if entry <= 0 or old_tp1 <= 0:
        return

    new_tp1, new_tp2 = v3_zone_tp_targets(side, entry)
    if new_tp1 <= 0 or new_tp2 <= 0:
        return

    if side == "SHORT":
        if new_tp1 >= entry or new_tp1 <= old_tp1:
            return
        band_mid = float(pb.get("active_support") or 0)
        band_top = float(pb.get("active_resistance") or 0)
        if band_mid > 0 and band_top > band_mid:
            mid = band_mid + (band_top - band_mid) * 0.5
            if old_tp1 >= mid:
                return
    elif side == "LONG":
        if new_tp1 <= entry or new_tp1 <= old_tp1:
            return
        band_mid = float(pb.get("active_support") or 0)
        band_top = float(pb.get("active_resistance") or 0)
        if band_mid > 0 and band_top > band_mid:
            mid = band_mid + (band_top - band_mid) * 0.5
            if old_tp1 <= mid:
                return
    else:
        return

    close_side = "SELL" if side == "LONG" else "BUY"
    mark = await _current_mark()
    tp1_adj, tp2_adj = await resolve_tp_levels(side, entry, new_tp1, new_tp2, mark)
    if side == "SHORT" and (tp1_adj <= 0 or tp1_adj >= entry or tp1_adj <= old_tp1):
        return
    if side == "LONG" and (tp1_adj <= 0 or tp1_adj <= entry or tp1_adj >= old_tp1):
        return

    await cancel_all_tp_algos(close_side)
    await asyncio.sleep(0.25)
    tp1_id = ""
    if state.pos_qty_tp1 >= 0.001 and tp1_adj > 0:
        tp1_id = await place_tp_algo(side, close_side, tp1_adj, state.pos_qty_tp1)
    if not tp1_id:
        log.warning("V3 kanal TP1 guncelleme basarisiz — emir yok")
        return

    state.pos_tp1 = tp1_adj
    state.pos_tp2 = tp2_adj
    state.pos_tp1_id = tp1_id
    state.pos_tp2_id = ""
    if pb:
        pb["tp1"] = float(tp1_adj)
        pb["tp2"] = float(tp2_adj)
        state.position_breakout = pb
    try:
        import execution.executor as ex
        from botlog.db import update_open_trade_tps

        update_open_trade_tps(
            int(getattr(ex, "_trade_id", 0) or 0),
            tp1=tp1_adj,
            tp2=tp2_adj,
        )
    except Exception:
        pass
    diff_bps = abs(tp1_adj - old_tp1) / entry * 10000.0
    log.info(
        f"{reason}: TP1 {old_tp1:.2f} → {tp1_adj:.2f}  "
        f"TP2_ref={tp2_adj:.2f} (kanal icinde, Δ{diff_bps:.0f}bps)"
    )


async def maybe_adjust_open_tp(*, force: bool = False, reason: str = "TP yakınlaştırma") -> None:
    """Açık kırılım pozisyonunda TP1 çok uzaksa borsa TP'lerini yakınlaştır."""
    import time
    from core.config import is_paper_mode
    from engine.structure_levels import recalc_open_position_tps

    if is_paper_mode() or not cfg.API_KEY:
        return
    if not state.in_position or state.pos_tp1_hit:
        return
    pb = dict(state.position_breakout or {})
    if str(pb.get("entry_mode") or pb.get("strategy") or "") == "v3":
        if force:
            log.info(
                f"TP1 korundu: {float(state.pos_tp1 or 0):.2f} "
                f"(V3 katman hedefi — breakout yaklastirma atlandi)"
            )
        return
    last = float(state.pos_tp_manage_ts or 0)
    cd = float(getattr(cfg, "TP_ADJUST_COOLDOWN_SEC", 120))
    if not force and (time.time() - last) < cd:
        return

    new_tp1, new_tp2 = recalc_open_position_tps(state)
    old_tp1 = float(state.pos_tp1 or 0)
    entry = float(state.pos_entry or 0)
    if new_tp1 <= 0 or old_tp1 <= 0 or entry <= 0:
        return
    if not _tp_yakinlastirma_warranted(state.pos_side, entry, old_tp1, new_tp1):
        if force:
            log.info(
                f"TP1 korundu: {old_tp1:.2f} (lokal hedef, "
                f"recalc={new_tp1:.2f} atlandi)"
            )
        return
    diff_bps = abs(new_tp1 - old_tp1) / entry * 10000.0
    if not force and diff_bps < 25:
        return

    close_side = "SELL" if state.pos_side == "LONG" else "BUY"
    mark = await _current_mark()
    tp1_adj, tp2_adj = await resolve_tp_levels(
        state.pos_side, entry, new_tp1, new_tp2, mark
    )
    await cancel_all_tp_algos(close_side)
    await asyncio.sleep(0.25)
    tp1_id = ""
    tp2_id = ""
    if state.pos_qty_tp1 >= 0.001 and tp1_adj > 0:
        tp1_id = await place_tp_algo(
            state.pos_side, close_side, tp1_adj, state.pos_qty_tp1
        )
    # Runner TP2 ile kapatilmaz; kalan parca yalnizca trailing SL ile takip edilir.
    if not tp1_id:
        log.warning("TP yakınlaştırma başarısız — emir gönderilemedi")
        return

    state.pos_tp1 = tp1_adj
    state.pos_tp2 = tp2_adj
    state.pos_tp1_id = tp1_id
    state.pos_tp2_id = ""
    state.pos_tp_manage_ts = time.time()
    pb = dict(state.position_breakout or {})
    if pb:
        pb["tp1"] = float(tp1_adj)
        pb["tp2"] = float(tp2_adj)
        state.position_breakout = pb
    try:
        import execution.executor as ex
        from botlog.db import update_open_trade_tps

        update_open_trade_tps(
            int(getattr(ex, "_trade_id", 0) or 0),
            tp1=tp1_adj,
            tp2=tp2_adj,
        )
    except Exception:
        pass
    log.info(
        f"{reason}: TP1 {old_tp1:.2f} → {tp1_adj:.2f}  "
        f"runner_ref={tp2_adj:.2f}  (Δ{diff_bps:.0f}bps)"
    )


async def cancel_legacy_close_stop_orders(close_side: str) -> int:
    """Eski /fapi/v1/order STOP_MARKET closePosition (algo öncesi hesaplar)."""
    raw = await _signed("GET", "/fapi/v1/openOrders", {"symbol": cfg.SYMBOL})
    orders = [o for o in _normalize_list(raw) if isinstance(o, dict)]
    n = 0
    for o in orders:
        ot = str(o.get("type") or "").upper()
        if "STOP" not in ot or "TAKE_PROFIT" in ot:
            continue
        cp = o.get("closePosition")
        if str(cp).lower() not in ("true", "1") and cp is not True:
            continue
        if str(o.get("side") or "").upper() != close_side.upper():
            continue
        oid = o.get("orderId")
        if not oid:
            continue
        r = await _signed(
            "DELETE",
            "/fapi/v1/order",
            {"symbol": cfg.SYMBOL, "orderId": int(oid)},
        )
        if isinstance(r, dict) and int(r.get("code", 0) or 0) < 0:
            code = int(r.get("code", 0) or 0)
            if code not in (-2011, -2013):
                log.warning(f"Eski SL iptal: {r}")
                continue
        n += 1
    if n:
        log.info(f"Eski SL iptal: {n} emir ({close_side})")
    return n


def defer_runner_sl_to_15m() -> bool:
    """TP1 alindi — mevcut SL korunur; ilk sikilastirma 15m kapanisinda."""
    if not state.in_position:
        return False
    pb = dict(state.position_breakout or {})
    pb["sl_stage"] = "tp1_wait_15m"
    state.position_breakout = pb
    sl = float(state.pos_sl or 0)
    log.info(
        f"TP1 alindi — SL 15m onay bekleniyor "
        f"(mevcut SL={sl:.2f}, tp1={float(state.pos_tp1 or 0):.2f})"
    )
    return True


async def _apply_runner_sl_tighten(
    candidate: float,
    current_sl: float,
    side: str,
    reason: str,
) -> bool:
    from core.config import is_paper_mode
    from engine.position_sl import mark_sl_managed, _sl_tighter

    if candidate <= 0 or not _sl_tighter(side, candidate, current_sl):
        return False

    if is_paper_mode():
        from execution.paper import paper_replace_sl

        ok = await paper_replace_sl(candidate, reason)
    else:
        ok = await replace_sl_algo(candidate, reason)

    if ok:
        pb = dict(state.position_breakout or {})
        pb["sl_stage"] = "trail_15m"
        state.position_breakout = pb
        state.pos_be_active = True
        mark_sl_managed()
        log.info(
            f"Runner SL guncellendi ({reason}): "
            f"{current_sl:.2f} -> {candidate:.2f}"
        )
    return ok


def _is_5m_bucket_close_1m(candle: dict) -> bool:
    """1m mumu 5m periyodunun son dakikasinda kapandi mi."""
    ts = int(float(candle.get("ts", 0) or 0))
    return ts > 0 and ts % 300 == 240


async def apply_5m_runner_sl_confirm(candle_1m: dict) -> bool:
    """TP1 sonrasi: 15m onayindan sonra 5m kapanis onayi ile SL sikilastir."""
    from core.config import cfg
    from engine.position_sl import (
        trailing_sl_from_15m_close,
        tp1_break_confirmed,
        mark_sl_managed,
        sl_manage_cooldown_ok,
    )

    if not getattr(cfg, "TP1_CONFIRM_5M", True):
        return False
    if not state.in_position or not state.pos_tp1_hit:
        return False

    pb = dict(state.position_breakout or {})
    if str(pb.get("sl_stage", "")) != "tp1_wait_5m":
        return False
    if not _is_5m_bucket_close_1m(candle_1m):
        return False
    if not sl_manage_cooldown_ok():
        return False

    from engine.v3_common import aggregate_5m, bars_1m

    bars5 = aggregate_5m(bars_1m(8))
    if not bars5:
        return False

    last5 = bars5[-1]
    bucket_ts = int(float(last5.get("ts", 0) or 0))
    if pb.get("tp1_last_5m_ts") == bucket_ts:
        return False
    pb["tp1_last_5m_ts"] = bucket_ts

    close_5m = float(last5.get("close", 0) or 0)
    current_sl = float(state.pos_sl or 0)
    tp1 = float(state.pos_tp1 or 0)
    side = state.pos_side
    confirm_15m = float(pb.get("tp1_15m_confirm_close", 0) or 0)

    if close_5m <= 0:
        state.position_breakout = pb
        return False

    if not tp1_break_confirmed(side, close_5m, tp1):
        pb["sl_stage"] = "tp1_wait_15m"
        pb.pop("tp1_15m_confirm_close", None)
        state.position_breakout = pb
        mark_sl_managed()
        log.info(
            f"TP1 5m onay yok: close={close_5m:.2f} tp1={tp1:.2f} "
            f"(15m={confirm_15m:.2f}) — SL korundu, yeni 15m bekleniyor"
        )
        return False

    candidate = trailing_sl_from_15m_close(side, close_5m, current_sl)
    state.position_breakout = pb
    ok = await _apply_runner_sl_tighten(
        candidate,
        current_sl,
        side,
        "TP1 onayli 15m+5m trail",
    )
    if not ok:
        pb = dict(state.position_breakout or {})
        pb["sl_stage"] = "trail_15m"
        state.position_breakout = pb
        mark_sl_managed()
        log.info(
            f"TP1 15m+5m onayli ama SL sikilastirma yok: 5m={close_5m:.2f} "
            f"mevcut={current_sl:.2f}"
        )
    return ok


async def apply_15m_trailing_sl(close_15m: float) -> bool:
    """TP1 sonrası: onaylı 15m (+ isteğe bağlı 5m) kapanışında SL sıkılaştır."""
    from core.config import cfg, is_paper_mode
    from engine.position_sl import (
        trailing_sl_from_15m_close,
        tp1_15m_close_confirmed,
        mark_sl_managed,
        sl_manage_cooldown_ok,
        _sl_tighter,
    )

    if not state.in_position or not state.pos_tp1_hit or close_15m <= 0:
        return False
    if not sl_manage_cooldown_ok():
        return False

    stage = str((state.position_breakout or {}).get("sl_stage", ""))
    current_sl = float(state.pos_sl or 0)
    tp1 = float(state.pos_tp1 or 0)
    side = state.pos_side

    if stage == "tp1_wait_15m":
        if not tp1_15m_close_confirmed(side, close_15m, tp1):
            mark_sl_managed()
            log.info(
                f"TP1 sonrasi 15m onay yok: close={close_15m:.2f} "
                f"tp1={tp1:.2f} — SL korundu {current_sl:.2f}, sonraki 15m bekleniyor"
            )
            return False

        if getattr(cfg, "TP1_CONFIRM_5M", True):
            pb = dict(state.position_breakout or {})
            pb["sl_stage"] = "tp1_wait_5m"
            pb["tp1_15m_confirm_close"] = close_15m
            pb.pop("tp1_last_5m_ts", None)
            state.position_breakout = pb
            mark_sl_managed()
            log.info(
                f"TP1 15m onay: close={close_15m:.2f} tp1={tp1:.2f} "
                f"— SL korundu, 5m kapanis bekleniyor"
            )
            return False

        candidate = trailing_sl_from_15m_close(side, close_15m, current_sl)
        if candidate <= 0 or not _sl_tighter(side, candidate, current_sl):
            pb = dict(state.position_breakout or {})
            pb["sl_stage"] = "trail_15m"
            state.position_breakout = pb
            mark_sl_managed()
            log.info(
                f"TP1 onayli 15m ama SL sikilastirma yok: close={close_15m:.2f} "
                f"mevcut={current_sl:.2f}"
            )
            return False

        return await _apply_runner_sl_tighten(
            candidate,
            current_sl,
            side,
            "TP1 onayli 15m trail",
        )

    candidate = trailing_sl_from_15m_close(side, close_15m, current_sl)
    if candidate <= 0 or not _sl_tighter(side, candidate, current_sl):
        mark_sl_managed()
        return False

    if is_paper_mode():
        from execution.paper import paper_replace_sl

        ok = await paper_replace_sl(candidate, "15m trailing")
    else:
        ok = await replace_sl_algo(candidate, "15m trailing")

    if ok:
        pb = dict(state.position_breakout or {})
        pb["sl_stage"] = "trail_15m"
        state.position_breakout = pb
        state.pos_be_active = True
        mark_sl_managed()
    return ok


async def replace_sl_algo(
    new_sl: float, reason: str = "", *, force: bool = False
) -> bool:
    """Mevcut algo SL iptal + yeni seviye (varsayilan: yalnizca sikilastirma)."""
    if not state.in_position or new_sl <= 0:
        return False
    side = state.pos_side
    old = float(state.pos_sl or 0)
    from engine.position_sl import _sl_tighter, _sl_valid_trigger

    mark = float(state.mark_price or state.price or state.pos_entry or 0)
    if not force:
        if not _sl_valid_trigger(side, new_sl, mark):
            log.warning(f"SL güncelleme atlandı (tetik): {new_sl:.2f} mark={mark:.2f}")
            return False
        if old > 0 and (abs(new_sl - old) < 0.5 or not _sl_tighter(side, new_sl, old)):
            return False
        from engine.position_sl import sl_replace_allowed_vs_initial

        if not sl_replace_allowed_vs_initial(side, new_sl):
            return False
    elif old > 0 and abs(new_sl - old) < 0.5:
        return False

    close_side = "SELL" if side == "LONG" else "BUY"
    await sync_protection_ids_from_exchange()
    await cancel_all_close_sl_algos(close_side)
    await cancel_legacy_close_stop_orders(close_side)
    await asyncio.sleep(0.2)

    new_id = await place_sl_algo(close_side, new_sl)
    if not new_id:
        await cancel_all_close_sl_algos(close_side)
        await cancel_legacy_close_stop_orders(close_side)
        await asyncio.sleep(0.35)
        new_id = await place_sl_algo(close_side, new_sl)
    if new_id:
        state.pos_sl = new_sl
        state.pos_sl_id = new_id
        try:
            import execution.executor as ex
            from botlog.db import update_open_trade_sl

            update_open_trade_sl(int(getattr(ex, "_trade_id", 0) or 0), new_sl)
        except Exception:
            pass
        tag = reason or "güncelleme"
        log.info(f"SL sıkılaştırıldı ({tag}): {old:.2f} → {new_sl:.2f}")
        return True
    return False


async def manage_position_sl(*, force: bool = False) -> None:
    """Yapısal kâr kilidi (TP1 öncesi). TP1 sonrası SL: 15m trail."""
    from core.config import is_paper_mode
    from engine.position_sl import (
        structural_sl_lock_price,
        in_profit_min_bps,
        sl_manage_cooldown_ok,
        mark_sl_managed,
        sl_lock_reason_tag,
        resolve_sl_profile,
        _sl_tighter,
        _mark,
    )

    if is_paper_mode() or not state.in_position:
        return
    if not force and not sl_manage_cooldown_ok():
        return

    side = state.pos_side
    entry = float(state.pos_entry or 0)
    mark = _mark()
    if entry <= 0 or mark <= 0:
        return

    stage = str((state.position_breakout or {}).get("sl_stage", ""))

    if state.pos_tp1_hit or stage in ("runner", "trail_15m", "tp1_wait_15m", "tp1_wait_5m"):
        # TP1 sonrasi SL: 15m (+5m) onayinda; sonraki 15m trail.
        mark_sl_managed()
        return

    from engine.position_sl import pre_tp1_structural_lock_enabled

    if not state.pos_tp1_hit and not pre_tp1_structural_lock_enabled():
        return

    # ── Breakeven SL kontrolü ─────────────────────────────────────────────────
    from engine.position_sl import breakeven_sl_triggered, breakeven_sl_price

    if breakeven_sl_triggered(side, entry, mark):
        be_sl = breakeven_sl_price(side, entry)
        old_sl = float(state.pos_sl or 0)
        if be_sl > 0 and _sl_tighter(side, be_sl, old_sl):
            log.info(
                f"[BREAKEVEN] SL entry'ye çekildi: {old_sl:.2f} → {be_sl:.2f} "
                f"(entry={entry:.2f} mark={mark:.2f})"
            )
            if await replace_sl_algo(be_sl, "breakeven"):
                pb = dict(state.position_breakout or {})
                pb["sl_stage"] = "breakeven"
                state.position_breakout = pb
                mark_sl_managed()
            return
    # ─────────────────────────────────────────────────────────────────────────

    if not in_profit_min_bps(side, entry, mark):
        log.debug(
            f"SL yönetimi atlandı: min kâr yok "
            f"(entry={entry:.2f} mark={mark:.2f})"
        )
        return

    profile = resolve_sl_profile(side, entry, mark)
    new_sl = structural_sl_lock_price(side, entry, mark)
    old_sl = float(state.pos_sl or 0)
    if new_sl > 0 and old_sl > 0:
        diff_bps = abs(new_sl - old_sl) / entry * 10000.0 if entry > 0 else 0
        if abs(new_sl - old_sl) < 0.5:
            log.debug(
                f"SL aynı ({profile}): {old_sl:.2f} — borsa güncellemesi yok"
            )
            mark_sl_managed()
            return
        if not _sl_tighter(side, new_sl, old_sl):
            log.info(
                f"SL sıkılaştırma yok ({profile}): hedef={new_sl:.2f} "
                f"mevcut={old_sl:.2f} (Δ{diff_bps:.0f}bps)"
            )
            mark_sl_managed()
            return

    reason = sl_lock_reason_tag(side, entry, mark)
    log.info(
        f"SL güncelleniyor [{profile}]: {old_sl:.2f} -> {new_sl:.2f} "
        f"({reason})"
    )
    if new_sl > 0 and await replace_sl_algo(new_sl, reason):
        pb = dict(state.position_breakout or {})
        pb["sl_stage"] = "structural"
        state.position_breakout = pb
        mark_sl_managed()

    try:
        await maybe_adjust_open_tp()
    except Exception as e:
        log.debug(f"TP yakınlaştırma: {e}")


async def place_sl_algo(close_side: str, sl_price: float) -> str:
    r = await _place_algo(
        {
            "algoType": "CONDITIONAL",
            "symbol": cfg.SYMBOL,
            "side": close_side,
            "type": "STOP_MARKET",
            "triggerPrice": await format_price(sl_price),
            "closePosition": "true",
            "workingType": "MARK_PRICE",
            "priceProtect": "TRUE",
        }
    )
    aid = str(r.get("algoId", r.get("orderId", "")))
    if aid:
        log.info(f"Algo SL @ {sl_price:.2f}  algoId={aid}")
    return aid


async def place_tp_algo(
    direction: str, close_side: str, tp_price: float, qty: float
) -> str:
    q = await format_qty(qty)
    if float(q) <= 0:
        return ""
    params = {
        "algoType": "CONDITIONAL",
        "symbol": cfg.SYMBOL,
        "side": close_side,
        "type": "TAKE_PROFIT_MARKET",
        "triggerPrice": await format_price(tp_price),
        "quantity": q,
        "reduceOnly": "true",
        "workingType": "MARK_PRICE",
        "priceProtect": "TRUE",
    }
    r = await _place_algo(params)
    code = int(r.get("code", 0) or 0) if isinstance(r, dict) else 0
    if code == -2021:
        mark = await _current_mark()
        entry = state.pos_entry or 0.0
        adj, _ = await resolve_tp_levels(
            direction, entry, tp_price, 0.0, mark
        )
        extra = float(cfg.PROTECTION_TP_MIN_BPS) + 15.0
        if direction == "SHORT" and mark > 0:
            adj = await round_price_float(mark * (1.0 - extra / 10000.0))
        elif direction == "LONG" and mark > 0:
            adj = await round_price_float(mark * (1.0 + extra / 10000.0))
        if adj > 0 and abs(adj - tp_price) > 0:
            log.warning(f"TP -2021 yeniden deneme: {tp_price:.2f} → {adj:.2f}")
            params["triggerPrice"] = await format_price(adj)
            r = await _place_algo(params)
            tp_price = adj
    aid = str(r.get("algoId", r.get("orderId", ""))) if isinstance(r, dict) else ""
    if aid and not (isinstance(r, dict) and int(r.get("code", 0) or 0) < 0):
        log.info(f"Algo TP @ {tp_price:.2f}  qty={q}  algoId={aid}")
        return aid
    if isinstance(r, dict) and int(r.get("code", 0) or 0) < 0:
        log.error(f"Algo TP başarısız @ {tp_price:.2f}: {r}")
    return ""


async def place_position_protection(
    direction: str,
    qty_total: float,
    qty_tp1: float,
    qty_tp2: float,
    sl: float,
    tp1: float,
    tp2: float,
    entry: float = 0.0,
) -> tuple[str, str, str, float, float]:
    """Giriş sonrası yalnızca SL + TP1 gönderilir; TP2 runner referansıdır."""
    close_side = "SELL" if direction == "LONG" else "BUY"
    sl_id = await place_sl_algo(close_side, sl) if sl > 0 else ""
    mark = await _current_mark()
    ent = entry or state.pos_entry or 0.0
    tp1_adj, tp2_adj = await resolve_tp_levels(direction, ent, tp1, tp2, mark)
    tp1_id = ""
    tp2_id = ""
    if qty_tp1 >= 0.001 and tp1_adj > 0:
        tp1_id = await place_tp_algo(direction, close_side, tp1_adj, qty_tp1)
    return sl_id, tp1_id, tp2_id, tp1_adj, tp2_adj


async def _fill_missing_levels() -> None:
    """Restart sonrası SL/TP state boşsa yapıdan hesapla."""
    if state.pos_entry <= 0 or not state.pos_side:
        return
    from engine.structure_levels import calc_trade_levels, recalc_open_position_tps

    pb = state.position_breakout or {}
    if state.pos_sl > 0 and state.pos_tp1 > 0:
        if pb.get("break_mode") or pb.get("break_level"):
            n1, n2 = recalc_open_position_tps(state)
            if n1 > 0 and _tp_yakinlastirma_warranted(
                state.pos_side, float(state.pos_entry or 0), state.pos_tp1, n1
            ):
                state.pos_tp1 = n1
                if n2 > 0:
                    state.pos_tp2 = n2
        return

    if pb.get("break_mode") or pb.get("break_level"):
        from engine.structure_levels import calc_break_levels

        bl = float(pb.get("break_level") or 0)
        s = float(pb.get("range_support") or pb.get("active_support") or 0)
        r = float(pb.get("range_resistance") or pb.get("active_resistance") or 0)
        _, sl, tp1, tp2 = calc_break_levels(
            state.pos_side, state.pos_entry, bl, s, r, state
        )
    else:
        _, sl, tp1, tp2 = calc_trade_levels(
            state.pos_side, state.pos_entry, state
        )
    if sl > 0:
        state.pos_sl = sl
        state.pos_sl_initial = sl
    if tp1 > 0:
        state.pos_tp1 = tp1
    if tp2 > 0:
        state.pos_tp2 = tp2
    if state.pos_qty_tp1 <= 0 and state.pos_qty > 0:
        state.pos_qty_tp1 = round(state.pos_qty * cfg.TP1_PCT, 4)
        state.pos_qty_tp2 = round(max(state.pos_qty - state.pos_qty_tp1, 0), 4)


async def ensure_protection_orders() -> bool:
    """
    Açık pozisyon var ama algo SL/TP yoksa gönder (restart / eksik koruma).
    """
    global _ensure_busy
    if _ensure_busy:
        return False
    if not state.in_position or state.pos_qty < 0.001:
        return False
    _ensure_busy = True
    try:
        return await _ensure_protection_orders_impl()
    finally:
        _ensure_busy = False


async def _ensure_protection_orders_impl() -> bool:
    from execution.executor import get_position_qty

    await _fill_missing_levels()
    if state.pos_sl <= 0 and state.pos_tp1 <= 0:
        log.warning("Koruma emri yok — SL/TP hesaplanamadı")
        return False
    target_state_sl = float(state.pos_sl or 0)

    ex_qty = await get_position_qty()
    if ex_qty >= 0.001:
        state.pos_qty = round(ex_qty, 4)
    await sync_tp1_hit_state(ex_qty)

    await sync_protection_ids_from_exchange()
    algos = await get_open_algo_orders()
    sl, tp1, tp2, _ = _parse_algo_orders(algos, state.pos_side)
    close_side = "SELL" if state.pos_side == "LONG" else "BUY"

    if tp2 > 0 and not bool(getattr(cfg, "SEND_TP2_ORDER", False)):
        log.warning("TP2 emri bulundu — runner SL kurgusu için TP emirleri yenileniyor")
        await cancel_all_tp_algos(close_side)
        state.pos_tp1_id = ""
        state.pos_tp2_id = ""
        tp1 = 0.0
        tp2 = 0.0

    if state.pos_tp1_hit:
        if tp1 > 0 or tp2 > 0:
            log.warning("TP1 alinmis — borsadaki TP emirleri iptal ediliyor")
            await cancel_all_tp_algos(close_side)
            state.pos_tp1_id = ""
            state.pos_tp2_id = ""
        has_sl = sl > 0 or bool(state.pos_sl_id)
        if sl > 0 and target_state_sl > 0 and abs(sl - target_state_sl) >= 0.5:
            log.warning(
                f"Exchange SL state ile uyumsuz — restore replace: "
                f"exchange={sl:.2f} hedef={target_state_sl:.2f}"
            )
            await replace_sl_algo(target_state_sl, "restore SL sync", force=True)
            return bool(state.pos_sl_id)
        if not has_sl and state.pos_sl > 0:
            log.warning("TP1 sonrasi SL eksik — yeniden gonderiliyor")
            state.pos_sl_id = await place_sl_algo(close_side, target_state_sl or state.pos_sl)
        return bool(state.pos_sl_id or sl > 0)

    has_sl = sl > 0
    has_tp = tp1 > 0
    if has_sl and target_state_sl > 0 and abs(sl - target_state_sl) >= 0.5:
        log.warning(
            f"Exchange SL state ile uyumsuz — restore replace: "
            f"exchange={sl:.2f} hedef={target_state_sl:.2f}"
        )
        await replace_sl_algo(target_state_sl, "restore SL sync", force=True)
        return bool(state.pos_sl_id)
    if has_sl and (has_tp or state.pos_tp1 <= 0):
        return True

    log.warning(
        f"Koruma emirleri eksik (algo SL={has_sl} TP={has_tp}) — yeniden gönderiliyor"
    )
    if not has_sl and state.pos_sl > 0:
        state.pos_sl_id = await place_sl_algo(close_side, target_state_sl or state.pos_sl)
    if not has_tp and state.pos_tp1 > 0 and not state.pos_tp1_hit:
        mark = await _current_mark()
        tp1_adj, tp2_adj = await resolve_tp_levels(
            state.pos_side,
            state.pos_entry,
            state.pos_tp1,
            state.pos_tp2,
            mark,
        )
        state.pos_tp1 = tp1_adj
        state.pos_tp2 = tp2_adj
        state.pos_tp1_id = await place_tp_algo(
            state.pos_side, close_side, tp1_adj, state.pos_qty_tp1
        )
        state.pos_tp2_id = ""
    return bool(state.pos_sl_id or state.pos_tp1_id)
