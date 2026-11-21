"""占用成员哈希核证：成员事件有序指纹落库 + 读时互证 + 重建留痕。

形态择一：指纹持久化在 ``occupancy_proofs.content_hash`` 列（不另立哈希核证表）；
``occupancy_proof_audits`` 仅为重建留痕，不存指纹真值。
闭路择一：占用开账 / 关账 / 成员变更在**同一事务**内重算指纹
（``refresh_proof`` 由 seed / ingest 在提交前调用），不存在「成员变了哈希仍旧」。

成员集合与事件表同源：同一格口 + 同一运单、事件时刻落在
``[opened_at, closed_at]``（开放占用无右界）的 ``parcel_events``，
按 ``(event_ts, id)`` 排序后的 id 序列即成员事件集合。
"""
import hashlib
import json
from datetime import datetime, timezone

from app.modules import occupancy_hash_members as ohm

STATUS = "implemented"

# 反查缺失/分叉的失败原因（成员反查核证与指纹核证共用一套语义）
REVERSE_MISSING = "reverse_missing"
REVERSE_HASH_STALE = "reverse_hash_stale"
MEMBER_SET_FORK = "member_set_fork"

HASH_ALGO = "sha256"
HASH_VERSION = "lockerbay-occ-hash-v1"

AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS occupancy_proof_audits (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  occupancy_id TEXT NOT NULL,
  old_hash TEXT,
  new_hash TEXT NOT NULL,
  old_event_ids_json TEXT,
  new_event_ids_json TEXT NOT NULL,
  actor TEXT NOT NULL DEFAULT 'system',
  reason TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fingerprint(event_ids) -> str:
    """成员事件 id 有序指纹：版本域分隔前缀 + 逐 id 分行写入的 SHA-256。"""
    h = hashlib.sha256()
    h.update(HASH_VERSION.encode("utf-8"))
    for eid in event_ids:
        h.update(b"\n")
        h.update(str(eid).encode("utf-8"))
    return f"{HASH_ALGO}:{h.hexdigest()}"


def member_event_ids(conn, occupancy) -> list:
    """与集合同源：从 parcel_events 现算某占用的成员事件 id（有序）。"""
    sql = "SELECT id FROM parcel_events WHERE cell_id=? AND tracking_no=? AND event_ts>=?"
    params = [occupancy["cell_id"], occupancy["tracking_no"], occupancy["opened_at"]]
    if occupancy["closed_at"]:
        sql += " AND event_ts<=?"
        params.append(occupancy["closed_at"])
    sql += " ORDER BY event_ts, id"
    return [r["id"] for r in conn.execute(sql, params).fetchall()]


class MemberReverseError(RuntimeError):
    """hash 与成员集合反查未能同成败落库（缺行 / 分叉），整笔必须回滚。"""


def _assert_inverse(conn, occupancy_id: str, digest: str, ids: list) -> None:
    """提交前互逆自检：正算成员 ⇔ 反查同集，且反查 hash 与 proof hash 同源。

    任一不成立即抛 MemberReverseError，由保存点回滚整笔（含 proof 与反查行），
    杜绝「hash 已落、反查空 / 分叉」的半截写。
    """
    rev_ids = ohm.reverse_event_ids(conn, occupancy_id)
    rev_hash = ohm.reverse_hash(conn, occupancy_id)
    if not rev_ids:
        raise MemberReverseError(f"反查缺行: {occupancy_id}")
    if rev_hash != digest:
        raise MemberReverseError(f"反查 hash 与核证指纹分叉: {occupancy_id}")
    if rev_ids != ids:
        raise MemberReverseError(f"反查集合与正算成员分叉: {occupancy_id}")


def refresh_proof(conn, occupancy_id: str) -> dict:
    """同事务重算：现算成员集合与指纹，proof 与反查表同成败 upsert。

    保存点包裹「写 proof + 建 hash→成员反查 + 互逆自检」：任一步失败，
    本保存点整笔回滚并向上抛错（外层事务随提交策略回滚），禁止只留 hash。
    须在调用方事务内使用。
    """
    occ = conn.execute("SELECT * FROM occupancies WHERE id=?", (occupancy_id,)).fetchone()
    if occ is None:
        raise KeyError(f"占用不存在: {occupancy_id}")
    ids = member_event_ids(conn, occ)
    digest = fingerprint(ids)
    ids_json = json.dumps(ids, ensure_ascii=False)
    conn.execute("SAVEPOINT occ_proof_refresh")
    try:
        existing = conn.execute(
            "SELECT id FROM occupancy_proofs WHERE occupancy_id=?", (occupancy_id,)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE occupancy_proofs SET cell_id=?, tracking_no=?, opened_at=?, closed_at=?, event_ids_json=?, content_hash=? WHERE id=?",
                (occ["cell_id"], occ["tracking_no"], occ["opened_at"], occ["closed_at"], ids_json, digest, existing["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO occupancy_proofs(occupancy_id, cell_id, tracking_no, opened_at, closed_at, event_ids_json, content_hash, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (occupancy_id, occ["cell_id"], occ["tracking_no"], occ["opened_at"], occ["closed_at"], ids_json, digest, utcnow()),
            )
        # 同事务建反查（hash → 成员事件 id 集合），与 proof 同生共死
        ohm.replace_members(conn, occupancy_id, digest, ids)
        # 提交前互逆自检，缺行/分叉即抛错回滚本保存点
        _assert_inverse(conn, occupancy_id, digest, ids)
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT occ_proof_refresh")
        conn.execute("RELEASE SAVEPOINT occ_proof_refresh")
        raise
    conn.execute("RELEASE SAVEPOINT occ_proof_refresh")
    return {"occupancy_id": occupancy_id, "content_hash": digest, "event_ids": ids}


def _verify(conn, occ, proof) -> dict:
    """读时互证：落库指纹 vs 当前事件集合现算指纹，并核成员集合反查同集同源。

    集合被改 / 核证缺失 / hash 通过却反查缺行 / 反查与正算分叉，均判失败。
    """
    current_ids = member_event_ids(conn, occ)
    current_hash = fingerprint(current_ids)
    stored_hash = proof["content_hash"] if proof else None
    stored_ids = json.loads(proof["event_ids_json"]) if proof else None
    reverse_ids = ohm.reverse_event_ids(conn, occ["id"])
    reverse_hash = ohm.reverse_hash(conn, occ["id"])
    if proof is None:
        ok, reason = False, "proof_missing"
    elif stored_hash is None:
        ok, reason = False, "hash_missing"
    elif stored_hash != current_hash or stored_ids != current_ids:
        ok, reason = False, "member_set_changed"
    elif not reverse_ids:
        # 硬约束：禁止 hash 通过而反查缺行
        ok, reason = False, REVERSE_MISSING
    elif reverse_hash != stored_hash:
        # 硬约束：禁止反查挂旧 hash（hash 在反查空/旧）
        ok, reason = False, REVERSE_HASH_STALE
    elif reverse_ids != current_ids:
        # 硬约束：禁止反查集合与正算成员分叉
        ok, reason = False, MEMBER_SET_FORK
    else:
        ok, reason = True, None
    return {
        "occupancy_id": occ["id"],
        "verify_ok": ok,
        "fail_reason": reason,
        "stored_hash": stored_hash,
        "current_hash": current_hash,
        "stored_event_ids": stored_ids,
        "current_event_ids": current_ids,
        "reverse_event_ids": reverse_ids,
        "reverse_hash": reverse_hash,
        "reverse_aligned": ok,
        "verified_at": utcnow(),
    }


def verify_occupancy(conn, occupancy_id: str) -> dict:
    occ = conn.execute("SELECT * FROM occupancies WHERE id=?", (occupancy_id,)).fetchone()
    if occ is None:
        raise KeyError(f"占用不存在: {occupancy_id}")
    proof = conn.execute(
        "SELECT * FROM occupancy_proofs WHERE occupancy_id=?", (occupancy_id,)
    ).fetchone()
    return _verify(conn, occ, proof)


def list_proofs(conn, limit: int = 100) -> list:
    """核证只读视图：每条占用（含漏挂无核证的）带校验结果字段。"""
    rows = conn.execute(
        """
        SELECT o.id AS occupancy_id, o.cell_id, o.tracking_no, o.opened_at, o.closed_at, o.status,
               p.id AS proof_id, p.event_ids_json, p.content_hash, p.created_at AS proof_created_at
        FROM occupancies o
        LEFT JOIN occupancy_proofs p ON p.occupancy_id = o.id
        ORDER BY o.opened_at DESC, o.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    out = []
    for row in rows:
        occ = conn.execute("SELECT * FROM occupancies WHERE id=?", (row["occupancy_id"],)).fetchone()
        proof = (
            conn.execute("SELECT * FROM occupancy_proofs WHERE id=?", (row["proof_id"],)).fetchone()
            if row["proof_id"] is not None
            else None
        )
        verdict = _verify(conn, occ, proof)
        out.append(
            {
                "occupancy_id": row["occupancy_id"],
                "cell_id": row["cell_id"],
                "tracking_no": row["tracking_no"],
                "opened_at": row["opened_at"],
                "closed_at": row["closed_at"],
                "status": row["status"],
                "proof_id": row["proof_id"],
                "proof_created_at": row["proof_created_at"],
                **verdict,
            }
        )
    return out


def summary(conn) -> dict:
    """占用物化 ↔ 核证协同的总览计数：总占用数与校验失败数。"""
    proofs = list_proofs(conn, limit=100000)
    failed = [p for p in proofs if not p["verify_ok"]]
    return {"proofs_total": len(proofs), "proofs_failed": len(failed)}


def rebuild_proof(conn, occupancy_id: str, actor: str = "ops", reason: str = "manual rebuild") -> dict:
    """重建指纹入口：与集合同源重算并落库，同事务写审计。须在调用方提交前使用。"""
    before = verify_occupancy(conn, occupancy_id)  # 占用不存在时抛 KeyError
    refreshed = refresh_proof(conn, occupancy_id)
    conn.execute(
        "INSERT INTO occupancy_proof_audits(occupancy_id, old_hash, new_hash, old_event_ids_json, new_event_ids_json, actor, reason, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (
            occupancy_id,
            before["stored_hash"],
            refreshed["content_hash"],
            json.dumps(before["stored_event_ids"], ensure_ascii=False)
            if before["stored_event_ids"] is not None
            else None,
            json.dumps(refreshed["event_ids"], ensure_ascii=False),
            actor,
            reason,
            utcnow(),
        ),
    )
    return {
        "rebuilt": True,
        "audit": {
            "old_hash": before["stored_hash"],
            "new_hash": refreshed["content_hash"],
            "actor": actor,
            "reason": reason,
        },
        "proof": verify_occupancy(conn, occupancy_id),
    }


def migrate(conn):
    """老库迁移：补 content_hash 列、建审计表、去重后加一占用一核证唯一索引。

    同步建 hash→成员反查表，并为存量 proof 同源补齐反查行（同事务）。
    """
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(occupancy_proofs)").fetchall()}
    if "content_hash" not in cols:
        conn.execute("ALTER TABLE occupancy_proofs ADD COLUMN content_hash TEXT")
    conn.executescript(AUDIT_DDL)
    conn.execute(
        "DELETE FROM occupancy_proofs WHERE id NOT IN (SELECT MAX(id) FROM occupancy_proofs GROUP BY occupancy_id)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_occupancy_proofs_occ ON occupancy_proofs(occupancy_id)"
    )
    ohm.migrate(conn)


def backfill(conn):
    """存量占用补齐/校准指纹：与集合同源，upsert 幂等；反查同事务同成败补齐。"""
    for row in conn.execute("SELECT id FROM occupancies ORDER BY opened_at, id").fetchall():
        refresh_proof(conn, row["id"])
