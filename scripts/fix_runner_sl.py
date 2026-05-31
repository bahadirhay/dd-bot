"""
TP1 alinmis pozisyon: TP emirlerini kaldir, SL'yi verilen seviyeye cek.

Ornek:
  python scripts/fix_runner_sl.py --sl 2017
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sl", type=float, default=2017.0, help="Yeni SL fiyati")
    args = parser.parse_args()

    from core.config import cfg

    if not cfg.API_KEY:
        print("API_KEY yok — canli onarim yapilamaz")
        return 1

    from execution.account_sync import refresh_account_snapshot
    from execution.protection_orders import repair_runner_after_tp1
    from core.state import state

    ok_sync = await refresh_account_snapshot(force=True)
    if not ok_sync or not state.in_position:
        print("Acik pozisyon bulunamadi")
        return 1

    print(
        f"Pozisyon: {state.pos_side} qty={state.pos_qty:.4f} "
        f"entry={state.pos_entry:.2f} tp1_hit={state.pos_tp1_hit} "
        f"sl={state.pos_sl:.2f}"
    )

    ok = await repair_runner_after_tp1(
        sl=float(args.sl),
        reason="manual fix_runner_sl",
    )
    await refresh_account_snapshot(force=True)
    if ok:
        print(f"Tamam: TP kaldirildi, SL -> {args.sl:.2f}")
        return 0
    print("Onarim basarisiz — loglara bakin")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
