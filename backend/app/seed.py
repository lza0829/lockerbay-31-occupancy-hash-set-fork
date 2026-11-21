import json

from app.db import FIXTURES_DIR, connect
from app.modules import occupancy_hash_proof as ohp

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cabinets (
  id TEXT PRIMARY KEY,
  label TEXT NOT NULL,
  site TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cells (
  id TEXT PRIMARY KEY,
  cabinet_id TEXT NOT NULL REFERENCES cabinets(id),
  code TEXT NOT NULL,
  size_code TEXT NOT NULL DEFAULT 'M',
  status TEXT NOT NULL DEFAULT 'free',
  UNIQUE(cabinet_id, code)
);

CREATE TABLE IF NOT EXISTS parcel_events (
  id TEXT PRIMARY KEY,
  cabinet_id TEXT NOT NULL,
  cell_id TEXT,
  event_type TEXT NOT NULL,
  event_ts TEXT NOT NULL,
  tracking_no TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  channel TEXT NOT NULL DEFAULT 'primary'
);

CREATE TABLE IF NOT EXISTS occupancies (
  id TEXT PRIMARY KEY,
  cell_id TEXT NOT NULL REFERENCES cells(id),
  tracking_no TEXT NOT NULL,
  opened_at TEXT NOT NULL,
  closed_at TEXT,
  status TEXT NOT NULL DEFAULT 'occupied',
  last_event_id TEXT
);

CREATE TABLE IF NOT EXISTS quarantine (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  reason TEXT NOT NULL,
  member_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingest_batches (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  report_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS occupancy_proofs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  occupancy_id TEXT NOT NULL,
  cell_id TEXT NOT NULL,
  tracking_no TEXT NOT NULL,
  opened_at TEXT NOT NULL,
  closed_at TEXT,
  event_ids_json TEXT NOT NULL,
  content_hash TEXT,
  created_at TEXT NOT NULL
);

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

CREATE TABLE IF NOT EXISTS occupancy_hash_members (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  occupancy_id TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  member_event_id TEXT NOT NULL,
  ord INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(occupancy_id, member_event_id)
);

CREATE INDEX IF NOT EXISTS idx_occ_hash_members_hash
  ON occupancy_hash_members(content_hash);
CREATE INDEX IF NOT EXISTS idx_occ_hash_members_occ
  ON occupancy_hash_members(occupancy_id, ord);
"""


def overdue_hours(conn) -> int:
    row = conn.execute("SELECT value FROM settings WHERE key='overdue_hours'").fetchone()
    return int(row["value"]) if row else 48


def init_db():
    conn = connect()
    conn.executescript(SCHEMA)
    ohp.migrate(conn)
    if not conn.execute("SELECT 1 FROM settings WHERE key='overdue_hours'").fetchone():
        conn.execute("INSERT INTO settings(key, value) VALUES ('overdue_hours', '48')")
    if conn.execute("SELECT COUNT(*) AS c FROM cabinets").fetchone()["c"] == 0:
        _seed(conn)
    ohp.backfill(conn)
    conn.commit()
    conn.close()


def _seed(conn):
    path = FIXTURES_DIR / "seed_bay.json"
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    for c in data.get("cabinets", []):
        conn.execute(
            "INSERT INTO cabinets(id, label, site) VALUES (?,?,?)",
            (c["id"], c["label"], c["site"]),
        )
    for cell in data.get("cells", []):
        conn.execute(
            "INSERT INTO cells(id, cabinet_id, code, size_code, status) VALUES (?,?,?,?,?)",
            (cell["id"], cell["cabinet_id"], cell["code"], cell.get("size_code", "M"), "free"),
        )
    for ev in sorted(data.get("events", []), key=lambda e: (e["event_ts"], e["id"])):
        conn.execute(
            "INSERT INTO parcel_events(id, cabinet_id, cell_id, event_type, event_ts, tracking_no, payload_json, channel) VALUES (?,?,?,?,?,?,?,?)",
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
        if ev["event_type"] == "drop" and ev.get("cell_id"):
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
