"""
core/state.py — Botun anlık durumu, tek kaynak.
Her modül buradan okur ve buraya yazar.
"""
import time
from dataclasses import dataclass, field
from collections import deque
from typing import Deque

@dataclass
class BotState:
    # ── Fiyat ────────────────────────────────────────────────
    price        : float = 0.0
    bid          : float = 0.0
    ask          : float = 0.0
    mark_price   : float = 0.0
    trade_last_update: float = 0.0
    book_last_update : float = 0.0
    kline_last_update: float = 0.0
    price_history: Deque = field(default_factory=lambda: deque(maxlen=5000))
    metrics_history: Deque = field(default_factory=lambda: deque(maxlen=3000))
    # metrics: {"ts", "cvd", "taker"}
    last_15m_summary: dict = field(default_factory=dict)
    trend_view: dict = field(default_factory=dict)
    breakout_view: dict = field(default_factory=dict)
    range_view: dict = field(default_factory=dict)
    operation_view: dict = field(default_factory=dict)
    v3_levels: dict = field(default_factory=dict)
    v3_structure: dict = field(default_factory=dict)
    v3_cvd: dict = field(default_factory=dict)
    v3_scenario: dict = field(default_factory=dict)
    v3_entry_signal: dict = field(default_factory=dict)
    v3_decision: dict = field(default_factory=dict)
    market_narrative: dict = field(default_factory=dict)
    # Son kapanış — aynı yön tekrar giriş hesabı (süre değil, yapı)
    last_trade_exit: dict = field(default_factory=dict)
    # Kırılım girişi sonrası: seviye çevirme, TP1 kırılım/redd, yapısal çıkış
    position_breakout: dict = field(default_factory=dict)
    last_close_reason: str = ""
    last_close_source: str = ""
    auto_trade_period: int = 0
    last_auto_trade_ts: float = 0.0
    forming_15m: dict = field(default_factory=dict)
    intra_15m_summary: dict = field(default_factory=dict)
    _intra_last_log_pct: float = 0.0

    # ── aggTrade CVD ──────────────────────────────────────────
    # Her tick: m=False → +qty (agresif alım), m=True → -qty (agresif satım)
    cvd_raw      : float = 0.0          # ham birikimli delta
    # maxlen yok: 5 dk pencere _expire_ticks ile temizlenir (maxlen sessiz kayıp CVD bozuyordu)
    ticks        : Deque = field(default_factory=deque)
    # ticks eleman: {"ts": float, "price": float, "qty": float, "delta": float}

    buy_vol_5m   : float = 0.0
    sell_vol_5m  : float = 0.0
    cvd_5m       : float = 0.0
    taker_ratio  : float = 0.5          # agresif alım oranı (0-1)

    # ── CVD mum geçmişi (her 15m kapanışında eklenir) ─────────
    cvd_bars     : Deque = field(default_factory=lambda: deque(maxlen=100))
    # eleman: {"ts": float, "cvd": float, "direction": int}

    # ── OI ────────────────────────────────────────────────────
    oi_history   : Deque = field(default_factory=lambda: deque(maxlen=20))
    oi_current   : float = 0.0
    oi_rising    : bool  = False

    # ── Funding ───────────────────────────────────────────────
    funding_rate : float = 0.0
    funding_signal: str  = ""   # "LONG_CROWD" / "SHORT_CROWD" / "NEUTRAL"

    # ── Tasfiye geçmişi ───────────────────────────────────────
    liquidations : Deque = field(default_factory=lambda: deque(maxlen=50))
    # eleman: {"ts": float, "side": str, "qty": float, "price": float}
    liq_top_clusters: list = field(default_factory=list)
    # mmbot3: {"price": float, "usd": float, "side": "LONG"|"SHORT"}

    # ── Yapı seviyeleri (mmbot3 struct_*) ─────────────────────
    struct_bias_15m     : str   = ""
    struct_invalidation : float = 0.0
    struct_tp1_target   : float = 0.0

    # ── Swing noktaları ───────────────────────────────────────
    swing_highs_15m : list = field(default_factory=list)
    swing_lows_15m  : list = field(default_factory=list)
    swing_highs_1h  : list = field(default_factory=list)
    swing_lows_1h   : list = field(default_factory=list)

    structure_15m   : str = ""   # "UP" / "DOWN" / "UNCLEAR"
    structure_1h    : str = ""

    # ── Rejim ─────────────────────────────────────────────────
    regime          : str   = "UNKNOWN"   # "TREND" / "RANGE"
    regime_score    : int   = 0           # 0-4
    regime_answers  : dict  = field(default_factory=dict)
    # {"structure": bool, "cvd": bool, "oi": bool, "taker": bool}

    # ── Sinyal ────────────────────────────────────────────────
    signal          : str   = "FLAT"      # "LONG" / "SHORT" / "FLAT"
    signal_ts       : float = 0.0
    signal_reason   : str   = ""
    no_entry_reason : str   = ""          # neden giriş yapılmadı

    # ── Giriş bekleme ─────────────────────────────────────────
    waiting_entry   : bool  = False
    waiting_dir     : str   = ""
    waiting_since   : float = 0.0
    entry_bars_left : int   = 0

    # ── Pozisyon ──────────────────────────────────────────────
    in_position     : bool  = False
    pos_side        : str   = ""
    pos_entry       : float = 0.0
    pos_qty         : float = 0.0
    pos_qty_tp1     : float = 0.0
    pos_qty_tp2     : float = 0.0
    pos_sl          : float = 0.0
    pos_sl_initial  : float = 0.0
    pos_tp1         : float = 0.0
    pos_tp2         : float = 0.0
    pos_tp1_hit     : bool  = False
    pos_be_active   : bool  = False
    pos_sl_id       : str   = ""
    pos_sl_manage_ts: float = 0.0
    pos_tp_manage_ts: float = 0.0
    pos_tp1_id      : str   = ""
    pos_tp2_id      : str   = ""
    pos_liq_price   : float = 0.0
    pos_margin      : float = 0.0
    pos_open_ts     : float = 0.0
    unrealized_pnl  : float = 0.0

    # ── Binance API (mmbot3: arka planda güncellenir) ─────────
    api_ok          : bool  = False
    api_error       : str   = ""
    real_balance    : float = 0.0
    real_balance_ts : float = 0.0
    wallet_balance  : float = 0.0
    available_balance: float = 0.0
    equity_balance  : float = 0.0
    account_sync_ts : float = 0.0
    exchange_position: dict = field(default_factory=dict)
    exchange_reconciled: bool = False
    startup_grace_until: float = 0.0

    # ── Paper / izleme simülasyonu ────────────────────────────
    paper_mode      : bool  = False
    paper_balance   : float = 0.0

    # ── Meta ──────────────────────────────────────────────────
    last_update     : float = field(default_factory=time.time)
    errors          : list  = field(default_factory=list)

    def reset_position(self):
        self.in_position  = False
        self.pos_side     = ""
        self.pos_entry    = 0.0
        self.pos_qty      = 0.0
        self.pos_qty_tp1  = 0.0
        self.pos_qty_tp2  = 0.0
        self.pos_sl       = 0.0
        self.pos_sl_initial = 0.0
        self.pos_tp1      = 0.0
        self.pos_tp2      = 0.0
        self.pos_tp1_hit  = False
        self.pos_be_active= False
        self.pos_sl_id    = ""
        self.pos_tp1_id   = ""
        self.pos_tp2_id   = ""
        self.pos_liq_price= 0.0
        self.pos_margin   = 0.0
        self.pos_open_ts  = 0.0
        self.unrealized_pnl = 0.0
        self.position_breakout = {}
        self.last_close_reason = ""
        self.last_close_source = ""
        try:
            from engine.breakout import on_position_closed

            on_position_closed()
        except Exception:
            pass

