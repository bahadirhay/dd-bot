"""
backtest/replay.py — Offline parametre sweep harness.

market_snapshots'taki yogun fiyat tick serisinden (price + taker_ratio + cvd)
15m OHLC yeniden kurar, Pine S/R hesaplar, kenar fade kurulumlarini canli kapilarla
(akis-teyit, zone yakinligi, TP1/SL bps) simule eder ve parametre izgarasini tarar.

YAKLASIK backtest (rekonstrukte OHLC, runner ust yonu modellenmez, sayisal yapi
skoru yok). Amac: parametre setlerini GORECELI kiyaslamak — mutlak PnL tahmini degil.

Kullanim:
    python -m backtest.replay              # veri sagligi + tek kosu
    python -m backtest.replay sweep        # parametre izgarasi
"""
from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass

from core.config import cfg


# ── Veri katmani ─────────────────────────────────────────────────────────────

def load_series(limit: int = 0) -> list[dict]:
    """Tum snapshot'lardan (ts, price, taker_ratio, cvd_5m) — ts artan, dedup."""
    con = sqlite3.connect(cfg.DB_PATH)
    con.row_factory = sqlite3.Row
    q = (
        "SELECT ts, price, "
        "json_extract(payload_json,'$.taker_ratio') AS tr, "
        "json_extract(payload_json,'$.cvd_5m') AS cvd "
        "FROM market_snapshots WHERE price>0 ORDER BY ts ASC"
    )
    rows = con.execute(q).fetchall()
    con.close()
    out: list[dict] = []
    last_ts = -1.0
    for r in rows:
        ts = float(r["ts"] or 0)
        if ts <= last_ts:
            continue
        last_ts = ts
        out.append({
            "ts": ts,
            "price": float(r["price"] or 0),
            "taker": float(r["tr"]) if r["tr"] is not None else None,
            "cvd": float(r["cvd"]) if r["cvd"] is not None else None,
        })
    if limit and len(out) > limit:
        out = out[-limit:]
    return out


