"""
botlog/db.py — SQLite loglama katmanı

4 tablo:
  signals   — Her 15m kapanışında sinyal değerlendirmesi
  trades    — Her pozisyonun tam kaydı
  regime_log— Rejim değişimleri
  errors    — Sistem hataları
"""
import sqlite3, json, time, os
from core.config import cfg

os.makedirs(os.path.dirname(cfg.DB_PATH), exist_ok=True)

def _conn():
    c = sqlite3.connect(cfg.DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    return c

def _migrate_v3_attribution_columns(db: sqlite3.Connection) -> None:
    """Mevcut DB: reject_reason kolonu."""
    cols = {r[1] for r in db.execute("PRAGMA table_info(v3_attribution)").fetchall()}
    if "reject_reason" not in cols:
        db.execute("ALTER TABLE v3_attribution ADD COLUMN reject_reason TEXT")
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_v3attr_reject "
            "ON v3_attribution(reject_reason, ts DESC)"
        )


def _migrate_trades_learning_columns(db: sqlite3.Connection) -> None:
    """
    Ogrenme altyapisi: her trade'e giris-baglami + sonuc-uc noktalari ekler.
    Bot bu sutunlardan hangi kurulumun kazandigini ogrenebilir (offline/online).
    Hepsi nullable; eski kayitlar NULL kalir.
    """
    cols = {r[1] for r in db.execute("PRAGMA table_info(trades)").fetchall()}
    add = {
        "entry_to_edge_bps": "REAL",   # girisin fade/breakout seviyesine uzakligi (bps)
        "buy_ratio_at_open": "REAL",   # acilis taker alis orani (akis teyidi)
        "zone_at_open": "TEXT",        # NEAR_SUPPORT/NEAR_RESISTANCE/MID_RANGE
        "path_at_open": "TEXT",        # fade / breakout
        "struct_long_at_open": "REAL",
        "struct_short_at_open": "REAL",
        "tp1_bps": "REAL",             # giris->TP1 mesafesi (bps)
        "sl_bps": "REAL",              # giris->SL mesafesi (bps)
        "mfe_bps": "REAL",             # max lehte hareket (bps) — kapanista
        "mae_bps": "REAL",             # max aleyhte hareket (bps) — kapanista
    }
    for name, typ in add.items():
        if name not in cols:
            db.execute(f"ALTER TABLE trades ADD COLUMN {name} {typ}")


