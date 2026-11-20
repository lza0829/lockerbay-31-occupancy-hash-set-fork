"""占用成员哈希核证测试（SQLite 本地夹具）。

可直接运行：  python -I tests/test_occupancy_hash_proof.py   （在 backend/ 下）
或 pytest:    pytest tests/test_occupancy_hash_proof.py

覆盖需求 4：
  * 开占用后核对哈希（落库指纹 == 独立手算的有序 id SHA-256）；
  * 故意改成员夹具（增/删成员事件、漏挂核证）必须使校验失败，且接口不得冒充成功；
  * 开账/关账同事务重算指纹；重建指纹留审计且与集合同源。
"""
import hashlib
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

# —— 本地夹具：独立临时 DATA_DIR / FIXTURES_DIR，须在 import app 之前设置 ——
TMP = Path(tempfile.mkdtemp(prefix="lockerbay-occhash-"))
os.environ["DATA_DIR"] = str(TMP / "data")
os.environ["FIXTURES_DIR"] = str(TMP / "fixtures")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FIX_DIR = Path(os.environ["FIXTURES_DIR"])
FIX_DIR.mkdir(parents=True, exist_ok=True)

SEED = {
    "cabinets": [{"id": "CAB-T", "label": "测试柜", "site": "本地"}],
    "cells": [
        {"id": "CELL-1", "cabinet_id": "CAB-T", "code": "T01", "size_code": "M"},
        {"id": "CELL-2", "cabinet_id": "CAB-T", "code": "T02", "size_code": "M"},
    ],
    "events": [
        {
            "id": "E1",
            "cabinet_id": "CAB-T",
            "cell_id": "CELL-1",
            "event_type": "drop",
            "event_ts": "2026-11-18T08:00:00Z",
            "tracking_no": "TN-T1",
            "payload": {},
        }
    ],
}
INGEST_DROP = {
    "events": [
        {
            "id": "E2",
            "cabinet_id": "CAB-T",
            "cell_id": "CELL-2",
            "event_type": "drop",
            "event_ts": "2026-11-19T02:00:00Z",
            "tracking_no": "TN-T2",
            "payload": {},
        }
    ]
}
INGEST_PICKUP = {
    "events": [
        {
            "id": "E3",
            "cabinet_id": "CAB-T",
            "cell_id": "CELL-1",
            "event_type": "pickup",
            "event_ts": "2026-11-19T09:00:00Z",
            "tracking_no": "TN-T1",
            "payload": {},
        }
    ]
}

(FIX_DIR / "seed_bay.json").write_text(json.dumps(SEED), encoding="utf-8")


def _write_ingest(data):
    (FIX_DIR / "ingest_batch.json").write_text(json.dumps(data), encoding="utf-8")


from app import seed  # noqa: E402
from app.db import DB_PATH, connect  # noqa: E402
from app.modules import occupancy_hash_proof as ohp  # noqa: E402
from app.services import ingest as ingest_svc  # noqa: E402

try:
    from app.routers import core as core_api

    HAS_API = True
except ModuleNotFoundError:  # fastapi 未装时仍跑核心逻辑
    HAS_API = False

OCC1 = "OCC-CELL-1-E1"
OCC2 = "OCC-CELL-2-E2"


def reset_db(ingest=INGEST_DROP):
    if DB_PATH.exists():
        DB_PATH.unlink()
    _write_ingest(ingest)
    seed.init_db()


def manual_fingerprint(event_ids):
    """不经过被测模块，独立手算有序 id 指纹。"""
    h = hashlib.sha256()
    h.update(b"lockerbay-occ-hash-v1")
    for eid in event_ids:
        h.update(b"\n")
        h.update(eid.encode("utf-8"))
    return "sha256:" + h.hexdigest()


