import os
import sqlite3
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
FIXTURES_DIR = Path(
    os.environ.get("FIXTURES_DIR", Path(__file__).resolve().parent.parent.parent / "fixtures")
)
DB_PATH = DATA_DIR / "lockerbay.db"


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
