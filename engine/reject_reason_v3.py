"""
engine/reject_reason_v3.py — Her WAIT icin TEK reddetme kodu + sayac.

Kodlar (oncelik sirasiyla ilk eslesen):
  POSITION_OPEN, NO_ACTIVE_LEVEL, RR_TOO_LOW, CVD_BLOCK,
  STRUCTURE_BLOCK, CLUSTER_BLOCK, VERDICT_BLOCK, SCENARIO_WAIT, OTHER
"""
from __future__ import annotations

import time
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Any

from core.config import cfg
from core.logger import get_logger
from core.state import state, effective_price

log = get_logger("RejectReason")

# ── Sabit kodlar ─────────────────────────────────────────────────────────────
POSITION_OPEN = "POSITION_OPEN"
NO_ACTIVE_LEVEL = "NO_ACTIVE_LEVEL"
RR_TOO_LOW = "RR_TOO_LOW"
CVD_BLOCK = "CVD_BLOCK"
STRUCTURE_BLOCK = "STRUCTURE_BLOCK"
CLUSTER_BLOCK = "CLUSTER_BLOCK"
VERDICT_BLOCK = "VERDICT_BLOCK"
SCENARIO_WAIT = "SCENARIO_WAIT"
CHANNEL_MID_RANGE = "CHANNEL_MID_RANGE"
CHANNEL_SCORE_LOW = "CHANNEL_SCORE_LOW"
THESIS_WAIT = "THESIS_WAIT"
LOW_EDGE = "LOW_EDGE"
TRADEABILITY = "TRADEABILITY"
LATE_ENTRY = "LATE_ENTRY"
BAD_GEOMETRY = "BAD_GEOMETRY"
SCORE_EDGE = "SCORE_EDGE"
OTHER = "OTHER"

REJECT_CODES = (
    POSITION_OPEN,
    NO_ACTIVE_LEVEL,
    RR_TOO_LOW,
    CVD_BLOCK,
    STRUCTURE_BLOCK,
    CLUSTER_BLOCK,
    VERDICT_BLOCK,
    CHANNEL_MID_RANGE,
    CHANNEL_SCORE_LOW,
    SCENARIO_WAIT,
    THESIS_WAIT,
    LOW_EDGE,
    TRADEABILITY,
    LATE_ENTRY,
    BAD_GEOMETRY,
    SCORE_EDGE,
    OTHER,
)

# Katman: decision=kanal karari, execute=giriş katmani, gate=verdict/tez
REJECT_LAYER_DECISION = "decision"
REJECT_LAYER_EXECUTE = "execute"
REJECT_LAYER_GATE = "gate"
REJECT_LAYER_OTHER = "other"

_EXEC_CODES = frozenset({POSITION_OPEN})
_DECISION_CODES = frozenset(
    {
        NO_ACTIVE_LEVEL,
        RR_TOO_LOW,
        CVD_BLOCK,
        CHANNEL_MID_RANGE,
        CHANNEL_SCORE_LOW,
        SCENARIO_WAIT,
    }
)
_GATE_CODES = frozenset(
    {
        STRUCTURE_BLOCK,
        CLUSTER_BLOCK,
        VERDICT_BLOCK,
        THESIS_WAIT,
        LOW_EDGE,
        TRADEABILITY,
        LATE_ENTRY,
        BAD_GEOMETRY,
        SCORE_EDGE,
    }
)


def reject_layer_for(code: str) -> str:
    c = str(code or "").strip().upper()
    if c in _EXEC_CODES:
        return REJECT_LAYER_EXECUTE
    if c in _DECISION_CODES:
        return REJECT_LAYER_DECISION
    if c in _GATE_CODES:
        return REJECT_LAYER_GATE
    return REJECT_LAYER_OTHER

