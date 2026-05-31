"""utils/notifier.py — Telegram bildirimleri"""
import aiohttp
from core.config import cfg
from core.logger import get_logger
log = get_logger("Notifier")

async def send_message(text: str):
    if not cfg.TG_TOKEN or not cfg.TG_CHAT_ID: return
    try:
        async with aiohttp.ClientSession() as s:
            await s.post(
                f"https://api.telegram.org/bot{cfg.TG_TOKEN}/sendMessage",
                json={"chat_id": cfg.TG_CHAT_ID, "text": text, "parse_mode": "HTML"},
                timeout=aiohttp.ClientTimeout(total=5),
            )
    except Exception as e:
        log.error(f"TG hata: {e}")

async def notify_open(plan, reason=""):
    ms = "TESTNET" if cfg.TESTNET else "🔴 CANLI"
    em = "🟢" if plan.direction == "LONG" else "🔴"
    await send_message(
        f"{em} POZİSYON AÇILDI [{ms}]\n\n"
        f"<b>ETH/USDT  {plan.direction}</b>  {plan.qty_total:.4f} ETH\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"Giriş : <code>{plan.entry:.2f}</code>\n"
        f"SL    : <code>{plan.sl:.2f}</code>\n"
        f"TP1   : <code>{plan.tp1:.2f}</code>  R:R 1:{plan.rr_tp1:.2f}"
        f"  ({plan.qty_tp1:.4f} ETH)\n"
        f"TP2   : <code>{plan.tp2:.2f}</code>  R:R 1:{plan.rr_tp2:.2f}"
        f"  ({plan.qty_tp2:.4f} ETH)\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"Marjin: {plan.margin_req:.2f} USDT  "
        f"({cfg.MARGIN} {cfg.LEVERAGE}x)\n"
        f"Liq   : {plan.liq_price:.2f}\n"
        f"Sebep : {reason}"
    )

async def notify_tp1(price, qty, entry):
    ms  = "TESTNET" if cfg.TESTNET else "🔴 CANLI"
    pnl = round((price - entry) * qty, 4)
    await send_message(
        f"✅ TP1 DOLDU [{ms}]\n"
        f"@ <code>{price:.2f}</code>  {qty:.4f} ETH\n"
        f"Kısmi PnL: <b>{pnl:+.4f} USDT</b>\n"
        f"SL breakeven'a taşındı"
    )

async def notify_close(reason: str, pnl: float):
    ms = "TESTNET" if cfg.TESTNET else "🔴 CANLI"
    em = "💚" if pnl >= 0 else "❤️"
    await send_message(
        f"{em} KAPATILDI [{ms}]\n"
        f"Sebep: {reason}\n"
        f"PnL  : <b>{pnl:+.4f} USDT</b>"
    )
