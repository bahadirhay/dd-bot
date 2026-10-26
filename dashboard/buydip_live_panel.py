"""dashboard/buydip_live_panel.py — CANLI buydip izleme panosu (HAFIF, built-in http.server).
Flask YOK (bellek icin). Port 8061. 10sn'de bir oto-yenilenir. buydip_live.db + Binance gercek pozisyon.
Calistir: python dashboard/buydip_live_panel.py
"""
import os, sys, time, json, hmac, hashlib, sqlite3
import urllib.request, urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.config import cfg
from engine import buydip_live as bl   # limitleri dinamik oku (PER_SIGNAL/MAX_OPEN/STOP)

PORT = 8061
DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "buydip_live.db")
_last_marks = {}      # son BASARILI mark/unreal/side -> API anlik duserse sifir gosterme
_last_bal = "?"


def _get(u):
    return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "bp/1"}), timeout=10).read())

def _signed(path):
    q = {"timestamp": int(time.time()*1000), "recvWindow": 5000}
    qs = urllib.parse.urlencode(q)
    sig = hmac.new(cfg.API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(f"https://fapi.binance.com{path}?{qs}&signature={sig}",
                               headers={"X-MBX-APIKEY": cfg.API_KEY}), timeout=10).read())


def render():
    rows_open, rows_closed = [], []
    realized = 0.0; unreal = 0.0; bal = "?"
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        open_syms = {r[0]: r for r in c.execute(
            "SELECT symbol,entry,qty,drop3,open_human,open_ts FROM buydip_live WHERE status='OPEN'")}
        closed = c.execute("SELECT symbol,drop3,entry,exit,pnl_usdt,open_human,close_ts "
                           "FROM buydip_live WHERE status='CLOSED' ORDER BY close_ts DESC").fetchall()
    except Exception:
        open_syms, closed = {}, []
    # borsa mark/unrealized — basarisizsa SON GECERLI degeri kullan (sifir gosterme)
    global _last_marks, _last_bal
    marks = {}
    try:
        pr = _signed("/fapi/v2/positionRisk")
        if not isinstance(pr, list):
            raise ValueError("positionRisk beklenmedik yanit")
        for p in pr:
            amt = float(p["positionAmt"])
            if abs(amt) > 0:
                side = "LONG" if amt > 0 else "SHORT"
                marks[p["symbol"]] = (float(p["markPrice"]), float(p["unRealizedProfit"]), side)
        acc = _signed("/fapi/v2/account")
        b = acc.get("totalWalletBalance") if isinstance(acc, dict) else None
        bal = b if b else _last_bal
        _last_marks = marks; _last_bal = bal     # basarili -> cache guncelle
    except Exception:
        marks = _last_marks; bal = _last_bal       # anlik hata -> son gecerli
    now_ts = time.time()
    for s, (sym, entry, qty, d3, oh, ots) in open_syms.items():
        mk, up, side = marks.get(s, (0.0, 0.0, "LONG")); unreal += up
        pct = ((mk-entry)/entry*100) if entry and mk else 0
        sc = "g" if side == "LONG" else "r"
        close_ts = ots + bl.HOLD_DAYS * 86400
        rem_s = close_ts - now_ts
        if rem_s <= 0:
            rem_txt = "KAPANIYOR"
        elif rem_s >= 86400:
            rem_txt = f"{int(rem_s//86400)}g {int((rem_s%86400)//3600)}s"   # X gun Y saat
        elif rem_s >= 3600:
            rem_txt = f"{int(rem_s//3600)}s {int((rem_s%3600)//60)}dk"       # X saat Y dk
        else:
            rem_txt = f"{int(rem_s//60)} dk"                                  # X dk
        close_clock = time.strftime("%d.%m %H:%M", time.localtime(close_ts))  # kapanis saati
        rows_open.append(f"<tr><td>{sym}</td><td class='{sc}'><b>{side}</b></td><td>{oh}</td><td>{d3:.1f}%</td><td>{entry:.5g}</td>"
                         f"<td>{mk:.5g}</td><td class='{'g' if up>=0 else 'r'}'>{up:+.3f}$ ({pct:+.1f}%)</td><td>{rem_txt}</td><td>{close_clock}</td></tr>")
    for sym, d3, entry, exit, pnl, oh, cts in closed:
        realized += (pnl or 0)
        rows_closed.append(f"<tr><td>{sym}</td><td>{oh}</td><td>{d3:.1f}%</td><td>{entry:.5g}</td>"
                           f"<td>{exit:.5g}</td><td class='{'g' if (pnl or 0)>=0 else 'r'}'>{pnl:+.2f}$</td></tr>")
    win = (100*sum(1 for x in closed if (x[4] or 0) > 0)//len(closed)) if closed else 0
    return f"""<!doctype html><html><head><meta charset=utf-8>
<meta http-equiv=refresh content=10><title>buydip CANLI</title>
<style>body{{font-family:system-ui;background:#0d1117;color:#e6edf3;margin:16px}}
h2{{margin:8px 0}} table{{border-collapse:collapse;width:100%;margin:6px 0 18px}}
td,th{{border:1px solid #30363d;padding:5px 8px;font-size:13px;text-align:right}} th{{background:#161b22}}
td:first-child,td:nth-child(2){{text-align:left}} .g{{color:#3fb950}} .r{{color:#f85149}}
.box{{display:inline-block;background:#161b22;border:1px solid #30363d;border-radius:6px;padding:8px 14px;margin:4px 8px 4px 0}}</style>
</head><body>
<h2>🟢 buydip CANLI (gercek para) — oto-yenile 10sn</h2>
<div class=box>Bakiye: <b>{bal}</b> USDT</div>
<div class=box>Acik: <b>{len(open_syms)}</b>/{bl.MAX_OPEN} | Unrealized <span class='{'g' if unreal>=0 else 'r'}'>{unreal:+.3f}$</span></div>
<div class=box>Kapanan: <b>{len(closed)}</b> | Gerceklesen <span class='{'g' if realized>=0 else 'r'}'>{realized:+.2f}$</span> | win {win}%</div>
<div class=box>Limit: ${bl.PER_SIGNAL_USDT:.0f}/sinyal × {bl.MAX_OPEN} = ${bl.PER_SIGNAL_USDT*bl.MAX_OPEN:.0f} max | stop ${bl.STOP_EXPERIMENT_USDT:.0f}</div>
<h2>Acik pozisyonlar</h2><table><tr><th>coin</th><th>yon</th><th>acilis</th><th>dip</th><th>giris</th><th>mark</th><th>unrealized</th><th>kapanmaya</th><th>kapanis saati</th></tr>
{''.join(rows_open) or '<tr><td colspan=9>acik pozisyon yok (sinyal bekleniyor)</td></tr>'}</table>
<h2>Kapanan islemler</h2><table><tr><th>coin</th><th>acilis</th><th>dip</th><th>giris</th><th>cikis</th><th>pnl</th></tr>
{''.join(rows_closed) or '<tr><td colspan=6>henuz kapanan yok</td></tr>'}</table>
<p style='color:#8b949e;font-size:12px'>Son guncelleme: {time.strftime('%H:%M:%S')}</p>
</body></html>"""


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            html = render().encode("utf-8")
        except Exception as e:
            html = f"<pre>hata: {e}</pre>".encode()
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html))); self.end_headers(); self.wfile.write(html)
    def log_message(self, *a): pass


if __name__ == "__main__":
    print(f"buydip CANLI panosu: http://localhost:{PORT}")
    HTTPServer(("0.0.0.0", PORT), H).serve_forever()
