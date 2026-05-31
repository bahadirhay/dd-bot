"""
CVD doğrulama: son 5 dk Binance aggTrade REST vs bot formülü.
Çalıştır: PYTHONPATH=. python scripts/diag_cvd.py
(main.py açıkken ikinci terminalde state ile karşılaştırın)
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

from core.config import cfg

WINDOW = 300.0


def fetch_agg_trades(limit: int = 1000) -> list[dict]:
    url = f"{cfg.REST}/fapi/v1/aggTrades"
    params = {"symbol": cfg.SYMBOL, "limit": limit}
    q = urllib.parse.urlencode(params)
    with urllib.request.urlopen(f"{url}?{q}", timeout=15) as r:
        rows = json.loads(r.read().decode())
    out = []
    for row in rows:
        ts = row["T"] / 1000.0
        qty = float(row["q"])
        is_sell = bool(row["m"])
        delta = -qty if is_sell else qty
        out.append({"ts": ts, "qty": qty, "delta": delta, "is_sell": is_sell})
    return out


def rolling_cvd(trades: list[dict], window_sec: float = WINDOW) -> dict:
    now = time.time()
    cutoff = now - window_sec
    buy = sell = 0.0
    n = 0
    for t in trades:
        if t["ts"] < cutoff:
            continue
        n += 1
        if t["delta"] > 0:
            buy += t["delta"]
        else:
            sell += -t["delta"]
    return {
        "n_ticks": n,
        "buy_vol": buy,
        "sell_vol": sell,
        "cvd_5m": buy - sell,
        "taker_ratio": buy / (buy + sell) if (buy + sell) > 0 else 0.5,
        "cutoff_utc": time.strftime("%H:%M:%S", time.gmtime(cutoff)),
    }


def main():
    trades = fetch_agg_trades(1000)
    if not trades:
        print("aggTrade verisi yok")
        return
    r = rolling_cvd(trades)
    print(f"Binance REST son {int(WINDOW)}s (max 1000 trade):")
    print(f"  tick sayisi: {r['n_ticks']}")
    print(f"  buy_vol:     {r['buy_vol']:.4f} ETH")
    print(f"  sell_vol:    {r['sell_vol']:.4f} ETH")
    print(f"  CVD 5m:      {r['cvd_5m']:+.4f} ETH")
    print(f"  taker:       {r['taker_ratio']:.2%}")
    print(f"  pencere:     {r['cutoff_utc']} UTC -> simdi")

    try:
        from core.state import state
        age = time.time() - state.trade_last_update if state.trade_last_update else -1
        print("\nBot state (main.py calisiyorsa):")
        print(f"  trade_age:   {age:.1f}s")
        print(f"  cvd_5m:      {state.cvd_5m:+.4f} ETH")
        print(f"  buy/sell:    {state.buy_vol_5m:.4f} / {state.sell_vol_5m:.4f}")
        print(f"  ticks deque: {len(state.ticks)}")
        diff = state.cvd_5m - r["cvd_5m"]
        print(f"  fark (bot - REST): {diff:+.4f} ETH")
        if abs(diff) > 50 and age < 30:
            print("  UYARI: buyuk fark — deque maxlen veya feed kopuklugu olabilir")
    except ImportError:
        pass


if __name__ == "__main__":
    main()
