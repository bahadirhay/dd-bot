"""TM 30m/1h — duzgun dogrulama: 4 pencere, 3 coin, Martingale YOK.

Binance futures resmi kline, paginated (~375 gun).
"""
from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.trend_magic_v3 import TrendMagicParams, run_backtest

REST = "https://fapi.binance.com"
SYMBOLS = ("ETHUSDT", "BTCUSDT", "SOLUSDT")
# 30m: 48 bar/gun * 375 ~ 18000; 1h: 24*375 ~ 9000
TARGETS = {
    "30m": 48 * 375,
    "1h": 24 * 375,
}
FEE = 8.0
SLIP = 2.0


def _parse(rows: list) -> list[dict]:
    out = []
    for row in rows:
        out.append(
            {
                "ts": row[0] / 1000.0,
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
            }
        )
    return out


def fetch_klines_paged(symbol: str, interval: str, n_bars: int) -> list[dict]:
    """endTime ile geriye dogru sayfa sayfa cek (max 1500/istek)."""
    out: list[dict] = []
    end_ms: int | None = None
    while len(out) < n_bars:
        params: dict = {"symbol": symbol, "interval": interval, "limit": 1500}
        if end_ms is not None:
            params["endTime"] = end_ms
        q = urllib.parse.urlencode(params)
        req = urllib.request.Request(f"{REST}/fapi/v1/klines?{q}", method="GET")
        with urllib.request.urlopen(req, timeout=30) as resp:
            rows = json.loads(resp.read().decode("utf-8"))
        if not rows:
            break
        chunk = _parse(rows)
        # daha eski sayfa: basina ekle
        out = chunk + out
        end_ms = int(rows[0][0]) - 1
        if len(rows) < 1500:
            break
        time.sleep(0.15)
    # unique by ts, sort, trim
    by_ts = {b["ts"]: b for b in out}
    bars = [by_ts[k] for k in sorted(by_ts)]
    return bars[-n_bars:] if len(bars) > n_bars else bars


def summarize(trades) -> dict:
    if not trades:
        return {"net": 0.0, "n": 0, "wr": 0.0}
    # Martingale kapali: qty hep 1, yine de carp
    pnls = [t.pnl_bps * t.qty for t in trades]
    wins = sum(1 for x in pnls if x > 0)
    return {
        "net": round(sum(pnls), 1),
        "n": len(pnls),
        "wr": round(100 * wins / len(pnls), 1),
    }


def four_windows(bars: list[dict], p: TrendMagicParams) -> dict:
    n = len(bars)
    if n < 200:
        return {"error": "insufficient", "n_bars": n}
    # esit 4 dilim
    chunk = n // 4
    quarters = []
    for qi in range(4):
        a = qi * chunk
        b = (qi + 1) * chunk if qi < 3 else n
        slice_bars = bars[a:b]
        trades = run_backtest(slice_bars, p, fee_bps=FEE, slip_bps=SLIP)
        st = summarize(trades)
        st["n_bars"] = len(slice_bars)
        quarters.append(st)
    # tum pencere (ham edge, tek sefer)
    all_tr = run_backtest(bars, p, fee_bps=FEE, slip_bps=SLIP)
    total = summarize(all_tr)
    total["n_bars"] = n
    pos_q = sum(1 for q in quarters if q["net"] > 0)
    return {
        "quarters": quarters,
        "total": total,
        "pos_quarters": pos_q,
        "robust": pos_q >= 3 and total["net"] > 0,
    }


def main() -> None:
    # Martingale KAPALI
    p = TrendMagicParams(volume_increase_pct=0.0)
    print("=== Trend Magic — duzgun test ===")
    print(f"fee={FEE} slip={SLIP}x2 | martingale=OFF | windows=4 | coins={','.join(SYMBOLS)}")
    print()

    verdicts = []
    for interval, n_need in TARGETS.items():
        print(f"--- {interval} (~{n_need} bar / ~375 gun) ---")
        rows = []
        for sym in SYMBOLS:
            print(f"  yukleniyor {sym} {interval}...", flush=True)
            bars = fetch_klines_paged(sym, interval, n_need)
            days = (bars[-1]["ts"] - bars[0]["ts"]) / 86400 if len(bars) > 1 else 0
            res = four_windows(bars, p)
            if res.get("error"):
                print(f"  {sym}: VERI YOK n={res.get('n_bars')}")
                continue
            qs = res["quarters"]
            tot = res["total"]
            qnets = "  ".join(f"Q{i+1}={q['net']:+.0f}" for i, q in enumerate(qs))
            print(
                f"  {sym:8} n_bars={len(bars):5d} (~{days:.0f}g)  "
                f"{qnets}  TOPLAM={tot['net']:+.0f}  "
                f"n={tot['n']} wr={tot['wr']}%  "
                f"({res['pos_quarters']}/4)  "
                f"{'OK' if res['robust'] else 'FAIL'}"
            )
            rows.append((sym, res))
            verdicts.append((interval, sym, res))
        print()

    print("=== VERDICT ===")
    ok = [v for v in verdicts if v[2].get("robust")]
    fail = [v for v in verdicts if not v[2].get("robust")]
    if not verdicts:
        print("Veri yok — test kosulamadı.")
        return
    print(f"  Robust gecen: {len(ok)}/{len(verdicts)}")
    for iv, sym, res in fail:
        print(f"  FAIL {iv} {sym}: total={res['total']['net']:+.0f} posQ={res['pos_quarters']}/4")
    for iv, sym, res in ok:
        print(f"  OK   {iv} {sym}: total={res['total']['net']:+.0f} posQ={res['pos_quarters']}/4")

    # Bot karar ozeti (ETH 30m odaklı)
    eth30 = next((v for v in verdicts if v[0] == "30m" and v[1] == "ETHUSDT"), None)
    if eth30:
        r = eth30[2]
        print()
        if r["total"]["net"] < 0 or r["pos_quarters"] < 3:
            print(
                f"SONUC: TM 30m ETH ZARARLI / ROBUST DEGIL "
                f"(net={r['total']['net']:+.0f} bps, {r['pos_quarters']}/4 pencere)"
            )
        else:
            print(
                f"SONUC: TM 30m ETH ROBUST GORUNUYOR "
                f"(net={r['total']['net']:+.0f} bps, {r['pos_quarters']}/4)"
            )


if __name__ == "__main__":
    main()