def _migrate_box_log(db: sqlite3.Connection) -> None:
    """Adaptif kutu kararlarini kaydeden tablo (her adim izlenebilsin)."""
    db.execute("""
        CREATE TABLE IF NOT EXISTS box_log (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            ts        REAL NOT NULL,
            ts_human  TEXT,
            price     REAL,
            pine_s    REAL,
            pine_r    REAL,
            box_s     REAL,
            box_r     REAL,
            used      INTEGER DEFAULT 0,
            zone      TEXT,
            reason    TEXT
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_box_log_ts ON box_log(ts DESC)")


def log_box_decision(data: dict) -> None:
    """Kutu kararini kaydet (yalniz degisiklik/kullanim aninda cagrilmali)."""
    from datetime import datetime, timezone
    try:
        with _conn() as db:
            db.execute(
                "INSERT INTO box_log (ts,ts_human,price,pine_s,pine_r,box_s,box_r,used,zone,reason) "
                "VALUES (:ts,:ts_human,:price,:pine_s,:pine_r,:box_s,:box_r,:used,:zone,:reason)",
                {
                    "ts": data.get("ts") or 0.0,
                    "ts_human": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                    "price": data.get("price") or 0.0,
                    "pine_s": data.get("pine_s") or 0.0,
                    "pine_r": data.get("pine_r") or 0.0,
                    "box_s": data.get("box_s") or 0.0,
                    "box_r": data.get("box_r") or 0.0,
                    "used": int(data.get("used") or 0),
                    "zone": str(data.get("zone") or ""),
                    "reason": str(data.get("reason") or ""),
                },
            )
    except Exception:
        pass


def init():
    with _conn() as db:
        db.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=NORMAL;

        CREATE TABLE IF NOT EXISTS signals (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            ts            REAL NOT NULL,
            ts_human      TEXT,
            direction     TEXT,          -- LONG/SHORT/FLAT
            entered       INTEGER,       -- 1=girdi 0=girmedi
            no_entry_reason TEXT,
            regime        TEXT,
            regime_score  INTEGER,
            regime_q1_structure INTEGER,
            regime_q2_cvd       INTEGER,
            regime_q3_oi        INTEGER,
            regime_q4_taker     INTEGER,
            structure_1h  TEXT,
            structure_15m TEXT,
            cvd_5m        REAL,
            cvd_consistent INTEGER,
            cvd_divergence INTEGER,
            oi_rising     INTEGER,
            taker_ratio   REAL,
            funding_rate  REAL,
            price         REAL,
            sl            REAL,
            tp1           REAL,
            tp2           REAL,
            rr            REAL,
            notes         TEXT
        );

        CREATE TABLE IF NOT EXISTS trades (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id     INTEGER REFERENCES signals(id),
            order_id      TEXT,
            direction     TEXT NOT NULL,
            entry_price   REAL,
            exit_price    REAL,
            qty           REAL,
            qty_tp1       REAL,
            qty_tp2       REAL,
            sl            REAL,
            tp1           REAL,
            tp2           REAL,
            liq_price     REAL,
            margin        REAL,
            leverage      INTEGER DEFAULT 5,
            margin_type   TEXT DEFAULT 'ISOLATED',
            pnl           REAL,
            pnl_pct       REAL,
            status        TEXT DEFAULT 'OPEN',
            close_reason  TEXT,
            open_ts       REAL,
            close_ts      REAL,
            duration_min  REAL,
            tp1_hit       INTEGER DEFAULT 0,
            be_activated  INTEGER DEFAULT 0,
            regime_at_open TEXT,
            cvd_at_open   REAL,
            notes         TEXT
        );

        CREATE TABLE IF NOT EXISTS regime_log (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            ts            REAL NOT NULL,
            ts_human      TEXT,
            regime        TEXT,
            score         INTEGER,
            price         REAL,
            q1            INTEGER,
            q2            INTEGER,
            q3            INTEGER,
            q4            INTEGER,
            prev_regime   TEXT
        );

        CREATE TABLE IF NOT EXISTS errors (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            ts            REAL NOT NULL,
            ts_human      TEXT,
            source        TEXT,
            error         TEXT,
            context       TEXT
        );

        CREATE TABLE IF NOT EXISTS market_snapshots (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            ts            REAL NOT NULL,
            ts_human      TEXT,
            kind          TEXT NOT NULL,
            price         REAL,
            note          TEXT,
            payload_json  TEXT
        );

        CREATE TABLE IF NOT EXISTS market_events (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            ts            REAL NOT NULL,
            ts_human      TEXT,
            event_type    TEXT,
            severity      TEXT DEFAULT 'info',
            title         TEXT,
            detail        TEXT,
            payload_json  TEXT
        );

        CREATE TABLE IF NOT EXISTS sr_changes (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            ts            REAL NOT NULL,
            ts_human      TEXT,
            price         REAL,
            old_support   REAL,
            new_support   REAL,
            old_resistance REAL,
            new_resistance REAL,
            d_support     REAL,          -- |yeni_S - eski_S| pt
            d_resistance  REAL,          -- |yeni_R - eski_R| pt
            support_reason   TEXT,
            resistance_reason TEXT,
            dist_support_pt  REAL,       -- px ile yeni destek arasi mesafe (pt)
            dist_resistance_pt REAL,     -- px ile yeni direnc arasi mesafe (pt)
            band_old_pt   REAL,
            band_new_pt   REAL,
            touched       INTEGER        -- 1: px degisen kenara dokunma toleransinda
        );

        CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals(ts DESC);
        CREATE INDEX IF NOT EXISTS idx_sr_changes_ts ON sr_changes(ts DESC);
        CREATE INDEX IF NOT EXISTS idx_trades_ts  ON trades(open_ts DESC);
        CREATE INDEX IF NOT EXISTS idx_regime_ts  ON regime_log(ts DESC);
        CREATE INDEX IF NOT EXISTS idx_snap_ts ON market_snapshots(ts DESC);
        CREATE INDEX IF NOT EXISTS idx_snap_kind ON market_snapshots(kind, ts DESC);
        CREATE INDEX IF NOT EXISTS idx_event_ts ON market_events(ts DESC);

        CREATE TABLE IF NOT EXISTS v3_attribution (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ts              REAL NOT NULL,
            ts_human        TEXT,
            price           REAL,
            action          TEXT,
            scenario        TEXT,
            intended_side   TEXT,
            trade_candidate INTEGER DEFAULT 0,
            entered         INTEGER DEFAULT 0,
            blocked         INTEGER DEFAULT 0,
            trade_id        INTEGER,
            reason_text     TEXT,
            trade_reason_json TEXT,
            block_reason_json TEXT,
            trade_reason_sum INTEGER DEFAULT 0,
            block_reason_sum INTEGER DEFAULT 0,
            net_score       INTEGER DEFAULT 0,
            primary_block   TEXT,
            primary_support   TEXT,
            context_json    TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_v3attr_ts ON v3_attribution(ts DESC);
        CREATE INDEX IF NOT EXISTS idx_v3attr_block ON v3_attribution(primary_block, ts DESC);
        CREATE INDEX IF NOT EXISTS idx_v3attr_trade ON v3_attribution(trade_id);
        """)
        _migrate_v3_attribution_columns(db)
        _migrate_trades_learning_columns(db)
        _migrate_box_log(db)
    print("DB hazır:", cfg.DB_PATH)
    try:
        n = backfill_closed_trade_metrics()
        if n:
            print(f"DB: {n} kapalı trade kaydı güncellendi (çıkış/PnL/süre)")
    except Exception:
        pass


