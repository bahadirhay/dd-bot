"""
engine/trend_magic_eval.py — TF degerlendirme + canli guvenlik kapisi.

Binance resmi kline uzerinde walk-forward. Canliya yalnizca onayli TF.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from core.config import cfg, BASE_DIR

EVAL_PATH = BASE_DIR / "data" / "tm_eval.json"
DEFAULT_ALLOWED_TF = (1800,)  # 30m — 5m/15m canli varsayilan KAPALI
BLOCKED_LIVE_TF = (300, 900)  # 5m, 15m


def _summarize(trades) -> dict:
    if not trades:
        return {"net": 0, "n": 0, "wr": 0, "avg": 0, "train": 0, "oos": 0, "oos_ok": False}
    pnls = [t.pnl_bps * t.qty for t in trades]
    n = len(pnls)
    split = max(1, int(n * 0.6))
    tr = sum(pnls[:split])
    oo = sum(pnls[split:])
    return {
        "net": round(sum(pnls), 1),
        "n": n,
        "wr": round(100 * sum(1 for x in pnls if x > 0) / n, 1),
        "avg": round(sum(pnls) / n, 1),
        "train": round(tr, 1),
        "oos": round(oo, 1),
        "oos_ok": oo > 0,
    }


def evaluate_all(*, limit: int = 1500, fee: float = 8.0, slip: float = 2.0) -> dict:
    from engine.trend_magic_v3 import TrendMagicParams, fetch_binance_bars, run_backtest

    p = TrendMagicParams()
    out: dict = {"ts": time.time(), "source": "binance_kline", "fee": fee, "slip": slip, "tfs": {}}
    for interval, sec, lbl in (
        ("5m", 300, "5m"),
        ("15m", 900, "15m"),
        ("30m", 1800, "30m"),
        ("1h", 3600, "1h"),
    ):
        bars = fetch_binance_bars(interval, limit)
        if len(bars) < 80:
            out["tfs"][lbl] = {"error": "insufficient_bars", "n_bars": len(bars), "tf_sec": sec}
            continue
        trades = run_backtest(bars, p, fee_bps=fee, slip_bps=slip)
        st = _summarize(trades)
        bh = (bars[-1]["close"] - bars[0]["close"]) / bars[0]["close"] * 1e4
        st["tf_sec"] = sec
        st["n_bars"] = len(bars)
        st["buy_hold_bps"] = round(bh, 1)
        st["live_allowed"] = _tf_live_allowed(sec, st)
        out["tfs"][lbl] = st
    out["recommended_tf_sec"] = recommend_tf(out)
    out["live_ok"] = any(t.get("live_allowed") for t in out["tfs"].values() if isinstance(t, dict))
    return out


def _tf_live_allowed(tf_sec: int, stats: dict) -> bool:
    allowed = _parse_allowed_tf()
    if tf_sec in BLOCKED_LIVE_TF:
        return False
    if tf_sec not in allowed:
        return False
    min_oos = float(getattr(cfg, "V3_TM_MIN_OOS_BPS", 0) or 0)
    if stats.get("n", 0) < int(getattr(cfg, "V3_TM_MIN_TRADES", 15) or 15):
        return False
    if stats.get("oos", 0) < min_oos:
        return False
    return bool(stats.get("oos_ok"))


def _parse_allowed_tf() -> tuple[int, ...]:
    raw = str(getattr(cfg, "V3_TM_ALLOWED_TF_SEC", "1800") or "1800")
    out: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return tuple(out) if out else DEFAULT_ALLOWED_TF


def recommend_tf(eval_result: dict) -> int:
    best = 0
    best_oos = -1e18
    for lbl, st in (eval_result.get("tfs") or {}).items():
        if not isinstance(st, dict) or not st.get("live_allowed"):
            continue
        if st.get("oos", 0) > best_oos:
            best_oos = st["oos"]
            best = int(st.get("tf_sec") or 0)
    return best or 1800


def save_eval(result: dict) -> None:
    EVAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVAL_PATH.write_text(json.dumps(result, indent=2), encoding="utf-8")


def load_eval() -> dict:
    if not EVAL_PATH.exists():
        return {}
    try:
        return json.loads(EVAL_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def live_gate_reason() -> str:
    """Canli TM acikken blok nedeni; '' = geç."""
    if not bool(getattr(cfg, "V3_STRATEGY_TM_ENABLED", False)):
        return ""
    tf = int(getattr(cfg, "V3_TREND_MAGIC_TF_SEC", 1800) or 1800)
    if tf in BLOCKED_LIVE_TF:
        return f"TM canli {tf}s yasak (5m/15m whipsaw — eval ile dogrulanmadi)"
    allowed = _parse_allowed_tf()
    if tf not in allowed:
        return f"TM tf={tf}s izin listesinde yok (V3_TM_ALLOWED_TF_SEC={allowed})"
    ev = load_eval()
    if not ev and bool(getattr(cfg, "V3_TM_REQUIRE_EVAL", True)):
        return "TM eval dosyasi yok — once: python scripts/_trend_magic_eval.py"
    tf_lbl = {300: "5m", 900: "15m", 1800: "30m", 3600: "1h"}.get(tf, str(tf))
    st = (ev.get("tfs") or {}).get(tf_lbl) or {}
    if bool(getattr(cfg, "V3_TM_REQUIRE_EVAL", True)) and st:
        if not st.get("live_allowed"):
            return (
                f"TM {tf_lbl} eval geçmedi: OOS={st.get('oos')} n={st.get('n')} "
                f"(min OOS={getattr(cfg, 'V3_TM_MIN_OOS_BPS', 0)})"
            )
    return ""


def assert_live_gate() -> None:
    reason = live_gate_reason()
    if reason:
        from core.logger import get_logger

        get_logger("TrendMagicEval").error(f"[TM-GATE] CANLI BLOK: {reason}")


def format_report(result: dict) -> str:
    lines = [
        f"=== Trend Magic Eval | Binance kline | {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(result.get('ts', 0)))} ===",
        "",
    ]
    for lbl in ("5m", "15m", "30m", "1h"):
        st = (result.get("tfs") or {}).get(lbl) or {}
        if st.get("error"):
            lines.append(f"  {lbl:4}  VERI YOK")
            continue
        flag = "CANLI-OK" if st.get("live_allowed") else "CANLI-YOK"
        lines.append(
            f"  {lbl:4}  net={st.get('net', 0):+7.0f}  n={st.get('n', 0):3d}  "
            f"wr={st.get('wr', 0):4.1f}%  TRAIN={st.get('train', 0):+.0f}  OOS={st.get('oos', 0):+.0f}  "
            f"B&H={st.get('buy_hold_bps', 0):+.0f}  [{flag}]"
        )
    rec = result.get("recommended_tf_sec", 1800)
    rec_lbl = {300: "5m", 900: "15m", 1800: "30m", 3600: "1h"}.get(rec, str(rec))
    lines.append(f"\n  Önerilen canli TF: {rec_lbl} ({rec})")
    lines.append(f"  5m/15m canlı: varsayılan KAPALI (whipsaw)")
    return "\n".join(lines)
