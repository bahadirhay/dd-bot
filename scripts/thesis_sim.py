"""Replay bot.log with thesis-based rules (approximation)."""
import re
from pathlib import Path

log = Path(__file__).resolve().parents[1] / "data" / "logs" / "bot.log"
lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
start = 517650
chunk = lines[start:]

re_15m = re.compile(r"^(\d{2}:\d{2}:\d{2}).*15m KAPANDI.*C=([\d.]+)")
re_levels = re.compile(r"aktif=([\d.]+)/([\d.]+)")
re_v3 = re.compile(
    r"^(\d{2}:\d{2}:\d{2}).*\[V3\] karar=(\w+).*senaryo=(\w+).*teyit=(\w+)"
)
re_trade_open = re.compile(
    r"^(\d{2}:\d{2}:\d{2}).*POZİSYON AÇILDI \(CANLI\) (\w+) @ ([\d.]+).*kaynak=(\w+)"
)
re_trade_close = re.compile(
    r"^(\d{2}:\d{2}:\d{2}).*POZİSYON KAPATILDI: (\w+) @ ([\d.]+).*PnL=([-\d.]+).*sebep=(.+)"
)
re_reverse = re.compile(r"^(\d{2}:\d{2}:\d{2}).*Ters sinyal: (\w+) → (\w+)")

BPS = 12.0  # ~bar_noise at 2030


def break_thr(level: float, direction: str) -> float:
    if direction == "LONG":
        return level * (1 + BPS / 10000)
    return level * (1 - BPS / 10000)


actual = []
for ln in chunk:
    m = re_trade_open.search(ln)
    if m:
        actual.append(("OPEN", m.group(1), m.group(2), float(m.group(3)), m.group(4)))
    m = re_trade_close.search(ln)
    if m:
        actual.append(
            (
                "CLOSE",
                m.group(1),
                m.group(2),
                float(m.group(3)),
                float(m.group(4)),
                m.group(5).strip(),
            )
        )
    m = re_reverse.search(ln)
    if m:
        actual.append(("REVERSE", m.group(1), m.group(2), m.group(3)))

bars = []
last_key = None
cur_s, cur_r = 2012.96, 2029.50
for ln in chunk:
    m = re_levels.search(ln)
    if m and "[LEVELS]" in ln:
        cur_s, cur_r = float(m.group(1)), float(m.group(2))
    m = re_15m.search(ln)
    if m:
        t, close = m.group(1), float(m.group(2))
        key = (t[:5], round(close, 2))
        if key == last_key:
            continue
        last_key = key
        bars.append({"t": t, "close": close, "S": cur_s, "R": cur_r, "v3": None})
    m = re_v3.search(ln)
    if m and "15m-kapanis" in ln:
        t, karar, senaryo, teyit = m.group(1), m.group(2), m.group(3), m.group(4)
        for b in reversed(bars):
            if b["t"][:5] == t[:5] and b["v3"] is None:
                b["v3"] = {"karar": karar, "senaryo": senaryo, "teyit": teyit}
                break

pos = None
sim = []

