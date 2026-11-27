"""行锁与争用痕（嵌进发布/熔断/空桩主写路径）。

BUGFIX-11 修复：acquire 由「先 SELECT 后 UPSERT」的 check-then-act
改为单条原子条件 UPSERT + 回读确认，两路并发抢同一把锁时恰好一路成功；
败方只写 contention_marks 留痕（同连接立即提交，保证痕在、且不与调用方
事务产生跨连接写锁互锁），随后抛 409，败方调用方整体回滚、正式零改。
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Header, HTTPException

from auth import current_user
from db import connect

router = APIRouter(prefix="/api/locks", tags=["locks"])
TTL = 20.0

#: 空桩工位锁互斥锁名（空桩演练 / 工位占用 / 正式实推共用同一把互斥锁）
SEAT_LOCK_KEY = "空桩工位锁互斥"


def acquire(conn, key: str, holder: str) -> None:
    """原子获取行锁。

    - 不存在 / 已过期 / 同 holder 重入：获取或续期成功；
    - 其他 holder 持有效锁：写争用痕并抛 409。

    必须在调用方任何正式写入之前调用：冲突分支会提交争用痕。
    成功分支不提交，锁行留在调用方事务内，由调用方与正式写同事务提交/回滚。
    """
    now = time.time()
    conn.execute("PRAGMA busy_timeout=5000")
    # 单条 UPSERT 完成仲裁：仅当「同 holder 重入」或「旧锁已过期」时才允许覆盖。
    conn.execute(
        """
        INSERT INTO row_locks(lock_key, holder, expires_at) VALUES(?,?,?)
        ON CONFLICT(lock_key) DO UPDATE
          SET holder=excluded.holder, expires_at=excluded.expires_at
        WHERE row_locks.holder = ? OR row_locks.expires_at <= ?
        """,
        (key, holder, now + TTL, holder, now),
    )
    row = conn.execute(
        "SELECT holder, expires_at FROM row_locks WHERE lock_key=?", (key,)
    ).fetchone()
    if row is None or row["holder"] != holder:
        winner = row["holder"] if row else "unknown"
        # 败方只留痕：争用痕与调用方待回滚事务同连接写入并单独提交，
        # 不使用第二个写连接，避免 SQLite 写锁自死锁。
        conn.execute(
            "INSERT INTO contention_marks(lock_key, loser, winner, created_at) VALUES(?,?,?,?)",
            (key, holder, winner, now),
        )
        conn.commit()
        raise HTTPException(409, detail="发布锁争用，请稍后重试")


def assert_no_conflict(conn, key: str, holder: str) -> None:
    """只读碰撞校验（乙路径影子草案用）：绝不 UPSERT，正式锁行逐列零改。

    其他会话持有效锁时留争用痕并 409；无锁 / 同 holder / 已过期均放行且不写锁。
    """
    now = time.time()
    row = conn.execute("SELECT holder, expires_at FROM row_locks WHERE lock_key=?", (key,)).fetchone()
    if row and row["expires_at"] > now and row["holder"] != holder:
        conn.execute(
            "INSERT INTO contention_marks(lock_key, loser, winner, created_at) VALUES(?,?,?,?)",
            (key, holder, row["holder"], now),
        )
        conn.commit()
        raise HTTPException(409, detail="工位锁互斥：其他会话持有中，影子草案不予受理")


def release(conn, key: str, holder: str) -> None:
    conn.execute("DELETE FROM row_locks WHERE lock_key=? AND holder=?", (key, holder))


@router.get("/contention")
def list_contention(authorization: str | None = Header(default=None), limit: int = 40):
    current_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM contention_marks ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return {"items": [dict(r) for r in rows]}
