"""占用指纹成员集合反查（hash → 成员事件 id 集合）。

形态择一：独立 ``occupancy_hash_members`` 反查表（不在 proof 行内塞不可按
hash 索引的 JSON 快照）。每个占用的成员事件逐行落库，``ord`` 与正算序列
``ORDER BY event_ts, id`` 同序；``content_hash`` 与 ``occupancy_proofs.content_hash``
同值同生共死。

硬约束（由 occupancy_hash_proof 的同事务写路径与本模块只读核证共同保证）：
  * 禁止 hash 通过而反查缺行；
  * 禁止反查集合与正算成员分叉；
  * 禁止只展示 hash 不建反查；
  * 禁止失败后 hash 在反查空（整笔回滚，不存在半截写）。
"""
import json
from datetime import datetime, timezone

STATUS = "implemented"

MEMBERS_DDL = """
CREATE TABLE IF NOT EXISTS occupancy_hash_members (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  occupancy_id TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  member_event_id TEXT NOT NULL,
  ord INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(occupancy_id, member_event_id)
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_schema(conn):
    """建表 + 反查索引。幂等，须在调用方事务内执行。

    注意：只能用 execute，不能用 executescript——后者会隐式 COMMIT，
    销毁调用方的事务/保存点，破坏「同事务同成败」。
    """
    conn.execute(MEMBERS_DDL)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_occ_hash_members_hash "
        "ON occupancy_hash_members(content_hash)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_occ_hash_members_occ "
        "ON occupancy_hash_members(occupancy_id, ord)"
    )


def replace_members(conn, occupancy_id: str, content_hash: str, event_ids: list) -> None:
    """同事务整组替换某占用的反查成员行。须在调用方事务/保存点内使用。

    先删后插：hash 每次重算都与反查行同值替换，不存在「hash 已落、反查仍旧」。
    任一步抛错由调用方保存点整笔回滚，杜绝半截写。
    """
    ensure_schema(conn)
    now = utcnow()
    conn.execute("DELETE FROM occupancy_hash_members WHERE occupancy_id=?", (occupancy_id,))
    conn.executemany(
        "INSERT INTO occupancy_hash_members(occupancy_id, content_hash, member_event_id, ord, created_at) "
        "VALUES (?,?,?,?,?)",
        [
            (occupancy_id, content_hash, str(eid), ord_no, now)
            for ord_no, eid in enumerate(event_ids)
        ],
    )


def reverse_event_ids(conn, occupancy_id: str) -> list:
    """反查读出的有序成员事件 id（与正算同序规则 ord 升序）。"""
    rows = conn.execute(
        "SELECT member_event_id FROM occupancy_hash_members "
        "WHERE occupancy_id=? ORDER BY ord, id",
        (occupancy_id,),
    ).fetchall()
    return [r["member_event_id"] for r in rows]


def reverse_hash(conn, occupancy_id: str):
    """反查表内该占用当前登记的 hash；无反查行返回 None。"""
    row = conn.execute(
        "SELECT content_hash FROM occupancy_hash_members WHERE occupancy_id=? LIMIT 1",
        (occupancy_id,),
    ).fetchone()
    return row["content_hash"] if row else None


def members_of(conn, occupancy_id: str) -> dict:
    """成员反查核证只读视图：hash → 反查成员集合，并与正算成员互逆对账。

    占用不存在抛 KeyError（路由映射 404）。任何分叉 / 缺行均 aligned=False，
    并给出 fail_reason，禁止冒充成功。
    """
    from app.modules import occupancy_hash_proof as ohp

    occ = conn.execute("SELECT * FROM occupancies WHERE id=?", (occupancy_id,)).fetchone()
    if occ is None:
        raise KeyError(f"占用不存在: {occupancy_id}")
    ensure_schema(conn)

    proof = conn.execute(
        "SELECT * FROM occupancy_proofs WHERE occupancy_id=?", (occupancy_id,)
    ).fetchone()
    forward_ids = ohp.member_event_ids(conn, occ)
    reverse_ids = reverse_event_ids(conn, occupancy_id)
    stored_hash = proof["content_hash"] if proof else None
    rev_hash = reverse_hash(conn, occupancy_id)

    fail_reason = None
    if proof is None:
        fail_reason = "proof_missing"
    elif not stored_hash:
        fail_reason = "hash_missing"
    elif not reverse_ids:
        # 禁止 hash 通过而反查缺行 / 禁止失败后 hash 在反查空
        fail_reason = "reverse_missing"
    elif rev_hash != stored_hash:
        # 禁止反查挂在旧 hash 上
        fail_reason = "reverse_hash_stale"
    elif reverse_ids != forward_ids:
        # 禁止反查集合与正算成员分叉
        fail_reason = "member_set_fork"

    return {
        "occupancy_id": occupancy_id,
        "cell_id": occ["cell_id"],
        "tracking_no": occ["tracking_no"],
        "hash": stored_hash,
        "reverse_hash": rev_hash,
        "member_ids": reverse_ids,
        "forward_event_ids": forward_ids,
        "reverse_count": len(reverse_ids),
        "forward_count": len(forward_ids),
        "aligned": fail_reason is None,
        "fail_reason": fail_reason,
        "verified_at": utcnow(),
    }


def members_by_hash(conn, content_hash: str) -> dict:
    """按 hash 反查成员集合（本模块存在的核心理由：反查表可按 hash 索引）。

    无该 hash 的反查行抛 KeyError（路由映射 404）；反查行与 proof / 正算
    成员分叉时 aligned=False 并给原因，不静默冒充。
    """
    from app.modules import occupancy_hash_proof as ohp

    ensure_schema(conn)
    rows = conn.execute(
        "SELECT DISTINCT occupancy_id FROM occupancy_hash_members WHERE content_hash=? ORDER BY occupancy_id",
        (content_hash,),
    ).fetchall()
    if not rows:
        raise KeyError(f"hash 反查无成员集合: {content_hash}")
    items = []
    aligned_all = True
    for row in rows:
        occupancy_id = row["occupancy_id"]
        occ = conn.execute("SELECT * FROM occupancies WHERE id=?", (occupancy_id,)).fetchone()
        if occ is None:
            items.append(
                {
                    "occupancy_id": occupancy_id,
                    "member_ids": reverse_event_ids(conn, occupancy_id),
                    "aligned": False,
                    "fail_reason": "occupancy_missing",
                }
            )
            aligned_all = False
            continue
        proof = conn.execute(
            "SELECT * FROM occupancy_proofs WHERE occupancy_id=?", (occupancy_id,)
        ).fetchone()
        reverse_ids = reverse_event_ids(conn, occupancy_id)
        forward_ids = ohp.member_event_ids(conn, occ)
        fail_reason = None
        if proof is None or not proof["content_hash"]:
            fail_reason = "proof_missing"
        elif reverse_ids != forward_ids:
            fail_reason = "member_set_fork"
        elif proof["content_hash"] != content_hash:
            fail_reason = "reverse_hash_stale"
        if fail_reason is not None:
            aligned_all = False
        items.append(
            {
                "occupancy_id": occupancy_id,
                "hash": content_hash,
                "member_ids": reverse_ids,
                "forward_event_ids": forward_ids,
                "aligned": fail_reason is None,
                "fail_reason": fail_reason,
            }
        )
    return {
        "hash": content_hash,
        "occupancies": items,
        "aligned": aligned_all,
        "verified_at": utcnow(),
    }


def verify_members(conn, occupancy_id: str) -> dict:
    """成员反查核证只读接口的核证形态：正算成员 ⇔ 反查同集 + hash 同源。"""
    view = members_of(conn, occupancy_id)
    view["verify_ok"] = view["aligned"]
    return view


def backfill_for_proof(conn, occupancy_id: str, content_hash: str, event_ids: list) -> None:
    """存量 proof 补反查：与正算成员同源写入，供迁移/回填在同事务内调用。"""
    replace_members(conn, occupancy_id, content_hash, event_ids)


def migrate(conn):
    """建反查表与索引；为存量已落 hash 的 proof 同源补齐反查行（同事务）。"""
    ensure_schema(conn)
    existing = {
        r["occupancy_id"]
        for r in conn.execute("SELECT DISTINCT occupancy_id FROM occupancy_hash_members").fetchall()
    }
    proofs = conn.execute(
        "SELECT occupancy_id, content_hash, event_ids_json FROM occupancy_proofs "
        "WHERE content_hash IS NOT NULL AND content_hash != '' ORDER BY id"
    ).fetchall()
    for proof in proofs:
        if proof["occupancy_id"] in existing:
            continue
        ids = json.loads(proof["event_ids_json"])
        backfill_for_proof(conn, proof["occupancy_id"], proof["content_hash"], ids)
