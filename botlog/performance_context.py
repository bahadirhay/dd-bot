"""
botlog/performance_context.py
─────────────────────────────
Startup'ta bot.db'den son trade geçmişini okur ve
cfg / state'e adaptif parametreler yazar.

Kullanım (main.py -> _main_loop içinde setup_api'den sonra):
    from botlog.performance_context import load_performance_context
    await load_performance_context()
"""

from __future__ import annotations

import sqlite3
import os
import time
from dataclasses import dataclass, field
from typing import Optional

from core.logger import get_logger

log = get_logger("PerfCtx")

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "bot.db")

# ─── Veri sınıfları ────────────────────────────────────────────────────────────

@dataclass
class TradeRecord:
    id: int
    ts: float
    side: str            # LONG / SHORT
    entry_mode: str      # range / break / hybrid / v3
    entry_price: float
    exit_price: float
    pnl_pct: float       # % — pozitif=kazanç, negatif=kayıp
    tp1_hit: int         # 0/1
    be_activated: int    # 0/1
    structure_1h: str    # BULLISH / BEARISH / UNCLEAR
    structure_15m: str


@dataclass
class PerformanceContext:
    """Hesaplanan özet metrikler — state'e yazılır."""
    total_trades: int = 0
    win_rate: float = 0.5
    avg_pnl_pct: float = 0.0
    daily_pnl_pct: float = 0.0      # bugünkü PnL (açık olmayan)

    # Mod bazlı win rate
    range_win_rate: Optional[float] = None
    break_win_rate: Optional[float] = None

    # Yapı bazlı win rate
    unclear_win_rate: Optional[float] = None

    # Adaptif kararlar
    suggested_risk_multiplier: float = 1.0   # 0.5 – 1.0
    suggested_entry_mode: Optional[str] = None
    disable_range: bool = False
    disable_break: bool = False

    warnings: list[str] = field(default_factory=list)


# ─── Yardımcı fonksiyonlar ─────────────────────────────────────────────────────

def _winrate(trades: list[TradeRecord]) -> Optional[float]:
    if not trades:
        return None
    wins = sum(1 for t in trades if t.pnl_pct > 0)
    return wins / len(trades)


def _avg_pnl(trades: list[TradeRecord]) -> float:
    if not trades:
        return 0.0
    return sum(t.pnl_pct for t in trades) / len(trades)


def _today_pnl(trades: list[TradeRecord]) -> float:
    """Bugünün UTC başından itibaren kapalı trade PnL'i."""
    from datetime import datetime, timezone
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).timestamp()
    today_trades = [t for t in trades if t.ts >= today_start]
    return sum(t.pnl_pct for t in today_trades)


# ─── Ana fonksiyon ─────────────────────────────────────────────────────────────

