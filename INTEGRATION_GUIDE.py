# ═══════════════════════════════════════════════════════════════════════════════
# dd-bot — main.py entegrasyon rehberi
# Tüm yeni modüllerin mevcut main.py'e nasıl ekleneceği
# ═══════════════════════════════════════════════════════════════════════════════

# ─── 1. IMPORT BLOĞU (main.py üstüne ekle) ────────────────────────────────────

from botlog.performance_context import load_performance_context
from botlog.db_backup import maybe_backup
from core.daily_loss_guard import get_guard
from core.error_handler import guard as err
from engine.v3_guard import v3_update_safe, is_v3_stale
from engine.adaptive_risk import get_effective_risk, liq_filter_ok


# ─── 2. _main_loop — setup_api sonrasına ekle ─────────────────────────────────

# ÖNCE:
#     await setup_api()
#     if cfg.API_KEY and not is_paper_mode():
#         from execution.account_sync import reconcile_startup_exchange
#         await reconcile_startup_exchange()

# SONRA (yeni satırlar +++):
#     await setup_api()
#     if cfg.API_KEY and not is_paper_mode():
#         from execution.account_sync import reconcile_startup_exchange
#         await reconcile_startup_exchange()
#
# +++ # Geçmiş performans context'ini yükle
# +++ await load_performance_context()
# +++
# +++ # Günlük kayıp guard'ı başlat
# +++ daily_guard = get_guard()


# ─── 3. _on_1h — V3 güncellemesini güvenli hale getir ────────────────────────

# ÖNCE (tehlikeli):
# async def _on_1h(candle: dict):
#     add_bar_1h(candle)
#     if getattr(cfg, "STRATEGY_V3_ENABLED", False):
#         try:
#             from engine.levels_v3 import update_levels
#             from engine.structure_v3 import update_structure
#             from engine.decision_v3 import update_decision
#             update_levels()
#             update_structure()
#             update_decision()
#         except Exception as e:
#             log.warning(f"V3 1h guncelleme: {e}")   # ← SORUNLU

# SONRA (güvenli):
async def _on_1h(candle: dict):
    add_bar_1h(candle)
    if getattr(cfg, "STRATEGY_V3_ENABLED", False):
        await v3_update_safe("1h")   # hata → state.v3_stale = True

    with err.warn("journal 1h"):
        from botlog.journal import on_bar
        on_bar("1h", candle, f"yapi 1h={state.structure_1h}")


# ─── 4. _on_15m — journal hata yönetimi ──────────────────────────────────────

# ÖNCE:
# async def _on_15m(candle: dict):
#     ...
#     try:
#         from dashboard.binance_chart import publish_bot_bars_to_cache
#         publish_bot_bars_to_cache()
#     except Exception:
#         pass

# SONRA (aynı davranış ama isimlendirmeli):
async def _on_15m(candle: dict):
    from engine.intra_15m import finalize_on_15m_close
    add_bar_15m(candle)
    cvd_on_bar(candle)
    await on_15m_market(candle)
    finalize_on_15m_close(candle)

    with err.silent("dashboard 15m yayını"):   # kasıtlı sessiz
        from dashboard.binance_chart import publish_bot_bars_to_cache
        publish_bot_bars_to_cache()


# ─── 5. execute_entry — güvenlik kontrolleri ekle ────────────────────────────

# execution/executor.py içindeki execute_entry veya engine/trader.py içinde
# open_position çağrısından ÖNCE şu kontroller eklenmeli:

async def _on_entry_confirmed(details: dict):
    """Mevcut fonksiyona kontroller eklenir."""

    # 1. Günlük kayıp guard
    daily_guard = get_guard()
    if not daily_guard.can_trade():
        log.warning("execute_entry: günlük limit → giriş iptal")
        return

    # 2. V3 stale kontrolü
    if getattr(cfg, "STRATEGY_V3_ENABLED", False) and is_v3_stale():
        log.warning("execute_entry: V3 stale → giriş iptal")
        return

    # 3. Adaptif risk hesapla
    effective_risk, min_rr = get_effective_risk()
    details["risk_pct_override"] = effective_risk
    details["min_rr_override"]   = min_rr

    # 4. Liquidation filtresi
    direction = "LONG" if details.get("side") == "BUY" else "SHORT"
    if not liq_filter_ok(direction):
        log.warning(f"execute_entry: liq filter → {direction} giriş iptal")
        return

    # 5. Normal giriş akışı
    if details.get("range_mode"):
        src = "range"
    elif details.get("break_mode"):
        src = "breakout"
    else:
        src = "1m-confirm"

    await execute_entry(details, source=src)

    # 6. Guard'a aç kaydı
    daily_guard.record_trade_open()


# ─── 6. Trade kapanışında PnL kaydet ─────────────────────────────────────────

# execution/executor.py içindeki close_position veya on_tp1_hit / on_tp2_hit
# fonksiyonlarına eklenecek — trade kapandıktan sonra:

# from core.daily_loss_guard import get_guard
# from botlog.performance_context import update_daily_pnl
#
# pnl_pct = ...  # hesaplanan % PnL
# get_guard().record_trade_pnl(pnl_pct)
# update_daily_pnl(pnl_pct)


# ─── 7. run_scheduled_analysis — backup ekle ─────────────────────────────────

# botlog/analyzer.py içindeki run_scheduled_analysis fonksiyonuna ekle:

# from botlog.db_backup import maybe_backup
# await maybe_backup()   # her çalışmada kontrol eder, günde 1 kez backup alır


# ─── 8. .env.example — yeni parametreler ─────────────────────────────────────

# Bu satırları .env dosyana ekle:

ENV_ADDITIONS = """
# Günlük kayıp ve trade limiti
MAX_DAILY_LOSS_PCT=3.0
MAX_DAILY_TRADES=10

# Likit kasım feed — önerilir: true
LIQ_WS_ENABLED=true

# CVD slope penceresi genişletme (daha güvenilir)
RANGE_CVD_SLOPE_SEC=180
RANGE_CVD_SLOPE_MIN=100
"""