def backfill_closed_trade_metrics() -> int:
    """Eski kayıtlarda eksik çıkış fiyatı, PnL ve süreyi tamamla."""
    n = 0
    with _conn() as db:
        rows = db.execute(
            """
            SELECT id, direction, entry_price, exit_price, qty, pnl, pnl_pct,
                   open_ts, close_ts, duration_min, close_reason, notes
            FROM trades WHERE status='CLOSED'
            """
        ).fetchall()
        for row in rows:
            entry = float(row["entry_price"] or 0)
            qty = float(row["qty"] or 0)
            side = str(row["direction"] or "")
            xp = float(row["exit_price"]) if row["exit_price"] is not None else 0.0
            pnl = float(row["pnl"] or 0)
            reason = str(row["close_reason"] or "").split("|")[0].strip()
            notes = str(row["notes"] or "")
            open_ts = float(row["open_ts"] or 0)
            close_ts = float(row["close_ts"] or 0)

            new_xp = xp
            new_pnl = pnl
            new_dur = float(row["duration_min"]) if row["duration_min"] is not None else 0.0

            if new_xp <= 0 and entry > 0:
                new_xp = entry
            if new_xp <= 0 and xp > 0:
                new_xp = xp

            skip_pnl = reason == "duplicate_open" or notes == "orphan_duplicate"
            if (
                not skip_pnl
                and abs(new_pnl) < 1e-8
                and entry > 0
                and qty > 0
                and new_xp > 0
            ):
                sign = 1.0 if side == "LONG" else -1.0
                new_pnl = round((new_xp - entry) * qty * sign, 4)

            if new_dur <= 0 and close_ts > 0 and open_ts > 0:
                new_dur = round((close_ts - open_ts) / 60, 1)

            notional = entry * qty if entry > 0 and qty > 0 else 0.0
            new_pct = round(new_pnl / notional * 100, 3) if notional > 0 else 0.0
            old_pct = float(row["pnl_pct"]) if row["pnl_pct"] is not None else 0.0

            changed = (
                (row["exit_price"] is None and new_xp > 0)
                or abs(xp - new_xp) > 1e-8
                or abs(pnl - new_pnl) > 1e-8
                or abs(new_dur - float(row["duration_min"] or 0)) > 0.05
                or (notional > 0 and abs(old_pct - new_pct) > 0.001)
            )
            if not changed:
                continue

            db.execute(
                """
                UPDATE trades SET
                    exit_price=:exit_price,
                    pnl=:pnl,
                    pnl_pct=:pnl_pct,
                    duration_min=:duration_min
                WHERE id=:id
                """,
                {
                    "id": int(row["id"]),
                    "exit_price": new_xp if new_xp > 0 else None,
                    "pnl": new_pnl,
                    "pnl_pct": new_pct,
                    "duration_min": new_dur,
                },
            )
            n += 1
    return n


def log_signal(data: dict) -> int:
    from datetime import datetime, timezone
    data["ts_human"] = datetime.fromtimestamp(
        data.get("ts", time.time()), tz=timezone.utc
    ).strftime("%Y-%m-%d %H:%M:%S UTC")
    with _conn() as db:
        cur = db.execute("""
            INSERT INTO signals
            (ts,ts_human,direction,entered,no_entry_reason,
             regime,regime_score,
             regime_q1_structure,regime_q2_cvd,regime_q3_oi,regime_q4_taker,
             structure_1h,structure_15m,
             cvd_5m,cvd_consistent,cvd_divergence,
             oi_rising,taker_ratio,funding_rate,
             price,sl,tp1,tp2,rr,notes)
            VALUES
            (:ts,:ts_human,:direction,:entered,:no_entry_reason,
             :regime,:regime_score,
             :q1_structure,:q2_cvd,:q3_oi,:q4_taker,
             :structure_1h,:structure_15m,
             :cvd_5m,:cvd_consistent,:cvd_divergence,
             :oi_rising,:taker_ratio,:funding_rate,
             :price,:sl,:tp1,:tp2,:rr,:notes)
        """, data)
        return cur.lastrowid


def count_open_trades(direction: str = "") -> int:
    with _conn() as db:
        if direction:
            row = db.execute(
                "SELECT COUNT(*) AS n FROM trades WHERE status='OPEN' AND direction=?",
                (direction.upper(),),
            ).fetchone()
        else:
            row = db.execute(
                "SELECT COUNT(*) AS n FROM trades WHERE status='OPEN'"
            ).fetchone()
    return int(row["n"]) if row else 0


def close_all_open_trades(
    reason: str = "closed",
    *,
    exit_price: float = 0.0,
    pnl: float | None = None,
) -> int:
    """Tüm OPEN trade kayıtlarını kapat (çift OPEN / restart artığı)."""
    from core.state import state

    now = time.time()
    mark = float(exit_price or state.mark_price or state.price or 0)
    n = 0
    with _conn() as db:
        rows = db.execute(
            """
            SELECT id, direction, entry_price, qty, open_ts
            FROM trades WHERE status='OPEN'
            """
        ).fetchall()
        for row in rows:
            entry = float(row["entry_price"] or 0)
            qty = float(row["qty"] or 0)
            side = str(row["direction"] or "")
            xp = mark if mark > 0 else entry
            if xp <= 0 and entry > 0:
                xp = entry
            row_pnl = pnl
            if row_pnl is None and entry > 0 and qty > 0 and xp > 0:
                sign = 1.0 if side == "LONG" else -1.0
                row_pnl = round((xp - entry) * qty * sign, 4)
            else:
                row_pnl = round(float(row_pnl or 0), 4)
            notional = entry * qty if entry > 0 and qty > 0 else 0.0
            pnl_pct = (
                round(row_pnl / notional * 100, 3) if notional > 0 else 0.0
            )
            open_ts = float(row["open_ts"] or 0)
            dur = round((now - open_ts) / 60, 1) if open_ts > 0 else 0.0
            db.execute(
                """
                UPDATE trades SET status='CLOSED', close_reason=?, close_ts=?,
                    exit_price=?, pnl=?, pnl_pct=?, duration_min=?
                WHERE id=?
                """,
                (reason, now, xp, row_pnl, pnl_pct, dur, int(row["id"])),
            )
            n += 1
    return n


