"""变更历史。"""
from __future__ import annotations

from fastapi import APIRouter, Header

from auth import current_user
from db import connect

router = APIRouter(prefix="/api/history", tags=["history"])


@router.get("")
def list_history(
    authorization: str | None = Header(default=None),
    limit: int = 100,
    action: str | None = None,
):
    current_user(authorization)
    sql = """
        SELECT h.*, f.key AS flag_key, e.code AS env_code
        FROM change_history h
        LEFT JOIN flags f ON f.id=h.flag_id
        LEFT JOIN environments e ON e.id=h.env_id
    """
    args: list = []
    if action:
        sql += " WHERE h.action=?"
        args.append(action)
    sql += " ORDER BY h.id DESC LIMIT ?"
    args.append(limit)
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(sql, tuple(args)).fetchall()]
    return {"items": rows}
