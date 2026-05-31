"""Extract TREND DN transition times from bot.log."""
import re
import sys
from pathlib import Path

log_path = Path(__file__).resolve().parents[1] / "data" / "logs" / "bot.log"
tail_lines = int(sys.argv[1]) if len(sys.argv) > 1 else 0
if tail_lines > 0:
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    text = "\n".join(lines[-tail_lines:])
    print(f"(Son {tail_lines} satir analiz ediliyor)\n")
else:
    text = log_path.read_text(encoding="utf-8", errors="replace")

pat = re.compile(
    r"^(\d{2}:\d{2}:\d{2})\s+INFO\s+\[Trend\s+\]\s+TREND\s+(\w+)\s+(.*?)(?:\s+\(down=|$)",
    re.M,
)

entries = []
for m in pat.finditer(text):
    ts, label, rest = m.group(1), m.group(2), m.group(3).strip()
    entries.append((ts, label, rest))

transitions = []
prev = None
for ts, label, rest in entries:
    if label != prev:
        transitions.append((ts, prev, label, rest))
        prev = label

print("=== SON OTURUM: DOWN'a GECIS ANLARI (00:00 sonrasi) ===")
for ts, old, new, rest in transitions:
    if ts >= "00:00:00" and new == "DN":
        print(f"{ts}  {old or '-'} -> DN  |  {rest[:90]}")

print("\n=== SON 40 TREND GECISI (tum yonler) ===")
for ts, old, new, rest in transitions[-40:]:
    print(f"{ts}  {old or '-'} -> {new}  |  {rest[:70]}")

# first/last DN in session after 00:43 (LONG acilis sonrasi)
print("\n=== 00:43 SONRASI (LONG acildiktan sonra) DOWN GECISLERI ===")
for ts, old, new, rest in transitions:
    if ts >= "00:43:00" and new == "DN":
        print(f"{ts}  {old} -> DN  |  {rest[:90]}")

print(f"\nToplam trend log: {len(entries)}, gecis: {len(transitions)}")

# V3 1h structure (dashboard TREND DOWN source)
struct_pat = re.compile(
    r"^(\d{2}:\d{2}:\d{2}).*\[StructV3\s+\].*dir=(UP|DOWN|UNCLEAR)", re.M
)
struct_entries = [(m.group(1), m.group(2)) for m in struct_pat.finditer(text)]
struct_trans = []
prev_d = None
for ts, d in struct_entries:
    if d != prev_d:
        struct_trans.append((ts, prev_d, d))
        prev_d = d

print("\n=== DASHBOARD 'TREND DOWN' = V3 1h dir=DOWN (00:43 LONG sonrasi) ===")
for ts, old, new in struct_trans:
    if ts >= "00:43:00" and new == "DOWN":
        print(f"  {ts}  {old or '-'} -> DOWN")

print("\n=== V3 1h SON 12 GECIS ===")
for ts, old, new in struct_trans[-12:]:
    print(f"  {ts}  {old or '-'} -> {new}")

print("\n=== 00:43 SONRASI 1h DOWN DONEMLERI ===")
periods = []
start = None
for ts, old, new in struct_trans:
    if ts < "00:43:00":
        continue
    if new == "DOWN" and start is None:
        start = ts
    elif new != "DOWN" and start is not None:
        periods.append((start, ts, new))
        start = None
if start:
    periods.append((start, "...", "(hala DOWN veya log sonu)"))
for s, e, nxt in periods:
    print(f"  {s} - {e}  (sonra {nxt})")
