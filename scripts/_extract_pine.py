import json
import re

p = r"C:\Users\BH\.cursor\projects\c-Users-BH-Desktop-bot\agent-transcripts\8ab476c7-ac87-46b4-a999-19cccd94385d\8ab476c7-ac87-46b4-a999-19cccd94385d.jsonl"
for line in open(p, encoding="utf-8"):
    if "level1 = source_type" not in line or "Julien_Eche" not in line:
        continue
    t = json.loads(line)["message"]["content"][0]["text"]
    for key in (
        "quick_pivot_support_close",
        "quick_pivot_resistance_close",
        "pivot_support_close",
        "pivot_resistance_close",
        "lookback_right",
        "line.new",
        "line.set",
    ):
        print(f"\n=== {key} ===")
        for m in re.finditer(rf".{{0,40}}{re.escape(key)}.{{0,120}}", t):
            s = m.group(0).replace("\n", " ")
            if "tooltip" not in s.lower()[:80]:
                print(s[:160])
    for m in re.finditer(
        r"level[1-8] = source_type[^\n]+", t
    ):
        print("\n", m.group(0)[:220])
    break