def build_ohlc(series: list[dict], tf_sec: int) -> list[dict]:
    """Tick serisini tf_sec kovalarina OHLC olarak topla."""
    bars: list[dict] = []
    cur_bucket = -1
    o = h = l = c = 0.0
    bts = 0.0
    for s in series:
        px = s["price"]
        if px <= 0:
            continue
        b = int(s["ts"] // tf_sec)
        if b != cur_bucket:
            if cur_bucket >= 0:
                bars.append({"ts": bts, "open": o, "high": h, "low": l, "close": c})
            cur_bucket = b
            bts = b * tf_sec
            o = h = l = c = px
        else:
            h = max(h, px); l = min(l, px); c = px
    if cur_bucket >= 0:
        bars.append({"ts": bts, "open": o, "high": h, "low": l, "close": c})
    return bars


def levels_at(bars15: list[dict], price: float) -> tuple[float, float]:
    """Verilen 15m bar penceresinden aktif S/R (Pine). (s, r) ya da (0,0)."""
    try:
        from engine.sr_calculator import compute_sr_snapshot

        snap = compute_sr_snapshot(bars15, price=price, timeframe="15m")
        if not snap:
            return 0.0, 0.0
        s = float(getattr(snap.active_support, "price", 0) or 0) if snap.active_support else 0.0
        r = float(getattr(snap.active_resistance, "price", 0) or 0) if snap.active_resistance else 0.0
        return s, r
    except Exception:
        return 0.0, 0.0


# ── Simulasyon ───────────────────────────────────────────────────────────────

@dataclass
class Params:
    zone_bps: float = 30.0       # kenara bu kadar yakinsa fade adayi
    flow_ratio: float = 0.50     # akis-teyit esigi (SHORT: taker<=, LONG: taker>=)
    tp1_bps: float = 55.0        # TP1 mesafesi
    sl_bps: float = 55.0         # SL mesafesi
    trail_bps: float = 30.0      # TP1 sonrasi runner yapisal trail (en iyiden geri)
    cooldown_sec: float = 120.0  # cikis sonrasi yeniden giris beklemesi
    fee_bps: float = 4.0         # gidis-donus komisyon+kayma


@dataclass
class Result:
    n: int = 0
    wins: int = 0
    net_bps: float = 0.0
    tp1_reach: int = 0

    @property
    def winrate(self) -> float:
        return self.wins / self.n if self.n else 0.0

    def __str__(self) -> str:
        return (f"n={self.n:4d} wr={100*self.winrate:4.0f}% "
                f"TP1ulas={100*self.tp1_reach/max(1,self.n):4.0f}% "
                f"net={self.net_bps:+8.1f}bps")


def simulate(series: list[dict], p: Params, *, recompute_sec: int = 900,
             shared_bars: list[dict] | None = None) -> Result:
    res = Result()
    if len(series) < 1000:
        return res

    bars15 = shared_bars if shared_bars is not None else build_ohlc(series, 900)
    if len(bars15) < 60:
        return res

    s_lv = r_lv = 0.0
    last_recompute_bucket = -1
    in_pos = False
    phase = ""            # "pre_tp1" | "runner"
    side = ""
    entry = sl = tp1 = 0.0
    best_fav = 0.0        # runner: en iyi lehte fiyat
    cool_until = 0.0
    min_bars = 55

    bar_ts = [b["ts"] for b in bars15]

    def _close(pnl_bps: float, ts: float, win: bool):
        nonlocal in_pos, cool_until
        res.net_bps += pnl_bps
        if win:
            res.wins += 1
        in_pos = False
        cool_until = ts + p.cooldown_sec

    import bisect
    for s in series:
        ts = s["ts"]; px = s["price"]; taker = s["taker"]
        if px <= 0:
            continue

        # Pozisyon yonetimi (fill) — TP1 partial + runner trail
        if in_pos:
            if phase == "pre_tp1":
                hit_sl = (px >= sl) if side == "SHORT" else (px <= sl)
                hit_tp = (px <= tp1) if side == "SHORT" else (px >= tp1)
                if hit_sl:
                    _close(-(p.sl_bps + p.fee_bps), ts, False)
                    continue
                if hit_tp:
                    res.tp1_reach += 1
                    phase = "runner"; best_fav = px  # yarisi TP1'de kilitli
                continue
            else:  # runner: en iyiyi izle, trail'e degince cik
                if side == "SHORT":
                    best_fav = min(best_fav, px)
                    trail_stop = best_fav * (1 + p.trail_bps / 1e4)
                    if px >= trail_stop:
                        runner_bps = (entry - trail_stop) / entry * 1e4
                        _close(0.5 * (p.tp1_bps - p.fee_bps) + 0.5 * (runner_bps - p.fee_bps), ts, True)
                        continue
                else:
                    best_fav = max(best_fav, px)
                    trail_stop = best_fav * (1 - p.trail_bps / 1e4)
                    if px <= trail_stop:
                        runner_bps = (trail_stop - entry) / entry * 1e4
                        _close(0.5 * (p.tp1_bps - p.fee_bps) + 0.5 * (runner_bps - p.fee_bps), ts, True)
                        continue
                continue

        if ts < cool_until:
            continue

        # S/R guncelle (kapali 15m bar bazli, recompute_sec'te bir)
        bi = bisect.bisect_right(bar_ts, ts) - 1
        if bi < min_bars:
            continue
        if bi != last_recompute_bucket:
            window = bars15[max(0, bi - 200):bi]  # kapali barlar
            s_lv, r_lv = levels_at(window, px)
            last_recompute_bucket = bi
        if s_lv <= 0 or r_lv <= s_lv:
            continue

        # Zone + fade adayi
        near_r = (r_lv - px) / px * 1e4
        near_s = (px - s_lv) / px * 1e4
        cand = ""
        if 0 <= near_r <= p.zone_bps:
            cand = "SHORT"
        elif 0 <= near_s <= p.zone_bps:
            cand = "LONG"
        if not cand:
            continue

        # Akis-teyit kapisi
        if taker is not None:
            if cand == "SHORT" and taker > p.flow_ratio:
                continue
            if cand == "LONG" and taker < p.flow_ratio:
                continue

        # Giris
        side = cand; entry = px; in_pos = True; phase = "pre_tp1"
        if side == "SHORT":
            sl = entry * (1 + p.sl_bps / 1e4); tp1 = entry * (1 - p.tp1_bps / 1e4)
        else:
            sl = entry * (1 - p.sl_bps / 1e4); tp1 = entry * (1 + p.tp1_bps / 1e4)
        res.n += 1

    return res


_GRID_FLOW = [0.45, 0.50, 0.55]
_GRID_TP1 = [45, 55, 70]
_GRID_SL = [40, 55, 70]
_GRID_ZONE = [25, 35]


def _grid_results(series: list[dict]) -> list[tuple]:
    """Izgarayi tarar; bars bir kez kurulur (paylasimli)."""
    bars = build_ohlc(series, 900)
    rows = []
    for fr in _GRID_FLOW:
        for tp in _GRID_TP1:
            for sl in _GRID_SL:
                for zn in _GRID_ZONE:
                    p = Params(zone_bps=zn, flow_ratio=fr, tp1_bps=tp, sl_bps=sl)
                    r = simulate(series, p, shared_bars=bars)
                    if r.n >= 10:
                        rows.append((r.net_bps, fr, tp, sl, zn, r))
    rows.sort(reverse=True)
    return rows


def sweep(series: list[dict]) -> None:
    rows = _grid_results(series)
    print("\n=== EN IYI 12 PARAMETRE (tum donem) ===")
    print("flow tp1  sl  zone | sonuc")
    for net, fr, tp, sl, zn, r in rows[:12]:
        print(f"{fr:.2f} {tp:3d} {sl:3d} {zn:4d} | {r}")
    print("\n=== EN KOTU 3 ===")
    for net, fr, tp, sl, zn, r in rows[-3:]:
        print(f"{fr:.2f} {tp:3d} {sl:3d} {zn:4d} | {r}")


def walk_forward(series: list[dict]) -> None:
    """Overfit testi: ilk yarida en iyi -> ikinci yarida dogrula (ve tersi)."""
    mid = len(series) // 2
    train, test = series[:mid], series[mid:]
    print(f"\nwalk-forward: train n={len(train)}  test n={len(test)}")

    tr = _grid_results(train)
    te = _grid_results(test)
    te_map = {(fr, tp, sl, zn): r for _, fr, tp, sl, zn, r in te}
    tr_map = {(fr, tp, sl, zn): r for _, fr, tp, sl, zn, r in tr}

    print("\n=== TRAIN en iyi 8 -> TEST'te nasil? ===")
    print("flow tp1  sl  zone |  TRAIN            |  TEST")
    for net, fr, tp, sl, zn, r in tr[:8]:
        t = te_map.get((fr, tp, sl, zn))
        ts = str(t) if t else "(yetersiz n)"
        print(f"{fr:.2f} {tp:3d} {sl:3d} {zn:4d} | {r} | {ts}")

    # ROBUST: her iki yarida da pozitif olan, min(net) en yuksek
    robust = []
    for key, rtr in tr_map.items():
        rte = te_map.get(key)
        if rte and rtr.net_bps > 0 and rte.net_bps > 0:
            robust.append((min(rtr.net_bps, rte.net_bps), key, rtr, rte))
    robust.sort(reverse=True)
    print("\n=== ROBUST (iki yarida da +, min-net'e gore) ===")
    if not robust:
        print("  YOK — hicbir parametre iki yarida da pozitif degil (overfit/rejim-bagimli).")
    for mn, key, rtr, rte in robust[:6]:
        fr, tp, sl, zn = key
        print(f"flow={fr:.2f} tp1={tp} sl={sl} zone={zn} | train {rtr} | test {rte}")


if __name__ == "__main__":
    print("Veri yukleniyor...")
    series = load_series()
    print(f"tick serisi: n={len(series)}")
    bars15 = build_ohlc(series, 900)
    print(f"15m bar: n={len(bars15)}  (ilk close={bars15[0]['close']:.2f} son={bars15[-1]['close']:.2f})")
    s, r = levels_at(bars15[-200:-1], bars15[-1]["close"])
    print(f"ornek S/R (son pencere): S={s:.2f} R={r:.2f} px={bars15[-1]['close']:.2f}")

    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "sweep":
        sweep(series)
    elif mode in ("wf", "walkforward", "walk_forward"):
        walk_forward(series)
    else:
        r = simulate(series, Params())
        print("\nvarsayilan parametre kosusu (runner modeli):")
        print(" ", r)