def tamper_add_event(event_id="E9", cell="CELL-2", tracking="TN-T2", ts="2026-11-19T03:00:00Z"):
    """故意改成员夹具：绕过占用逻辑直接往事件集里塞成员事件。"""
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO parcel_events(id, cabinet_id, cell_id, event_type, event_ts, tracking_no, payload_json, channel) VALUES (?,?,?,?,?,?,?,?)",
            (event_id, "CAB-T", cell, "scan", ts, tracking, "{}", "primary"),
        )
        conn.commit()
    finally:
        conn.close()


def tamper_drop_event(event_id):
    conn = connect()
    try:
        conn.execute("DELETE FROM parcel_events WHERE id=?", (event_id,))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- tests


def test_open_occupancy_writes_matching_fingerprint():
    """开占用后核对哈希：落库指纹 == 独立手算 == 读时互证通过。"""
    reset_db()
    conn = connect()
    try:
        row = conn.execute(
            "SELECT content_hash, event_ids_json FROM occupancy_proofs WHERE occupancy_id=?", (OCC1,)
        ).fetchone()
        assert row is not None, "开占用后必须落核证行"
        assert row["content_hash"], "content_hash 必须落库，禁止只拼展示串"
        assert json.loads(row["event_ids_json"]) == ["E1"]
        assert row["content_hash"] == manual_fingerprint(["E1"]), "落库指纹须为成员 id 有序哈希"
        verdict = ohp.verify_occupancy(conn, OCC1)
        assert verdict["verify_ok"] is True and verdict["fail_reason"] is None
        assert verdict["stored_hash"] == verdict["current_hash"]
    finally:
        conn.close()


def test_ingest_open_close_recompute_same_transaction():
    """装载开账即同事务挂核证（堵漏挂）；关账同事务重算指纹。"""
    reset_db()
    report = ingest_svc.run_batch()
    assert report["accepted"] == 1
    conn = connect()
    try:
        verdict = ohp.verify_occupancy(conn, OCC2)
        assert verdict["verify_ok"] is True, "开账同事务须已重算并落库指纹"
        assert verdict["stored_hash"] == manual_fingerprint(["E2"])
    finally:
        conn.close()
    # 关账：取件事件进批，同事务重算 -> 成员集 [E1, E3]
    reset_db(ingest=INGEST_PICKUP)
    report = ingest_svc.run_batch()
    assert report["accepted"] == 1
    conn = connect()
    try:
        occ = conn.execute("SELECT status FROM occupancies WHERE id=?", (OCC1,)).fetchone()
        assert occ["status"] == "released"
        verdict = ohp.verify_occupancy(conn, OCC1)
        assert verdict["verify_ok"] is True, "关账同事务须重算指纹，禁止成员变了哈希仍旧"
        assert verdict["current_event_ids"] == ["E1", "E3"]
        assert verdict["stored_hash"] == manual_fingerprint(["E1", "E3"])
    finally:
        conn.close()


def test_tampered_member_set_fails_verification():
    """故意改成员夹具：加成员、删成员都必须校验失败，接口不得冒充成功。"""
    reset_db()
    ingest_svc.run_batch()
    # 1) 塞入额外成员事件
    tamper_add_event()
    conn = connect()
    try:
        verdict = ohp.verify_occupancy(conn, OCC2)
        assert verdict["verify_ok"] is False
        assert verdict["fail_reason"] == "member_set_changed"
        assert verdict["stored_hash"] != verdict["current_hash"]
        assert verdict["current_event_ids"] == ["E2", "E9"]
    finally:
        conn.close()
    if HAS_API:
        rows = {r["occupancy_id"]: r for r in core_api.occupancy_proofs()}
        assert rows[OCC2]["verify_ok"] is False, "接口必须暴露失败，禁止冒充成功"
        assert rows[OCC2]["fail_reason"] == "member_set_changed"
        assert rows[OCC1]["verify_ok"] is True
    # 2) 删掉成员事件（漏挂）
    reset_db()
    tamper_drop_event("E1")
    conn = connect()
    try:
        verdict = ohp.verify_occupancy(conn, OCC1)
        assert verdict["verify_ok"] is False
        assert verdict["fail_reason"] == "member_set_changed"
        assert verdict["current_event_ids"] == []
    finally:
        conn.close()