REJECT_LABELS: dict[str, str] = {
    POSITION_OPEN: "Pozisyon zaten acik.",
    NO_ACTIVE_LEVEL: "Aktif destek/direnc yok veya gecersiz.",
    RR_TOO_LOW: "Risk/odul orani yetersiz.",
    CVD_BLOCK: "CVD teyit etmiyor.",
    STRUCTURE_BLOCK: "Yapi / collapse rejimi blokladi.",
    CLUSTER_BLOCK: "Likidite / expected-move / cluster blok.",
    VERDICT_BLOCK: "Trade verdict veya zamanlama blok.",
    CHANNEL_MID_RANGE: "Orta bolge — islem yok.",
    CHANNEL_SCORE_LOW: "Giris / yapi skoru yetersiz.",
    SCENARIO_WAIT: "Senaryo kosullari olusmadi (WAIT).",
    THESIS_WAIT: "Tez / thesis katmani WAIT.",
    LOW_EDGE: "Direction score edge yok.",
    TRADEABILITY: "Tradeability kapisi.",
    LATE_ENTRY: "Gec giris.",
    BAD_GEOMETRY: "SL/TP geometrisi gecersiz.",
    SCORE_EDGE: "Skor edge yetersiz.",
    OTHER: "Diger blok.",
}

# Eski attribution primary_block -> REJECT kodu
_LEGACY_BLOCK_MAP: dict[str, str] = {
    "position_open": POSITION_OPEN,
    "levels_weak": NO_ACTIVE_LEVEL,
    "range_invalid": NO_ACTIVE_LEVEL,
    "rr": RR_TOO_LOW,
    "cvd": CVD_BLOCK,
    "collapse": STRUCTURE_BLOCK,
    "htf_wall": STRUCTURE_BLOCK,
    "cluster": CLUSTER_BLOCK,
    "liquidity_chase": CLUSTER_BLOCK,
    "expected_move": CLUSTER_BLOCK,
    "verdict_timing": VERDICT_BLOCK,
    "scenario_wait": SCENARIO_WAIT,
    "zone_mid": CHANNEL_MID_RANGE,
    "zone_lifecycle": CLUSTER_BLOCK,
    "entry": RR_TOO_LOW,
    "other": OTHER,
}

_COUNTERS: Counter = Counter()
_RING: deque = deque(maxlen=100)
_last_log_key = ""


@dataclass
class RejectContext:
    snap: dict
    reasons: list[str] = field(default_factory=list)
    trade_candidate: bool = False
    text: str = ""

    def __post_init__(self) -> None:
        parts = list(self.reasons) + [str(self.snap.get("reason") or "")]
        self.text = " | ".join(p for p in parts if p).lower()


def _levels(snap: dict) -> dict:
    return snap.get("levels") or {}


def _scenario(snap: dict) -> dict:
    return snap.get("scenario") or {}


def _no_active_band(ctx: RejectContext) -> bool:
    lv = _levels(ctx.snap)
    s = float(lv.get("active_support") or 0)
    r = float(lv.get("active_resistance") or 0)
    if s <= 0 or r <= s:
        return True
    if not lv.get("range_valid"):
        t = ctx.text
        if "range gecersiz" in t or "destek/direnc" in t or "kirilim referans" in t:
            return True
    return False