async def load_performance_context(limit: int = 30) -> PerformanceContext:
    """
    bot.db'den son `limit` kapalı trade'i okur, PerformanceContext üretir
    ve sonuçları state + cfg'ye yazar.

    Tamamen non-blocking: DB hatası -> varsayılan context döner, bot durmuyor.
    """
    ctx = PerformanceContext()

    # ── 1. DB oku ──────────────────────────────────────────────────────────────
    trades: list[TradeRecord] = []
    try:
        if not os.path.exists(DB_PATH):
            log.info("PerfCtx: bot.db bulunamadı — varsayılan parametreler kullanılacak")
            return ctx

        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # trades tablosunun sütunlarını keşfet (farklı şema versiyonlarına toleranslı)
        cur.execute("PRAGMA table_info(trades)")
        cols = {row["name"] for row in cur.fetchall()}

        # Sema toleransi: gercek tabloda side->direction, ts->close_ts/open_ts,
        # entry_mode/structure_* yok. Var olan sutunlara dinamik esle.
        side_col = "side" if "side" in cols else ("direction" if "direction" in cols else None)
        if "pnl_pct" not in cols or side_col is None:
            log.warning("PerfCtx: trades tablosu beklenen sütunları içermiyor — atlanıyor")
            conn.close()
            return ctx

        def _expr(real: str, alias: str, default: str) -> str:
            return f"{real} AS {alias}" if real in cols else f"{default} AS {alias}"

        ts_expr = (
            "ts AS ts" if "ts" in cols
            else "COALESCE(close_ts, open_ts, 0) AS ts"
        )
        select_cols = ", ".join([
            "id",
            ts_expr,
            f"{side_col} AS side",
            _expr("entry_mode", "entry_mode", "'unknown'"),
            "COALESCE(entry_price,0) AS entry_price",
            "COALESCE(exit_price,0) AS exit_price" if "exit_price" in cols else "0 AS exit_price",
            "COALESCE(pnl_pct,0) AS pnl_pct",
            "COALESCE(tp1_hit,0) AS tp1_hit",
            "COALESCE(be_activated,0) AS be_activated",
            _expr("structure_1h", "structure_1h", "'UNCLEAR'"),
            _expr("structure_15m", "structure_15m", "'UNCLEAR'"),
        ])

        exit_col = "exit_price" if "exit_price" in cols else None
        where = f"WHERE {exit_col} IS NOT NULL AND {exit_col} > 0" if exit_col else ""
        order_ts = "ts" if "ts" in cols else "COALESCE(close_ts, open_ts, 0)"
        cur.execute(
            f"SELECT {select_cols} FROM trades {where} "
            f"ORDER BY {order_ts} DESC LIMIT ?",
            (limit,),
        )

        rows = cur.fetchall()
        conn.close()

        trades = [
            TradeRecord(
                id=r["id"], ts=r["ts"], side=r["side"],
                entry_mode=r["entry_mode"],
                entry_price=r["entry_price"], exit_price=r["exit_price"],
                pnl_pct=r["pnl_pct"],
                tp1_hit=r["tp1_hit"], be_activated=r["be_activated"],
                structure_1h=r["structure_1h"], structure_15m=r["structure_15m"],
            )
            for r in rows
        ]
        log.info(f"PerfCtx: {len(trades)} kapalı trade yüklendi")

    except Exception as e:
        log.warning(f"PerfCtx: DB okuma hatası ({e}) — varsayılan parametreler")
        return ctx

    if not trades:
        log.info("PerfCtx: henüz kapalı trade yok — varsayılan parametreler")
        return ctx

    # ── 2. Metrik hesapla ──────────────────────────────────────────────────────
    ctx.total_trades = len(trades)
    ctx.win_rate = _winrate(trades) or 0.5
    ctx.avg_pnl_pct = _avg_pnl(trades)
    ctx.daily_pnl_pct = _today_pnl(trades)

    range_trades = [t for t in trades if "range" in t.entry_mode.lower()]
    break_trades  = [t for t in trades if "break" in t.entry_mode.lower()]
    unclear_trades = [t for t in trades if t.structure_1h == "UNCLEAR"]

    ctx.range_win_rate   = _winrate(range_trades)
    ctx.break_win_rate   = _winrate(break_trades)
    ctx.unclear_win_rate = _winrate(unclear_trades)

    # ── 3. Adaptif kararlar ────────────────────────────────────────────────────

    # Genel win rate düşükse risk azalt
    if ctx.win_rate < 0.35 and ctx.total_trades >= 8:
        ctx.suggested_risk_multiplier = 0.5
        ctx.warnings.append(
            f"Genel win rate düşük ({ctx.win_rate:.0%}, n={ctx.total_trades}) "
            f"-> risk çarpanı 0.5x"
        )
    elif ctx.win_rate < 0.45 and ctx.total_trades >= 8:
        ctx.suggested_risk_multiplier = 0.75
        ctx.warnings.append(
            f"Win rate zayıf ({ctx.win_rate:.0%}) -> risk çarpanı 0.75x"
        )

    # Range modu sürekli kaybediyorsa devre dışı bırak
    MIN_SAMPLE = 6
    if ctx.range_win_rate is not None and len(range_trades) >= MIN_SAMPLE:
        if ctx.range_win_rate < 0.35:
            ctx.disable_range = True
            ctx.warnings.append(
                f"Range WR={ctx.range_win_rate:.0%} (n={len(range_trades)}) "
                f"< 35% -> range modu devre dışı"
            )

    # Break modu sürekli kaybediyorsa
    if ctx.break_win_rate is not None and len(break_trades) >= MIN_SAMPLE:
        if ctx.break_win_rate < 0.35:
            ctx.disable_break = True
            ctx.warnings.append(
                f"Break WR={ctx.break_win_rate:.0%} (n={len(break_trades)}) "
                f"< 35% -> break modu devre dışı"
            )

    # Günlük PnL zaten kötüyse ek uyarı
    if ctx.daily_pnl_pct <= -2.5:
        ctx.warnings.append(
            f"Bugünkü PnL: {ctx.daily_pnl_pct:.2f}% — günlük limite yakın"
        )

    # ── 4. State + cfg'ye uygula ──────────────────────────────────────────────
    _apply_to_state_and_cfg(ctx)

    # ── 5. Özet log ───────────────────────────────────────────────────────────
    log.info(
        f"PerfCtx özet: n={ctx.total_trades} wr={ctx.win_rate:.0%} "
        f"avg_pnl={ctx.avg_pnl_pct:.2f}% risk_mult={ctx.suggested_risk_multiplier}"
    )
    for w in ctx.warnings:
        log.warning(f"PerfCtx [!] {w}")

    return ctx


