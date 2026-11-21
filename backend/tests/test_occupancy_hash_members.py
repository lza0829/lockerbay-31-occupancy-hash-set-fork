"""占用指纹成员集合反查（hash → 成员事件 id 集合）核证测试。

可直接运行：  python3 -I tests/test_occupancy_hash_members.py   （在 backend/ 下）
或 pytest:    pytest tests/test_occupancy_hash_members.py

覆盖本次修复的硬约束：
  * hash 核证通过时反查必须同成败落库（独立 occupancy_hash_members 表）；
  * 正算成员 ⇔ 反查同集、反查 hash 与 proof hash 同源，分叉即失败；
  * 人为清空反查 / 反查挂旧 hash 必须失败，禁止 hash 通过而反查缺行；
  * 半截写（建反查失败 / 互逆自检失败）整笔回滚，禁止 hash 在反查空；
  * 基线所无的成员反查核证只读接口（按占用 / 按 hash）；
  * 格阵占用列表携带反查对账结果。
"""
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

# —— 本地夹具：独立临时 DATA_DIR / FIXTURES_DIR，须在 import app 之前设置 ——
TMP = Path(tempfile.mkdtemp(prefix="lockerbay-hashmem-"))
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

(FIX_DIR / "seed_bay.json").write_text(json.dumps(SEED), encoding="utf-8")


def _write_ingest(data):
    (FIX_DIR / "ingest_batch.json").write_text(json.dumps(data), encoding="utf-8")


from app import seed  # noqa: E402
from app.db import DB_PATH, connect  # noqa: E402
from app.modules import occupancy_hash_members as ohm  # noqa: E402
from app.modules import occupancy_hash_proof as ohp  # noqa: E402
from app.services import bay as bay_svc  # noqa: E402
from app.services import ingest as ingest_svc  # noqa: E402

try:
    from app.routers import core as core_api

    HAS_API = True
except ModuleNotFoundError:  # fastapi 未装时仍跑核心逻辑
    HAS_API = False

OCC1 = "OCC-CELL-1-E1"
OCC2 = "OCC-CELL-2-E2"


def reset_db():
    if DB_PATH.exists():
        DB_PATH.unlink()
    _write_ingest(INGEST_DROP)
    seed.init_db()


def _stored_hash(conn, occupancy_id):
    return conn.execute(
        "SELECT content_hash FROM occupancy_proofs WHERE occupancy_id=?", (occupancy_id,)
    ).fetchone()["content_hash"]


# ---------------------------------------------------------------- tests


def test_reverse_set_built_same_transaction_as_hash():
    """hash 落库即同事务建反查：成员行、ord、hash 与 proof 全同源。"""
    reset_db()
    conn = connect()
    try:
        proof = conn.execute(
            "SELECT content_hash, event_ids_json FROM occupancy_proofs WHERE occupancy_id=?",
            (OCC1,),
        ).fetchone()
        assert proof is not None and proof["content_hash"]
        rows = conn.execute(
            "SELECT content_hash, member_event_id, ord FROM occupancy_hash_members "
            "WHERE occupancy_id=? ORDER BY ord",
            (OCC1,),
        ).fetchall()
        assert rows, "禁止 hash 通过而反查缺行"
        assert [r["member_event_id"] for r in rows] == json.loads(proof["event_ids_json"]) == ["E1"]
        assert [r["ord"] for r in rows] == [0]
        assert {r["content_hash"] for r in rows} == {proof["content_hash"]}
    finally:
        conn.close()


def test_members_of_inverse_aligned_and_unknown_404():
    """只读反查视图：正算成员 ⇔ 反查同集；占用不存在抛 KeyError（路由 404）。"""
    reset_db()
    conn = connect()
    try:
        view = ohm.members_of(conn, OCC1)
        assert view["aligned"] is True and view["fail_reason"] is None
        assert view["hash"] == _stored_hash(conn, OCC1)
        assert view["member_ids"] == view["forward_event_ids"] == ["E1"]
        assert view["reverse_count"] == view["forward_count"] == 1
        try:
            ohm.members_of(conn, "OCC-NOPE")
        except KeyError:
            pass
        else:
            raise AssertionError("占用不存在必须抛 KeyError 以便接口 404")
    finally:
        conn.close()
    if HAS_API:
        out = core_api.occupancy_hash_members(OCC1)
        assert out["aligned"] is True and out["member_ids"] == ["E1"]
        try:
            core_api.occupancy_hash_members("OCC-NOPE")
        except Exception as e:
            assert getattr(e, "status_code", None) == 404
        else:
            raise AssertionError("只读接口对未知占用必须 404")