def resolve_reject_reason(
    snap: dict,
    *,
    reasons: list[str] | None = None,
    trade_candidate: bool = False,
    attr: dict | None = None,
) -> str:
    """WAIT icin tek REJECT kodu — oncelik sirali."""
    ctx = RejectContext(
        snap=snap,
        reasons=list(reasons or []),
        trade_candidate=bool(trade_candidate),
    )
    t = ctx.text
    scn = str(_scenario(ctx.snap).get("name") or "WAIT")
    action = str(snap.get("action") or "WAIT").upper()

    if action != "WAIT":
        return OTHER

    preset = str(snap.get("reject_reason") or "").strip()
    if preset in REJECT_CODES:
        return preset

    ch = snap.get("channel_decision") or {}
    if snap.get("channel_authority") or _levels(snap).get("channel_authority"):
        if ch:
            from engine.channel_decision import channel_reject_code

            return channel_reject_code(ch)

    if "pozisyon acik" in t:
        return POSITION_OPEN

    if _no_active_band(ctx) or any(
        k in t
        for k in (
            "destek/direnc yok",
            "zayif",
            "range gecersiz",
            "kirilim referans destegi",
            "kirilim referans direnci",
            "aktif destek",
            "aktif direnc",
        )
    ):
        return NO_ACTIVE_LEVEL

    if "rr yetersiz" in t or "rr yetersiz:" in t:
        return RR_TOO_LOW

    entry = snap.get("entry") or {}
    if trade_candidate and not entry.get("valid"):
        rr = float(entry.get("rr", 0) or 0)
        min_rr = float(getattr(cfg, "V3_MIN_RR_RATIO", 2.0) or 2.0)
        if rr > 0 and rr < min_rr:
            return RR_TOO_LOW
        if "giris noktasi" in t:
            return RR_TOO_LOW

    if "cvd" in t or "teyit etmiyor" in t:
        return CVD_BLOCK

    ms = _levels(ctx.snap).get("market_state") or getattr(state, "v3_market_state", None) or {}
    collapse = ms.get("collapse") or {}
    if collapse.get("mode") in ("NO_TRADE", "STRUCTURE_CONTROLLED") and not collapse.get(
        "allow_trade"
    ):
        return STRUCTURE_BLOCK
    if any(
        k in t
        for k in (
            "collapse",
            "belirsiz rejim",
            "yapi",
            "rejim:",
            "structure",
            "counter-trend",
            "rejection_watch",
        )
    ):
        return STRUCTURE_BLOCK

    if any(
        k in t
        for k in (
            "cluster",
            "savas alani",
            "expected move",
            "em zayif",
            "likidite",
            "kovalama",
            "lifecycle",
            "archive",
        )
    ):
        return CLUSTER_BLOCK

    if any(
        k in t
        for k in (
            "trade verdict",
            "verdict",
            "trade_later",
            "observe",
            "timing blok",
            "timing_block",
            "entry=evet",
            "entry=hayir",
        )
    ):
        return VERDICT_BLOCK

    tv = ms.get("trade_verdict") or {}
    if tv and not tv.get("allow_entry"):
        return VERDICT_BLOCK

    attr = attr or snap.get("attribution") or getattr(state, "v3_last_attribution", None) or {}
    pb = str(attr.get("primary_block") or "").strip()
    if pb and pb in _LEGACY_BLOCK_MAP:
        return _LEGACY_BLOCK_MAP[pb]

    if "mid_range" in t or "orta bolge" in t or "orta bölge" in t:
        return CHANNEL_MID_RANGE

    if scn in ("CHANNEL_WAIT",) or str(_scenario(snap).get("channel_path") or ""):
        if "score" in t:
            return CHANNEL_SCORE_LOW
        if "mid_range" in t or "orta" in t:
            return CHANNEL_MID_RANGE

    if scn == "WAIT" or "senaryo:" in t or "band ortasinda" in t or "kosullar" in t:
        return SCENARIO_WAIT

    if not trade_candidate:
        return SCENARIO_WAIT

    return OTHER


def label_for(code: str) -> str:
    return REJECT_LABELS.get(str(code or ""), REJECT_LABELS[OTHER])


def should_count_reject(snap: dict, *, trade_candidate: bool = False) -> bool:
    """Reddedilen sinyal: aday vardi ama WAIT."""
    if str(snap.get("action") or "").upper() != "WAIT":
        return False
    scn = str(_scenario(snap).get("name") or "WAIT")
    if scn == "WAIT" and not trade_candidate:
        return bool(getattr(cfg, "V3_REJECT_COUNT_SCENARIO_WAIT", False))
    return bool(trade_candidate) or scn not in ("WAIT",)


