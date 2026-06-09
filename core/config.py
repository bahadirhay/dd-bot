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

    # .env yedek (api_key.csv yoksa veya placeholder ise)
    k = (os.getenv("BINANCE_API_KEY") or os.getenv("API_KEY") or "").strip()
    s = (os.getenv("BINANCE_API_SECRET") or os.getenv("API_SECRET") or "").strip()
    if k and k != "your_key_here" and s:
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
    # Destek/direnc: Pine SR Ultimate (pivot + opsiyonel POC)
    # Pine SR — hızlandırılmış parametreler:
    # left=10 (50'den), right=3 (20'den) → pivot teyidi 45 dakika (5 saat yerine)
    # SİNYAL için değil: SL anchor + yapısal bağlam için kullanılır
    V3_SR_ENABLED = os.getenv("V3_SR_ENABLED", "true").lower() in ("1", "true", "yes")
    V3_SR_USE_PIVOT = os.getenv("V3_SR_USE_PIVOT", "true").lower() in ("1", "true", "yes")
    V3_SR_USE_POC = os.getenv("V3_SR_USE_POC", "false").lower() in ("1", "true", "yes")
    V3_SR_SOURCE = os.getenv("V3_SR_SOURCE", "close").lower()
    # right=8 orta nokta: right=3 çok hassas (band jitter), right=20 çok yavaş.
    # ~2 saat pivot teyidi — bandı genişletir, gürültüyü azaltır, hâlâ güncel.
    V3_SR_LOOKBACK_LEFT = int(os.getenv("V3_SR_LOOKBACK_LEFT", "15"))   # 50 → 15
    V3_SR_LOOKBACK_RIGHT = int(os.getenv("V3_SR_LOOKBACK_RIGHT", "8"))  # 20 → 8
    V3_SR_QUICK_RIGHT = int(os.getenv("V3_SR_QUICK_RIGHT", "4"))        # 10 → 4
    # Real-time swing yapısı — indikatör değil, anlık fiyat hareketi
    V3_SWING_BAND_ENABLED = os.getenv("V3_SWING_BAND_ENABLED", "true").lower() in ("1", "true", "yes")
    V3_SWING_BAND_MAX_BARS = int(os.getenv("V3_SWING_BAND_MAX_BARS", "10"))  # son N bar swing
    # Aktif bant = zone katmanlari (supply_major / demand); persist/oturum ezmesin
    V3_LAYER_BAND_ACTIVE = os.getenv("V3_LAYER_BAND_ACTIVE", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    V3_SR_SKIP_PERSIST = os.getenv("V3_SR_SKIP_PERSIST", "false").lower() in (
        "1",
        "true",
        "yes",
    )
    V3_SR_ACTIVE_BAND = os.getenv("V3_SR_ACTIVE_BAND", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    # Makro band (persist) + en yakin ust direnc ile dar trade band
    V3_TRADE_BAND_ENABLED = os.getenv("V3_TRADE_BAND_ENABLED", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    # Pinli trade band: destekten ilk yukari bacak — direnc (TP) henuz test edilmemis olsa da LONG
    V3_TRADE_BAND_FIRST_LEG_LONG = os.getenv(
        "V3_TRADE_BAND_FIRST_LEG_LONG", "true"
    ).lower() in ("1", "true", "yes")
    # Pinli trade band: direncten ilk asagi bacak — destek (TP) henuz test edilmemis olsa da SHORT
    V3_TRADE_BAND_FIRST_LEG_SHORT = os.getenv(
        "V3_TRADE_BAND_FIRST_LEG_SHORT", "true"
    ).lower() in ("1", "true", "yes")
    V3_SR_CHART_MAX_LINES = int(os.getenv("V3_SR_CHART_MAX_LINES", "6"))
    V3_SR_CHART_SHOW_PREV_QUICK = os.getenv(
        "V3_SR_CHART_SHOW_PREV_QUICK", "false"
    ).lower() in ("1", "true", "yes")
    V3_SR_COLOR_SUPPORT = os.getenv("V3_SR_COLOR_SUPPORT", "#e6c200")
    V3_SR_COLOR_RESISTANCE = os.getenv("V3_SR_COLOR_RESISTANCE", "#ef5350")
    # Pine bire bir 6 cizgiye ek: onceki quick support'u (occurrence=1) ek cizgi olarak goster.
    V3_SR_INCLUDE_PREV_QUICK_SUPPORT = os.getenv(
        "V3_SR_INCLUDE_PREV_QUICK_SUPPORT", "true"
    ).lower() in ("1", "true", "yes")
    # quick_prev (occurrence=1) aktif bant + kirilim referansi — or. TV ~1975
    V3_SR_PREV_QUICK_FOR_ACTIVE_BAND = os.getenv(
        "V3_SR_PREV_QUICK_FOR_ACTIVE_BAND", "true"
    ).lower() in ("1", "true", "yes")
    # false = yapisal destek (L1/L3/L5 + quick_prev); true = yalnizca TV renk kurali
    V3_SR_USE_PINE_DIRECTION_BAND = os.getenv(
        "V3_SR_USE_PINE_DIRECTION_BAND", "false"
    ).lower() in ("1", "true", "yes")
    V3_SR_MERGE_TOL_PCT = float(os.getenv("V3_SR_MERGE_TOL_PCT", "0.0006"))
    # true = seviyeler YALNIZCA Pine SR pivot (zone/swing karistirma yok)
    V3_SR_ONLY = os.getenv("V3_SR_ONLY", "true").lower() in ("1", "true", "yes")
    V3_SR_NUM_LEVELS = int(os.getenv("V3_SR_NUM_LEVELS", "6"))
    V3_SR_1H_LOOKBACK_LEFT = int(os.getenv("V3_SR_1H_LOOKBACK_LEFT", "50"))
    V3_SR_1H_LOOKBACK_RIGHT = int(os.getenv("V3_SR_1H_LOOKBACK_RIGHT", "20"))
    V3_SR_1H_QUICK_RIGHT = int(os.getenv("V3_SR_1H_QUICK_RIGHT", "10"))
    V3_SR_1H_NUM_LEVELS = int(os.getenv("V3_SR_1H_NUM_LEVELS", "6"))
    V3_SR_TOUCH_RANGE_PCT = float(os.getenv("V3_SR_TOUCH_RANGE_PCT", "0.001"))
    # Spot fiyata yapışık pivot trade destegi/direnci sayilmasin (~0.1% = 2$ @ 2000)
    V3_SR_NEAR_PRICE_PCT = float(os.getenv("V3_SR_NEAR_PRICE_PCT", "0.001"))
    # Aktif bant: en yakin S/R en az bu kadar uzak (0.0025 ~ 5$ @ 2000)
    V3_SR_ACTIVE_MIN_SEP_PCT = float(os.getenv("V3_SR_ACTIVE_MIN_SEP_PCT", "0.0025"))
    V3_SR_POC_LOOKBACK = int(os.getenv("V3_SR_POC_LOOKBACK", "5"))
    # Pine L=50 R=20 için yeterli geçmiş (15m ~500 mum ≈ 5 gün)
    V3_CHART_BACKFILL_15M = int(os.getenv("V3_CHART_BACKFILL_15M", "500"))
    V3_CHART_BACKFILL_1H = int(os.getenv("V3_CHART_BACKFILL_1H", "150"))
    V3_STRUCTURE_SWING_COUNT = int(os.getenv("V3_STRUCTURE_SWING_COUNT", "6"))
    V3_SHELF_MIN_BARS = int(os.getenv("V3_SHELF_MIN_BARS", "3"))
    V3_LEVEL_MAX_AGE_1H = int(os.getenv("V3_LEVEL_MAX_AGE_1H", "150"))
    V3_LEVEL_MAX_AGE_15M = int(os.getenv("V3_LEVEL_MAX_AGE_15M", "500"))
    V3_LEVEL_SCORE_STRONG = int(os.getenv("V3_LEVEL_SCORE_STRONG", "6"))
    V3_LEVEL_SCORE_MEDIUM = int(os.getenv("V3_LEVEL_SCORE_MEDIUM", "4"))
    V3_LEVEL_SCORE_WEAK = int(os.getenv("V3_LEVEL_SCORE_WEAK", "3"))
    V3_LEVEL_CHANGE_LOG = os.getenv("V3_LEVEL_CHANGE_LOG", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    # Zone: destek/dirence yakinlik = bant_genisligi * oran (or. 28$ * 0.35 = 9.8$)
    V3_ZONE_RATIO = float(os.getenv("V3_ZONE_RATIO", "0.35"))
    # Kanal teyidi: swing'in aktif S/R fiyatina yakinligi (0.003 = ±%0.3)
    V3_CHANNEL_BAND_PCT = float(os.getenv("V3_CHANNEL_BAND_PCT", "0.003"))
    # Tek yonlu dusus/yukselis: son N 15m'de hem destek hem dirence dokunma = kanal traverse
    V3_CHANNEL_TRAVERSE_BARS = int(os.getenv("V3_CHANNEL_TRAVERSE_BARS", "48"))
    # Traverse (S+R dokunma) Kosul 4 yerine gecer mi
    V3_CHANNEL_TRAVERSE_TP_OK = os.getenv(
        "V3_CHANNEL_TRAVERSE_TP_OK", "true"
    ).lower() in ("1", "true", "yes")
    # Senaryo WAIT iken kanal traverse + range valid → entry üret
    V3_CHANNEL_ENTRY_ENABLED = os.getenv(
        "V3_CHANNEL_ENTRY_ENABLED", "true"
    ).lower() in ("1", "true", "yes")
    # Zone kenar: band % (0.20 = alt/ust %20) + dirence/destege mesafe %
    V3_ZONE_EDGE_FRAC = float(os.getenv("V3_ZONE_EDGE_FRAC", "0.20"))
    V3_ZONE_EDGE_DIST_PCT = float(os.getenv("V3_ZONE_EDGE_DIST_PCT", "0.012"))
    # Cold start: persist trade R, Pine/SR taze direncin gerisinde kalirsa guncelle
    V3_TRADE_BAND_FRESH_SR = os.getenv("V3_TRADE_BAND_FRESH_SR", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    V3_PERSIST_TRADE_R_STALE_PCT = float(
        os.getenv("V3_PERSIST_TRADE_R_STALE_PCT", "0.008")
    )
    V3_RANGE_REQUIRE_TP_TEST = os.getenv(
        "V3_RANGE_REQUIRE_TP_TEST", "true"
    ).lower() in ("1", "true", "yes")
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
    # Aktif bant: 1h swing destek + 1h swing direnc (makro kanal)
    # false = aktif S/R grafik katmanlari (supply_major / demand); true = lifecycle mikro bant
    V3_LIFECYCLE_ACTIVE_BAND = os.getenv("V3_LIFECYCLE_ACTIVE_BAND", "false").lower() in (
        "1",
        "true",
        "yes",
    )
    V3_ACTIVE_BAND_HTF = os.getenv("V3_ACTIVE_BAND_HTF", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    # 1h+15m ortusme: merge skor carpani
    V3_HTF_CONFLUENCE_MULT = int(os.getenv("V3_HTF_CONFLUENCE_MULT", "2"))
    # Kanitli kenar: zone + range giris icin min dokunus / min ret
    V3_MIN_RELIABILITY_TOUCHES = int(os.getenv("V3_MIN_RELIABILITY_TOUCHES", "2"))
    V3_MIN_RELIABILITY_REJECTIONS = int(os.getenv("V3_MIN_RELIABILITY_REJECTIONS", "2"))
    # Rol degisimi (kirilan R->S vb.) hafiza suresi
    V3_FLIP_MAX_AGE_SEC = int(os.getenv("V3_FLIP_MAX_AGE_SEC", str(48 * 3600)))
    # Bolge yaricapi (fiyat * pct); bar_noise ile genisletilir
    V3_ZONE_HALF_PCT = float(os.getenv("V3_ZONE_HALF_PCT", "0.0025"))
    # Bolge yasam dongusu (acceptance / strength / status)
    V3_ZONE_LIFECYCLE = os.getenv("V3_ZONE_LIFECYCLE", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    V3_ZONE_ACCEPT_BARS = int(os.getenv("V3_ZONE_ACCEPT_BARS", "3"))
    V3_ZONE_MIN_STRENGTH = int(os.getenv("V3_ZONE_MIN_STRENGTH", "40"))
    # Zone max age: tarihsel zone birikimini azalt (72h → 4h)
    # Sadece güncel yapısal bölgeler kullanılır
    V3_ZONE_MAX_AGE_SEC = int(os.getenv("V3_ZONE_MAX_AGE_SEC", str(4 * 3600)))
    V3_ZONE_W_TOUCH = int(os.getenv("V3_ZONE_W_TOUCH", "5"))
    V3_ZONE_W_REJECTION = int(os.getenv("V3_ZONE_W_REJECTION", "10"))
    V3_ZONE_W_SWEEP = int(os.getenv("V3_ZONE_W_SWEEP", "15"))
    V3_ZONE_W_ACCEPT = int(os.getenv("V3_ZONE_W_ACCEPT", "20"))
    V3_ZONE_STRENGTH_CAP = int(os.getenv("V3_ZONE_STRENGTH_CAP", "130"))
    V3_ZONE_TOUCH_CAP = int(os.getenv("V3_ZONE_TOUCH_CAP", "6"))
    V3_ZONE_AGE_BOOST_HOURS = float(os.getenv("V3_ZONE_AGE_BOOST_HOURS", "12"))
    V3_ZONE_TREND_BIAS_DOWN = float(os.getenv("V3_ZONE_TREND_BIAS_DOWN", "0.002"))
    V3_ZONE_TREND_BIAS_UP = float(os.getenv("V3_ZONE_TREND_BIAS_UP", "0.0015"))
    # Kirilim kalitesi (mum sayisi + displacement + hacim)
    V3_ZONE_BREAK_MIN_CLOSES = int(os.getenv("V3_ZONE_BREAK_MIN_CLOSES", "2"))
    V3_ZONE_BREAK_MIN_DISP = float(os.getenv("V3_ZONE_BREAK_MIN_DISP", "1.5"))
    V3_ZONE_BREAK_MIN_SCORE = float(os.getenv("V3_ZONE_BREAK_MIN_SCORE", "4.0"))
    V3_ZONE_TRANSITION_SCORE = float(os.getenv("V3_ZONE_TRANSITION_SCORE", "2.0"))
    V3_ZONE_RETEST_REJECTIONS = int(os.getenv("V3_ZONE_RETEST_REJECTIONS", "2"))
    V3_ZONE_RETEST_VOL_RATIO = float(os.getenv("V3_ZONE_RETEST_VOL_RATIO", "0.85"))
    # Likidite hedefi / kovalama blok
    V3_LIQ_WEEKLY_BARS = int(os.getenv("V3_LIQ_WEEKLY_BARS", "168"))
    V3_LIQ_CHASE_MIN_SCORE = int(os.getenv("V3_LIQ_CHASE_MIN_SCORE", "70"))
    V3_LIQ_CHASE_RATIO = float(os.getenv("V3_LIQ_CHASE_RATIO", "1.3"))
    V3_VACUUM_MIN_SPAN_BODY = float(os.getenv("V3_VACUUM_MIN_SPAN_BODY", "3.0"))
    V3_VACUUM_MAX_REACTION_RATIO = float(os.getenv("V3_VACUUM_MAX_REACTION_RATIO", "0.28"))
    # Zone hafiza (silme yok — ARCHIVED)
    V3_ZONE_ARCHIVE_MAX_AGE_SEC = int(os.getenv("V3_ZONE_ARCHIVE_MAX_AGE_SEC", str(15 * 24 * 3600)))
    V3_ZONE_ARCHIVE_MIN_AGE_HOURS = float(os.getenv("V3_ZONE_ARCHIVE_MIN_AGE_HOURS", "6"))
    V3_SWING_SUPPORT_BARS = int(os.getenv("V3_SWING_SUPPORT_BARS", "8"))
    V3_STORY_IMPULSE_MIN_PCT = float(os.getenv("V3_STORY_IMPULSE_MIN_PCT", "0.55"))
    V3_STORY_BOUNCE_MAX_RATIO = float(os.getenv("V3_STORY_BOUNCE_MAX_RATIO", "0.52"))
    V3_STORY_COMPRESSION_PCT = float(os.getenv("V3_STORY_COMPRESSION_PCT", "0.35"))
    # Unified market state windows
    V3_STRUCTURE_BARS = int(os.getenv("V3_STRUCTURE_BARS", "96"))
    V3_LIQ_MICRO_BARS = int(os.getenv("V3_LIQ_MICRO_BARS", "24"))
    V3_LIQ_MACRO_BARS = int(os.getenv("V3_LIQ_MACRO_BARS", "72"))
    V3_EVENT_BARS = int(os.getenv("V3_EVENT_BARS", "32"))
    V3_EVENT_COMPRESSION_BARS = int(os.getenv("V3_EVENT_COMPRESSION_BARS", "6"))
    V3_UNIFIED_STATE = os.getenv("V3_UNIFIED_STATE", "true").lower() in ("1", "true", "yes")
    V3_COLLAPSE_W_LIQUIDITY = float(os.getenv("V3_COLLAPSE_W_LIQUIDITY", "0.50"))
    V3_COLLAPSE_W_EVENT = float(os.getenv("V3_COLLAPSE_W_EVENT", "0.30"))
    V3_COLLAPSE_W_STRUCTURE = float(os.getenv("V3_COLLAPSE_W_STRUCTURE", "0.20"))
    V3_COLLAPSE_ACTIVE = int(os.getenv("V3_COLLAPSE_ACTIVE", "70"))
    V3_COLLAPSE_TRANSITION = int(os.getenv("V3_COLLAPSE_TRANSITION", "40"))
    V3_COLLAPSE_STRUCTURE_CONTROL = int(os.getenv("V3_COLLAPSE_STRUCTURE_CONTROL", "80"))
    V3_COLLAPSE_EVENT_OVERRIDE = int(os.getenv("V3_COLLAPSE_EVENT_OVERRIDE", "85"))
    V3_COLLAPSE_LIQ_MIN_DIRECTION = int(os.getenv("V3_COLLAPSE_LIQ_MIN_DIRECTION", "50"))
    # Physics realism: zaman, kalite, fractal, execution, adaptation
    V3_EVENT_DECAY_RATE = float(os.getenv("V3_EVENT_DECAY_RATE", "0.85"))
    V3_EVENT_MAX_AGE_HOURS = float(os.getenv("V3_EVENT_MAX_AGE_HOURS", "4.0"))
    V3_EVENT_MIN_DECAYED_STRENGTH = float(os.getenv("V3_EVENT_MIN_DECAYED_STRENGTH", "22.0"))
    V3_STRUCTURE_MID_BARS = int(os.getenv("V3_STRUCTURE_MID_BARS", "48"))
    V3_STRUCTURE_MICRO_BARS = int(os.getenv("V3_STRUCTURE_MICRO_BARS", "16"))
    V3_LIQ_MIN_QUALITY_DIRECTION = int(os.getenv("V3_LIQ_MIN_QUALITY_DIRECTION", "40"))
    V3_EXEC_WINDOW_SCALE = float(os.getenv("V3_EXEC_WINDOW_SCALE", "0.06"))
    V3_EXEC_WINDOW_MIN_BARS = int(os.getenv("V3_EXEC_WINDOW_MIN_BARS", "1"))
    V3_EXEC_WINDOW_MAX_BARS = int(os.getenv("V3_EXEC_WINDOW_MAX_BARS", "5"))
    V3_EXEC_WINDOW_MIN_EVENT = float(os.getenv("V3_EXEC_WINDOW_MIN_EVENT", "35.0"))
    V3_EXEC_WINDOW_GATE_ENTRY = os.getenv("V3_EXEC_WINDOW_GATE_ENTRY", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    V3_ADAPTATION_ENABLED = os.getenv("V3_ADAPTATION_ENABLED", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    V3_ADAPTATION_STEP = float(os.getenv("V3_ADAPTATION_STEP", "0.02"))
    V3_ADAPTATION_MIN_W = float(os.getenv("V3_ADAPTATION_MIN_W", "0.08"))
    V3_ADAPTATION_MAX_W = float(os.getenv("V3_ADAPTATION_MAX_W", "0.65"))
    V3_URGENCY_NOW_PRESSURE = float(os.getenv("V3_URGENCY_NOW_PRESSURE", "72"))
    V3_URGENCY_LATER_PRESSURE = float(os.getenv("V3_URGENCY_LATER_PRESSURE", "42"))
    V3_URGENCY_BYPASS_EM = os.getenv("V3_URGENCY_BYPASS_EM", "false").lower() in (
        "1",
        "true",
        "yes",
    )
    V3_EXEC_TIMING_GATE = os.getenv("V3_EXEC_TIMING_GATE", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    V3_EXEC_BLOCK_ON_LATER = os.getenv("V3_EXEC_BLOCK_ON_LATER", "false").lower() in (
        "1",
        "true",
        "yes",
    )
    V3_LIFETIME_INSTANT = float(os.getenv("V3_LIFETIME_INSTANT", "70"))
    V3_LIFETIME_READY_PLUS = float(
        os.getenv("V3_LIFETIME_READY_PLUS", os.getenv("V3_LIFETIME_READY", "62"))
    )
    V3_LIFETIME_READY_MINUS = float(os.getenv("V3_LIFETIME_READY_MINUS", "58"))
    V3_LIFETIME_READY = float(
        os.getenv("V3_LIFETIME_READY", os.getenv("V3_LIFETIME_READY_MINUS", "58"))
    )
    V3_LIFETIME_WATCH = float(os.getenv("V3_LIFETIME_WATCH", "48"))
    V3_LIFETIME_CONDITIONAL = float(
        os.getenv("V3_LIFETIME_CONDITIONAL", os.getenv("V3_LIFETIME_WATCH", "48"))
    )
    V3_INERTIA_MIN_CONTINUATION = int(os.getenv("V3_INERTIA_MIN_CONTINUATION", "58"))
    V3_TRAP_MIN_REACTION = float(os.getenv("V3_TRAP_MIN_REACTION", "0.45"))
    V3_EXTREME_FALLBACK_ENABLED = os.getenv("V3_EXTREME_FALLBACK_ENABLED", "false").lower() in (
        "1",
        "true",
        "yes",
    )
    V3_ZONE_MEMORY_REACTIVATE_PCT = float(os.getenv("V3_ZONE_MEMORY_REACTIVATE_PCT", "0.004"))
    V3_ZONE_MEMORY_REACTIVATE_STRENGTH = int(os.getenv("V3_ZONE_MEMORY_REACTIVATE_STRENGTH", "35"))
    V3_ZONE_TIME_FACTOR_WEIGHT = float(os.getenv("V3_ZONE_TIME_FACTOR_WEIGHT", "0.5"))
    # Cluster
    V3_CLUSTER_GAP_PCT = float(os.getenv("V3_CLUSTER_GAP_PCT", "0.0035"))
    # Multi-TF trend
    V3_TREND_W_4H = float(os.getenv("V3_TREND_W_4H", "0.50"))
    V3_TREND_W_1H = float(os.getenv("V3_TREND_W_1H", "0.35"))
    V3_TREND_W_15M = float(os.getenv("V3_TREND_W_15M", "0.15"))
    V3_TREND_4H_BARS = int(os.getenv("V3_TREND_4H_BARS", "6"))
    V3_TREND_4H_MIN_MOVE = float(os.getenv("V3_TREND_4H_MIN_MOVE", "0.004"))
    # Expected move
    V3_EXPECTED_MOVE_BOOST_RR = float(os.getenv("V3_EXPECTED_MOVE_BOOST_RR", "2.0"))
    V3_EXPECTED_MOVE_PRIORITY_BOOST = int(os.getenv("V3_EXPECTED_MOVE_PRIORITY_BOOST", "20"))
    V3_EXPECTED_MOVE_MIN_PRIORITY = int(os.getenv("V3_EXPECTED_MOVE_MIN_PRIORITY", "25"))
    # Trade attribution (olcum — yeni ozellik degil)
    V3_ATTRIBUTION_ENABLED = os.getenv("V3_ATTRIBUTION_ENABLED", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    V3_ATTRIBUTION_LOG_SEC = float(os.getenv("V3_ATTRIBUTION_LOG_SEC", "90"))
    # Pozisyon acilmama — tek satir [NO_TRADE] (bot.log)
    V3_NO_TRADE_LOG_ENABLED = os.getenv("V3_NO_TRADE_LOG_ENABLED", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    V3_NO_TRADE_LOG_SEC = float(os.getenv("V3_NO_TRADE_LOG_SEC", "60"))
    # WAIT — tek REJECT_REASON + sayac (scripts/reject_reason_report.py)
    V3_REJECT_REASON_ENABLED = os.getenv("V3_REJECT_REASON_ENABLED", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    V3_REJECT_LOG_SEC = float(os.getenv("V3_REJECT_LOG_SEC", "45"))
    V3_REJECT_REPORT_EVERY = int(os.getenv("V3_REJECT_REPORT_EVERY", "100"))
    V3_REJECT_COUNT_SCENARIO_WAIT = os.getenv(
        "V3_REJECT_COUNT_SCENARIO_WAIT", "false"
    ).lower() in ("1", "true", "yes")
    # LONG/SHORT yon skoru logu (her sinyal)
    V3_DIRECTION_SCORE_LOG_ENABLED = os.getenv(
        "V3_DIRECTION_SCORE_LOG_ENABLED", "true"
    ).lower() in ("1", "true", "yes")
    V3_DIRECTION_SCORE_LOG_SEC = float(os.getenv("V3_DIRECTION_SCORE_LOG_SEC", "30"))
    V3_DIRECTION_SCORE_MIN_EDGE = int(os.getenv("V3_DIRECTION_SCORE_MIN_EDGE", "10"))
    # Olasilik tabanli karar (veto kapali) — varsayilan acik
    V3_SCORE_DECISION_ENABLED = os.getenv(
        "V3_SCORE_DECISION_ENABLED", "true"
    ).lower() in ("1", "true", "yes")
    V3_THESIS_DECISION_ENABLED = os.getenv(
        "V3_THESIS_DECISION_ENABLED", "true"
    ).lower() in ("1", "true", "yes")
    V3_SCORE_BASE = float(os.getenv("V3_SCORE_BASE", "50"))
    V3_PROB_ENTRY_THRESHOLD = float(os.getenv("V3_PROB_ENTRY_THRESHOLD", "0.65"))
    V3_CVD_NORM = float(os.getenv("V3_CVD_NORM", "8000"))
    V3_STRUCTURE_MAX_CONTRIB = float(os.getenv("V3_STRUCTURE_MAX_CONTRIB", "25"))
    V3_STRUCTURE_OPPOSE_PENALTY = float(os.getenv("V3_STRUCTURE_OPPOSE_PENALTY", "0.35"))
    V3_STRUCTURE_MIN_PENALTY = float(os.getenv("V3_STRUCTURE_MIN_PENALTY", "8"))
    V3_SCORE_ATTRIBUTION_LOG = os.getenv(
        "V3_SCORE_ATTRIBUTION_LOG", "true"
    ).lower() in ("1", "true", "yes")
    # Son N karar: modul katki % ortalamasi (Structure+Trend cift sayim tespiti)
    V3_ATTRIBUTION_ROLLING_WINDOW = int(
        os.getenv("V3_ATTRIBUTION_ROLLING_WINDOW", "1000")
    )
    V3_ATTRIBUTION_ROLLING_LOG_EVERY = int(
        os.getenv("V3_ATTRIBUTION_ROLLING_LOG_EVERY", "50")
    )
    V3_TREND_MAX_CONTRIB = float(os.getenv("V3_TREND_MAX_CONTRIB", "28"))
    V3_TREND_CONT_BREAK_MIN_UNIT = float(
        os.getenv("V3_TREND_CONT_BREAK_MIN_UNIT", "0.12")
    )
    # Structure zaten gucluyken Trend puanini kisalt (0=kapali, 1=tam)
    V3_STRUCTURE_TREND_OVERLAP_DISCOUNT = float(
        os.getenv("V3_STRUCTURE_TREND_OVERLAP_DISCOUNT", "0.45")
    )
    # Ayni kararda Structure+Trend toplam puan tavanı (0=kapali)
    V3_STRUCTURE_TREND_MAX_COMBINED_POINTS = float(
        os.getenv("V3_STRUCTURE_TREND_MAX_COMBINED_POINTS", "32")
    )
    # Kirilim sonrasi karsi taraf skor decay (ATR yok — nefes/kanal birimi)
    V3_BREAK_DECAY_ENABLED = os.getenv("V3_BREAK_DECAY_ENABLED", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    V3_BREAK_DECAY_MODULES = os.getenv(
        "V3_BREAK_DECAY_MODULES", "zone,liquidity,trend,volume"
    )
    V3_BREAK_DECAY_BREATH_MULT = float(
        os.getenv("V3_BREAK_DECAY_BREATH_MULT", "1.0")
    )
    V3_BREAK_DECAY_BAND_FRAC = float(os.getenv("V3_BREAK_DECAY_BAND_FRAC", "0.10"))
    V3_BREAK_DECAY_MIN_BPS = float(os.getenv("V3_BREAK_DECAY_MIN_BPS", "8"))
    V3_BREAK_DECAY_FLOOR = float(os.getenv("V3_BREAK_DECAY_FLOOR", "0.08"))
    V3_BREAK_DECAY_EXP_SCALE = float(os.getenv("V3_BREAK_DECAY_EXP_SCALE", "1.0"))
    # Rejim baskın yön — destek üstü bounce'ta karsi taraf (kirilim olmadan)
    V3_REGIME_DECAY_ENABLED = os.getenv("V3_REGIME_DECAY_ENABLED", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    V3_REGIME_DECAY_SCALE = float(os.getenv("V3_REGIME_DECAY_SCALE", "0.55"))
    V3_REGIME_DECAY_MIN_STRENGTH = float(
        os.getenv("V3_REGIME_DECAY_MIN_STRENGTH", "0.42")
    )
    V3_REGIME_DECAY_REJECTION_BONUS = float(
        os.getenv("V3_REGIME_DECAY_REJECTION_BONUS", "0.28")
    )
    V3_REGIME_DECAY_COUNTER_TREND_BONUS = float(
        os.getenv("V3_REGIME_DECAY_COUNTER_TREND_BONUS", "0.12")
    )
    V3_REGIME_DECAY_PROXIMITY_BAND = float(
        os.getenv("V3_REGIME_DECAY_PROXIMITY_BAND", "0.80")
    )
    V3_REGIME_DECAY_PROXIMITY_WEIGHT = float(
        os.getenv("V3_REGIME_DECAY_PROXIMITY_WEIGHT", "1.15")
    )
    V3_SCORE_SHORT_TP_BAND_FRAC = float(os.getenv("V3_SCORE_SHORT_TP_BAND_FRAC", "0.18"))
    V3_SCORE_LONG_TP_BAND_FRAC = float(os.getenv("V3_SCORE_LONG_TP_BAND_FRAC", "0.18"))
    V3_SCORE_SHORT_SL_BAND_FRAC = float(os.getenv("V3_SCORE_SHORT_SL_BAND_FRAC", "0.035"))
    # Esit tepe/dip kumesi
    V3_EQUAL_CLUSTER_MIN = int(os.getenv("V3_EQUAL_CLUSTER_MIN", "2"))
    V3_EQUAL_CLUSTER_SCORE = int(os.getenv("V3_EQUAL_CLUSTER_SCORE", "3"))
    # 15m kirilim + 1h duvar bu kadar yakin (bps) -> breakout veto
    V3_HTF_WALL_MAX_BPS = float(os.getenv("V3_HTF_WALL_MAX_BPS", "65"))
    V3_HTF_WALL_MIN_SEP_BPS = float(os.getenv("V3_HTF_WALL_MIN_SEP_BPS", "8"))
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
    # Reverse (flip) sinyal için min SHORT/LONG olasılık eşiği
    V3_REVERSE_MIN_SCORE_PROB = float(os.getenv("V3_REVERSE_MIN_SCORE_PROB", "55.0"))
    # Swing high fallback için min RR (normal RR'den daha gevşek olabilir)
    V3_REVERSE_SWING_HIGH_MIN_RR = float(os.getenv("V3_REVERSE_SWING_HIGH_MIN_RR", "1.5"))
    # Ekstrem CVD ters-akış vetosu (range girişlerde squeeze önlemi)
    V3_CVD_COUNTERFLOW_SHORT = float(os.getenv("V3_CVD_COUNTERFLOW_SHORT", "0.70"))
    V3_CVD_COUNTERFLOW_LONG = float(os.getenv("V3_CVD_COUNTERFLOW_LONG", "0.30"))
    # Dar bant + güçlü yön: RR eşiği gevşetme
    V3_NARROW_BAND_PCT = float(os.getenv("V3_NARROW_BAND_PCT", "0.02"))      # bant <%2 = dar
    V3_STRONG_DIR_PROB = float(os.getenv("V3_STRONG_DIR_PROB", "65.0"))      # prob >=%65 = güçlü
    V3_MIN_RR_NARROW_STRONG = float(os.getenv("V3_MIN_RR_NARROW_STRONG", "1.5"))
    # Breakeven SL: pozisyon %X kâra geçince SL entry'ye çekil
    V3_SL_BREAKEVEN_ENABLED = os.getenv("V3_SL_BREAKEVEN_ENABLED", "true").lower() in ("1", "true", "yes")
    V3_SL_BREAKEVEN_TRIGGER_PCT = float(os.getenv("V3_SL_BREAKEVEN_TRIGGER_PCT", "0.6"))
    V3_SL_BREAKEVEN_BUFFER_BPS = float(os.getenv("V3_SL_BREAKEVEN_BUFFER_BPS", "5"))
    # Dar trade-band'da giriş SL'ini uzak swing yerine aktif banda sabitle
    V3_SL_BAND_CLAMP_ENABLED = os.getenv("V3_SL_BAND_CLAMP_ENABLED", "true").lower() in ("1", "true", "yes")
    V3_SL_BAND_CLAMP_BUFFER_BPS = float(os.getenv("V3_SL_BAND_CLAMP_BUFFER_BPS", "30"))
    # Min SL mesafe tabanı: SL girişe %X'ten yakınsa giriş geçersiz (gürültü-stop önlemi)
    V3_MIN_SL_DIST_PCT = float(os.getenv("V3_MIN_SL_DIST_PCT", "0.25"))
    # Yapı skoru kaynağı: "swing" (BOS/CHoCH, anlık) | "collapse" (eski, yapışkan)
    V3_STRUCTURE_SOURCE = os.getenv("V3_STRUCTURE_SOURCE", "swing")
    V3_STRUCT_PIVOT_BARS = int(os.getenv("V3_STRUCT_PIVOT_BARS", "60"))
    V3_STRUCT_PIVOT_LEFT = int(os.getenv("V3_STRUCT_PIVOT_LEFT", "3"))
    V3_STRUCT_PIVOT_RIGHT = int(os.getenv("V3_STRUCT_PIVOT_RIGHT", "3"))
    V3_STRUCT_BREAK_BUFFER_BPS = float(os.getenv("V3_STRUCT_BREAK_BUFFER_BPS", "5"))
    # Tradeability / Conviction Gate: skor ne derse desin işlenebilirlik kapısı
    V3_TRADEABILITY_GATE_ENABLED = os.getenv("V3_TRADEABILITY_GATE_ENABLED", "true").lower() in ("1", "true", "yes")
    V3_TRADEABLE_REQUIRE_FLOW_ALIGN = os.getenv("V3_TRADEABLE_REQUIRE_FLOW_ALIGN", "true").lower() in ("1", "true", "yes")
    V3_TRADEABLE_MIN_BAND_PCT = float(os.getenv("V3_TRADEABLE_MIN_BAND_PCT", "0.006"))   # bant <%0.6 = dar
    V3_TRADEABLE_MIN_CONVICTION = float(os.getenv("V3_TRADEABLE_MIN_CONVICTION", "70"))  # collapse state_score
    V3_TRADEABLE_MIN_FLOW_EDGE = float(os.getenv("V3_TRADEABLE_MIN_FLOW_EDGE", "0.03"))  # |buy_ratio-0.5|
    V3_TRADEABLE_CVD_CUM_COUNTER = float(os.getenv("V3_TRADEABLE_CVD_CUM_COUNTER", "4000"))  # kümülatif akış counterflow eşiği
    # Tez yolu min edge: yön olasılığı bunun altındaysa giriş yok (50/50 = edge yok)
    V3_THESIS_MIN_PROB = float(os.getenv("V3_THESIS_MIN_PROB", "0.55"))
    # Açılış ısınması (veri tazeliği): canlı akış + ilk 5m kapanış beklenir
    V3_STARTUP_WARMUP_ENABLED = os.getenv("V3_STARTUP_WARMUP_ENABLED", "true").lower() in ("1", "true", "yes")
    V3_STARTUP_WARMUP_MIN_SEC = float(os.getenv("V3_STARTUP_WARMUP_MIN_SEC", "150"))
    V3_STARTUP_REQUIRE_5M_CLOSE = os.getenv("V3_STARTUP_REQUIRE_5M_CLOSE", "true").lower() in ("1", "true", "yes")
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

    BALANCE_API_TIMEOUT_SEC = float(os.getenv("BALANCE_API_TIMEOUT_SEC", "25"))
    API_CONNECT_TIMEOUT_SEC = float(os.getenv("API_CONNECT_TIMEOUT_SEC", "15"))
    API_REQUEST_TIMEOUT_SEC = float(os.getenv("API_REQUEST_TIMEOUT_SEC", "25"))
    API_RETRY_COUNT = int(os.getenv("API_RETRY_COUNT", "3"))
    # API yokken botu kapatma; reconcile atlanir, arka planda tekrar dener
    STARTUP_CONTINUE_ON_API_FAIL = os.getenv(
        "STARTUP_CONTINUE_ON_API_FAIL", "true"
    ).lower() in ("1", "true", "yes")

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
    # TP1 runner SL mark'a cok yakin tasinmasin; retest nefesi birakir.
    RUNNER_SL_MIN_MARK_BUFFER_BPS = float(
        os.getenv("RUNNER_SL_MIN_MARK_BUFFER_BPS", "80")
    )
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
    # TP2 borsa emri gönderilmez; runner yalnızca SL ile (TP1 sonrası 15m trail).
    # Eski .env değerleri TP2'yi yanlışlıkla tekrar açmasın diye kod seviyesinde kapalı.
    SEND_TP2_ORDER = False
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
    # Mevcut TP1 bu mesafenin altindaysa startup/runtime yaklastirma yapilmaz (lokal S/R korunur)
    TP_ADJUST_MIN_OVERSHOOT_BPS = float(os.getenv("TP_ADJUST_MIN_OVERSHOOT_BPS", "300"))

    # İzleme: API emri yok, iç simülasyon (PAPER_MODE=true veya api_key yok)
    PAPER_MODE = os.getenv("PAPER_MODE", "").lower() in ("1", "true", "yes")
    PAPER_BALANCE_USD = float(os.getenv("PAPER_BALANCE_USD", "10000"))

    REST = (
        os.getenv("BINANCE_FAPI_REST", "").strip()
        or (
            "https://testnet.binancefuture.com"
            if TESTNET
            else "https://fapi.binance.com"
        )
    )

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