def test_lookup_by_hash_returns_member_set():
    """按 hash 反查成员集合（基线所无接口）：命中返回成员；未知 hash 404。"""
    reset_db()
    ingest_svc.run_batch()
    conn = connect()
    try:
        digest = _stored_hash(conn, OCC2)
        out = ohm.members_by_hash(conn, digest)
        assert out["aligned"] is True
        hit = {x["occupancy_id"]: x for x in out["occupancies"]}
        assert OCC2 in hit and hit[OCC2]["member_ids"] == ["E2"]
        assert hit[OCC2]["aligned"] is True
        try:
            ohm.members_by_hash(conn, "sha256:deadbeef")
        except KeyError:
            pass
        else:
            raise AssertionError("未知 hash 反查必须 KeyError（修复前 404/空集合）")
    finally:
        conn.close()
    if HAS_API:
        digest = _stored_hash(connect(), OCC2)
        out = core_api.occupancy_hash_lookup(digest)
        assert any(x["occupancy_id"] == OCC2 for x in out["occupancies"])
        try:
            core_api.occupancy_hash_lookup("sha256:deadbeef")
        except Exception as e:
            assert getattr(e, "status_code", None) == 404
        else:
            raise AssertionError("按 hash 反查未知值必须 404")


def test_manual_clear_reverse_must_fail():
    """人为清空反查：hash 仍在也必须判失败，禁止 hash 通过而反查缺行。"""
    reset_db()
    conn = connect()
    try:
        conn.execute("DELETE FROM occupancy_hash_members WHERE occupancy_id=?", (OCC1,))
        conn.commit()
    finally:
        conn.close()
    conn = connect()
    try:
        verdict = ohp.verify_occupancy(conn, OCC1)
        assert verdict["verify_ok"] is False
        assert verdict["fail_reason"] == "reverse_missing"
        assert verdict["reverse_event_ids"] == []
        view = ohm.verify_members(conn, OCC1)
        assert view["verify_ok"] is False and view["aligned"] is False
        assert view["fail_reason"] == "reverse_missing"
    finally:
        conn.close()
    if HAS_API:
        rows = {r["occupancy_id"]: r for r in core_api.occupancy_proofs()}
        assert rows[OCC1]["verify_ok"] is False
        assert rows[OCC1]["fail_reason"] == "reverse_missing"
        v = core_api.occupancy_hash_members_verify(OCC1)
        assert v["verify_ok"] is False and v["fail_reason"] == "reverse_missing"


def test_reverse_fork_and_stale_hash_must_fail():
    """反查与正算分叉 / 反查挂旧 hash：两类篡改都必须失败且原因可辨。"""
    reset_db()
    # 1) 分叉：反查成员被改
    conn = connect()
    try:
        conn.execute(
            "UPDATE occupancy_hash_members SET member_event_id=? WHERE occupancy_id=? AND ord=0",
            ("E-FORK", OCC1),
        )
        conn.commit()
        verdict = ohp.verify_occupancy(conn, OCC1)
        assert verdict["verify_ok"] is False
        assert verdict["fail_reason"] == "member_set_fork"
        view = ohm.members_of(conn, OCC1)
        assert view["aligned"] is False and view["fail_reason"] == "member_set_fork"
    finally:
        conn.close()
    # 2) 旧 hash：反查 hash 被改、成员仍旧
    reset_db()
    conn = connect()
    try:
        conn.execute(
            "UPDATE occupancy_hash_members SET content_hash=? WHERE occupancy_id=?",
            ("sha256:stale", OCC1),
        )
        conn.commit()
        verdict = ohp.verify_occupancy(conn, OCC1)
        assert verdict["verify_ok"] is False
        assert verdict["fail_reason"] == "reverse_hash_stale"
    finally:
        conn.close()


def test_half_write_rolls_back_savepoint():
    """半截写：建反查失败 / 互逆自检失败 -> 保存点整笔回滚，无 hash 在反查空。"""
    reset_db()
    conn = connect()
    try:
        before_hash = _stored_hash(conn, OCC1)
        before_rev = ohm.reverse_event_ids(conn, OCC1)

        # 场景 A：写 proof 之后建反查抛错 -> proof 改动一并回滚
        orig_replace = ohm.replace_members

        def _boom(c, occ_id, digest, ids):
            raise RuntimeError("模拟反查落库失败")

        ohm.replace_members = _boom
        try:
            try:
                ohp.refresh_proof(conn, OCC1)
            except RuntimeError:
                pass
            else:
                raise AssertionError("反查失败必须向上抛错")
        finally:
            ohm.replace_members = orig_replace
        assert _stored_hash(conn, OCC1) == before_hash, "proof 必须随保存点回滚，禁止半截 hash"
        assert ohm.reverse_event_ids(conn, OCC1) == before_rev, "反查必须保持原状，禁止 hash 在反查空"

        # 场景 B：互逆自检发现分叉 -> 同样整笔回滚
        orig_assert = ohp._assert_inverse

        def _fail(c, occ_id, digest, ids):
            raise ohp.MemberReverseError("模拟互逆自检失败")

        ohp._assert_inverse = _fail
        try:
            try:
                ohp.refresh_proof(conn, OCC1)
            except ohp.MemberReverseError:
                pass
            else:
                raise AssertionError("互逆自检失败必须抛错")
        finally:
            ohp._assert_inverse = orig_assert
        assert _stored_hash(conn, OCC1) == before_hash
        assert ohm.reverse_event_ids(conn, OCC1) == before_rev
    finally:
        conn.close()


