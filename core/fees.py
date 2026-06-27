"""
core/fees.py — Gercek (NET) PnL hesabi: brut fiyat farki - round-trip taker ucreti.

Binance USDT-M futures'ta her market emri taker ucreti oder (giris + cikis = 2 bacak).
Bot eskiden brut pnl ((exit-entry)*qty) kaydediyordu; bu Binance'in NET realized pnl'inden
~her bacak notional*fee_rate kadar fazla gosteriyordu. Bu modul tek otorite.
"""
from __future__ import annotations


def round_trip_fee(entry: float, exit_px: float, qty: float, fee_rate: float | None = None) -> float:
    """Giris + cikis taker ucreti (USDT). entry/exit notional * fee_rate toplami."""
    if fee_rate is None:
        from core.config import cfg
        fee_rate = float(getattr(cfg, "FEE_RATE_TAKER", 0.0005))
    if entry <= 0 or qty <= 0:
        return 0.0
    entry_notional = entry * qty
    exit_notional = (exit_px if exit_px > 0 else entry) * qty
    return (entry_notional + exit_notional) * fee_rate


def net_pnl(entry: float, exit_px: float, qty: float, side: str,
            fee_rate: float | None = None) -> float:
    """NET realized PnL (USDT) = brut fiyat farki - round-trip taker ucreti."""
    if entry <= 0 or qty <= 0 or exit_px <= 0:
        return 0.0
    sign = 1.0 if str(side).upper() == "LONG" else -1.0
    gross = (exit_px - entry) * qty * sign
    return round(gross - round_trip_fee(entry, exit_px, qty, fee_rate), 4)
