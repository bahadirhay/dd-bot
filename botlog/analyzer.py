from __future__ import annotations
"""
botlog/analyzer.py

Logları okur, analiz eder, Telegram'a bildirir.
Günde 4 kez otomatik çalışır (06:00, 12:00, 18:00, 00:00 UTC).

Ne analiz eder:
  - Son periyotta kaç sinyal, kaçı girildi, kaçı girilmedi ve neden
  - Trade performansı: WR, PnL, ortalama süre
  - Rejim dağılımı: ne kadar TREND, ne kadar RANGE
  - CVD diverjans tespiti doğru muydu
  - Zarar eden tradelerin ortak özelliği var mı
  - Sistem hatası var mı
"""
import time
from datetime import datetime, timezone
from botlog.db import get_recent_signals, get_recent_trades, get_stats
from core.config import cfg
from core.shutdown import is_stopping
from core.async_sleep import stoppable_sleep
from core.logger import get_logger

log = get_logger("Analyzer")


def _pct(n, total) -> str:
    if not total: return "0%"
    return f"{n/total*100:.0f}%"


def analyze(hours: int = 6) -> str:
    """
    Son `hours` saati analiz et.
    Döner: insan okunabilir rapor metni.
    """
    stats   = get_stats(hours)
    signals = get_recent_signals(hours)
    trades  = get_recent_trades(hours)

    lines = []
    now   = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")

    lines.append(f"📊 BOT RAPORU — Son {hours}h  ({now})")
    lines.append("━" * 40)

    # ── SİNYAL ÖZETİ ─────────────────────────────────────────
    total_sig  = stats["signals_total"]
    entered    = stats["signals_entered"]
    not_entered = total_sig - entered

    lines.append(f"\n🔔 Sinyal Özeti")
    lines.append(f"  Toplam sinyal  : {total_sig}")
    lines.append(f"  Giriş yapılan  : {entered}  ({_pct(entered, total_sig)})")
    lines.append(f"  Giriş yapılmayan: {not_entered}  ({_pct(not_entered, total_sig)})")

    # Neden girilmedi — en sık sebepler
    if stats["top_no_entry"]:
        lines.append(f"\n  Giriş yapılmama sebepleri:")
        for reason, cnt in stats["top_no_entry"]:
            lines.append(f"    • {reason}: {cnt} kez")

    # ── TRADE PERFORMANSI ─────────────────────────────────────
    lines.append(f"\n💰 Trade Performansı")
    if stats["total_trades"] == 0:
        lines.append("  Bu periyotta trade yok.")
    else:
        lines.append(f"  Trade sayısı    : {stats['total_trades']}")
        lines.append(f"  Kazanan/Kaybeden: {stats['wins']} / {stats['losses']}")
        lines.append(f"  Win rate        : {stats['win_rate']}%")
        lines.append(f"  Toplam PnL      : {stats['total_pnl']:+.4f} USDT")
        lines.append(f"  Ort. PnL%       : {stats['avg_pnl_pct']:+.3f}%")
        lines.append(f"  Ort. süre       : {stats['avg_duration_min']:.0f} dk")

    # ── REJİM DAĞILIMI ────────────────────────────────────────
    trend_sigs = [s for s in signals if s.get("regime") == "TREND"]
    range_sigs = [s for s in signals if s.get("regime") == "RANGE"]
    lines.append(f"\n🌊 Rejim Dağılımı")
    lines.append(f"  TREND periyodu  : {len(trend_sigs)} sinyal değerlendirmesi")
    lines.append(f"  RANGE periyodu  : {len(range_sigs)} sinyal değerlendirmesi")

    # ── DETAY: REJİM SORULARI ─────────────────────────────────
    if signals:
        q1_pass = sum(1 for s in signals if s.get("regime_q1_structure"))
        q2_pass = sum(1 for s in signals if s.get("regime_q2_cvd"))
        q3_pass = sum(1 for s in signals if s.get("regime_q3_oi"))
        q4_pass = sum(1 for s in signals if s.get("regime_q4_taker"))
        n = len(signals)
        lines.append(f"\n  Rejim soru geçiş oranları:")
        lines.append(f"    Q1 Yapı ilerliyor  : {_pct(q1_pass, n)}")
        lines.append(f"    Q2 CVD tutarlı     : {_pct(q2_pass, n)}")
        lines.append(f"    Q3 OI artıyor      : {_pct(q3_pass, n)}")
        lines.append(f"    Q4 Taker baskısı   : {_pct(q4_pass, n)}")

    # ── ZARAR ANALYSIS ────────────────────────────────────────
    losing = [t for t in trades if (t.get("pnl") or 0) < 0]
    if losing:
        lines.append(f"\n❌ Zarar Eden Tradeler ({len(losing)} adet)")
        reasons = {}
        for t in losing:
            r = t.get("close_reason","?")
            reasons[r] = reasons.get(r, 0) + 1
        for r, cnt in sorted(reasons.items(), key=lambda x: -x[1]):
            lines.append(f"  • {r}: {cnt} kez")

        # CVD diverjans vardı mı
        div_trades = [t for t in losing if t.get("notes","").find("diverjans") >= 0]
        if div_trades:
            lines.append(f"  ⚠ CVD diverjans işaretlenmiş: {len(div_trades)} trade")

    # ── SİSTEM HATALARI ───────────────────────────────────────
    import sqlite3
    from core.config import cfg as _cfg
    with sqlite3.connect(_cfg.DB_PATH) as db:
        db.row_factory = sqlite3.Row
        cutoff = time.time() - hours * 3600
        errs = db.execute(
            "SELECT source, error FROM errors WHERE ts > ? ORDER BY ts DESC LIMIT 5",
            (cutoff,)
        ).fetchall()

    if errs:
        lines.append(f"\n⚠️ Sistem Hataları ({len(errs)} adet)")
        for e in errs:
            lines.append(f"  [{e['source']}] {e['error'][:80]}")

    # ── YORUM ─────────────────────────────────────────────────
    lines.append(f"\n💡 Otomatik Yorum")
    pnl = stats["total_pnl"]
    wr  = stats["win_rate"]
    er  = stats["entry_rate_pct"]

    if stats["total_trades"] == 0 and total_sig == 0:
        lines.append("  Periyotta hiç sinyal üretilmedi — piyasa RANGE'de olabilir.")
    elif er < 20 and total_sig > 3:
        lines.append(
            f"  Sinyal giriş oranı çok düşük (%{er:.0f}). "
            "Giriş filtresi çok sıkı olabilir — 1m CVD eşiğini gözden geçir."
        )
    elif wr < 35 and stats["total_trades"] >= 3:
        lines.append(
            f"  Win rate %{wr:.0f} — düşük. "
            "Rejim filtresinin yanlış TREND dediği durumlar olabilir. "
            "Q1 (yapı) oranına bak."
        )
    elif pnl > 0 and wr >= 45:
        lines.append(f"  Sistem bu periyotta iyi çalıştı. PnL={pnl:+.4f} USDT, WR=%{wr:.0f}.")
    elif pnl < 0:
        lines.append(
            f"  Periyot zararla kapandı ({pnl:.4f} USDT). "
            "En sık zarar sebebine ve o anlardaki rejim skoruna bak."
        )

    return "\n".join(lines)


async def run_scheduled_analysis():
    """
    Zamanlanmış analiz döngüsü.
    Her ANALYSIS_HOURS'ta bir çalışır, sonucu Telegram'a gönderir.
    """
    import asyncio
    from utils.notifier import send_message
    from datetime import datetime, timezone

    log.info("Zamanlanmış analiz döngüsü başladı")

    while not is_stopping():
        now_utc = datetime.now(timezone.utc)
        current_hour = now_utc.hour
        current_min  = now_utc.minute

        if current_hour in cfg.ANALYSIS_HOURS and current_min < 2:
            log.info(f"Periyodik analiz başlıyor (UTC {current_hour:02d}:00)")
            try:
                report = analyze(hours=6)
                log.info(f"Analiz tamamlandı:\n{report}")
                await send_message(report)
            except Exception as e:
                log.error(f"Analiz hatası: {e}")
                await log_error_async("Analyzer", str(e))
            await stoppable_sleep(120)
            if is_stopping():
                break
        else:
            await stoppable_sleep(30)
            if is_stopping():
                break


async def log_error_async(source: str, error: str):
    from botlog.db import log_error
    log_error(source, error)