def log_trade_open(data: dict) -> int:
    from datetime import datetime, timezone

    xp = float(data.get("entry_price") or 0)
    close_all_open_trades("superseded_by_new_open", exit_price=xp)

    with _conn() as db:
        cur = db.execute("""
            INSERT INTO trades
            (signal_id,order_id,direction,entry_price,qty,qty_tp1,qty_tp2,
             sl,tp1,tp2,liq_price,margin,leverage,margin_type,
             status,open_ts,regime_at_open,cvd_at_open,notes)
            VALUES
            (:signal_id,:order_id,:direction,:entry_price,:qty,:qty_tp1,:qty_tp2,
             :sl,:tp1,:tp2,:liq_price,:margin,:leverage,:margin_type,
             'OPEN',:open_ts,:regime_at_open,:cvd_at_open,:notes)
        """, data)
        return cur.lastrowid


def update_trade_open_features(trade_id: int, feats: dict) -> None:
    """Ogrenme sutunlarini (giris-baglami) gunceller. Sadece verilen alanlar."""
    if not trade_id or not feats:
        return
    allowed = {
        "entry_to_edge_bps", "buy_ratio_at_open", "zone_at_open", "path_at_open",
        "struct_long_at_open", "struct_short_at_open", "tp1_bps", "sl_bps",
        "mfe_bps", "mae_bps",
    }
    items = {k: v for k, v in feats.items() if k in allowed and v is not None}
    if not items:
        return
    sets = ", ".join(f"{k}=:{k}" for k in items)
    items["_id"] = trade_id
    try:
        with _conn() as db:
            db.execute(f"UPDATE trades SET {sets} WHERE id=:_id", items)
    except Exception:
        pass


def update_trade_entry(trade_id: int, entry_price: float, qty: float | None = None) -> None:
    if trade_id <= 0 or entry_price <= 0:
        return
    with _conn() as db:
        if qty is not None and qty > 0:
            db.execute(
                "UPDATE trades SET entry_price=?, qty=? WHERE id=?",
                (entry_price, qty, trade_id),
            )
        else:
            db.execute(
                "UPDATE trades SET entry_price=? WHERE id=?",
                (entry_price, trade_id),
            )


def reconcile_open_trades_with_exchange(
    direction: str, entry_price: float, qty: float
) -> int:
    """Borsa pozisyonu ile DB OPEN kayıtlarını hizala; çift OPEN temizle."""
    now = time.time()
    with _conn() as db:
        rows = db.execute(
            "SELECT id FROM trades WHERE status='OPEN' ORDER BY open_ts DESC"
        ).fetchall()
        if not rows:
            cur = db.execute(
                """
                INSERT INTO trades
                (direction, entry_price, qty, status, open_ts, notes)
                VALUES (?, ?, ?, 'OPEN', ?, 'restored_from_exchange')
                """,
                (direction, entry_price, qty, now),
            )
            return int(cur.lastrowid)

        keep_id = int(rows[0]["id"])
        db.execute(
            """
            UPDATE trades SET direction=?, entry_price=?, qty=?
            WHERE id=?
            """,
            (direction, entry_price, qty, keep_id),
        )
        for row in rows[1:]:
            oid = int(row["id"])
            prev = db.execute(
                "SELECT open_ts, entry_price FROM trades WHERE id=?", (oid,)
            ).fetchone()
            ots = float(prev["open_ts"] or now) if prev else now
            dur = round((now - ots) / 60, 1) if ots > 0 else 0.0
            db.execute(
                """
                UPDATE trades SET status='CLOSED', close_reason='duplicate_open',
                    close_ts=?, exit_price=?,
                    entry_price=CASE WHEN entry_price IS NULL OR entry_price<=0
                        THEN ? ELSE entry_price END,
                    pnl=0, pnl_pct=0, duration_min=?,
                    notes='orphan_duplicate'
                WHERE id=?
                """,
                (now, entry_price, entry_price, dur, oid),
            )
        return keep_id


def parse_tp1_original_from_notes(notes: str) -> float:
    """notes icinde tp1o= tokeni (giris anindaki orijinal TP1)."""
    for token in (notes or "").replace("|", " ").split():
        if token.startswith("tp1o="):
            try:
                return float(token[5:])
            except ValueError:
                pass
    return 0.0


def notes_tp1_restored(notes: str) -> bool:
    return "tp1r=1" in (notes or "").replace("|", " ")


def merge_notes_with_tp1_original(notes: str, tp1: float) -> str:
    tokens = [
        t
        for t in (notes or "canli").replace("|", " ").split()
        if t and not t.startswith("tp1o=") and not t.startswith("tp1r=")
    ]
    if tp1 > 0:
        tokens.append(f"tp1o={tp1:.2f}")
    return " ".join(tokens) if tokens else "canli"


def update_trade_entry_tp1_original(trade_id: int, tp1: float) -> None:
    if trade_id <= 0 or tp1 <= 0:
        return
    with _conn() as db:
        row = db.execute(
            "SELECT notes FROM trades WHERE id=? AND status='OPEN'",
            (trade_id,),
        ).fetchone()
    if not row:
        return
    notes = merge_notes_with_tp1_original(str(row["notes"] or ""), tp1)
    with _conn() as db:
        db.execute("UPDATE trades SET notes=? WHERE id=?", (notes, trade_id))


def mark_trade_tp1_restored(trade_id: int) -> None:
    if trade_id <= 0:
        return
    with _conn() as db:
        row = db.execute(
            "SELECT notes FROM trades WHERE id=? AND status='OPEN'",
            (trade_id,),
        ).fetchone()
    if not row:
        return
    notes = str(row["notes"] or "")
    if notes_tp1_restored(notes):
        return
    notes = f"{notes} tp1r=1".strip()
    with _conn() as db:
        db.execute("UPDATE trades SET notes=? WHERE id=?", (notes, trade_id))