state = BotState()


def mid_price() -> float:
    if state.bid > 0 and state.ask > 0:
        return (state.bid + state.ask) / 2.0
    return 0.0


def effective_price() -> float:
    """En taze kaynak: book mid veya aggTrade."""
    now = time.time()
    book_mid = mid_price()
    trade_age = now - state.trade_last_update if state.trade_last_update else 9999.0
    book_age = now - state.book_last_update if state.book_last_update else 9999.0

    if book_mid > 0 and book_age <= trade_age:
        return book_mid
    if state.price > 0 and trade_age < 120:
        return state.price
    if book_mid > 0:
        return book_mid
    return state.mark_price or 0.0


def record_price_tick(px: float | None = None):
    """Dashboard canlı grafik + gösterim fiyatı."""
    p = px if px and px > 0 else effective_price()
    if p <= 0:
        return
    ts = time.time()
    if state.price_history and state.price_history[-1]["price"] == p:
        if ts - state.price_history[-1]["ts"] < 0.5:
            return
    state.price_history.append({"ts": ts, "price": p})


def record_metrics_sample():
    """CVD / taker zaman serisi (dashboard grafikleri)."""
    ts = time.time()
    if state.metrics_history and ts - state.metrics_history[-1]["ts"] < 10:
        return
    state.metrics_history.append({
        "ts": ts,
        "cvd": state.cvd_5m,
        "taker": state.taker_ratio,
    })


def trade_is_fresh(max_age_sec: float = 30.0) -> bool:
    """aggTrade (CVD 5m / taker) son N sn içinde güncellendi mi."""
    if state.trade_last_update <= 0:
        return False
    return (time.time() - state.trade_last_update) < max_age_sec


def data_is_fresh(max_age_sec: float = 5.0) -> bool:
    """Herhangi bir canlı feed son N sn içinde güncellendi mi."""
    now = time.time()
    for ts in (state.trade_last_update, state.book_last_update, state.kline_last_update):
        if ts > 0 and (now - ts) < max_age_sec:
            return True
    return False
