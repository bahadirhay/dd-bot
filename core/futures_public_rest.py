"""
core/futures_public_rest.py — Binance USDT-M herkese açık REST (ai-treding uyumlu).

İmzalı emir: execution/executor.py
Retry + üstel bekleme; WS kopunca market_recovery bu modülü kullanır.
"""
from __future__ import annotations

import asyncio
import socket
from typing import Any, Optional

import aiohttp

from core.config import cfg
from core.logger import get_logger

log = get_logger("PublicREST")

_TIMEOUT = aiohttp.ClientTimeout(total=30, connect=15)
_session: aiohttp.ClientSession | None = None
_connector: aiohttp.TCPConnector | None = None


def _build_connector() -> aiohttp.TCPConnector:
    return aiohttp.TCPConnector(
        ttl_dns_cache=300,
        family=socket.AF_INET,
        limit=20,
        limit_per_host=10,
        enable_cleanup_closed=True,
    )


async def _get_session() -> aiohttp.ClientSession:
    global _session, _connector
    if _session is None or _session.closed:
        _connector = _build_connector()
        _session = aiohttp.ClientSession(
            timeout=_TIMEOUT,
            connector=_connector,
        )
    return _session


async def close_public_rest_session() -> None:
    global _session, _connector
    if _session is not None and not _session.closed:
        await _session.close()
    _session = None
    _connector = None


async def _get(
    endpoint: str,
    params: Optional[dict] = None,
    retries: int = 3,
) -> Any:
    url = f"{cfg.REST}{endpoint}"
    last_err: Optional[Exception] = None
    for attempt in range(retries):
        try:
            sess = await _get_session()
            async with sess.get(url, params=params or {}) as r:
                r.raise_for_status()
                return await r.json(content_type=None)
        except Exception as e:
            last_err = e
            await close_public_rest_session()
            if attempt < retries - 1:
                wait = 2**attempt
                log.warning(f"Public REST {endpoint} ({attempt + 1}/{retries}): {e}")
                await asyncio.sleep(wait)
    log.error(f"Public REST başarısız {endpoint}: {last_err}")
    return None


async def get_book_ticker(symbol: str | None = None) -> Optional[dict]:
    sym = symbol or cfg.SYMBOL
    data = await _get("/fapi/v1/ticker/bookTicker", {"symbol": sym})
    if not data or not isinstance(data, dict):
        return None
    return {
        "bid": float(data.get("bidPrice", 0) or 0),
        "ask": float(data.get("askPrice", 0) or 0),
        "bid_qty": float(data.get("bidQty", 0) or 0),
        "ask_qty": float(data.get("askQty", 0) or 0),
    }


async def get_agg_trades(
    symbol: str | None = None,
    limit: int = 1000,
    start_time_ms: Optional[int] = None,
) -> list[dict]:
    sym = symbol or cfg.SYMBOL
    params: dict = {"symbol": sym, "limit": min(limit, 1000)}
    if start_time_ms:
        params["startTime"] = int(start_time_ms)
    data = await _get("/fapi/v1/aggTrades", params)
    if not data or not isinstance(data, list):
        return []
    out = []
    for row in data:
        ts = float(row["T"]) / 1000.0
        qty = float(row["q"])
        is_sell = bool(row["m"])
        delta = -qty if is_sell else qty
        out.append(
            {
                "id": int(row.get("a", 0) or 0),
                "ts": ts,
                "price": float(row["p"]),
                "qty": qty,
                "delta": delta,
                "is_sell": is_sell,
            }
        )
    return out


async def get_klines(
    interval: str,
    limit: int = 5,
    symbol: str | None = None,
) -> list[dict]:
    sym = symbol or cfg.SYMBOL
    data = await _get(
        "/fapi/v1/klines",
        {"symbol": sym, "interval": interval, "limit": limit},
    )
    if not data:
        return []
    bars = []
    for k in data:
        bars.append(
            {
                "ts": k[0] / 1000,
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "buy_vol": float(k[9]),
                "sell_vol": float(k[5]) - float(k[9]),
                "delta": float(k[9]) - (float(k[5]) - float(k[9])),
            }
        )
    return bars


async def get_premium_index(symbol: str | None = None) -> Optional[dict]:
    sym = symbol or cfg.SYMBOL
    data = await _get("/fapi/v1/premiumIndex", {"symbol": sym})
    if not data or not isinstance(data, dict):
        return None
    return {
        "mark_price": float(data.get("markPrice", 0) or 0),
        "funding_rate": float(data.get("lastFundingRate", 0) or 0),
    }