def parse_entry_anchors_from_notes(notes: str) -> tuple[float, float]:
    """notes icinde es= / er= tokenlari (or. 'canli es=1983.60 er=2070.42')."""
    entry_support = 0.0
    entry_resistance = 0.0
    for token in (notes or "").replace("|", " ").split():
        if token.startswith("es="):
            try:
                entry_support = float(token[3:])
            except ValueError:
                pass
        elif token.startswith("er="):
            try:
                entry_resistance = float(token[3:])
            except ValueError:
                pass
    return entry_support, entry_resistance


def merge_notes_with_entry_anchors(
    notes: str, entry_support: float, entry_resistance: float
) -> str:
    tokens = [
        t
        for t in (notes or "canli").replace("|", " ").split()
        if t and not t.startswith("es=") and not t.startswith("er=")
    ]
    if entry_support > 0:
        tokens.append(f"es={entry_support:.2f}")
    if entry_resistance > 0:
        tokens.append(f"er={entry_resistance:.2f}")
    return " ".join(tokens) if tokens else "canli"


def update_trade_entry_anchors(
    trade_id: int, entry_support: float, entry_resistance: float
) -> None:
    if trade_id <= 0 or (entry_support <= 0 and entry_resistance <= 0):
        return
    with _conn() as db:
        row = db.execute(
            "SELECT notes FROM trades WHERE id=? AND status='OPEN'",
            (trade_id,),
        ).fetchone()
    if not row:
        return
    notes = merge_notes_with_entry_anchors(
        str(row["notes"] or ""),
        entry_support,
        entry_resistance,
    )
    with _conn() as db:
        db.execute("UPDATE trades SET notes=? WHERE id=?", (notes, trade_id))


