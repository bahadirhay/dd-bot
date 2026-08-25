import json
import re

p = r"C:\Users\BH\.cursor\projects\c-Users-BH-Desktop-bot\agent-transcripts\8ab476c7-ac87-46b4-a999-19cccd94385d\8ab476c7-ac87-46b4-a999-19cccd94385d.jsonl"
out = r"c:\Users\BH\Desktop\bot\scripts\pine_sr_ultimate_reference.pine"
for line in open(p, encoding="utf-8"):
    if "level1 = source_type" not in line or "Julien_Eche" not in line:
        continue
    t = json.loads(line)["message"]["content"][0]["text"]
    # user query embeds pine after some text
    i = t.find("// @")
    if i < 0:
        i = t.find("//@version")
    if i < 0:
        i = t.find("indicator(")
    body = t[i:] if i >= 0 else t
    open(out, "w", encoding="utf-8").write(body)
    print("written", out, "chars", len(body))
    break
