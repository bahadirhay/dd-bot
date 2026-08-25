"""
engine/adaptation_v3.py — Islem sonucu → katman agirligi geri beslemesi.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from core.config import cfg
from core.logger import get_logger

log = get_logger("AdaptationV3")

_PERSIST = Path(__file__).resolve().parent.parent / "data" / "v3_adaptation.json"


def _default_weights() -> dict:
    return {
        "liquidity": float(getattr(cfg, "V3_COLLAPSE_W_LIQUIDITY", 0.50) or 0.50),
        "event": float(getattr(cfg, "V3_COLLAPSE_W_EVENT", 0.30) or 0.30),
        "structure": float(getattr(cfg, "V3_COLLAPSE_W_STRUCTURE", 0.20) or 0.20),
    }


def _load() -> dict:
    if not _PERSIST.exists():
        return {"weights": _default_weights(), "history": [], "n_trades": 0}
    try:
        data = json.loads(_PERSIST.read_text(encoding="utf-8"))
        w = data.get("weights") or _default_weights()
        return {"weights": w, "history": data.get("history", [])[-50:], "n_trades": data.get("n_trades", 0)}
    except Exception:
        return {"weights": _default_weights(), "history": [], "n_trades": 0}


def _save(data: dict) -> None:
    _PERSIST.parent.mkdir(parents=True, exist_ok=True)
    _PERSIST.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_adaptive_weights() -> tuple[float, float, float]:
    if not getattr(cfg, "V3_ADAPTATION_ENABLED", True):
        w = _default_weights()
        return w["liquidity"], w["event"], w["structure"]
    data = _load()
    w = data.get("weights") or _default_weights()
    total = w["liquidity"] + w["event"] + w["structure"]
    if total <= 0:
        return 0.5, 0.3, 0.2
    return w["liquidity"] / total, w["event"] / total, w["structure"] / total


def record_trade_outcome(
    *,
    won: bool,
    pnl_pct: float = 0.0,
    market_state: dict | None = None,
    controller: str = "",
) -> None:
    if not getattr(cfg, "V3_ADAPTATION_ENABLED", True):
        return

    ms = market_state or {}
    collapse = ms.get("collapse") or {}
    ctrl = controller or str(collapse.get("controller") or "blend")
    scores = collapse.get("scores") or {}

    data = _load()
    w = dict(data.get("weights") or _default_weights())
    step = float(getattr(cfg, "V3_ADAPTATION_STEP", 0.02) or 0.02)
    min_w = float(getattr(cfg, "V3_ADAPTATION_MIN_W", 0.08) or 0.08)
    max_w = float(getattr(cfg, "V3_ADAPTATION_MAX_W", 0.65) or 0.65)

    key = "event" if ctrl == "event" else "liquidity" if ctrl == "liquidity" else "structure"
    if won:
        w[key] = min(max_w, w.get(key, 0.33) + step)
        others = [k for k in w if k != key]
        if others:
            loser = min(others, key=lambda k: w.get(k, 0))
            w[loser] = max(min_w, w.get(loser, 0.33) - step * 0.5)
    else:
        w[key] = max(min_w, w.get(key, 0.33) - step)
        best = max(scores, key=scores.get, default="structure")
        w[best] = min(max_w, w.get(best, 0.33) + step * 0.5)

    total = sum(w.values())
    for k in w:
        w[k] /= total

    data["weights"] = w
    data["n_trades"] = int(data.get("n_trades", 0) or 0) + 1
    data.setdefault("history", []).append(
        {
            "ts": time.time(),
            "won": won,
            "pnl_pct": round(pnl_pct, 3),
            "controller": ctrl,
            "weights": dict(w),
        }
    )
    data["history"] = data["history"][-50:]
    _save(data)
    log.info(
        f"[ADAPT] {'win' if won else 'loss'} ctrl={ctrl} pnl={pnl_pct:+.2f}% "
        f"→ W L={w['liquidity']:.2f} E={w['event']:.2f} S={w['structure']:.2f}"
    )
