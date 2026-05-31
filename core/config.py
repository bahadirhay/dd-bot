"""
core/config.py
API key'leri api_key.csv'den okur (mmbot3 uyumlu: tek satır veya başlıklı CSV).
"""
import csv
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent.parent
load_dotenv(BASE_DIR / ".env")


def load_keys() -> tuple[str, str]:
    p = BASE_DIR / "api_key.csv"
    if not p.exists():
        p.write_text("api_key,api_secret\nyour_key_here,your_secret_here\n", encoding="utf-8")
        return "", ""

    text = p.read_text(encoding="utf-8-sig").strip()
    if not text:
        return "", ""

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return "", ""

    # mmbot3: tek satır "key,secret" (başlık yok)
    if "," in lines[0] and lines[0].split(",")[0].strip().lower() not in ("api_key", "your_key_here"):
        parts = lines[0].split(",", 1)
        if len(parts) == 2:
            k, s = parts[0].strip(), parts[1].strip()
            if k and k != "your_key_here":
                return k, s

    # Başlıklı CSV
    with open(p, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            k = (row.get("api_key") or row.get("\ufeffapi_key") or "").strip()
            s = (row.get("api_secret") or "").strip()
            if k and k != "your_key_here":
                return k, s

    return "", ""


def reload_keys() -> tuple[str, str]:
    k, s = load_keys()
    cfg.API_KEY = k
    cfg.API_SECRET = s
    return k, s


_K, _S = load_keys()


class Config:
    API_KEY = _K
    API_SECRET = _S
    # mmbot3 canlı Futures kullanır; varsayılan canlı (TESTNET=false)
    TESTNET = os.getenv("TESTNET", "false").lower() == "true"

    SYMBOL = "ETHUSDT"
    SYMBOL_WS = "ethusdt"

    LEVERAGE = int(os.getenv("LEVERAGE", "5"))
    MARGIN = os.getenv("MARGIN", "ISOLATED")
    # Sabit marjin (USDT) — her işlemde bu kadar isolated marjin (5x → notional = marjin × kaldıraç)
    TRADE_MARGIN_USD = float(os.getenv("TRADE_MARGIN_USD", "10"))
    # Üst sınır: equity'nin en fazla bu kadarı tek pozisyonda marjin (güvenlik)
    MAX_MARGIN_PCT = float(os.getenv("MAX_MARGIN_PCT", "20"))
    # TRADE_MARGIN_USD=0 iken eski mod: bakiye × RISK_PCT / SL mesafesi
    RISK_PCT = float(os.getenv("RISK_PCT", "1.0"))
    TP1_PCT = 0.50
    MIN_RR = 1.5

    # mmbot3 yapı / SL-TP (structure_levels + structure_analyzer)
    FS_STRUCT_SL_BUFFER_BPS = 10.0
    FS_STRUCT_BREAK_BPS = 8.0
    FS_TP2_LIQ_MIN_USD = 15_000.0
    LIQ_CLUSTER_WINDOW_SEC = 300.0
    LIQ_BUCKET_USD = 5.0
    LIQ_CLUSTER_TOP_N = 6

    SWING_LB_15M = 10
    SWING_LB_1H = 3
    # Güncel çerez S/R: son N×15m (varsayılan 32 ≈ 8s) — grafikteki destek/direnç bandı
    STRUCTURE_COOKIE_BARS = int(os.getenv("STRUCTURE_COOKIE_BARS", "32"))
    STRUCTURE_COOKIE_BARS_MACRO = int(os.getenv("STRUCTURE_COOKIE_BARS_MACRO", "96"))
    STRUCTURE_COOKIE_BARS_TREND = int(os.getenv("STRUCTURE_COOKIE_BARS_TREND", "16"))
    STRUCTURE_COOKIE_IMPULSE_TOP_N = int(
        os.getenv("STRUCTURE_COOKIE_IMPULSE_TOP_N", "3")
    )
    STRUCTURE_COOKIE_MIN_FAILED_BREAKS = int(
        os.getenv("STRUCTURE_COOKIE_MIN_FAILED_BREAKS", "2")
    )
    STRUCTURE_COOKIE_MIN_EDGE_CONF = float(
        os.getenv("STRUCTURE_COOKIE_MIN_EDGE_CONF", "0.5")
    )
    STRUCTURE_COOKIE_CLUSTER_MUL = float(
        os.getenv("STRUCTURE_COOKIE_CLUSTER_MUL", "2.5")
    )
    STRUCTURE_COOKIE_MIN_TOUCHES = int(os.getenv("STRUCTURE_COOKIE_MIN_TOUCHES", "3"))
    STRUCTURE_COOKIE_1M_ENABLED = os.getenv(
        "STRUCTURE_COOKIE_1M_ENABLED", "true"
    ).lower() in ("1", "true", "yes")
    STRUCTURE_COOKIE_1M_LOOKBACK = int(os.getenv("STRUCTURE_COOKIE_1M_LOOKBACK", "20"))
    STRUCTURE_COOKIE_MIN_WIDTH_BPS = float(
        os.getenv("STRUCTURE_COOKIE_MIN_WIDTH_BPS", "90")
    )
    STRUCTURE_COOKIE_MAX_MIN_WIDTH_BPS = float(
        os.getenv("STRUCTURE_COOKIE_MAX_MIN_WIDTH_BPS", "140")
    )
    # Katmanlı trend (grafikle hizalı):
    # 1m nabız = grafikte son X dakika (erken hareket)
    PULSE_BARS_1M = int(os.getenv("PULSE_BARS_1M", "15"))
    # 15m onay = son M kapalı 15m (varsayılan 4 = 1 saat, 3 saat DEĞİL)
    _timing_env = os.getenv("TIMING_BARS_15M") or os.getenv("TREND_BARS_15M")
    TIMING_BARS_15M = int(_timing_env) if _timing_env else 4
    TREND_BARS_15M = TIMING_BARS_15M  # geriye uyumluluk (diag scriptleri)
    REGIME_MIN = 3
    CVD_BARS = 10
    CVD_CONSIST = 0.60
    CVD_MIN = 200.0
    OI_LOOKBACK = 3
    OI_POLL = 10
    TAKER_MIN = 0.60
    ENTRY_TIMEOUT = 5
    # Otomatik trade: trend analizi → pozisyon (paper veya canlı)
    AUTO_TRADE_ENABLED = os.getenv("AUTO_TRADE", "true").lower() in ("1", "true", "yes")
    # break = swing kırılım + CVD/taker anlık (varsayılan)
    # confirm = 15m sinyal + 1m onay | trend = eski 15m/impulse
    ENTRY_MODE = os.getenv("ENTRY_MODE", "break").lower()
    STRATEGY_V2_ENABLED = os.getenv("STRATEGY_V2_ENABLED", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    STRATEGY_V3_ENABLED = os.getenv("STRATEGY_V3_ENABLED", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    V3_SWING_LOOKBACK = int(os.getenv("V3_SWING_LOOKBACK", "2"))
    # 1h swing: daha genis pencere (legacy SWING_LB_1H=3 ile uyumlu)
    V3_SWING_LOOKBACK_1H = int(os.getenv("V3_SWING_LOOKBACK_1H", "3"))
    V3_STRUCTURE_SWING_COUNT = int(os.getenv("V3_STRUCTURE_SWING_COUNT", "6"))
    V3_SHELF_MIN_BARS = int(os.getenv("V3_SHELF_MIN_BARS", "3"))
    V3_LEVEL_MAX_AGE_1H = int(os.getenv("V3_LEVEL_MAX_AGE_1H", "50"))
    V3_LEVEL_MAX_AGE_15M = int(os.getenv("V3_LEVEL_MAX_AGE_15M", "96"))
    V3_LEVEL_SCORE_STRONG = int(os.getenv("V3_LEVEL_SCORE_STRONG", "6"))
    V3_LEVEL_SCORE_MEDIUM = int(os.getenv("V3_LEVEL_SCORE_MEDIUM", "4"))
    V3_LEVEL_SCORE_WEAK = int(os.getenv("V3_LEVEL_SCORE_WEAK", "3"))
    # Zone: destek/dirence yakinlik = bant_genisligi * oran (or. 28$ * 0.35 = 9.8$)
    V3_ZONE_RATIO = float(os.getenv("V3_ZONE_RATIO", "0.35"))
    # Kanal teyidi: swing'in aktif S/R fiyatina yakinligi (0.003 = ±%0.3)
    V3_CHANNEL_BAND_PCT = float(os.getenv("V3_CHANNEL_BAND_PCT", "0.003"))
    # Tek yonlu dusus/yukselis: son N 15m'de hem destek hem dirence dokunma = kanal traverse
    V3_CHANNEL_TRAVERSE_BARS = int(os.getenv("V3_CHANNEL_TRAVERSE_BARS", "48"))
    # Grafik dis destek/direnc: ana seviyeden min mesafe (%0.4 veya en az $2)
    V3_OUTER_LEVEL_GAP_PCT = float(os.getenv("V3_OUTER_LEVEL_GAP_PCT", "0.004"))
    # Grafikte ana band disinda gosterilecek max destek/direnc cizgisi (her yon)
    V3_CHART_MAX_LEVELS_PER_SIDE = int(os.getenv("V3_CHART_MAX_LEVELS_PER_SIDE", "5"))
    # Kanal kirilimi: fiyat S/R disina cikinca kac 15m kapanis teyit edilir (2-3)
    V3_LEVEL_BREAK_CONFIRM_BARS = int(os.getenv("V3_LEVEL_BREAK_CONFIRM_BARS", "1"))
    # Aktif bant kilidi: S/R disina bu kadar (%0.15) cikmadan yeniden secim yapma
    V3_BAND_UNLOCK_BUFFER_PCT = float(os.getenv("V3_BAND_UNLOCK_BUFFER_PCT", "0.0015"))
    # Aktif bant min genislik: fiyat * pct veya USD taban (dar lokal gurultu elenir)
    V3_BAND_MIN_WIDTH_PCT = float(os.getenv("V3_BAND_MIN_WIDTH_PCT", "0.004"))
    V3_BAND_MIN_WIDTH_USD = float(os.getenv("V3_BAND_MIN_WIDTH_USD", "8.0"))
    # Skor birbirine yakin adaylarda (max * frac) en genis bant tercih
    V3_BAND_SCORE_FRAC = float(os.getenv("V3_BAND_SCORE_FRAC", "0.88"))
    # Aktif bant: 1h swing destek + 1h swing direnc (makro kanal) — varsayilan kapali
    V3_ACTIVE_BAND_HTF = os.getenv("V3_ACTIVE_BAND_HTF", "false").lower() in (
        "1",
        "true",
        "yes",
    )
    # 1h yapi: son N 1h kapanis egimi (swing yerine)
    V3_STRUCTURE_1H_CLOSE_BARS = int(os.getenv("V3_STRUCTURE_1H_CLOSE_BARS", "6"))
    V3_STRUCTURE_1H_MIN_MOVE_PCT = float(os.getenv("V3_STRUCTURE_1H_MIN_MOVE_PCT", "0.002"))
    # 15m yapi: son N 15m kapanis egimi (swing yerine; 1h ile hizalama icin)
    V3_STRUCTURE_15M_CLOSE_BARS = int(os.getenv("V3_STRUCTURE_15M_CLOSE_BARS", "8"))
    V3_STRUCTURE_15M_MIN_MOVE_PCT = float(os.getenv("V3_STRUCTURE_15M_MIN_MOVE_PCT", "0.0004"))
    # Güçlü seviye yokken geçici S/R: son N×15m mumun en düşük low / en yüksek high
    V3_EXTREME_FALLBACK_BARS = int(os.getenv("V3_EXTREME_FALLBACK_BARS", "24"))
    V3_CVD_WINDOW_TRADES = int(os.getenv("V3_CVD_WINDOW_TRADES", "500"))
    V3_WICK_STRENGTH_MULTIPLIER = float(
        os.getenv("V3_WICK_STRENGTH_MULTIPLIER", "2.0")
    )
    V3_MIN_RR_RATIO = float(os.getenv("V3_MIN_RR_RATIO", "2.0"))
    # RANGE_BUY / RANGE_SELL: yon bazli min skor + guc (BUY siki, SELL daha esnek)
    V3_MIN_RANGE_SCORE = int(os.getenv("V3_MIN_RANGE_SCORE", "10"))
    V3_MIN_RANGE_SCORE_BUY = int(
        os.getenv("V3_MIN_RANGE_SCORE_BUY", os.getenv("V3_MIN_RANGE_SCORE", "10"))
    )
    V3_MIN_RANGE_SCORE_SELL = int(
        os.getenv("V3_MIN_RANGE_SCORE_SELL", os.getenv("V3_MIN_RANGE_SCORE", "10"))
    )
    V3_DECISION_LOG_SEC = float(os.getenv("V3_DECISION_LOG_SEC", "120"))
    # V3 akis ozet logu (data/logs/v3_flow.log) — saniye, varsayilan 30 dk
    V3_FLOW_LOG_SEC = float(os.getenv("V3_FLOW_LOG_SEC", "1800"))
    # Tez cikisi: stale bar sayisi (15m) ve minimum ilerleme orani
    V3_THESIS_STALE_BARS = int(os.getenv("V3_THESIS_STALE_BARS", "8"))
    V3_THESIS_MIN_PROGRESS = float(os.getenv("V3_THESIS_MIN_PROGRESS", "0.003"))
    # range = kanal fade (TP band içi) | hybrid = band içi range, dışı break
    RANGE_MIN_WIDTH_BPS = float(os.getenv("RANGE_MIN_WIDTH_BPS", "50"))
    RANGE_PROXIMITY_BPS = float(os.getenv("RANGE_PROXIMITY_BPS", "35"))
    RANGE_SIDE_MARGIN_BPS = float(os.getenv("RANGE_SIDE_MARGIN_BPS", "8"))
    RANGE_CHOP_DIFF_BPS = float(os.getenv("RANGE_CHOP_DIFF_BPS", "15"))
    RANGE_CENTER_MIN_BPS = float(os.getenv("RANGE_CENTER_MIN_BPS", "28"))
    RANGE_MIN_SCORE = float(os.getenv("RANGE_MIN_SCORE", "65"))
    # HTF: sabit +10 yerine archetype (RANGE_ARCHETYPE_STRICT)
    RANGE_HTF_PENALTY = float(os.getenv("RANGE_HTF_PENALTY", "0"))
    RANGE_ARCHETYPE_STRICT = os.getenv("RANGE_ARCHETYPE_STRICT", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    RANGE_MIN_EDGE_P = float(os.getenv("RANGE_MIN_EDGE_P", "0.52"))
    RANGE_MIN_FLOW_P = float(os.getenv("RANGE_MIN_FLOW_P", "0.45"))
    RANGE_TAKER_MIN = float(os.getenv("RANGE_TAKER_MIN", "0.55"))
    RANGE_CVD_SLOPE_SEC = float(os.getenv("RANGE_CVD_SLOPE_SEC", "90"))
    RANGE_CVD_SLOPE_MIN = float(os.getenv("RANGE_CVD_SLOPE_MIN", "60"))
    RANGE_1M_LOOKBACK = int(os.getenv("RANGE_1M_LOOKBACK", "5"))
    RANGE_WICK_MIN = float(os.getenv("RANGE_WICK_MIN", "0.45"))
    RANGE_LEVEL_TOUCH_BPS = float(os.getenv("RANGE_LEVEL_TOUCH_BPS", "25"))
    RANGE_TP_BUFFER_BPS = float(os.getenv("RANGE_TP_BUFFER_BPS", "12"))
    RANGE_MIN_RR = float(os.getenv("RANGE_MIN_RR", "1.2"))
    # Kanal içi hareket — sabit bps gevşetme değil, hesaplanan seviyeler
    RANGE_REF_WIDTH_BPS = float(os.getenv("RANGE_REF_WIDTH_BPS", "300"))
    RANGE_LOCAL_LOOKBACK = int(os.getenv("RANGE_LOCAL_LOOKBACK", "12"))
    RANGE_USE_LOCAL_SHELF = os.getenv("RANGE_USE_LOCAL_SHELF", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    RANGE_USE_INNER_SWING = os.getenv("RANGE_USE_INNER_SWING", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    RANGE_ZONE_REJECTION = os.getenv("RANGE_ZONE_REJECTION", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    RANGE_ZONE_FRAC = float(os.getenv("RANGE_ZONE_FRAC", "0.25"))
    BREAK_HOLD_SEC = float(os.getenv("BREAK_HOLD_SEC", "2"))
    # Yapısal kırılım (destek altı / direnç üstü): pozisyon zorunlu, yumuşak filtre yok
    BREAK_STRUCTURAL_MANDATORY = os.getenv("BREAK_STRUCTURAL_MANDATORY", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    BREAK_STRUCTURAL_HOLD_SEC = float(os.getenv("BREAK_STRUCTURAL_HOLD_SEC", "1"))
    BREAK_PROXIMITY_BPS = float(os.getenv("BREAK_PROXIMITY_BPS", "35"))
    BREAK_OUTSIDE_MAX_BPS = float(os.getenv("BREAK_OUTSIDE_MAX_BPS", "500"))
    BREAK_MIN_RANGE_BPS = float(os.getenv("BREAK_MIN_RANGE_BPS", "50"))
    BREAK_POST_BREAK_MIN_BPS = float(os.getenv("BREAK_POST_BREAK_MIN_BPS", "80"))
    BREAK_MIN_RR_UNCLEAR = float(os.getenv("BREAK_MIN_RR_UNCLEAR", "2.0"))
    ENTRY_COOLDOWN_SEC = float(os.getenv("ENTRY_COOLDOWN_SEC", "300"))
    OI_ENTRY_LOOKBACK_SEC = float(os.getenv("OI_ENTRY_LOOKBACK_SEC", "30"))
    BREAK_MAX_FAILED_TESTS = int(os.getenv("BREAK_MAX_FAILED_TESTS", "8"))
    TP1_RETEST_MAX_REJECTS = int(os.getenv("TP1_RETEST_MAX_REJECTS", "2"))
    TP1_RETEST_EXIT_AFTER = int(os.getenv("TP1_RETEST_EXIT_AFTER", "3"))
    MARKET_DATA_MODE = os.getenv("MARKET_DATA_MODE", "aggtrade_ws_rest").lower()
    USER_STREAM_ENABLED = os.getenv("USER_STREAM_ENABLED", "false").lower() in (
        "1",
        "true",
        "yes",
    )
    LIQ_WS_ENABLED = os.getenv("LIQ_WS_ENABLED", "false").lower() in (
        "1",
        "true",
        "yes",
    )
    # listenKey ~60dk expire; varsayılan 30dk'da bir PUT (başarısız → stream restart)
    USER_STREAM_KEEPALIVE_SEC = float(os.getenv("USER_STREAM_KEEPALIVE_SEC", str(30 * 60)))
    # WS kopunca ilk reconnect denemesi için taban bekleme
    WS_RECONNECT_DELAY_SEC = float(os.getenv("WS_RECONNECT_DELAY_SEC", "5"))
    REST_PRICE_POLL_SEC = float(os.getenv("REST_PRICE_POLL_SEC", "5"))
    REST_KLINE_1M_POLL_SEC = float(os.getenv("REST_KLINE_1M_POLL_SEC", "5"))
    REST_KLINE_15M_POLL_SEC = float(os.getenv("REST_KLINE_15M_POLL_SEC", "20"))
    REST_KLINE_1H_POLL_SEC = float(os.getenv("REST_KLINE_1H_POLL_SEC", "60"))
    TRADE_REST_POLL_SEC = float(os.getenv("TRADE_REST_POLL_SEC", "2"))
    TRADE_WS_FIRST_MSG_TIMEOUT_SEC = float(os.getenv("TRADE_WS_FIRST_MSG_TIMEOUT_SEC", "12"))
    TRADE_STALE_SECONDS = float(os.getenv("TRADE_STALE_SECONDS", "45"))
    # WS baglanir ama mesaj gelmiyorsa (VPN/firewall) false yap — dogrudan REST poll
    TRADE_WS_ENABLED = os.getenv("TRADE_WS_ENABLED", "true").lower() in ("1", "true", "yes")
    # Market WS koptuğunda REST recovery hemen başlamasın
    MARKET_RECOVERY_POLL_SEC = float(os.getenv("MARKET_RECOVERY_POLL_SEC", "5"))
    MARKET_RECOVERY_REST_DELAY_SEC = float(os.getenv("MARKET_RECOVERY_REST_DELAY_SEC", "30"))
    MARKET_RECOVERY_COOLDOWN_SEC = float(os.getenv("MARKET_RECOVERY_COOLDOWN_SEC", "15"))
    # 15m mum bitmeden drop/rise ile giriş (trend güçlüyse)
    IMPULSE_1M_TRADE = os.getenv("IMPULSE_1M_TRADE", "true").lower() in ("1", "true", "yes")
    # Trade için 1h + 15m yapı aynı yönde olmalı (15m tek başına yetmez)
    REQUIRE_HTF_ALIGN = os.getenv("REQUIRE_HTF_ALIGN", "true").lower() in (
        "1", "true", "yes"
    )
    # Piyasa gunlugu: her N saniye tam state DB'ye
    JOURNAL_SAMPLE_SEC = float(os.getenv("JOURNAL_SAMPLE_SEC", "10"))
    CHART_HOURS = int(os.getenv("CHART_HOURS", "8"))
    ANALYSIS_HOURS = [6, 12, 18, 0]

    TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

    # Dashboard "Açıkla" — Claude (Anthropic); anahtar dashboard veya .env
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "") or os.getenv("CLAUDE_API_KEY", "")
    CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")

    DB_PATH = str(BASE_DIR / "data" / "bot.db")
    LOG_DIR = str(BASE_DIR / "data" / "logs")

    BALANCE_API_TIMEOUT_SEC = float(os.getenv("BALANCE_API_TIMEOUT_SEC", "18"))

    # Algo TP: mark'tan min mesafe (SHORT altında, LONG üstünde) — -2021 önleme
    PROTECTION_TP_MIN_BPS = float(os.getenv("PROTECTION_TP_MIN_BPS", "30"))
    PROTECTION_TP2_EXTRA_BPS = float(os.getenv("PROTECTION_TP2_EXTRA_BPS", "55"))
    # SL yönetimi: destek/direnç üstü kâr kilidi + TP1 sonrası runner
    SL_LOCK_BUFFER_BPS = float(os.getenv("SL_LOCK_BUFFER_BPS", "12"))
    SL_LOCK_MARK_BUFFER_BPS = float(os.getenv("SL_LOCK_MARK_BUFFER_BPS", "10"))
    SL_LOCK_MIN_PROFIT_BPS = float(os.getenv("SL_LOCK_MIN_PROFIT_BPS", "12"))
    # Kâr kilidinde girişten en az bu kadar bps kâr kilitlenir (SHORT: SL en fazla entry - X bps)
    SL_LOCK_MIN_LOCKED_BPS = float(os.getenv("SL_LOCK_MIN_LOCKED_BPS", "80"))
    SL_LOCK_RETEST_BARS_15M = int(os.getenv("SL_LOCK_RETEST_BARS_15M", "48"))
    # auto | break_retest | swing_trail | range_band
    SL_LOCK_PROFILE = os.getenv("SL_LOCK_PROFILE", "auto").lower()
    RUNNER_SL_BUFFER_BPS = float(os.getenv("RUNNER_SL_BUFFER_BPS", "18"))
    # TP1 sonrası ilk SL = TP1 (0 = tam TP1; küçük buffer için bps)
    TRAIL_SL_TP1_BUFFER_BPS = float(os.getenv("TRAIL_SL_TP1_BUFFER_BPS", "0"))
    # TP1 dolunca SL hemen TP1'e taşınmaz; ilk sıkılaştırma 15m kapanışında
    TP1_DEFER_SL_TO_15M = os.getenv("TP1_DEFER_SL_TO_15M", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    # TP1 sonrası ilk trail: 15m kapanış TP1'in doğru tarafında olmalı
    TP1_CONFIRM_15M = os.getenv("TP1_CONFIRM_15M", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    # 15m onayindan sonra ek 5m kapanis onayi (1m aggregate)
    TP1_CONFIRM_5M = os.getenv("TP1_CONFIRM_5M", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    TP1_CONFIRM_BUFFER_BPS = float(os.getenv("TP1_CONFIRM_BUFFER_BPS", "0"))
    # TP2 borsa emri gönderilmez; runner yalnızca SL ile (TP1 sonrası 15m trail)
    SEND_TP2_ORDER = os.getenv("SEND_TP2_ORDER", "false").lower() in (
        "1",
        "true",
        "yes",
    )
    SL_MANAGE_COOLDOWN_SEC = float(os.getenv("SL_MANAGE_COOLDOWN_SEC", "45"))
    # TP1 öncesi swing_trail kâr kilidi (varsayılan kapalı — invalidation SL korunur)
    SL_STRUCTURAL_PRE_TP1 = os.getenv("SL_STRUCTURAL_PRE_TP1", "").lower() in (
        "1",
        "true",
        "yes",
    )
    # TP1 en fazla girişten bu kadar bps (0=kapalı); uzak swing hedefini yaklaştırır
    TP1_MAX_DISTANCE_BPS = float(os.getenv("TP1_MAX_DISTANCE_BPS", "120"))
    # Kırılım TP1/TP2
    BREAK_TP1_MIN_RR = float(os.getenv("BREAK_TP1_MIN_RR", "1.2"))
    # Restart / geç kırılım: tanık veya retest olmadan giriş yok
    NARRATIVE_ENABLED = os.getenv("NARRATIVE_ENABLED", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    # Global yapı: tüm bps eşikleri kanal/span oranı (ATR ve sabit bps yok)
    GLOBAL_STRUCTURE_MODE = os.getenv("GLOBAL_STRUCTURE_MODE", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    GS_BREAK_BAND_FRAC = float(os.getenv("GS_BREAK_BAND_FRAC", "0.04"))
    GS_PROXIMITY_BAND_FRAC = float(os.getenv("GS_PROXIMITY_BAND_FRAC", "0.12"))
    GS_MIN_CHANNEL_FRAC = float(os.getenv("GS_MIN_CHANNEL_FRAC", "0.18"))
    GS_OUTSIDE_BAND_FRAC = float(os.getenv("GS_OUTSIDE_BAND_FRAC", "0.85"))
    GS_OUTSIDE_LEG_FRAC = float(os.getenv("GS_OUTSIDE_LEG_FRAC", "0.72"))
    GS_OUTSIDE_FLOOR = float(os.getenv("GS_OUTSIDE_FLOOR", "70"))
    GS_OUTSIDE_CAP = float(os.getenv("GS_OUTSIDE_CAP", "650"))
    GS_POST_BREAK_FRAC = float(os.getenv("GS_POST_BREAK_FRAC", "0.20"))
    GS_RANGE_MIN_WIDTH_FRAC = float(os.getenv("GS_RANGE_MIN_WIDTH_FRAC", "0.18"))
    GS_TOUCH_BAND_FRAC = float(os.getenv("GS_TOUCH_BAND_FRAC", "0.085"))
    GS_CVD_LOOKBACK_SEC = float(os.getenv("GS_CVD_LOOKBACK_SEC", "3600"))
    GS_CVD_MEDIAN_MULT = float(os.getenv("GS_CVD_MEDIAN_MULT", "0.32"))
    GS_CVD_SLOPE_BAND_FRAC = float(os.getenv("GS_CVD_SLOPE_BAND_FRAC", "0.22"))
    GS_CVD_WEAK_MULT = float(os.getenv("GS_CVD_WEAK_MULT", "0.5"))
    GS_OI_MIN_REL = float(os.getenv("GS_OI_MIN_REL", "0"))
    GS_SL_BUFFER_FRAC = float(os.getenv("GS_SL_BUFFER_FRAC", "0.05"))
    GS_TP1_MAX_BAND_FRAC = float(os.getenv("GS_TP1_MAX_BAND_FRAC", "0.35"))
    # Geç kırılım / retest: kanal + span oranı
    NARRATIVE_EXT_CALC = os.getenv("NARRATIVE_EXT_CALC", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    NARRATIVE_EXTENDED_MIN_BPS = float(os.getenv("NARRATIVE_EXTENDED_MIN_BPS", "120"))
    NARRATIVE_EXTENDED_MIN_BPS_FLOOR = float(
        os.getenv("NARRATIVE_EXTENDED_MIN_BPS_FLOOR", "45")
    )
    NARRATIVE_EXTENDED_MIN_BPS_CAP = float(
        os.getenv("NARRATIVE_EXTENDED_MIN_BPS_CAP", "280")
    )
    NARRATIVE_EXT_BAND_FRAC = float(os.getenv("NARRATIVE_EXT_BAND_FRAC", "0.38"))
    NARRATIVE_EXT_ATR_MULT = float(os.getenv("NARRATIVE_EXT_ATR_MULT", "1.25"))
    NARRATIVE_EXT_SPAN_FRAC = float(os.getenv("NARRATIVE_EXT_SPAN_FRAC", "0.45"))
    NARRATIVE_EXTENDED_MIN_BARS = int(os.getenv("NARRATIVE_EXTENDED_MIN_BARS", "2"))
    NARRATIVE_RETEST_ZONE_BPS = float(os.getenv("NARRATIVE_RETEST_ZONE_BPS", "80"))
    NARRATIVE_RETEST_ZONE_BPS_FLOOR = float(
        os.getenv("NARRATIVE_RETEST_ZONE_BPS_FLOOR", "35")
    )
    NARRATIVE_RETEST_ZONE_BPS_CAP = float(
        os.getenv("NARRATIVE_RETEST_ZONE_BPS_CAP", "150")
    )
    NARRATIVE_RETEST_BAND_FRAC = float(os.getenv("NARRATIVE_RETEST_BAND_FRAC", "0.22"))
    NARRATIVE_RETEST_ATR_MULT = float(os.getenv("NARRATIVE_RETEST_ATR_MULT", "0.85"))
    NARRATIVE_RETEST_BAR_LOOKBACK = int(os.getenv("NARRATIVE_RETEST_BAR_LOOKBACK", "8"))
    BREAK_TP1_EXTENSION_BPS = float(os.getenv("BREAK_TP1_EXTENSION_BPS", "30"))
    BREAK_TP2_MIN_EXTENSION_BPS = float(os.getenv("BREAK_TP2_MIN_EXTENSION_BPS", "100"))
    TP_ADJUST_COOLDOWN_SEC = float(os.getenv("TP_ADJUST_COOLDOWN_SEC", "120"))

    # İzleme: API emri yok, iç simülasyon (PAPER_MODE=true veya api_key yok)
    PAPER_MODE = os.getenv("PAPER_MODE", "").lower() in ("1", "true", "yes")
    PAPER_BALANCE_USD = float(os.getenv("PAPER_BALANCE_USD", "10000"))

    REST = "https://testnet.binancefuture.com" if TESTNET else "https://fapi.binance.com"

    WS_SINGLE = (
        "wss://stream.binancefuture.com/ws/"
        if TESTNET
        else "wss://fstream.binance.com/ws/"
    )
    WS_MULTI = (
        "wss://stream.binancefuture.com/stream?streams="
        if TESTNET
        else "wss://fstream.binance.com/stream?streams="
    )


cfg = Config()


def is_paper_mode() -> bool:
    """Gerçek emir gönderme — izleme / paper trade."""
    if cfg.PAPER_MODE:
        return True
    return not bool(cfg.API_KEY)
