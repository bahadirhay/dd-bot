"""
engine/decision_block_stats_v3.py — WAIT karar engeli sayaçları (karar katmanı).

Her v3_attribution DB yazımında (action=WAIT) reject_reason + zone + path sayar.
Kalıcı: data/decision_blocked.json

Rapor: python scripts/decision_block_report.py --hours 24
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from core.config import cfg
from core.logger import get_logger

log = get_logger("DecisionBlockStats")

_STATS_FILE = Path(__file__).resolve().parent.parent / "data" / "decision_blocked.json"
_last_summary_ts = 0.0
_last_event_key = ""
_last_event_ts = 0.0

CHANNEL_REJECT_CODES = (
    "CVD_BLOCK",
    "RR_TOO_LOW",
    "CHANNEL_SCORE_LOW",
    "CHANNEL_MID_RANGE",
    "NO_ACTIVE_LEVEL",
)


def _enabled() -> bool:
    return bool(getattr(cfg, "V3_DECISION_BLOCK_STATS_ENABLED", True))


def _empty_store() -> dict:
    return {
        "totals": {},
        "by_zone": {},
        "zone_reject": {},
        "rr_samples": [],
        "updated_ts": 0.0,
    }


def _load_store() -> dict:
    if not _STATS_FILE.exists():
        return _empty_store()
    try:
        raw = json.loads(_STATS_FILE.read_text(encoding="utf-8"))
        out = _empty_store()
        out["totals"] = dict(raw.get("totals") or {})
        out["by_zone"] = dict(raw.get("by_zone") or {})
        out["zone_reject"] = dict(raw.get("zone_reject") or {})
        samples = raw.get("rr_samples") or []
        out["rr_samples"] = list(samples)[-500:]
        out["updated_ts"] = float(raw.get("updated_ts", 0) or 0)
        return out
    except Exception:
        return _empty_store()


def _save_store(store: dict) -> None:
    try:
        _STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATS_FILE.write_text(
            json.dumps(store, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as ex:
        log.warning(f"decision_block stats yazilamadi: {ex}")


def get_totals() -> dict[str, int]:
    return dict(_load_store().get("totals") or {})


def format_summary_line(totals: dict[str, int] | None = None) -> str:
    t = totals or get_totals()
    items = sorted(t.items(), key=lambda x: -x[1])
    parts = [f"{k}={v}" for k, v in items if int(v) > 0][:12]
    if not parts:
        return "[DECISION_BLOCK] WAIT: (henüz yok)"
    return "[DECISION_BLOCK] WAIT toplam: " + " ".join(parts)


def maybe_log_summary(*, force: bool = False) -> None:
    global _last_summary_ts
    if not _enabled():
        return
    interval = float(getattr(cfg, "V3_DECISION_BLOCK_LOG_SEC", 3600) or 3600)
    now = time.time()
    if not force and (now - _last_summary_ts) < interval:
        return
    _last_summary_ts = now
    log.info(format_summary_line())


def record_decision_wait(
    attr: dict,
    *,
    context: dict | None = None,
) -> None:
    """Karar katmanı WAIT — reject_reason + zone/path."""
    if not _enabled():
        return
    if str(attr.get("action") or "").upper() != "WAIT":
        return

    code = str(attr.get("reject_reason") or "OTHER").strip() or "OTHER"
    ctx = context or attr.get("context") or {}
    layer = str(ctx.get("reject_layer") or attr.get("reject_layer") or "")
    if not layer:
        try:
            from engine.reject_reason_v3 import reject_layer_for

            layer = reject_layer_for(code)
        except Exception:
            layer = ""
    if layer == "execute":
        return
    zone = str(ctx.get("zone") or "—").upper()
    path = str(ctx.get("path") or "—")
    favored = str(ctx.get("favored_side") or ctx.get("favored") or "—").upper()
    candidate = str(ctx.get("channel_candidate") or "—").upper()

    store = _load_store()
    totals = store.get("totals") or {}
    totals[code] = int(totals.get(code, 0) or 0) + 1
    store["totals"] = totals

    by_zone = store.get("by_zone") or {}
    if zone and zone != "—":
        by_zone[zone] = int(by_zone.get(zone, 0) or 0) + 1
    store["by_zone"] = by_zone

    zr_key = f"{zone}|{code}"
    zone_reject = store.get("zone_reject") or {}
    zone_reject[zr_key] = int(zone_reject.get(zr_key, 0) or 0) + 1
    store["zone_reject"] = zone_reject

    if code == "RR_TOO_LOW":
        rr = float(ctx.get("entry_rr") or 0)
        risk = float(ctx.get("risk_usd") or 0)
        reward = float(ctx.get("reward_usd") or 0)
        if rr > 0 or risk > 0:
            samples = list(store.get("rr_samples") or [])
            samples.append(
                {
                    "ts": float(attr.get("ts") or time.time()),
                    "px": float(attr.get("price") or 0),
                    "sl": float(ctx.get("entry_sl") or 0),
                    "tp": float(ctx.get("entry_tp") or 0),
                    "rr": rr,
                    "risk_usd": risk,
                    "reward_usd": reward,
                    "zone": zone,
                    "candidate": candidate,
                    "favored": favored,
                    "sl_source": str(ctx.get("sl_source") or ""),
                    "sl_anchor": float(ctx.get("sl_anchor") or 0),
                }
            )
            store["rr_samples"] = samples[-500:]

    store["updated_ts"] = time.time()
    _save_store(store)

    global _last_event_key, _last_event_ts
    n = int(totals.get(code, 0))
    event_key = f"{code}|{zone}|{path}|{round(float(attr.get('price') or 0), 1)}"
    now = time.time()
    interval = float(getattr(cfg, "V3_DECISION_BLOCK_EVENT_LOG_SEC", 45) or 45)
    periodic = (now - _last_event_ts) >= interval
    if event_key != _last_event_key or periodic or n % 25 == 0:
        _last_event_key = event_key
        _last_event_ts = now
        zr_note = ""
        if zone != "—" and code in CHANNEL_REJECT_CODES:
            zr_note = f" zone={zone} path={path} cand={candidate} fav={favored}"
        log.info(
            f"[DECISION_BLOCK] {code} #{n}{zr_note} "
            f"| DECISION_BLOCK_{code}={n}"
        )
    maybe_log_summary()