def _apply_to_state_and_cfg(ctx: PerformanceContext) -> None:
    """
    Hesaplanan context'i mevcut cfg ve state'e yazar.
    Import hatası olursa sessizce geçer — bot durmamalı.
    """
    try:
        from core.config import cfg

        # Risk çarpanı uygula — TABAN degere gore (idempotent). Tekrar cagrilinca
        # bilesik kuculmesin diye orijinal RISK_PCT bir kez saklanir; her uygulama
        # base*carpan olarak hesaplanir.
        if not hasattr(cfg, "_risk_pct_base"):
            cfg._risk_pct_base = cfg.RISK_PCT
        base = float(cfg._risk_pct_base)
        new_risk = round(base * ctx.suggested_risk_multiplier, 3)
        if abs(new_risk - float(cfg.RISK_PCT)) > 1e-9:
            log.warning(
                f"RISK_PCT: {cfg.RISK_PCT}% -> {new_risk}% "
                f"(taban={base}% carpan={ctx.suggested_risk_multiplier})"
            )
        cfg.RISK_PCT = new_risk

        # Entry mode kilitleme
        current_mode = str(getattr(cfg, "ENTRY_MODE", "break")).lower()
        if ctx.disable_range and current_mode in ("range", "hybrid"):
            cfg.ENTRY_MODE = "break"
            log.warning("ENTRY_MODE: range/hybrid -> break (range WR < 35%)")
        elif ctx.disable_break and current_mode == "break":
            cfg.ENTRY_MODE = "range"
            log.warning("ENTRY_MODE: break -> range (break WR < 35%)")

    except Exception as e:
        log.warning(f"PerfCtx cfg uygulama hatası: {e}")

    try:
        from core.state import state
        state.perf_ctx = ctx           # dashboard / analyzer için
        state.daily_pnl_pct = ctx.daily_pnl_pct
    except Exception as e:
        log.warning(f"PerfCtx state uygulama hatası: {e}")


# ─── Günlük PnL güncel tutan yardımcı ─────────────────────────────────────────

def update_daily_pnl(pnl_delta_pct: float) -> float:
    """
    Her trade kapanışında çağrılır.
    state.daily_pnl_pct'yi günceller ve güncel değeri döner.
    """
    try:
        from core.state import state
        current = getattr(state, "daily_pnl_pct", 0.0) or 0.0
        state.daily_pnl_pct = round(current + pnl_delta_pct, 4)
        return state.daily_pnl_pct
    except Exception:
        return 0.0