def get_trade_levels(trade_id: int) -> dict | None:
    """OPEN trade SL/TP ve kısmi miktarlar (restart sonrası koruma emirleri)."""
    if trade_id <= 0:
        return None
    with _conn() as db:
        row = db.execute(
            """
            SELECT sl, tp1, tp2, qty, qty_tp1, qty_tp2, direction, notes,
                   tp1_hit, be_activated
            FROM trades WHERE id=? AND status='OPEN'
            """,
            (trade_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "sl": float(row["sl"] or 0),
        "tp1": float(row["tp1"] or 0),
        "tp2": float(row["tp2"] or 0),
        "qty": float(row["qty"] or 0),
        "qty_tp1": float(row["qty_tp1"] or 0),
        "qty_tp2": float(row["qty_tp2"] or 0),
        "direction": str(row["direction"] or ""),
        "notes": str(row["notes"] or ""),
        "tp1_hit": bool(int(row["tp1_hit"] or 0)),
        "be_activated": bool(int(row["be_activated"] or 0)),
    }


def update_open_trade_sl(trade_id: int, sl: float) -> None:
    """Restart restore sirasinda hatali OPEN trade SL seviyesini onar."""
    if trade_id <= 0 or sl <= 0:
        return
    with _conn() as db:
        db.execute(
            "UPDATE trades SET sl=? WHERE id=? AND status='OPEN'",
            (float(sl), int(trade_id)),
        )


def update_open_trade_tps(trade_id: int, tp1: float = 0.0, tp2: float = 0.0) -> None:
    """Borsada manuel degisen TP seviyelerini OPEN trade kaydina yansit."""
    if trade_id <= 0 or (tp1 <= 0 and tp2 <= 0):
        return
    sets: list[str] = []
    vals: list[float | int] = []
    if tp1 > 0:
        sets.append("tp1=?")
        vals.append(float(tp1))
    if tp2 > 0:
        sets.append("tp2=?")
        vals.append(float(tp2))
    vals.append(int(trade_id))
    with _conn() as db:
        db.execute(
            f"UPDATE trades SET {', '.join(sets)} WHERE id=? AND status='OPEN'",
            vals,
        )


def get_open_trade_flags() -> dict:
    with _conn() as db:
        row = db.execute(
            """
            SELECT id, tp1_hit, be_activated, qty, qty_tp1, qty_tp2
            FROM trades WHERE status='OPEN' ORDER BY open_ts DESC LIMIT 1
            """
        ).fetchone()
    if not row:
        return {}
    return {
        "id": int(row["id"]),
        "tp1_hit": bool(int(row["tp1_hit"] or 0)),
        "be_activated": bool(int(row["be_activated"] or 0)),
        "qty": float(row["qty"] or 0),
        "qty_tp1": float(row["qty_tp1"] or 0),
        "qty_tp2": float(row["qty_tp2"] or 0),
    }


def mark_open_trade_tp1_hit(trade_id: int = 0) -> None:
    with _conn() as db:
        if trade_id and trade_id > 0:
            db.execute(
                "UPDATE trades SET tp1_hit=1 WHERE id=? AND status='OPEN'",
                (trade_id,),
            )
        else:
            db.execute(
                """
                UPDATE trades SET tp1_hit=1
                WHERE id = (
                    SELECT id FROM trades WHERE status='OPEN'
                    ORDER BY open_ts DESC LIMIT 1
                )
                """
            )


def get_open_trade_id() -> int:
    with _conn() as db:
        row = db.execute(
            "SELECT id FROM trades WHERE status='OPEN' ORDER BY open_ts DESC LIMIT 1"
        ).fetchone()
    return int(row["id"]) if row else 0


def record_position_close(
    reason: str,
    *,
    exit_price: float = 0.0,
    pnl: float | None = None,
    trade_id: int = 0,
    source: str = "",
) -> bool:
    """
    Açık trade kaydını kapat (borsa SL/TP, bot, restart senkronu).
    executor.close_position zaten log_trade_close yapıyorsa tekrar yazmaz.
    """
    from core.state import state

    tag = reason or "closed"
    if source:
        tag = f"{tag}|{source}"

    xp = float(exit_price or 0)
    if xp <= 0:
        xp = float(
            state.mark_price or state.price or state.pos_entry or 0
        )

    if trade_id and trade_id > 0:
        with _conn() as db:
            row = db.execute(
                """
                SELECT id, status FROM trades WHERE id=? AND status='OPEN'
                """,
                (trade_id,),
            ).fetchone()
        if not row:
            return close_all_open_trades(tag, exit_price=xp, pnl=pnl) > 0

    n = close_all_open_trades(tag, exit_price=xp, pnl=pnl)
    return n > 0


def close_orphan_open_trades(reason: str = "orphan_no_position") -> int:
    """Borsada pozisyon yokken OPEN kalan kayıtları kapat."""
    from core.state import state

    now = time.time()
    mark = float(state.mark_price or state.price or 0)
    n = 0
    with _conn() as db:
        rows = db.execute(
            """
            SELECT id, direction, entry_price, qty, open_ts
            FROM trades WHERE status='OPEN'
            """
        ).fetchall()
        for row in rows:
            entry = float(row["entry_price"] or 0)
            qty = float(row["qty"] or 0)
            side = str(row["direction"] or "")
            xp = mark if mark > 0 else entry
            if xp <= 0 and entry > 0:
                xp = entry
            pnl = 0.0
            if entry > 0 and qty > 0 and xp > 0:
                sign = 1.0 if side == "LONG" else -1.0
                pnl = round((xp - entry) * qty * sign, 4)
            notional = entry * qty if entry > 0 and qty > 0 else 0.0
            pnl_pct = round(pnl / notional * 100, 3) if notional > 0 else 0.0
            open_ts = float(row["open_ts"] or 0)
            dur = round((now - open_ts) / 60, 1) if open_ts > 0 else 0.0
            db.execute(
                """
                UPDATE trades SET status='CLOSED', close_reason=?, close_ts=?,
                    exit_price=?, pnl=?, pnl_pct=?, duration_min=?
                WHERE id=?
                """,
                (reason, now, xp, pnl, pnl_pct, dur, int(row["id"])),
            )
            n += 1
    return n


def log_trade_close(trade_id: int, data: dict):
    data["id"] = trade_id
    with _conn() as db:
        db.execute("""
            UPDATE trades SET
                exit_price  = :exit_price,
                pnl         = :pnl,
                pnl_pct     = :pnl_pct,
                status      = :status,
                close_reason= :close_reason,
                close_ts    = :close_ts,
                duration_min= :duration_min,
                tp1_hit     = :tp1_hit,
                be_activated= :be_activated
            WHERE id = :id
        """, data)


def log_sr_change(data: dict) -> None:
    """S/R aktif kenar degisimi audit kaydi (yalniz gercek degisimde cagrilir)."""
    from datetime import datetime, timezone

    data.setdefault("ts", time.time())
    data["ts_human"] = datetime.fromtimestamp(
        data["ts"], tz=timezone.utc
    ).strftime("%Y-%m-%d %H:%M:%S UTC")
    try:
        with _conn() as db:
            db.execute(
                """
                INSERT INTO sr_changes
                (ts,ts_human,price,old_support,new_support,old_resistance,new_resistance,
                 d_support,d_resistance,support_reason,resistance_reason,
                 dist_support_pt,dist_resistance_pt,band_old_pt,band_new_pt,touched)
                VALUES
                (:ts,:ts_human,:price,:old_support,:new_support,:old_resistance,:new_resistance,
                 :d_support,:d_resistance,:support_reason,:resistance_reason,
                 :dist_support_pt,:dist_resistance_pt,:band_old_pt,:band_new_pt,:touched)
                """,
                data,
            )
    except Exception:
        pass


def log_regime_change(data: dict):
    from datetime import datetime, timezone
    data["ts_human"] = datetime.fromtimestamp(
        data.get("ts", time.time()), tz=timezone.utc
    ).strftime("%Y-%m-%d %H:%M:%S UTC")
    with _conn() as db:
        db.execute("""
            INSERT INTO regime_log
            (ts,ts_human,regime,score,price,q1,q2,q3,q4,prev_regime)
            VALUES
            (:ts,:ts_human,:regime,:score,:price,:q1,:q2,:q3,:q4,:prev_regime)
        """, data)


def log_error(source: str, error: str, context: str = ""):
    import time as _t
    from datetime import datetime, timezone
    ts = _t.time()
    with _conn() as db:
        db.execute("""
            INSERT INTO errors (ts,ts_human,source,error,context)
            VALUES (?,?,?,?,?)
        """, (ts,
              datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
              source, str(error)[:500], context[:500]))


def get_recent_signals(hours: int = 24) -> list:
    cutoff = time.time() - hours * 3600
    with _conn() as db:
        rows = db.execute(
            "SELECT * FROM signals WHERE ts > ? ORDER BY ts DESC", (cutoff,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_recent_trades(hours: int = 24) -> list:
    cutoff = time.time() - hours * 3600
    with _conn() as db:
        rows = db.execute(
            "SELECT * FROM trades WHERE open_ts > ? ORDER BY open_ts DESC", (cutoff,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_stats(hours: int = 24) -> dict:
    cutoff = time.time() - hours * 3600
    with _conn() as db:
        r = db.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END) as losses,
                COALESCE(SUM(pnl), 0) as total_pnl,
                COALESCE(AVG(pnl_pct), 0) as avg_pnl_pct,
                COALESCE(AVG(duration_min), 0) as avg_duration
            FROM trades
            WHERE open_ts > ? AND status != 'OPEN'
        """, (cutoff,)).fetchone()
        signals_r = db.execute(
            "SELECT COUNT(*) as total, SUM(entered) as entered FROM signals WHERE ts > ?",
            (cutoff,)
        ).fetchone()
        no_entry = db.execute("""
            SELECT no_entry_reason, COUNT(*) as cnt
            FROM signals WHERE ts > ? AND entered=0 AND no_entry_reason != ''
            GROUP BY no_entry_reason ORDER BY cnt DESC LIMIT 5
        """, (cutoff,)).fetchall()
    total = r["total"] or 1
    return {
        "hours"         : hours,
        "total_trades"  : r["total"],
        "wins"          : r["wins"] or 0,
        "losses"        : r["losses"] or 0,
        "win_rate"      : round((r["wins"] or 0) / total * 100, 1),
        "total_pnl"     : round(r["total_pnl"], 4),
        "avg_pnl_pct"   : round(r["avg_pnl_pct"], 3),
        "avg_duration_min": round(r["avg_duration"], 1),
        "signals_total" : signals_r["total"] or 0,
        "signals_entered": signals_r["entered"] or 0,
        "entry_rate_pct": round((signals_r["entered"] or 0) /
                                max(signals_r["total"] or 1, 1) * 100, 1),
        "top_no_entry"  : [(r["no_entry_reason"], r["cnt"]) for r in no_entry],
    }


def log_v3_attribution(attr: dict) -> int:
    """V3 attribution kaydi — trade_reason / block_reason."""
    from datetime import datetime, timezone

    from core.state import state as bot_state

    ts = float(attr.get("ts") or time.time())
    ts_human = datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )
    with _conn() as db:
        cur = db.execute(
            """
            INSERT INTO v3_attribution
            (ts, ts_human, price, action, scenario, intended_side,
             trade_candidate, entered, blocked, trade_id, reason_text,
             trade_reason_json, block_reason_json,
             trade_reason_sum, block_reason_sum, net_score,
             primary_block, primary_support, reject_reason, context_json)
            VALUES
            (?, ?, ?, ?, ?, ?,
             ?, ?, ?, ?, ?,
             ?, ?,
             ?, ?, ?,
             ?, ?, ?, ?)
            """,
            (
                ts,
                ts_human,
                float(attr.get("price") or 0),
                str(attr.get("action") or "WAIT"),
                str(attr.get("scenario") or ""),
                str(attr.get("intended_side") or ""),
                1 if attr.get("trade_candidate") else 0,
                1 if attr.get("entered") or attr.get("would_trade") else 0,
                1 if attr.get("blocked_opportunity") else 0,
                int(attr.get("trade_id") or 0) or None,
                str(attr.get("reason_text") or "")[:2000],
                json.dumps(attr.get("trade_reason") or {}, ensure_ascii=False),
                json.dumps(attr.get("block_reason") or {}, ensure_ascii=False),
                int(attr.get("trade_reason_sum") or 0),
                int(attr.get("block_reason_sum") or 0),
                int(attr.get("net_score") or 0),
                str(attr.get("primary_block") or ""),
                str(attr.get("primary_support") or ""),
                str(attr.get("reject_reason") or ""),
                json.dumps(
                    attr.get("context")
                    or {
                        "liquidity_bias": bot_state.v3_liquidity_bias,
                        "vacuum_score": bot_state.v3_vacuum_score,
                        "multi_tf_trend": bot_state.v3_multi_tf_trend,
                    },
                    ensure_ascii=False,
                    default=str,
                ),
            ),
        )
        return int(cur.lastrowid)


def reject_reason_stats(*, limit: int = 100, hours: int = 0) -> list[tuple[str, int]]:
    """REJECT_REASON sayilari (son limit kayit veya saat penceresi)."""
    with _conn() as db:
        if hours > 0:
            cutoff = time.time() - hours * 3600
            rows = db.execute(
                """
                SELECT reject_reason, COUNT(*) AS cnt
                FROM v3_attribution
                WHERE ts > ? AND action='WAIT' AND reject_reason != ''
                GROUP BY reject_reason
                ORDER BY cnt DESC
                """,
                (cutoff,),
            ).fetchall()
        else:
            rows = db.execute(
                """
                SELECT reject_reason, COUNT(*) AS cnt
                FROM (
                    SELECT reject_reason FROM v3_attribution
                    WHERE action='WAIT' AND reject_reason != ''
                    ORDER BY ts DESC
                    LIMIT ?
                )
                GROUP BY reject_reason
                ORDER BY cnt DESC
                """,
                (max(int(limit), 1),),
            ).fetchall()
    return [(str(r["reject_reason"]), int(r["cnt"])) for r in rows]


def link_v3_attribution_trade(
    attribution_id: int,
    trade_id: int,
    *,
    entered: int = 1,
    action: str = "",
    intended_side: str = "",
) -> int:
    """Trade ile attribution bagla. Baglanan attribution id dondurur (0=basarisiz)."""
    if trade_id <= 0:
        return 0
    side = str(intended_side or action or "").upper()
    if side in ("BUY",):
        side = "LONG"
    elif side in ("SELL",):
        side = "SHORT"
    act = str(action or "").upper()
    if act in ("BUY",):
        act = "LONG"
    elif act in ("SELL",):
        act = "SHORT"

    with _conn() as db:
        if attribution_id > 0:
            db.execute(
                """
                UPDATE v3_attribution
                SET trade_id=?, entered=?, blocked=0,
                    action=CASE
                        WHEN ? != '' AND action='WAIT' THEN ?
                        WHEN ? != '' THEN ?
                        ELSE action
                    END
                WHERE id=?
                """,
                (trade_id, entered, act, act, act, act, attribution_id),
            )
            return int(attribution_id)

        cutoff = time.time() - 180.0
        row = None
        if side in ("LONG", "SHORT"):
            row = db.execute(
                """
                SELECT id FROM v3_attribution
                WHERE trade_id IS NULL AND ts > ?
                  AND (
                    action = ?
                    OR (action IN ('LONG','SHORT') AND action = ?)
                    OR (trade_candidate=1 AND intended_side IN (?, ?))
                  )
                ORDER BY ts DESC LIMIT 1
                """,
                (
                    cutoff,
                    side,
                    side,
                    side,
                    "BUY" if side == "LONG" else "SELL",
                ),
            ).fetchone()
        if not row:
            row = db.execute(
                """
                SELECT id FROM v3_attribution
                WHERE trade_id IS NULL AND ts > ?
                  AND action IN ('LONG', 'SHORT')
                ORDER BY ts DESC LIMIT 1
                """,
                (cutoff,),
            ).fetchone()
        if not row:
            row = db.execute(
                """
                SELECT id FROM v3_attribution
                WHERE trade_id IS NULL AND ts > ?
                ORDER BY ts DESC LIMIT 1
                """,
                (cutoff,),
            ).fetchone()
        if row:
            aid = int(row["id"])
            db.execute(
                """
                UPDATE v3_attribution
                SET trade_id=?, entered=?, blocked=0,
                    action=CASE WHEN ? != '' THEN ? ELSE action END
                WHERE id=?
                """,
                (trade_id, entered, act, act, aid),
            )
            return aid
    return 0


def append_trade_attribution_notes(trade_id: int, notes_json: str) -> None:
    if trade_id <= 0:
        return
    tag = f"attr:{notes_json[:600]}"
    with _conn() as db:
        row = db.execute(
            "SELECT notes FROM trades WHERE id=?", (trade_id,)
        ).fetchone()
        old = str(row["notes"] or "") if row else ""
        if "attr:" in old:
            base = old.split("attr:")[0].strip()
            new_notes = f"{base} {tag}".strip() if base else tag
        else:
            new_notes = f"{old} {tag}".strip() if old else tag
        db.execute(
            "UPDATE trades SET notes=? WHERE id=?",
            (new_notes[:2000], trade_id),
        )


def attribution_block_stats(hours: int = 24 * 14) -> list[dict]:
    """primary_block bazinda engel sayilari."""
    cutoff = time.time() - hours * 3600
    with _conn() as db:
        rows = db.execute(
            """
            SELECT primary_block, COUNT(*) AS cnt,
                   SUM(trade_reason_sum) AS avg_support,
                   SUM(block_reason_sum) AS total_block
            FROM v3_attribution
            WHERE ts > ? AND blocked=1 AND primary_block != ''
            GROUP BY primary_block
            ORDER BY cnt DESC
            """,
            (cutoff,),
        ).fetchall()
    return [dict(r) for r in rows]


def attribution_blocked_with_outcome(
    hours: int = 24 * 14,
    forward_sec: float = 4 * 3600,
) -> list[dict]:
    """
    Engellenen firsatlar + sonraki fiyat hareketi (market_snapshots).
    Basit counterfactual: intended_side SHORT ise fiyat dususu = 'would_win'.
    """
    cutoff = time.time() - hours * 3600
    out: list[dict] = []
    with _conn() as db:
        rows = db.execute(
            """
            SELECT id, ts, price, intended_side, primary_block,
                   trade_reason_sum, block_reason_sum, scenario
            FROM v3_attribution
            WHERE ts > ? AND blocked=1 AND intended_side != ''
            ORDER BY ts DESC
            """,
            (cutoff,),
        ).fetchall()
        for row in rows:
            ts0 = float(row["ts"])
            p0 = float(row["price"] or 0)
            side = str(row["intended_side"] or "").upper()
            if p0 <= 0:
                continue
            snaps = db.execute(
                """
                SELECT price FROM market_snapshots
                WHERE ts > ? AND ts <= ? AND price > 0
                ORDER BY ts ASC
                """,
                (ts0, ts0 + forward_sec),
            ).fetchall()
            if not snaps:
                out.append({**dict(row), "outcome": "unknown", "move_pct": 0.0})
                continue
            prices = [float(s["price"]) for s in snaps]
            p_min, p_max = min(prices), max(prices)
            if side in ("SELL", "SHORT"):
                move_pct = (p0 - p_min) / p0 * 100
                would_win = move_pct >= 0.15
            else:
                move_pct = (p_max - p0) / p0 * 100
                would_win = move_pct >= 0.15
            out.append(
                {
                    **dict(row),
                    "outcome": "would_win" if would_win else "would_lose",
                    "move_pct": round(move_pct, 3),
                }
            )
    return out
