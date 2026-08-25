"""
engine/good_signal_stats_v3.py — Karar LONG/SHORT iken execute engeli istatistikleri.

Karar zinciri geçti (v3_mode + yön) ama execute_entry açılmadıysa sayar:
  GOOD_SIGNAL_BLOCKED_WARMUP, _RR, _CVD, ...

Kalıcı: data/good_signal_blocked.json
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from core.config import cfg
from core.logger import get_logger

log = get_logger("GoodSignalStats")

_STATS_FILE = Path(__file__).resolve().parent.parent / "data" / "good_signal_blocked.json"
_last_summary_ts = 0.0

# Sayaç anahtarları (log/rapor etiketi)
CATEGORIES = (
    "warmup",
    "rr",
    "cvd",
    "daily_guard",
    "stale",
    "auto_trade",
    "narrative",
    "position",
    "reconcile",
    "grace",
    "balance",
    "risk",
    "other",
)


def _enabled() -> bool:
    return bool(getattr(cfg, "V3_GOOD_SIGNAL_STATS_ENABLED", True))


def classify_block(blocker: str, detail: str = "") -> str:
    b = str(blocker or "").lower().strip()
    d = str(detail or "").lower()
    text = f"{b} {d}"
    if "warmup" in b or "isinmas" in d or "startup_warmup" in b:
        return "warmup"
    if "rr" in b or "rr yetersiz" in d or "rr_too" in b:
        return "rr"
    if "cvd" in text:
        return "cvd"
    if "daily" in b or "gunluk" in d or "loss_guard" in b:
        return "daily_guard"
    if "stale" in b:
        return "stale"
    if "auto_trade" in b:
        return "auto_trade"
    if "narrative" in b or "anlat" in d:
        return "narrative"
    if "position" in b or "pozisyon" in d:
        return "position"
    if "reconcil" in b or "senkron" in d:
        return "reconcile"
    if "grace" in b:
        return "grace"
    if "bakiye" in d or "balance" in b:
        return "balance"
    if "risk" in b or "plan" in d:
        return "risk"
    return "other"


def _empty_totals() -> dict[str, int]:
    return {k: 0 for k in CATEGORIES}


def _load_store() -> dict:
    if not _STATS_FILE.exists():
        return {"totals": _empty_totals(), "updated_ts": 0.0}
    try:
        raw = json.loads(_STATS_FILE.read_text(encoding="utf-8"))
        totals = raw.get("totals") or {}
        out = _empty_totals()
        for k in CATEGORIES:
            out[k] = int(totals.get(k, 0) or 0)
        return {"totals": out, "updated_ts": float(raw.get("updated_ts", 0) or 0)}
    except Exception:
        return {"totals": _empty_totals(), "updated_ts": 0.0}


def _save_store(store: dict) -> None:
    try:
        _STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATS_FILE.write_text(
            json.dumps(store, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as ex:
        log.warning(f"good_signal stats yazilamadi: {ex}")


def get_totals() -> dict[str, int]:
    return dict(_load_store().get("totals") or _empty_totals())


def format_summary_line(totals: dict[str, int] | None = None) -> str:
    t = totals or get_totals()
    parts = [f"{k}={int(t.get(k, 0) or 0)}" for k in CATEGORIES if int(t.get(k, 0) or 0) > 0]
    if not parts:
        return "[GOOD_SIGNAL] blocked: (henüz yok)"
    return "[GOOD_SIGNAL] blocked toplam: " + " ".join(parts)


def maybe_log_summary(*, force: bool = False) -> None:
    global _last_summary_ts
    if not _enabled():
        return
    interval = float(getattr(cfg, "V3_GOOD_SIGNAL_LOG_SEC", 3600) or 3600)
    now = time.time()
    if not force and (now - _last_summary_ts) < interval:
        return
    _last_summary_ts = now
    log.info(format_summary_line())


def record_blocked(
    category: str,
    *,
    direction: str = "",
    detail: str = "",
    source: str = "",
    blocker: str = "",
) -> None:
    if not _enabled():
        return
    cat = category if category in CATEGORIES else "other"
    store = _load_store()
    totals = store.get("totals") or _empty_totals()
    totals[cat] = int(totals.get(cat, 0) or 0) + 1
    store["totals"] = totals
    store["updated_ts"] = time.time()
    _save_store(store)

    side = str(direction or "").upper()
    src = f" src={source}" if source else ""
    det = (detail or "")[:120]
    log.info(
        f"[GOOD_SIGNAL_BLOCKED] {cat.upper()} {side}{src} "
        f"| GOOD_SIGNAL_BLOCKED_{cat.upper()}={totals[cat]} "
        f"| {det}"
    )
    maybe_log_summary()


def note_v3_execute_block(
    details: dict,
    source: str,
    blocker: str,
    detail: str,
) -> None:
    """execute_entry: karar gecti (v3_mode) ama emir acilmadi."""
    if not details.get("v3_mode"):
        return
    direction = str(details.get("direction") or "").upper()
    if direction not in ("LONG", "SHORT"):
        return
    cat = classify_block(blocker, detail)
    record_blocked(
        cat,
        direction=direction,
        detail=detail,
        source=source,
        blocker=blocker,
    )
