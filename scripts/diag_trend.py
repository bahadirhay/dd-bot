"""Trend skoru dökümü — neden RANGE / güç %?"""
from __future__ import annotations

from dashboard.binance_chart import fetch_15m_klines, fetch_1h_klines
from engine.structure import add_bar_15m, add_bar_1h, _update_structure_15m, _update_structure_1h
from engine.trend import update_trend, _flow_scores, _bar_direction, _pct
from core.state import state

# State sıfırla yapı için
for c in fetch_15m_klines(96):
    add_bar_15m(c)
for c in fetch_1h_klines(48):
    add_bar_1h(c)
_update_structure_15m()
_update_structure_1h()

bars = fetch_15m_klines(12)
print("=== Son 12 kapali 15m mum (trend bunlara bakar) ===")
for b in bars:
    chg = _pct(b["open"], b["close"])
    d = "YESIL" if b["close"] > b["open"] else "KIRMIZI"
    print(f"  {d}  O={b['open']:.2f} C={b['close']:.2f}  ({chg:+.2f}%)  delta={b.get('delta',0):+.0f}")

dirs = [_bar_direction(b) for b in bars]
red = sum(1 for d in dirs if d < 0)
green = sum(1 for d in dirs if d > 0)
print(f"\n12 mum: yesil={green} kirmizi={red}  -> momentum up={green/12*100:.0f}% down={red/12*100:.0f}%")

print(f"\nYapi: 15m={state.structure_15m}  1h={state.structure_1h}")
print(f"CVD 5m (state): {state.cvd_5m:+.0f}  taker={state.taker_ratio:.2%}")

tv = update_trend("diag")
print("\n=== BOT TREND SKORU ===")
print(f"  bias={tv['bias']}  phase={tv['phase']}  guc={tv['strength']}%")
print(f"  down_score={tv['down_score']}  up_score={tv['up_score']}")
print(f"  serial_up={tv['serial_up']}  serial_down={tv['serial_down']}")
print(f"  flow_down={tv['flow_down']}  flow_up={tv['flow_up']}")
print(f"  forming_15m chg={tv.get('forming_chg_pct')}%")

print("\n=== Neden RANGE? ===")
if tv["bias"] == "RANGE":
    print(
        f"  UP icin: up_score>=40 VE up>=down+12  -> "
        f"simdi up={tv['up_score']} down={tv['down_score']}"
    )
    need_up = max(40, tv["down_score"] + 12)
    print(f"  UP olmasi icin up_score en az {need_up} olmali")

flow_dn, flow_up = _flow_scores()
print(f"\n  Flow katkisi (max 25): down={flow_dn*25:.1f} up={flow_up*25:.1f}")
print("  (CVD 5m state=0 ise flow zayif kalir — aggTrade kopuksa)")