for b in bars:
    t, c, s, r = b["t"], b["close"], b["S"], b["R"]
    v3 = b["v3"] or {}

    if pos:
        side, entry, br, typ = pos["side"], pos["entry"], pos["break_R"], pos["type"]
        if typ == "BREAKOUT_BUY" and side == "LONG":
            fail = break_thr(br, "SHORT")
            if c < fail:
                pnl = (c - entry) * 0.025
                sim.append(
                    f"{t} thesis_failed LONG close~{c:.2f} entry={entry:.2f} "
                    f"broken_R={br:.2f} fail<{fail:.2f} pnl~{pnl:+.4f}"
                )
                if v3.get("karar") == "SHORT" and v3.get("teyit") == "evet":
                    sim.append(f"  -> reverse SHORT (15m gate OK)")
                    pos = {"side": "SHORT", "entry": c, "break_R": r, "type": "RANGE_SELL"}
                else:
                    pos = None
        elif typ == "RANGE_SELL" and side == "SHORT":
            fail = break_thr(br, "LONG")
            if c > fail:
                pnl = (entry - c) * 0.025
                sim.append(
                    f"{t} thesis_failed SHORT close~{c:.2f} entry={entry:.2f} "
                    f"broken_R={br:.2f} fail>{fail:.2f} pnl~{pnl:+.4f}"
                )
                if v3.get("karar") == "LONG" and v3.get("teyit") == "evet":
                    br_long = r
                    thr = break_thr(br_long, "LONG")
                    if c > thr:
                        sim.append(f"  -> reverse LONG (thesis fail + breakout gate)")
                        pos = {
                            "side": "LONG",
                            "entry": c,
                            "break_R": br_long,
                            "type": "BREAKOUT_BUY",
                        }
                    else:
                        sim.append(f"  -> no reverse (close {c:.2f} <= thr {thr:.2f})")
                        pos = None
                else:
                    pos = None

    if not pos:
        if v3.get("karar") == "LONG" and v3.get("teyit") == "evet":
            if v3.get("senaryo") == "BREAKOUT_BUY":
                thr = break_thr(r, "LONG")
                if c > thr:
                    sim.append(
                        f"{t} ENTRY LONG breakout close={c:.2f} R={r:.2f} thr={thr:.2f}"
                    )
                    pos = {
                        "side": "LONG",
                        "entry": c,
                        "break_R": r,
                        "type": "BREAKOUT_BUY",
                    }
                else:
                    sim.append(
                        f"{t} BLOCKED LONG (close {c:.2f} <= thr {thr:.2f}) "
                        f"— would skip marginal breakout"
                    )
        elif v3.get("karar") == "SHORT" and v3.get("teyit") == "evet":
            if v3.get("senaryo") in ("RANGE_SELL", "RANGE_SELL"):
                sim.append(f"{t} ENTRY SHORT range close={c:.2f} R={r:.2f}")
                pos = {"side": "SHORT", "entry": c, "break_R": r, "type": "RANGE_SELL"}

print("=== ACTUAL (18:14 session) ===")
for a in actual:
    print(a)

print("\n=== KEY MOMENTS (thresholds @ 12bps) ===")
moments = [
    "18:14",
    "18:44",
    "00:16",
    "00:24",
    "00:30",
    "00:43",
    "01:00",
    "02:00",
    "03:00",
    "04:00",
    "05:00",
    "06:00",
    "07:00",
    "08:00",
    "09:00",
    "10:00",
    "11:00",
    "12:00",
]
seen = set()
for b in bars:
    hm = b["t"][:5]
    if hm in moments and hm not in seen:
        v3 = b["v3"] or {}
        print(
            f"{b['t']} C={b['close']:.2f} band={b['S']:.2f}/{b['R']:.2f} "
            f"long_thr={break_thr(b['R'], 'LONG'):.2f} "
            f"long_fail={break_thr(b['R'], 'SHORT'):.2f} | "
            f"{v3.get('karar', '?')} {v3.get('senaryo', '?')} teyit={v3.get('teyit', '?')}"
        )
        seen.add(hm)

print("\n=== SIMULATED THESIS PATH ===")
for s in sim:
    print(s)

if pos:
    print(
        f"\n>>> Still open: {pos['side']} entry~{pos['entry']:.2f} "
        f"broken_R={pos['break_R']:.2f} fail="
        f"{break_thr(pos['break_R'], 'SHORT' if pos['side']=='LONG' else 'LONG'):.2f}"
    )

print("\n=== ACTUAL realized PnL ===")
total = 0.0
for a in actual:
    if a[0] == "CLOSE":
        total += a[4]
        print(f"  {a[1]} {a[2]} @ {a[3]}  {a[4]:+.4f}  ({a[5]})")
print(f"  TOTAL: {total:+.4f} USDT")
