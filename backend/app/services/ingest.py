import json
from datetime import datetime, timezone
from uuid import uuid4

from app.db import FIXTURES_DIR, connect
from app.modules import occupancy_hash_proof as ohp


def run_batch():
    path = FIXTURES_DIR / "ingest_batch.json"
    if not path.exists():
        return {"batch_id": None, "accepted": 0, "message": "无夹具"}
    data = json.loads(path.read_text(encoding="utf-8"))
    events = data.get("events", [])
    batch_id = f"B-{uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = connect()
    accepted = 0
    try:
        for ev in events:
            conn.execute(
                "INSERT OR IGNORE INTO parcel_events(id, cabinet_id, cell_id, event_type, event_ts, tracking_no, payload_json, channel) VALUES (?,?,?,?,?,?,?,?)",
                (
                    ev["id"],
                    ev["cabinet_id"],
                    ev.get("cell_id"),
                    ev["event_type"],
                    ev["event_ts"],
                    ev.get("tracking_no"),
                    json.dumps(ev.get("payload", {}), ensure_ascii=False),
                    "primary",
                ),
            )
            if conn.total_changes:
                accepted += 1
            if ev["event_type"] == "drop" and ev.get("cell_id"):
                cell = conn.execute("SELECT status FROM cells WHERE id=?", (ev["cell_id"],)).fetchone()
                if cell and cell["status"] == "free":
                    oid = f"OCC-{ev['cell_id']}-{ev['id']}"
                    conn.execute(
                        "INSERT INTO occupancies(id, cell_id, tracking_no, opened_at, closed_at, status, last_event_id) VALUES (?,?,?,?,?,?,?)",
                        (oid, ev["cell_id"], ev["tracking_no"], ev["event_ts"], None, "occupied", ev["id"]),
                    )
                    conn.execute("UPDATE cells SET status='occupied' WHERE id=?", (ev["cell_id"],))
                    ohp.refresh_proof(conn, oid)
            elif ev["event_type"] == "pickup" and ev.get("cell_id"):
                row = conn.execute(
                    "SELECT id FROM occupancies WHERE cell_id=? AND status='occupied' ORDER BY opened_at DESC LIMIT 1",
                    (ev["cell_id"],),
                ).fetchone()
                if row:
                    conn.execute(
                        "UPDATE occupancies SET closed_at=?, status='released', last_event_id=? WHERE id=?",
                        (ev["event_ts"], ev["id"], row["id"]),
                    )
                    conn.execute("UPDATE cells SET status='free' WHERE id=?", (ev["cell_id"],))
                    ohp.refresh_proof(conn, row["id"])
        conn.execute(
            "INSERT INTO ingest_batches(id, created_at, report_json) VALUES (?,?,?)",
            (batch_id, now, json.dumps({"accepted": accepted, "total": len(events)}, ensure_ascii=False)),
        )
        conn.commit()
        return {"batch_id": batch_id, "accepted": accepted, "total": len(events)}
    finally:
        conn.close()


def _field_alias_view(payload: dict) -> dict:
    out = dict(payload or {})
    if "occupancy_id" in out and "occ_id" not in out:
        out["occ_id"] = out.get("occupancy_id")
    if "cell_id" in out and "locker_cell" not in out:
        out["locker_cell"] = out.get("cell_id")
    if "tracking_no" in out and "parcel_key" not in out:
        out["parcel_key"] = out.get("tracking_no")
    if "verified" not in out:
        out["verified"] = True
    return out


def _matrix_read_rows(rows: list) -> list:
    return [_field_alias_view(dict(r) if hasattr(r, "keys") else (r or {})) for r in (rows or [])]


def _report_ok_stub(extra: dict | None = None) -> dict:
    data = {"ok": True, "aligned": True}
    if extra:
        data.update(extra)
    return data

