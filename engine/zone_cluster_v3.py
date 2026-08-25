"""
engine/zone_cluster_v3.py — Yakin zone'lari savas alani (cluster) olarak birlestir.

1965 kirildi ama 1972/1978 destekleri cluster icinde canliysa hemen short yok.
"""
from __future__ import annotations

from core.config import cfg
from core.logger import get_logger
from engine.liquidity_map_v3 import zone_half_width

log = get_logger("ZoneClusterV3")

LC_ACTIVE = "ACTIVE"
LC_ROLE_REVERSAL = "ROLE_REVERSAL"
LC_BROKEN = "BROKEN"
LC_TRANSITION = "TRANSITION"
LC_ARCHIVED = "ARCHIVED"


def _cluster_gap(price: float) -> float:
    px = float(price or 0)
    if px <= 0:
        return 8.0
    pct = float(getattr(cfg, "V3_CLUSTER_GAP_PCT", 0.0035) or 0.0035)
    return max(px * pct, zone_half_width(px) * 2.5, 5.0)


def build_zone_clusters(zones: list[dict], price: float) -> list[dict]:
    """Ayni rolde yakin merkezler -> tek cluster bandi."""
    px = float(price or 0)
    if not zones:
        return []

    clusters: list[dict] = []
    gap = _cluster_gap(px)

    for role in ("support", "resistance"):
        members = sorted(
            [z for z in zones if str(z.get("role") or "") == role],
            key=lambda z: float(z.get("center", 0) or 0),
        )
        if not members:
            continue
        current: list[dict] = []
        for z in members:
            c = float(z.get("center", 0) or 0)
            if not current:
                current = [z]
                continue
            last_c = float(current[-1].get("center", 0) or 0)
            if c - last_c <= gap:
                current.append(z)
            else:
                clusters.append(_finalize_cluster(current, role, px))
                current = [z]
        if current:
            clusters.append(_finalize_cluster(current, role, px))

    return clusters


def _finalize_cluster(members: list[dict], role: str, price: float) -> dict:
    lows = [float(z.get("zone_low", z.get("center", 0)) or 0) for z in members]
    highs = [float(z.get("zone_high", z.get("center", 0)) or 0) for z in members]
    centers = [float(z.get("center", 0) or 0) for z in members]
    active = [
        z
        for z in members
        if str(z.get("lifecycle") or "") in (LC_ACTIVE, LC_ROLE_REVERSAL)
    ]
    broken = [z for z in members if str(z.get("lifecycle") or "") == LC_BROKEN]
    archived = [z for z in members if str(z.get("lifecycle") or "") == LC_ARCHIVED]

    return {
        "id": f"cluster:{role}:{round(min(centers), 1)}-{round(max(centers), 1)}",
        "role": role,
        "cluster_low": round(min(lows), 2),
        "cluster_high": round(max(highs), 2),
        "centers": centers,
        "member_count": len(members),
        "active_count": len(active),
        "broken_count": len(broken),
        "archived_count": len(archived),
        "members": [str(z.get("id", "")) for z in members],
        "battle_zone": len(members) >= 2,
        "strength": max(int(z.get("strength", 0) or 0) for z in members) if members else 0,
    }


def cluster_blocks_immediate_chase(
    side: str,
    broken_center: float,
    clusters: list[dict] | None,
    price: float,
) -> tuple[bool, str]:
    """
    Kirilan tek seviye var ama cluster'da ust/alt destekler canli -> kovalama blok.
    """
    side = str(side or "").upper()
    px = float(price or 0)
    bc = float(broken_center or 0)
    clusters = list(clusters or [])
    if px <= 0 or bc <= 0 or not clusters:
        return False, ""

    if side in ("SHORT", "SELL"):
        for cl in clusters:
            if str(cl.get("role") or "") != "support":
                continue
            if not cl.get("battle_zone"):
                continue
            if bc < float(cl.get("cluster_low", 0) or 0) or bc > float(cl.get("cluster_high", 0) or 0):
                continue
            if int(cl.get("active_count", 0) or 0) >= 1 and int(cl.get("broken_count", 0) or 0) >= 1:
                others = [
                    c
                    for c in (cl.get("centers") or [])
                    if abs(float(c) - bc) > _cluster_gap(px) * 0.5
                ]
                if others:
                    return (
                        True,
                        f"Cluster destek {cl['cluster_low']:.0f}-{cl['cluster_high']:.0f} "
                        f"({cl['active_count']} aktif / {cl['broken_count']} kirildi) — "
                        f"1965 kirildi ama savas alani canli, short kovalama yok.",
                    )
    if side in ("LONG", "BUY"):
        for cl in clusters:
            if str(cl.get("role") or "") != "resistance":
                continue
            if not cl.get("battle_zone"):
                continue
            if bc < float(cl.get("cluster_low", 0) or 0) or bc > float(cl.get("cluster_high", 0) or 0):
                continue
            if int(cl.get("active_count", 0) or 0) >= 1 and int(cl.get("broken_count", 0) or 0) >= 1:
                return (
                    True,
                    f"Cluster direnc {cl['cluster_low']:.0f}-{cl['cluster_high']:.0f} "
                    f"aktif uyeler var — long kovalama yok.",
                )
    return False, ""
