#!/usr/bin/env python3
"""
Grafikte bir noktayi sor: o zaman ne olmustu?

Ornek:
  python scripts/explain.py "2026-05-21 15:00"
  python scripts/explain.py 1747845600
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from engine.explain import explain_at


def main():
    if len(sys.argv) < 2:
        print("Kullanim: python scripts/explain.py \"2026-05-21 15:00 UTC\"")
        print("         python scripts/explain.py <unix_timestamp>")
        sys.exit(1)
    when = " ".join(sys.argv[1:])
    print(explain_at(when))


if __name__ == "__main__":
    main()
