"""scripts/_dumpfade_stop_wf.py — dumpfade cross-sectional backtest'i YENIDEN kur (orijinal
521-coin/500g script repo'da yok/kaydedilmemis) ve per-islem STOP-LOSS esigini coklu-pencere
walk-forward ile test et. engine/dumpfade_paper.py ile AYNI sinyal/fill mantigi (DUMP_PCT=12.5,
DUMP_CAP=30, OFFSET_BPS=300, MIN_QVOL=25M rolling-30g, MIN_DAYS=60, MAKER_FEE=10).

Amac: canli forward'da (53 islem, kucuk n) -15/-20%% stop en iyi cikti ama in-sample-overfit
supheli. Burada COK DAHA BUYUK n (yuzlerce coin x ~500 gun) + 4 kronolojik pencereli WF ile
hangi stop esigi (varsa) TRAIN+TUM-OOS-PENCERELERDE tutarli pozitif katki veriyor onu bul.
"""
from __future__ import annotations

import json
import time
import urllib.request

CAND_N = 300
DUMP_PCT = 12.5
DUMP_CAP = 30.0
OFFSET_BPS = 300.0
MAKER_FEE = 10.0
MIN_QVOL = 25e6
MIN_DAYS = 60
DAYS = 520

STOPS = [None, -15, -20, -25, -30, -35, -40, -50, -60]


def _get(u: str, timeout=20):
    return json.loads(urllib.request.urlopen(u, timeout=timeout).read())


def candidates() -> list[str]:
    t = _get("https://fapi.binance.com/fapi/v1/ticker/24hr")
    rows = [(x["symbol"], float(x.get("quoteVolume", 0) or 0)) for x in t
            if str(x.get("symbol", "")).endswith("USDT")]
    rows.sort(key=lambda z: -z[1])
    return [s for s, _ in rows[:CAND_N]]


def daily(sym: str, limit: int = DAYS + 40):
    try:
        r = _get(f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1d&limit={limit}")
        return [(int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[7])) for x in r]
    except Exception:
        return None


def gen_trades(bars: list[tuple]) -> list[tuple]:
    """Verilen bir coinin gunluk bar dizisinden (open_time,o,h,l,c,qvol) dump-fade islemlerini uretir.
    Donen: (day_ts, dump_pct, limit_px, low_px, close_px, filled)"""
    out = []
    n = len(bars)
    for i in range(MIN_DAYS, n - 1):
        vols = [bars[j][5] for j in range(max(0, i - 30), i) if bars[j][5] > 0]
        if not vols or (sum(vols) / len(vols)) < MIN_QVOL:
            continue
        o_d, c_d = bars[i][1], bars[i][4]
        if o_d <= 0:
            continue
        dump = (c_d - o_d) / o_d * 100
        if dump > -DUMP_PCT or dump <= -DUMP_CAP:
            continue
        D1 = bars[i + 1]
        o1, l1, c1 = D1[1], D1[3], D1[4]
        if o1 <= 0:
            continue
        lim = o1 * (1 - OFFSET_BPS / 1e4)
        filled = l1 <= lim
        out.append((D1[0], dump, lim, l1, c1, filled))
    return out


def apply_stop(lim: float, low: float, close: float, stop_pct) -> float:
    """Return kapanis(%%). stop_pct None ise stopsuz (gun-sonu close)."""
    mae = (low - lim) / lim * 100.0
    cret = (close - lim) / lim * 100.0
    if stop_pct is not None and mae <= stop_pct:
        return stop_pct
    return cret


def main() -> None:
    print("Aday evren cekiliyor...")
    syms = candidates()
    print(f"{len(syms)} sembol, gunluk {DAYS}g veri cekiliyor (biraz surer)...")
    all_trades = []  # (day_ts, sym, dump, lim, low, close)
    ok = 0
    for idx, sym in enumerate(syms):
        bars = daily(sym)
        if not bars or len(bars) < MIN_DAYS + 30:
            continue
        trs = gen_trades(bars)
        for (ts, dump, lim, low, close, filled) in trs:
            if filled:
                all_trades.append((ts, sym, dump, lim, low, close))
        ok += 1
        if (idx + 1) % 50 == 0:
            print(f"  {idx+1}/{len(syms)} sembol islendi, su ana kadar {len(all_trades)} dolu islem")
        time.sleep(0.05)
    print(f"\nTOPLAM: {ok} sembol basarili, {len(all_trades)} dolu (FILLED) islem\n")
    if not all_trades:
        print("Islem yok, cikiliyor.")
        return

    all_trades.sort(key=lambda z: z[0])
    ts_min, ts_max = all_trades[0][0], all_trades[-1][0]
    span = ts_max - ts_min
    n_win = 4
    edges = [ts_min + span * k / n_win for k in range(n_win + 1)]

    def window_idx(ts):
        for k in range(n_win):
            if edges[k] <= ts <= edges[k + 1] or (k == n_win - 1 and ts > edges[k + 1]):
                return k
        return n_win - 1

    print(f"{'stop':>6} | " + " | ".join(f"Q{k+1}(n,net)" for k in range(n_win)) + " |   TUM-net |  win-pencere")
    for stop in STOPS:
        win_stats = [[0, 0.0] for _ in range(n_win)]
        total_net = 0.0
        for ts, sym, dump, lim, low, close in all_trades:
            ret = apply_stop(lim, low, close, stop)
            bps = ret * 100 - MAKER_FEE
            total_net += bps
            k = window_idx(ts)
            win_stats[k][0] += 1
            win_stats[k][1] += bps
        pos_windows = sum(1 for n, net in win_stats if net > 0)
        row = " | ".join(f"({n:4d},{net:+8.0f})" for n, net in win_stats)
        label = "stopsuz" if stop is None else f"{stop:.0f}%"
        print(f"{label:>6} | {row} | {total_net:+9.0f} |   {pos_windows}/{n_win}")


if __name__ == "__main__":
    main()