def test_missing_proof_fails_verification():
    """核证漏挂：占用在但无核证行 -> 校验失败且原因 proof_missing。"""
    reset_db()
    conn = connect()
    try:
        conn.execute("DELETE FROM occupancy_proofs WHERE occupancy_id=?", (OCC1,))
        conn.commit()
        verdict = ohp.verify_occupancy(conn, OCC1)
        assert verdict["verify_ok"] is False
        assert verdict["fail_reason"] == "proof_missing"
    finally:
        conn.close()
    if HAS_API:
        rows = {r["occupancy_id"]: r for r in core_api.occupancy_proofs()}
        assert rows[OCC1]["verify_ok"] is False
        assert rows[OCC1]["fail_reason"] == "proof_missing"


def test_rebuild_restores_and_leaves_audit():
    """重建指纹：与集合同源重算后校验恢复，且留审计（旧/新指纹、操作者）。"""
    reset_db()
    ingest_svc.run_batch()
    tamper_add_event()
    conn = connect()
    try:
        before = ohp.verify_occupancy(conn, OCC2)
        assert before["verify_ok"] is False
        result = ohp.rebuild_proof(conn, OCC2, actor="tester", reason="测试重建")
        conn.commit()
        assert result["proof"]["verify_ok"] is True, "重建后须与当前集合同源一致"
        assert result["proof"]["stored_hash"] == manual_fingerprint(["E2", "E9"])
        audits = conn.execute(
            "SELECT * FROM occupancy_proof_audits WHERE occupancy_id=?", (OCC2,)
        ).fetchall()
        assert len(audits) == 1, "重建必须留审计"
        audit = audits[0]
        assert audit["old_hash"] == before["stored_hash"]
        assert audit["new_hash"] == result["proof"]["stored_hash"]
        assert audit["old_hash"] != audit["new_hash"]
        assert audit["actor"] == "tester"
        assert json.loads(audit["new_event_ids_json"]) == ["E2", "E9"]
    finally:
        conn.close()
    if HAS_API:
        reset_db()
        ingest_svc.run_batch()
        tamper_add_event()
        out = core_api.rebuild_occupancy_proof(OCC2, {"actor": "api-tester", "reason": "接口重建"})
        assert out["rebuilt"] is True and out["proof"]["verify_ok"] is True
        conn = connect()
        try:
            n = conn.execute(
                "SELECT COUNT(*) AS c FROM occupancy_proof_audits WHERE occupancy_id=?", (OCC2,)
            ).fetchone()["c"]
            assert n == 1
        finally:
            conn.close()


def test_overview_counts_failed_proofs():
    """占用物化 ↔ 核证页协同：总览须暴露校验失败计数。"""
    if not HAS_API:
        return
    reset_db()
    assert core_api.overview()["proofs_failed"] == 0
    tamper_add_event(cell="CELL-1", tracking="TN-T1", ts="2026-11-18T10:00:00Z")
    data = core_api.overview()
    assert data["proofs_total"] >= 1
    assert data["proofs_failed"] == 1


ALL_TESTS = [
    test_open_occupancy_writes_matching_fingerprint,
    test_ingest_open_close_recompute_same_transaction,
    test_tampered_member_set_fails_verification,
    test_missing_proof_fails_verification,
    test_rebuild_restores_and_leaves_audit,
    test_overview_counts_failed_proofs,
]

if __name__ == "__main__":
    failures = 0
    for fn in ALL_TESTS:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception:
            failures += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(ALL_TESTS) - failures}/{len(ALL_TESTS)} 通过（夹具目录 {TMP}）")
    sys.exit(1 if failures else 0)
