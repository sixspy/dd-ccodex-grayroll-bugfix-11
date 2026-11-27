"""控制台摘要。"""
from __future__ import annotations

import time

from fastapi import APIRouter, Header

from auth import current_user
from db import connect
from locks import SEAT_LOCK_KEY
from models import FLAG_STATUS_LABEL

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("")
def dashboard(authorization: str | None = Header(default=None)):
    current_user(authorization)
    with connect() as conn:
        flags = conn.execute("SELECT COUNT(*) c FROM flags").fetchone()["c"]
        active = conn.execute("SELECT COUNT(*) c FROM flags WHERE status='active'").fetchone()["c"]
        killed = conn.execute("SELECT COUNT(*) c FROM flags WHERE status='killed'").fetchone()["c"]
        releases = conn.execute("SELECT COUNT(*) c FROM releases WHERE official=1").fetchone()["c"]
        shadows = conn.execute(
            "SELECT COUNT(*) c FROM release_shadows WHERE status='open'"
        ).fetchone()["c"]
        overrides = conn.execute("SELECT COUNT(*) c FROM overrides WHERE active=1").fetchone()["c"]
        by_status = [
            {
                "status": r["status"],
                "label": FLAG_STATUS_LABEL.get(r["status"], r["status"]),
                "c": r["c"],
            }
            for r in conn.execute("SELECT status, COUNT(*) c FROM flags GROUP BY status").fetchall()
        ]
        recent = [
            dict(r)
            for r in conn.execute(
                "SELECT id, action, detail, actor, created_at FROM change_history ORDER BY id DESC LIMIT 8"
            ).fetchall()
        ]

        # ---- BUGFIX-11 空桩工位锁 / 实推队列 / 争用核销分账 ----
        now = time.time()
        seat = conn.execute(
            "SELECT holder, expires_at FROM row_locks WHERE lock_key=?", (SEAT_LOCK_KEY,)
        ).fetchone()
        seat_held = bool(seat and seat["expires_at"] > now)
        queued = conn.execute(
            "SELECT COUNT(*) c FROM stub_real_pushes WHERE status='queued'"
        ).fetchone()["c"]
        pushed = conn.execute(
            "SELECT COUNT(*) c FROM stub_real_pushes WHERE status='pushed'"
        ).fetchone()["c"]
        stub_contention = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM contention_marks WHERE lock_key=? ORDER BY id DESC LIMIT 20",
                (SEAT_LOCK_KEY,),
            ).fetchall()
        ]
        stub_writeoffs = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM seat_lock_writeoffs WHERE lock_key=? ORDER BY id DESC LIMIT 20",
                (SEAT_LOCK_KEY,),
            ).fetchall()
        ]
        queued_items = [
            dict(r)
            for r in conn.execute(
                "SELECT id, note, actor, created_at FROM stub_real_pushes "
                "WHERE status='queued' ORDER BY id DESC LIMIT 10"
            ).fetchall()
        ]
        # 任意未过期行锁都视为锁面不健康（含泄漏的发布锁 / 工位锁）
        active_locks = conn.execute(
            "SELECT COUNT(*) c FROM row_locks WHERE expires_at > ?", (now,)
        ).fetchone()["c"]

    # BUGFIX-11 修复：灯板由账实计算。占锁 -> danger；仅排队 -> warning；全清才 success。
    if seat_held:
        tone, panel_status, label = "danger", "seat_held", "工位锁被占用，后续实推排队中"
    elif queued:
        tone, panel_status, label = "warning", "queued", f"{queued} 条正式实推排队中"
    else:
        tone, panel_status, label = "success", "clear", "空桩演练/实推正常"
    stub_panel_ok = not seat_held and queued == 0
    publish_lock_ok = active_locks == 0

    return {
        "flags": flags,
        "active": active,
        "killed": killed,
        "releases": releases,
        "open_shadows": shadows,
        "active_overrides": overrides,
        "by_status": by_status,
        "recent": recent,
        # 工位锁 / 实推计数（账实口径真实）
        "seat_lock_held": seat_held,
        "stub_seat_lock": {
            "lock_key": SEAT_LOCK_KEY,
            "held": seat_held,
            "holder": seat["holder"] if seat_held else None,
        },
        "real_push_queued": queued,
        "real_push_pushed": pushed,
        "stub_real_push": {"queued": queued, "pushed": pushed, "items": queued_items},
        "stub_contention": stub_contention,
        "stub_writeoffs": stub_writeoffs,
        # 锁健康与面板灯一律来自 row_locks / stub_real_pushes 账实，禁止硬编码假绿
        "publish_lock_ok": publish_lock_ok,
        "active_lock_count": active_locks,
        "stub_panel_ok": stub_panel_ok,
        "stub_panel": {
            "tone": tone,
            "status": panel_status,
            "label": label,
            "queued": queued,
            "held": seat_held,
        },
    }
