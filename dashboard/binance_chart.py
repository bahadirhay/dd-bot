"""
dashboard/binance_chart.py — Grafik verisi: once bot motoru, sonra cache, en son REST.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

from core.config import cfg

log = logging.getLogger("DashChart")

_cache: dict = {"ts": 0.0, "bars_15m": [], "bars_1h": [], "bars_1m": [], "oi": []}
CACHE_SEC = 25
REST_TIMEOUT_SEC = 8
REST_RETRIES = 3
REST_RETRY_SLEEP_SEC = 2.0


def _parse_klines(rows: list) -> list[dict]:
    if not isinstance(rows, list):
        return []
    out = []
    for row in rows:
        buy = float(row[9]) if len(row) > 9 else 0.0
        vol = float(row[5])
        out.append({
            "ts": row[0] / 1000.0,
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": vol,
            "buy_vol": buy,
            "sell_vol": vol - buy,
            "delta": buy - (vol - buy),
            "taker": (buy / vol) if vol > 0 else 0.5,
        })
    return out


def _bars_from_bot(
    limit_15m: int,
    limit_1h: int,
    limit_1m: int,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Canli botun bellekteki mumlari (REST poller / backfill)."""
    try:
        from engine.structure import get_bars_15m, get_bars_1h
        from engine.bars_1m import get_bars_1m

        bars_15m = list(get_bars_15m(limit_15m) or [])
        bars_1h = list(get_bars_1h(limit_1h) or [])
        bars_1m = list(get_bars_1m(limit_1m) or [])
        return bars_15m, bars_1h, bars_1m
    except Exception as e:
        log.debug(f"Bot mumlari okunamadi: {e}")
        return [], [], []



def fetch_klines(interval: str, limit: int) -> list[dict]:
    rows = _get(
        f"{cfg.REST}/fapi/v1/klines",
        {"symbol": cfg.SYMBOL, "interval": interval, "limit": limit},
    )
    return _parse_klines(rows)


def _get(url: str, params: dict) -> list | dict:
    q = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{q}", method="GET")
    last_err: Exception | None = None
    for attempt in range(REST_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=REST_TIMEOUT_SEC) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = e
            if attempt < REST_RETRIES - 1:
                time.sleep(REST_RETRY_SLEEP_SEC)
    log.warning(f"REST basarisiz ({url.rsplit('/', 1)[-1]}): {last_err}")
    return []


def fetch_15m_klines(limit: int = 96) -> list[dict]:
    return fetch_klines("15m", limit)


def fetch_1h_klines(limit: int = 48) -> list[dict]:
    return fetch_klines("1h", limit)


def fetch_1m_klines(limit: int = 120) -> list[dict]:
    return fetch_klines("1m", min(limit, 500))


def fetch_oi_hist(limit: int = 96) -> list[dict]:
    try:
        rows = _get(
            f"{cfg.REST}/futures/data/openInterestHist",
            {"symbol": cfg.SYMBOL, "period": "15m", "limit": limit},
        )
    except Exception:
        return []
    if not isinstance(rows, list):
        return []
    return [
        {
            "ts": r["timestamp"] / 1000.0,
            "oi": float(r.get("sumOpenInterest", 0) or 0),
        }
        for r in rows
    ]


def series_from_bars(bars: list[dict]) -> dict:
    """CVD (kümülatif delta) + taker + hacim — 15m barlardan."""
    cvd = 0.0
    cvd_series, taker_series, vol_series, ts_list = [], [], [], []
    for b in bars:
        cvd += b.get("delta", 0)
        ts_list.append(b["ts"])
        cvd_series.append(cvd)
        taker_series.append(b.get("taker", 0.5))
        vol_series.append(b.get("volume", 0))
    return {
        "ts": ts_list,
        "cvd": cvd_series,
        "taker": taker_series,
        "volume": vol_series,
    }


def _build_pkg(
    bars_15m: list[dict],
    bars_1h: list[dict],
    bars_1m: list[dict],
    oi: list[dict],
    *,
    source: str,
) -> dict:
    now = time.time()
    return {
        "ts": now,
        "bars": bars_15m,
        "bars_15m": bars_15m,
        "bars_1h": bars_1h,
        "bars_1m": bars_1m,
        "oi": oi,
        "series": series_from_bars(bars_15m) if bars_15m else {},
        "source": source,
    }


def latest_closed_15m_ts() -> float:
    """Grafikteki son kapanmış 15m mumun açılış zamanı (UTC saniye)."""
    from engine.time_align import snap_15m_open

    bars = get_mtf_package().get("bars_15m") or []
    now = time.time()
    if not bars:
        return snap_15m_open(now - 900)
    if len(bars) >= 2 and bars[-1]["ts"] + 900 > now - 30:
        return float(bars[-2]["ts"])
    return float(bars[-1]["ts"])


def get_chart_package(limit: int = 96, force: bool = False) -> dict:
    return get_mtf_package(limit_15m=limit, force=force)


def _dedupe_bars(bars: list[dict]) -> list[dict]:
    """Ayni ts tekrarlarini birlestir (son kayit gecerli)."""
    by_ts: dict[float, dict] = {}
    for bar in bars:
        ts = float(bar.get("ts", 0) or 0)
        if ts > 0:
            by_ts[ts] = bar
    return [by_ts[k] for k in sorted(by_ts)]


