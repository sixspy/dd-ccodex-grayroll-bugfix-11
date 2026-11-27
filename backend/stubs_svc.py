"""题面空桩：远程推送 / 实验桥接（固定 501）。

BUGFIX-11 新增：空桩演练提交 + 工位锁「空桩工位锁互斥」+ 正式实推队列。
正确性边界：
- 甲路径（use_shadow=false，默认）：演练正式账、工位锁卸载、跨表核销同一事务提交；
  任一环节失败整体回滚，正式区零增。
- 乙路径（use_shadow=true）：只落影子卸锁草案 seat_unlock_shadows，
  row_locks 正式工位锁逐列零改；经 /unlock-drafts/{id}/apply 原子卸锁。
两条路径一次写入内不得混用。
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from audit_svc import write_audit, write_history
from auth import current_user, require_writer
from db import connect
from locks import SEAT_LOCK_KEY, acquire, assert_no_conflict, release

router = APIRouter(prefix="/api/stubs", tags=["stubs"])


class DrillIn(BaseModel):
    note: str = ""
    summary: str | None = None
    use_shadow: bool = False
    fault: str | None = None


class PushIn(BaseModel):
    note: str = ""
    payload: str | None = None


class AcquireIn(BaseModel):
    holder: str | None = None


# ---------------------------------------------------------------------------
# 原题面空桩：固定 501，契约不动
# ---------------------------------------------------------------------------
@router.get("/remote-push")
def remote_push_status(authorization: str | None = Header(default=None)):
    current_user(authorization)
    with connect() as conn:
        row = conn.execute("SELECT * FROM remote_push_stub WHERE id=1").fetchone()
    return {"stub": "remote_push", "ready": False, "note": dict(row)["note"] if row else "未初始化"}


@router.post("/remote-push/run")
def remote_push_run(authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    require_writer(user)
    raise HTTPException(501, detail="远程配置推送空桩未接通")


@router.get("/experiment-bridge")
def experiment_status(authorization: str | None = Header(default=None)):
    current_user(authorization)
    with connect() as conn:
        row = conn.execute("SELECT * FROM experiment_bridge_stub WHERE id=1").fetchone()
    return {"stub": "experiment_bridge", "ready": False, "note": dict(row)["note"] if row else "未初始化"}


@router.post("/experiment-bridge/link")
def experiment_link(authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    require_writer(user)
    raise HTTPException(501, detail="实验平台桥接空桩未接通")


# ---------------------------------------------------------------------------
# 工位锁状态：争用痕与核销账分列
# ---------------------------------------------------------------------------
def seat_lock_snapshot(conn) -> dict:
    now = time.time()
    row = conn.execute("SELECT * FROM row_locks WHERE lock_key=?", (SEAT_LOCK_KEY,)).fetchone()
    held = bool(row and row["expires_at"] > now)
    contention = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM contention_marks WHERE lock_key=? ORDER BY id DESC LIMIT 50",
            (SEAT_LOCK_KEY,),
        ).fetchall()
    ]
    writeoffs = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM seat_lock_writeoffs WHERE lock_key=? ORDER BY id DESC LIMIT 50",
            (SEAT_LOCK_KEY,),
        ).fetchall()
    ]
    queued = conn.execute(
        "SELECT COUNT(*) c FROM stub_real_pushes WHERE status='queued'"
    ).fetchone()["c"]
    pushed = conn.execute(
        "SELECT COUNT(*) c FROM stub_real_pushes WHERE status='pushed'"
    ).fetchone()["c"]
    if held:
        tone, panel_status, label = "danger", "seat_held", "工位锁被占用，后续实推排队中"
    elif queued:
        tone, panel_status, label = "warning", "queued", f"{queued} 条正式实推排队中"
    else:
        tone, panel_status, label = "success", "clear", "空桩演练/实推正常"
    return {
        "lock_key": SEAT_LOCK_KEY,
        "held": held,
        "holder": row["holder"] if held else None,
        "expires_at": row["expires_at"] if row else None,
        "contention": contention,
        "writeoffs": writeoffs,
        "queued_real_pushes": queued,
        "pushed_real_pushes": pushed,
        "stub_panel_ok": not held and queued == 0,
        "stub_panel": {"tone": tone, "status": panel_status, "label": label,
                       "queued": queued, "held": held},
    }


@router.get("/panel")
def stub_panel(authorization: str | None = Header(default=None)):
    """空桩页面板口径（总览 /api/dashboard 内嵌同口径副本）。"""
    current_user(authorization)
    with connect() as conn:
        return seat_lock_snapshot(conn)


@router.get("/seat-lock")
@router.get("/seatlock")
@router.get("/seat")
def get_seat_lock(authorization: str | None = Header(default=None)):
    current_user(authorization)
    with connect() as conn:
        return seat_lock_snapshot(conn)


@router.post("/seat-lock")
@router.post("/seat-lock/acquire")
@router.post("/seatlock")
@router.post("/seatlock/acquire")
@router.post("/seat/acquire")
def acquire_seat_lock(body: AcquireIn | None = None, authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    require_writer(user)
    conn = connect()
    try:
        # locks.acquire 已为原子条件 UPSERT：并发两路恰好一成一败，败方 409 留痕
        acquire(conn, SEAT_LOCK_KEY, user["username"])
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "acquired": True, "lock_key": SEAT_LOCK_KEY, "holder": user["username"]}


@router.post("/seat-lock/release")
@router.post("/seatlock/release")
@router.post("/seat/release")
@router.delete("/seat-lock")
@router.delete("/seatlock")
def release_seat_lock(body: AcquireIn | None = None, authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    require_writer(user)
    conn = connect()
    try:
        row = conn.execute(
            "SELECT holder FROM row_locks WHERE lock_key=?", (SEAT_LOCK_KEY,)
        ).fetchone()
        if row and row["holder"] != user["username"]:
            raise HTTPException(409, detail="工位锁由其他会话持有，无权卸载")
        release(conn, SEAT_LOCK_KEY, user["username"])
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "released": True, "lock_key": SEAT_LOCK_KEY}


# ---------------------------------------------------------------------------
# 空桩演练提交（甲路径同事务 / 乙路径影子卸锁草案）
# ---------------------------------------------------------------------------
@router.post("/drills")
@router.post("/drill")
@router.post("/drills/submit")
@router.post("/drill/submit")
def submit_drill(body: DrillIn | None = None, authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    require_writer(user)
    body = body or DrillIn()
    note = (body.note or body.summary or "").strip()
    actor = user["username"]
    now = time.time()
    conn = connect()
    try:
        if body.use_shadow:
            # 乙路径：草案前只做只读互斥校验，绝不 UPSERT 续期，
            # 保证正式 row_locks 的 holder/expires_at 逐列零改。
            assert_no_conflict(conn, SEAT_LOCK_KEY, actor)
            # 乙路径：只落影子卸锁草案，正式 row_locks 零改，等待原子 apply
            holder_row = conn.execute(
                "SELECT holder FROM row_locks WHERE lock_key=?", (SEAT_LOCK_KEY,)
            ).fetchone()
            holder = holder_row["holder"] if holder_row else actor
            cur = conn.execute(
                "INSERT INTO seat_unlock_shadows(lock_key, holder, drill_note, status, created_by, created_at) "
                "VALUES(?,?,?,'open',?,?)",
                (SEAT_LOCK_KEY, holder, note, actor, now),
            )
            draft_id = cur.lastrowid
            write_audit(conn, actor, "stub_drill_shadow", "seat_unlock_shadow", draft_id, "乙路径：影子卸锁草案，正式工位锁零改")
            write_history(conn, actor, "stub_drill_shadow", f"空桩演练影子卸锁草案 #{draft_id} {note}".strip())
            conn.commit()
            return {
                "ok": True,
                "mode": "shadow",
                "path": "乙路径：先落影子卸锁草案，原子升正前正式工位锁零改",
                "draft_id": draft_id,
                "lock_key": SEAT_LOCK_KEY,
                "formal_lock_changed": False,
            }

        # 甲路径：原子抢互斥锁 -> 演练正式落账 -> 同事务卸工位锁
        # -> 跨表联查账实 -> 写核销账与审计/历史，最后一次 commit。
        acquire(conn, SEAT_LOCK_KEY, actor)
        cur = conn.execute(
            "INSERT INTO stub_drills(note, path, official, status, actor, created_at) "
            "VALUES(?,'official',1,'success',?,?)",
            (note, actor, now),
        )
        drill_id = cur.lastrowid

        # 同事务卸锁（修复 BUGFIX-11：不再跳过 release）
        release(conn, SEAT_LOCK_KEY, actor)

        # 跨表联查账实：演练正式账必须 success，且工位锁行在本事务内已消失
        acct = conn.execute(
            """
            SELECT d.status AS status, d.actor AS actor, l.holder AS lock_holder
            FROM stub_drills d
            LEFT JOIN row_locks l ON l.lock_key = ?
            WHERE d.id = ?
            """,
            (SEAT_LOCK_KEY, drill_id),
        ).fetchone()
        if acct is None or acct["status"] != "success" or acct["lock_holder"] is not None:
            raise HTTPException(500, detail="演练提交与工位锁卸载账实不符，整事务回滚")

        wo_cur = conn.execute(
            "INSERT INTO seat_lock_writeoffs(lock_key, holder, source, matched, drill_id, detail, actor, created_at) "
            "VALUES(?,?,'drill_official_txn',1,?,?,?,?)",
            (SEAT_LOCK_KEY, actor, drill_id, f"演练 #{drill_id} 成功，同事务卸锁并联查核销", actor, time.time()),
        )
        writeoff_id = wo_cur.lastrowid
        write_audit(conn, actor, "stub_drill_official", "stub_drill", drill_id, "甲路径：演练成功，同事务卸工位锁并联查核销")
        write_history(conn, actor, "stub_drill", f"空桩演练成功 #{drill_id} 并卸工位锁 {note}".rstrip())

        if body.fault == "after_write":
            # 故障注入点在全部正式写入之后、commit 之前：
            # 演练账/核销账/锁行必须随整事务回滚为零。
            raise HTTPException(500, detail="演练提交后注入故障：应整事务回滚")

        conn.commit()
        return {
            "ok": True,
            "mode": "official",
            "path": "甲路径：演练成功同事务卸工位锁，跨表联查核销",
            "drill_id": drill_id,
            "lock_key": SEAT_LOCK_KEY,
            "lock_released": True,
            "reconciled": True,
            "writeoff_id": writeoff_id,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@router.get("/drills")
def list_drills(authorization: str | None = Header(default=None), limit: int = 50):
    current_user(authorization)
    with connect() as conn:
        items = [dict(r) for r in conn.execute(
            "SELECT * FROM stub_drills ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()]
        drafts = [dict(r) for r in conn.execute(
            "SELECT * FROM seat_unlock_shadows ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()]
    return {"items": items, "drafts": drafts}


@router.get("/unlock-drafts")
def list_unlock_drafts(authorization: str | None = Header(default=None)):
    current_user(authorization)
    with connect() as conn:
        items = [dict(r) for r in conn.execute(
            "SELECT * FROM seat_unlock_shadows ORDER BY id DESC"
        ).fetchall()]
    return {"items": items}


@router.post("/unlock-drafts/{draft_id}/apply")
def apply_unlock_draft(draft_id: int, authorization: str | None = Header(default=None)):
    """乙路径原子升正：校验草案 + 锁主后原子卸锁、核销、置 applied。"""
    user = current_user(authorization)
    require_writer(user)
    actor = user["username"]
    conn = connect()
    try:
        draft = conn.execute(
            "SELECT * FROM seat_unlock_shadows WHERE id=?", (draft_id,)
        ).fetchone()
        if not draft:
            raise HTTPException(404, detail="影子卸锁草案不存在")

        # 原子认领草案：条件更新 + rowcount 仲裁，双发 apply 恰好一次成功，
        # 败者 400 且不产生第二笔核销。
        claim = conn.execute(
            "UPDATE seat_unlock_shadows SET status='applied', applied_at=? "
            "WHERE id=? AND status='open'",
            (time.time(), draft_id),
        )
        if claim.rowcount == 0:
            raise HTTPException(400, detail="影子卸锁草案已核销，不可重复升正")

        row = conn.execute(
            "SELECT holder FROM row_locks WHERE lock_key=?", (SEAT_LOCK_KEY,)
        ).fetchone()
        if not row:
            raise HTTPException(409, detail="正式工位锁不存在，无法原子卸锁")
        if row["holder"] != draft["holder"]:
            raise HTTPException(409, detail="工位锁主与影子草案不一致，拒绝卸锁")

        release(conn, SEAT_LOCK_KEY, draft["holder"])
        cur = conn.execute(
            "INSERT INTO seat_lock_writeoffs(lock_key, holder, source, matched, detail, actor, created_at) "
            "VALUES(?,?,'shadow_atomic',1,?,?,?)",
            (SEAT_LOCK_KEY, draft["holder"], f"影子草案 #{draft_id} 原子升正卸锁", actor, time.time()),
        )
        wo_id = cur.lastrowid
        write_audit(conn, actor, "stub_unlock_apply", "seat_unlock_shadow", draft_id, f"原子卸锁核销 #{wo_id}")
        write_history(conn, actor, "stub_unlock_apply", f"影子卸锁草案 #{draft_id} 升正，工位锁已卸")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"ok": True, "mode": "official", "draft_id": draft_id, "lock_released": True, "writeoff_id": wo_id}


# ---------------------------------------------------------------------------
# 正式实推：工位锁被占时如实排队，禁止面板假绿
# ---------------------------------------------------------------------------
def _push_list(conn):
    items = [dict(r) for r in conn.execute(
        "SELECT * FROM stub_real_pushes ORDER BY id DESC LIMIT 100"
    ).fetchall()]
    queued = sum(1 for i in items if i["status"] == "queued")
    pushed = sum(1 for i in items if i["status"] == "pushed")
    return items, queued, pushed


@router.post("/real-push")
@router.post("/real-push/run")
@router.post("/real-pushes")
def real_push(body: PushIn | None = None, authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    require_writer(user)
    note = ((body.note if body else "") or (body.payload if body else "") or "").strip()
    actor = user["username"]
    now = time.time()
    conn = connect()
    try:
        row = conn.execute(
            "SELECT holder, expires_at FROM row_locks WHERE lock_key=?", (SEAT_LOCK_KEY,)
        ).fetchone()
        locked_by_other = bool(row and row["expires_at"] > now and row["holder"] != actor)
        if locked_by_other:
            cur = conn.execute(
                "INSERT INTO stub_real_pushes(note, status, actor, created_at) VALUES(?,'queued',?,?)",
                (note, actor, now),
            )
            pid = cur.lastrowid
            conn.commit()
            return JSONResponse(status_code=202, content={
                "ok": True,
                "delivered": False,
                "queued": True,
                "status": "queued",
                "push_id": pid,
                "blocked_by": row["holder"],
            })

        cur = conn.execute(
            "INSERT INTO stub_real_pushes(note, status, actor, created_at, finished_at) "
            "VALUES(?,'pushed',?, ?, ?)",
            (note, actor, now, now),
        )
        pid = cur.lastrowid
        conn.commit()
        return {"ok": True, "delivered": True, "queued": False, "status": "pushed", "push_id": pid}
    finally:
        conn.close()


@router.get("/real-push")
@router.get("/real-pushes")
def list_real_push(authorization: str | None = Header(default=None)):
    current_user(authorization)
    with connect() as conn:
        items, queued, pushed = _push_list(conn)
    return {"items": items, "queued": queued, "pushed": pushed}


@router.post("/real-pushes/drain")
@router.post("/real-push/drain")
def drain_real_push(authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    require_writer(user)
    conn = connect()
    pushed = 0
    try:
        row = conn.execute(
            "SELECT holder, expires_at FROM row_locks WHERE lock_key=?", (SEAT_LOCK_KEY,)
        ).fetchone()
        if row and row["expires_at"] > time.time():
            raise HTTPException(409, detail=f"工位锁仍由 {row['holder']} 持有，队列不可排空")
        rows = conn.execute(
            "SELECT id FROM stub_real_pushes WHERE status='queued' ORDER BY id ASC"
        ).fetchall()
        now = time.time()
        for r in rows:
            conn.execute(
                "UPDATE stub_real_pushes SET status='pushed', finished_at=? WHERE id=?",
                (now, r["id"]),
            )
            pushed += 1
        write_audit(conn, user["username"], "stub_real_push_drain", "stub_real_pushes", None, f"排空排队实推 {pushed} 条")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"ok": True, "pushed": pushed}