def test_ingest_failure_rolls_back_whole_batch():
    """装载途中反查失败：整笔回滚，事件/占用/proof/反查均不得半留。"""
    reset_db()
    orig_replace = ohm.replace_members

    def _boom(c, occ_id, digest, ids):
        raise RuntimeError("模拟装载时反查落库失败")

    ohm.replace_members = _boom
    try:
        try:
            ingest_svc.run_batch()
        except RuntimeError:
            pass
        else:
            raise AssertionError("装载失败必须向上抛错")
    finally:
        ohm.replace_members = orig_replace
    conn = connect()
    try:
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM occupancies WHERE id=?", (OCC2,)
        ).fetchone()["c"] == 0, "占用账必须随整笔回滚"
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM occupancy_proofs WHERE occupancy_id=?", (OCC2,)
        ).fetchone()["c"] == 0, "proof 不得半留"
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM occupancy_hash_members WHERE occupancy_id=?", (OCC2,)
        ).fetchone()["c"] == 0, "反查不得半留（禁止 hash 在反查空）"
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM parcel_events WHERE id='E2'"
        ).fetchone()["c"] == 0, "事件须随事务回滚"
    finally:
        conn.close()
    # 恢复后重跑须成功且反查齐全
    report = ingest_svc.run_batch()
    assert report["accepted"] == 1
    conn = connect()
    try:
        assert ohm.reverse_event_ids(conn, OCC2) == ["E2"]
        assert ohp.verify_occupancy(conn, OCC2)["verify_ok"] is True
    finally:
        conn.close()


def test_rebuild_rebuilds_reverse_set_with_audit():
    """重建指纹同事务重建反查：集合对齐恢复，审计留痕。"""
    reset_db()
    ingest_svc.run_batch()
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO parcel_events(id, cabinet_id, cell_id, event_type, event_ts, tracking_no, payload_json, channel) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("E9", "CAB-T", "CELL-2", "scan", "2026-11-19T03:00:00Z", "TN-T2", "{}", "primary"),
        )
        conn.commit()
        assert ohp.verify_occupancy(conn, OCC2)["verify_ok"] is False
        result = ohp.rebuild_proof(conn, OCC2, actor="tester", reason="反查重建")
        conn.commit()
        assert result["proof"]["verify_ok"] is True
        assert ohm.reverse_event_ids(conn, OCC2) == ["E2", "E9"]
        assert ohm.reverse_hash(conn, OCC2) == result["proof"]["stored_hash"]
        view = ohm.members_of(conn, OCC2)
        assert view["aligned"] is True and view["member_ids"] == ["E2", "E9"]
    finally:
        conn.close()


def test_bay_matrix_carries_reverse_reconciliation():
    """格阵占用列表与 hash 派生集合同源：占用物化行携带反查核证结果。"""
    reset_db()
    conn = connect()
    try:
        matrix = bay_svc.cabinet_matrix(conn)
        occs = [
            cell["occupancy"]
            for cab in matrix
            for cell in cab["cells"]
            if cell["occupancy"] is not None
        ]
        occ1 = next(o for o in occs if o["id"] == OCC1)
        assert occ1["reverse_aligned"] is True
        assert occ1["reverse_member_ids"] == ["E1"]
        assert occ1["reverse_fail_reason"] is None
        # 清空反查后格阵必须显红，不得继续冒充对齐
        conn.execute("DELETE FROM occupancy_hash_members WHERE occupancy_id=?", (OCC1,))
        conn.commit()
        matrix = bay_svc.cabinet_matrix(conn)
        occ1 = next(
            cell["occupancy"]
            for cab in matrix
            for cell in cab["cells"]
            if cell.get("occupancy") and cell["occupancy"]["id"] == OCC1
        )
        assert occ1["reverse_aligned"] is False
        assert occ1["reverse_fail_reason"] == "reverse_missing"
    finally:
        conn.close()


ALL_TESTS = [
    test_reverse_set_built_same_transaction_as_hash,
    test_members_of_inverse_aligned_and_unknown_404,
    test_lookup_by_hash_returns_member_set,
    test_manual_clear_reverse_must_fail,
    test_reverse_fork_and_stale_hash_must_fail,
    test_half_write_rolls_back_savepoint,
    test_ingest_failure_rolls_back_whole_batch,
    test_rebuild_rebuilds_reverse_set_with_audit,
    test_bay_matrix_carries_reverse_reconciliation,
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
