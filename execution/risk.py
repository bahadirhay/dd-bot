"""
execution/risk.py — Pozisyon boyutu + isolated margin hesabı
Leverage: 5x sabit. Margin: ISOLATED sabit.
"""
from dataclasses import dataclass
from core.config import cfg
from core.state  import state
from core.logger import get_logger

log = get_logger("Risk")

@dataclass
class Plan:
    direction  : str
    entry      : float
    sl         : float
    tp1        : float
    tp2        : float
    qty_total  : float
    qty_tp1    : float
    qty_tp2    : float
    risk_usdt  : float
    notional   : float
    margin_req : float
    liq_price  : float
    rr_tp1     : float
    rr_tp2     : float
    warnings   : list

    def ok(self) -> bool:
        fatal = [w for w in self.warnings if w.startswith("HATA")]
        return self.qty_total >= 0.001 and not fatal

    def log_str(self) -> str:
        sl_dist = abs(self.entry - self.sl)
        tp1_dist= abs(self.tp1 - self.entry)
        tp2_dist= abs(self.tp2 - self.entry)
        lines = [
            f"  Yön     : {self.direction}",
            f"  Giriş   : {self.entry:.2f}",
            f"  SL      : {self.sl:.2f}  ({sl_dist:.2f} USDT uzak)",
            f"  TP1     : {self.tp1:.2f}  R:R=1:{self.rr_tp1:.2f}  "
            f"({self.qty_tp1:.4f} ETH, %{cfg.TP1_PCT*100:.0f})",
            f"  TP2     : {self.tp2:.2f}  R:R=1:{self.rr_tp2:.2f}  "
            f"(kalan {self.qty_tp2:.4f} ETH)",
            f"  Miktar  : {self.qty_total:.4f} ETH",
            f"  Risk    : {self.risk_usdt:.2f} USDT",
            f"  Pozisyon değeri: {self.notional:.2f} USDT  (hedef marjin {self.margin_req:.2f} USDT)",
            f"  Liq     : {self.liq_price:.2f}  ({cfg.MARGIN} {cfg.LEVERAGE}x)",
        ]
        if self.warnings:
            lines.append(f"  ⚠ {' | '.join(self.warnings)}")
        return "\n".join(lines)


def calculate(
    direction: str,
    sl: float,
    tp1: float,
    tp2: float,
    balance: float,
    entry_price: float | None = None,
    min_rr: float | None = None,
) -> Plan:
    warnings = []
    if entry_price and entry_price > 0:
        entry = float(entry_price)
    else:
        entry = state.ask if direction == "LONG" else state.bid
        if entry <= 0:
            entry = state.mark_price
    if entry <= 0:
        warnings.append("HATA: fiyat sıfır")
        return _empty(direction, sl, tp1, tp2, warnings)

    sl_dist  = abs(entry - sl)
    tp1_dist = abs(tp1 - entry)
    tp2_dist = abs(tp2 - entry)

    if sl_dist < 0.01:
        warnings.append("HATA: SL çok yakın")
        return _empty(direction, sl, tp1, tp2, warnings)

    # R:R
    rr_tp1 = round(tp1_dist / sl_dist, 2)
    rr_tp2 = round(tp2_dist / sl_dist, 2)

    rr_min = float(min_rr) if min_rr is not None else float(cfg.MIN_RR)
    if rr_tp1 < rr_min:
        warnings.append(f"HATA: R:R={rr_tp1:.2f} < min {rr_min}")
        return _empty(direction, sl, tp1, tp2, warnings)

    max_margin = balance * (float(cfg.MAX_MARGIN_PCT) / 100.0)
    trade_margin = float(getattr(cfg, "TRADE_MARGIN_USD", 0) or 0)

    if trade_margin > 0:
        # Sabit marjin modu (varsayılan 10 USDT @ 5x → ~50 USDT notional)
        margin_req = min(trade_margin, max_margin) if max_margin > 0 else trade_margin
        if margin_req < trade_margin:
            warnings.append(
                f"UYARI: marjin {trade_margin:.0f}→{margin_req:.2f} USDT "
                f"(max %{cfg.MAX_MARGIN_PCT:.0f} equity)"
            )
        notional = round(margin_req * cfg.LEVERAGE, 2)
        qty_total = round(notional / entry, 3)
        notional = round(qty_total * entry, 2)
        risk_usdt = round(qty_total * sl_dist, 2)
    else:
        # Eski: SL mesafesine göre risk %
        risk_usdt = balance * (cfg.RISK_PCT / 100)
        qty_total = round(risk_usdt / sl_dist, 3)
        notional = round(qty_total * entry, 2)
        margin_req = round(notional / cfg.LEVERAGE, 2)
        if margin_req > max_margin and max_margin > 0:
            scale = (max_margin * cfg.LEVERAGE) / notional
            qty_total = round(qty_total * scale, 3)
            notional = round(qty_total * entry, 2)
            margin_req = round(notional / cfg.LEVERAGE, 2)
            risk_usdt = round(qty_total * sl_dist, 2)
            warnings.append("UYARI: miktar marjin sınırına göre küçültüldü")

    if qty_total < 0.001:
        warnings.append(f"HATA: qty={qty_total:.4f} < 0.001 ETH minimum")
        return _empty(direction, sl, tp1, tp2, warnings)

    qty_tp1 = round(qty_total * cfg.TP1_PCT, 3)
    qty_tp2 = round(qty_total - qty_tp1, 3)
    if qty_tp1 < 0.001:
        qty_tp1 = qty_total
        qty_tp2 = 0.0
        warnings.append("UYARI: TP1 miktarı çok küçük, tümü TP1'de kapatılacak")

    # Liq fiyatı (isolated)
    maint = 0.004
    if direction == "LONG":
        liq = round(entry * (1 - 1/cfg.LEVERAGE + maint), 2)
    else:
        liq = round(entry * (1 + 1/cfg.LEVERAGE - maint), 2)

    # SL liq'a çok yakın mı?
    if direction == "LONG" and sl <= liq * 1.01:
        warnings.append(f"UYARI: SL={sl} liq={liq}'a çok yakın!")
    if direction == "SHORT" and sl >= liq * 0.99:
        warnings.append(f"UYARI: SL={sl} liq={liq}'a çok yakın!")

    plan = Plan(
        direction=direction, entry=round(entry,2),
        sl=sl, tp1=tp1, tp2=tp2,
        qty_total=qty_total, qty_tp1=qty_tp1, qty_tp2=qty_tp2,
        risk_usdt=round(risk_usdt,2), notional=notional,
        margin_req=margin_req, liq_price=liq,
        rr_tp1=rr_tp1, rr_tp2=rr_tp2, warnings=warnings,
    )
    log.info(f"\n{'─'*48}\n  Risk Planı:\n{plan.log_str()}\n{'─'*48}")
    return plan


def _empty(d, sl, tp1, tp2, w):
    return Plan(d, 0, sl, tp1, tp2, 0, 0, 0, 0, 0, 0, 0, 0, 0, w)