def publish_bot_bars_to_cache(
    limit_15m: int = 96,
    limit_1h: int = 48,
    limit_1m: int = 120,
) -> bool:
    """Ana bot thread — bellekteki mumlari dash cache'e yazar (urllib gerektirmez)."""
    b15, b1h, b1m = _bars_from_bot(limit_15m, limit_1h, limit_1m)
    if len(b15) < 10:
        return False
    b15 = _dedupe_bars(b15)
    try:
        from core.state import effective_price
        from engine.intra_15m import get_forming_for_chart

        px = float(effective_price() or 0)
        forming = get_forming_for_chart()
        b15 = _merge_forming_bar(b15, forming, live_px=px)
        b15 = _patch_live_last_bar(b15, px)
    except Exception:
        pass
    pkg = _build_pkg(b15, b1h, b1m, list(_cache.get("oi") or []), source="bot")
    _cache.update(pkg)
    return True


def _bars_contiguous(bars: list[dict], step_sec: int = 900) -> bool:
    if len(bars) < 2:
        return True
    for i in range(1, len(bars)):
        gap = float(bars[i].get("ts", 0) or 0) - float(bars[i - 1].get("ts", 0) or 0)
        if gap > step_sec * 1.5:
            return False
    return True


def _is_open_15m_bar(bar_ts: float, now: float | None = None) -> bool:
    now = now or time.time()
    return bar_ts > 0 and bar_ts + 900 > now - 5


def _patch_live_last_bar(bars: list[dict], price: float) -> list[dict]:
    """Acik 15m mumunun close/high/low degerlerini canli fiyatla esitle."""
    if not bars or price <= 0:
        return bars
    out = list(bars)
    last = dict(out[-1])
    bar_ts = float(last.get("ts", 0) or 0)
    if not _is_open_15m_bar(bar_ts):
        return bars
    last["close"] = price
    last["high"] = max(float(last.get("high", 0) or 0), price)
    lo = float(last.get("low", price) or price)
    last["low"] = min(lo, price) if lo > 0 else price
    last["live"] = True
    out[-1] = last
    return out


def _merge_forming_bar(
    bars: list[dict],
    forming: dict | None,
    live_px: float = 0,
) -> list[dict]:
    """Canli forming mumu son bar ile birlestir; en genis H/L + canli close."""
    if not bars or not forming or not forming.get("open"):
        return bars
    fts = float(forming.get("ts", 0) or 0)
    if fts <= 0:
        return bars
    px = float(live_px or forming.get("close", 0) or 0)
    out = list(bars)
    if out and float(out[-1].get("ts", 0) or 0) == fts:
        prev = out[-1]
        close = px if px > 0 else float(forming.get("close") or prev.get("close") or 0)
        out[-1] = {
            **prev,
            "open": float(forming.get("open") or prev.get("open") or 0),
            "high": max(
                float(prev.get("high", 0) or 0),
                float(forming.get("high", 0) or 0),
                close,
            ),
            "low": min(
                float(prev.get("low", close) or close),
                float(forming.get("low", close) or close),
                close,
            ),
            "close": close,
            "forming": True,
        }
    elif px > 0 or forming.get("close"):
        out.append(dict(forming))
    return out


def get_mtf_package(
    limit_15m: int = 96,
    limit_1h: int = 48,
    limit_1m: int = 120,
    force: bool = False,
) -> dict:
    now = time.time()
    if (
        not force
        and _cache.get("bars_15m")
        and (now - float(_cache.get("ts", 0) or 0)) < CACHE_SEC
        and _bars_contiguous(_cache.get("bars_15m") or [], 900)
    ):
        return _cache

    bars_15m: list[dict] = []
    bars_1h: list[dict] = []
    bars_1m: list[dict] = []
    source = "cache"

    publish_bot_bars_to_cache(limit_15m, limit_1h, limit_1m)
    cached = list(_cache.get("bars_15m") or [])
    if len(cached) >= 10 and _bars_contiguous(cached, 900):
        return _cache

    rest_15: list[dict] = []
    rest_1h: list[dict] = []
    rest_1m: list[dict] = []
    try:
        rest_15 = _dedupe_bars(fetch_15m_klines(limit_15m))
        rest_1h = fetch_1h_klines(limit_1h)
        rest_1m = fetch_1m_klines(limit_1m)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        log.warning(f"Binance REST grafik atlandi: {e}")

    if rest_15 and len(rest_15) >= 10:
        bars_15m, bars_1h, bars_1m = rest_15, rest_1h or [], rest_1m or []
        source = "binance_rest"
        try:
            from core.state import effective_price
            from engine.intra_15m import get_forming_for_chart

            px = float(effective_price() or 0)
            forming = get_forming_for_chart()
            bars_15m = _merge_forming_bar(bars_15m, forming, live_px=px)
            bars_15m = _patch_live_last_bar(bars_15m, px)
        except Exception:
            pass
    elif cached:
        return _cache
    elif _cache.get("bars_15m"):
        bars_15m = list(_cache.get("bars_15m") or [])
        bars_1h = list(_cache.get("bars_1h") or [])
        bars_1m = list(_cache.get("bars_1m") or [])
        source = str(_cache.get("source") or "cache")

    oi: list[dict] = []
    if bars_15m and source.startswith("binance"):
        try:
            oi = fetch_oi_hist(limit_15m)
        except Exception:
            oi = list(_cache.get("oi") or [])

    if bars_15m:
        pkg = _build_pkg(bars_15m, bars_1h, bars_1m, oi, source=source)
        _cache.update(pkg)
    return _cache
