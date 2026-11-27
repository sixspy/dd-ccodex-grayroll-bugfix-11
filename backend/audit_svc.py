"""审计与变更历史写入。"""
from __future__ import annotations

import time

from fastapi import APIRouter, Header

from auth import current_user
from db import connect

router = APIRouter(prefix="/api/audit", tags=["audit"])


def write_audit(conn, actor: str, action: str, entity: str, entity_id: int | None, detail: str) -> None:
    conn.execute(
        "INSERT INTO audit_log(actor, action, entity, entity_id, detail, created_at) VALUES(?,?,?,?,?,?)",
        (actor, action, entity, entity_id, detail, time.time()),
    )


def write_history(conn, actor: str, action: str, detail: str, flag_id: int | None = None, env_id: int | None = None) -> None:
    conn.execute(
        "INSERT INTO change_history(flag_id, env_id, action, detail, actor, created_at) VALUES(?,?,?,?,?,?)",
        (flag_id, env_id, action, detail, actor, time.time()),
    )


@router.get("")
def list_audit(
    authorization: str | None = Header(default=None),
    limit: int = 80,
    action: str | None = None,
    entity: str | None = None,
):
    current_user(authorization)
    sql = "SELECT * FROM audit_log"
    where, args = [], []
    if action:
        where.append("action=?")
        args.append(action)
    if entity:
        where.append("entity=?")
        args.append(entity)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with connect() as conn:
        rows = conn.execute(sql, tuple(args)).fetchall()
    return {"items": [dict(r) for r in rows]}
