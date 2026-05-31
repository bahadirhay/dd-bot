"""
engine/time_align.py — Binance saati ↔ UTC ↔ 15m mum hizalama.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

# Binance web arayüzü Türkiye'de genelde UTC+3 gösterir
BINANCE_TZ_OFFSET_HOURS = 3
ANALYSIS_WINDOW_MIN = 15


def snap_15m_open(ts: float) -> float:
    """15m mumun açılış zamanına hizala (Binance mum etiketi = açılış)."""
    period = 900
    return float(int(ts // period) * period)


def utc_to_binance_local(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc) + timedelta(
        hours=BINANCE_TZ_OFFSET_HOURS
    )


def binance_local_to_utc(dt: datetime) -> float:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    utc_dt = dt - timedelta(hours=BINANCE_TZ_OFFSET_HOURS)
    return utc_dt.replace(tzinfo=timezone.utc).timestamp()


def parse_when(when: str | float, tz_mode: str = "tr") -> tuple[float, dict]:
    """
    Kullanici girisi → UTC timestamp + aciklama metni.
    tz_mode: 'tr' = Binance'te gorulen saat (UTC+3), 'utc' = dogrudan UTC.
    """
    if isinstance(when, (int, float)):
        ts = float(when)
        meta = _meta_for_ts(ts, tz_mode, raw_input=str(when))
        return snap_15m_open(ts), meta

    s = (when or "").strip().replace(" UTC", "").replace(" TR", "")
    if not s:
        raise ValueError("Saat girilmedi")

    fmts = (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d.%m.%Y %H:%M",
        "%d.%m.%Y %H:%M:%S",
    )
    dt = None
    for fmt in fmts:
        try:
            dt = datetime.strptime(s, fmt)
            break
        except ValueError:
            continue
    if dt is None:
        raise ValueError(
            f"Saat anlasilmadi: {when!r}. Ornek: 2026-05-22 11:30"
        )

    if tz_mode == "utc":
        ts = dt.replace(tzinfo=timezone.utc).timestamp()
    else:
        ts = binance_local_to_utc(dt)

    ts_snap = snap_15m_open(ts)
    meta = _meta_for_ts(ts_snap, tz_mode, raw_input=s, requested_ts=ts)
    return ts_snap, meta


def _meta_for_ts(
    ts_snap: float,
    tz_mode: str,
    raw_input: str,
    requested_ts: float | None = None,
) -> dict:
    utc_dt = datetime.fromtimestamp(ts_snap, tz=timezone.utc)
    tr_dt = utc_to_binance_local(ts_snap)
    win = ANALYSIS_WINDOW_MIN
    return {
        "raw_input": raw_input,
        "tz_mode": tz_mode,
        "ts_utc": ts_snap,
        "requested_ts": requested_ts or ts_snap,
        "utc_human": utc_dt.strftime("%Y-%m-%d %H:%M UTC"),
        "tr_human": tr_dt.strftime("%Y-%m-%d %H:%M") + " (Binance TR)",
        "window": f"±{win} dk (toplam ~{win * 2} dk)",
        "snap_note": (
            "15m mum baslangicina hizalandi"
            if requested_ts and abs(requested_ts - ts_snap) > 60
            else "15m mum baslangici"
        ),
    }


def format_time_sync_line(meta: dict, db_nearest: str | None = None) -> str:
    lines = [
        f"Binance (TR): {meta['tr_human']}",
        f"Bot sorgusu (UTC): {meta['utc_human']}",
        f"15m mum: {meta['snap_note']}",
        f"Analiz araligi: {meta['window']}",
    ]
    if db_nearest:
        lines.append(f"DB en yakin kayit: {db_nearest}")
    return " | ".join(lines)