def record_reject(
    snap: dict,
    code: str,
    *,
    trade_candidate: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Sayac + son 100 kayit + DB (attribution uzerinden)."""
    global _last_log_key

    if not getattr(cfg, "V3_REJECT_REASON_ENABLED", True):
        return {"reject_reason": code, "count": 0}

    px = float(
        (_levels(snap).get("price"))
        or snap.get("price")
        or effective_price()
        or state.mark_price
        or 0
    )
    scn = str(_scenario(snap).get("name") or "WAIT")
    side = str((snap.get("attribution") or {}).get("intended_side") or "")
    if not side:
        from engine.attribution_v3 import _intended_side_from_snap

        side = _intended_side_from_snap(snap)

    _COUNTERS[code] += 1
    n = int(_COUNTERS[code])
    row = {
        "ts": time.time(),
        "reject_reason": code,
        "price": px,
        "scenario": scn,
        "intended_side": side,
        "trade_candidate": trade_candidate,
        "count": n,
    }
    _RING.append(row)

    state.v3_reject_counters = dict(_COUNTERS)
    state.v3_reject_ring = list(_RING)
    state.v3_last_reject_reason = code

    key = f"{code}|{scn}|{side}|{round(px, 1)}"
    interval = float(getattr(cfg, "V3_REJECT_LOG_SEC", 45) or 45)
    now = time.time()
    last_ts = float(getattr(state, "v3_reject_last_log_ts", 0) or 0)
    periodic = (now - last_ts) >= interval
    if force or key != _last_log_key or periodic or n % 10 == 0:
        _last_log_key = key
        state.v3_reject_last_log_ts = now
        log.info(
            f"[REJECT] {code} #{n} px={px:.2f} scn={scn} side={side or '-'} "
            f"candidate={int(trade_candidate)}"
        )

    if n == int(getattr(cfg, "V3_REJECT_REPORT_EVERY", 100) or 100):
        log.info(format_reject_table())

    return row


def get_reject_counters() -> dict[str, int]:
    return dict(_COUNTERS)


def get_reject_ring() -> list[dict]:
    return list(_RING)


def format_reject_table(*, limit: int = 0) -> str:
    """Markdown-benzeri tablo: Sebep | Adet."""
    items = _COUNTERS.most_common()
    if not items:
        return "Sebep\tAdet\n(hic kayit yok)"
    lines = ["Sebep\tAdet", "---\t---"]
    total = 0
    for code, cnt in items:
        if limit and total >= limit:
            break
        lines.append(f"{code}\t{cnt}")
        total += cnt
    lines.append(f"TOPLAM\t{sum(_COUNTERS.values())}")
    return "\n".join(lines)


def reject_stats_from_db(*, limit: int = 100, hours: int = 0) -> list[tuple[str, int]]:
    """DB v3_attribution.reject_reason son N kayit veya saat."""
    try:
        from botlog.db import reject_reason_stats

        return reject_reason_stats(limit=limit, hours=hours)
    except Exception:
        return []


def attach_reject_to_snap(
    snap: dict,
    *,
    reasons: list[str] | None = None,
    trade_candidate: bool = False,
) -> dict:
    """snap['reject_reason'] + tek satir reason."""
    attr = snap.get("attribution") or getattr(state, "v3_last_attribution", None)
    code = resolve_reject_reason(
        snap,
        reasons=reasons,
        trade_candidate=trade_candidate,
        attr=attr,
    )
    snap["reject_reason"] = code
    snap["reason"] = label_for(code)
    if reasons and len(reasons) > 1:
        snap["reason_detail"] = " | ".join(reasons)
    elif reasons:
        snap["reason_detail"] = reasons[0]
    # Oturum sayaci: maybe_log_attribution -> record_reject (tek kaynak)
    return snap
