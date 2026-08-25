"""Son N saatlik bot.log ozeti."""
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 2.0
LOG = Path(__file__).resolve().parents[1] / "data" / "logs" / "bot.log"

now = datetime.now()
cutoff = now - timedelta(hours=HOURS)
pat = re.compile(r"^(\d{2}):(\d{2}):(\d{2})\s+(\w+)\s+\[([^\]]+)\]\s+(.*)")

size = LOG.stat().st_size
chunk = 8_000_000
start = max(0, size - chunk)
lines = []
with LOG.open("r", encoding="utf-8", errors="replace") as f:
    if start:
        f.seek(start)
        f.readline()
    lines = f.readlines()

matched = []
for line in lines:
    m = pat.match(line)
    if not m:
        continue
    h, mi, s, lvl, mod, msg = m.groups()
    t = now.replace(hour=int(h), minute=int(mi), second=int(s), microsecond=0)
    if t > now:
        t -= timedelta(days=1)
    if t >= cutoff:
        matched.append((t, lvl, mod, msg.strip()))

print(f"=== bot.log son {HOURS:.0f}s saat ({cutoff:%H:%M} - {now:%H:%M}) ===")
print(f"Toplam satir: {len(matched)}")

SKIP_MODS = {"V3Decision", "NoTradeV3", "DirectionScore", "AttributionV3", "ZoneEngineV3"}
KEYS = (
    "POZ", "TP1", "SL ", "ERROR", "reconcile", "onarimi",
    "KAPANDI", "kapat", "Borsa", "POZISYON", "POZİSYON", "Startup", "restore",
    "Algo TP", "Algo SL", "runner", "Durdurma", "Bot durdu", "KIRILIM", "POZİSYON",
)
important = [
    x for x in matched
    if x[2] not in SKIP_MODS
    and (
        any(k in f"{x[2]} {x[3]}" for k in KEYS)
        or (x[1] in ("ERROR", "WARNING") and x[2] not in ("LevelsV3", "SRLevelsV3", "StructV3", "MarketStateV3", "ScenarioV3", "Intra15m"))
    )
]
print(f"\n--- Onemli olaylar ({len(important)}) ---")
for t, lvl, mod, msg in important:
    print(f"{t:%H:%M:%S} {lvl:7} [{mod:16}] {msg[:200]}")

# karar ozeti
decisions = [x for x in matched if x[2] == "V3Decision"]
if decisions:
    first, last = decisions[0], decisions[-1]
    print(f"\n--- Karar ozeti ---")
    print(f"ilk: {first[3][:220]}")
    print(f"son: {last[3][:220]}")
    print(f"toplam V3Decision: {len(decisions)}")

# price range from V3Decision
prices = []
for t, _, mod, msg in matched:
    if mod == "V3Decision" and "px=" in msg:
        pm = re.search(r"px=(\d+\.\d+)", msg)
        if pm:
            prices.append(float(pm.group(1)))
if prices:
    print(f"\n--- Fiyat araligi (V3Decision) ---")
    print(f"min={min(prices):.2f} max={max(prices):.2f} son={prices[-1]:.2f}")

print("\n--- Son 15 satir ---")
for t, lvl, mod, msg in matched[-15:]:
    print(f"{t:%H:%M:%S} [{mod}] {msg[:180]}")
